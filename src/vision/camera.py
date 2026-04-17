"""
Camera capture and management for vision system
"""
import cv2
import threading
import time
import logging
from typing import Optional, Tuple, List, Callable, Any
from enum import Enum
import numpy as np

logger = logging.getLogger(__name__)

class CameraState(Enum):
    """Camera operation states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STREAMING = "streaming"
    ERROR = "error"

class CameraError(Exception):
    """Camera-related errors"""
    pass

class CameraCapture:
    """
    Camera capture class for video stream acquisition
    
    Features:
    - Multiple camera backend support
    - Auto-detect USB camera (0ac8:3370)
    - Frame buffering for inference
    - ROI selection and cropping
    - Real-time frame rate control
    """
    
    def __init__(self, camera_source: Any = None,
                 camera_index: int = None,
                 width: int = 1280, 
                 height: int = 720, 
                 fps: int = 30):
        # Initialize base attributes first
        self.cap: Optional[cv2.VideoCapture] = None
        self.state = CameraState.DISCONNECTED
        self.frame_buffer = []
        self.buffer_size = 5
        self.current_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()
        self.streaming = False
        self.stream_thread: Optional[threading.Thread] = None
        self.read_thread: Optional[threading.Thread] = None
        self.callbacks: List[Callable] = []
        self.roi: Optional[Tuple[int, int, int, int]] = None
        self.max_consecutive_read_failures = 12
        self.reopen_backoff_sec = 0.3
        self._last_reopen_ts = 0.0
        
        # Backward compatibility: allow old camera_index parameter.
        if camera_source is None and camera_index is not None:
            camera_source = camera_index

        # Auto-detect USB camera index if source not specified.
        if camera_source is None:
            camera_source = self._find_usb_camera()

        self.camera_source = camera_source
        self.width = width
        self.height = height
        self.fps = fps

    def _open_capture_once(self) -> bool:
        """Open and configure cv2.VideoCapture once."""
        cap = cv2.VideoCapture(self.camera_source)
        if not cap.isOpened():
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if actual_width != self.width or actual_height != self.height:
            logger.warning(
                f"Camera resolution mismatch: requested {self.width}x{self.height}, "
                f"got {actual_width}x{actual_height}"
            )

        # Swap handle only after successful open+configure.
        old_cap = self.cap
        self.cap = cap
        if old_cap is not None:
            try:
                old_cap.release()
            except Exception:
                pass
        return True

    def _reopen_capture(self) -> bool:
        """Best-effort camera reopen for stream auto-recovery."""
        now = time.time()
        if (now - self._last_reopen_ts) < self.reopen_backoff_sec:
            return False
        self._last_reopen_ts = now

        try:
            if self.cap and self.cap.isOpened():
                self.cap.release()
        except Exception:
            pass

        ok = self._open_capture_once()
        if ok:
            logger.info(f"Camera source={self.camera_source} stream recovered")
        else:
            logger.warning(f"Camera source={self.camera_source} reopen failed")
        return ok
    
    def _find_usb_camera(self) -> int:
        """Find USB camera 0ac8:3370 automatically"""
        import subprocess
        for index in range(10):
            try:
                cap = cv2.VideoCapture(index)
                if cap.isOpened():
                    result = subprocess.run(
                        ['v4l2-ctl', '-d', str(index), '--info'],
                        capture_output=True, text=True, timeout=1
                    )
                    if '0ac8' in result.stdout or 'USB 2.0 Camera' in result.stdout:
                        cap.release()
                        logger.info(f"Auto-detected USB Camera (0ac8:3370) at index {index}")
                        return index
                    cap.release()
            except:
                pass
        logger.warning("USB camera not found, using default index 0")
        return 0
        
    def connect(self, max_attempts: int = 3) -> bool:
        """
        Connect to camera with retry logic
        
        Args:
            max_attempts: Maximum connection attempts
            
        Returns:
            bool: True if connection successful
        """
        self.state = CameraState.CONNECTING
        logger.info(f"Connecting to camera source={self.camera_source}...")
        
        for attempt in range(max_attempts):
            try:
                if not self._open_capture_once():
                    raise CameraError(f"Camera source {self.camera_source} not opened")

                self.state = CameraState.CONNECTED
                logger.info(f"Camera source={self.camera_source} connected successfully")
                return True
                
            except Exception as e:
                logger.error(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt == max_attempts - 1:
                    self.state = CameraState.ERROR
                    logger.error(f"Failed to connect to camera source={self.camera_source}")
                    return False
                
                time.sleep(1)  # Wait before retry
    
    def disconnect(self):
        """Disconnect from camera"""
        self.stop_streaming()
        
        if self.cap and self.cap.isOpened():
            self.cap.release()
        
        self.cap = None
        self.state = CameraState.DISCONNECTED
        logger.info(f"Camera source={self.camera_source} disconnected")
    
    def start_streaming(self):
        """Start continuous frame streaming"""
        if self.state != CameraState.CONNECTED:
            raise CameraError("Camera not connected")
            
        if self.streaming:
            logger.warning("Already streaming")
            return
            
        self.streaming = True
        self.stream_thread = threading.Thread(target=self._stream_frames, daemon=True)
        self.stream_thread.start()
        self.state = CameraState.STREAMING
        logger.info("Camera streaming started")
    
    def stop_streaming(self):
        """Stop frame streaming"""
        self.streaming = False
        if self.stream_thread:
            self.stream_thread.join(timeout=2.0)
        self.state = CameraState.CONNECTED
        logger.info("Camera streaming stopped")
    
    def _stream_frames(self):
        """Internal frame streaming loop"""
        frame_count = 0
        start_time = time.time()
        consecutive_read_failures = 0
        
        while self.streaming:
            try:
                if self.cap is None or not self.cap.isOpened():
                    if not self._reopen_capture():
                        time.sleep(0.1)
                        continue
                    consecutive_read_failures = 0

                ret, frame = self.cap.read()
                
                if not ret:
                    consecutive_read_failures += 1
                    if consecutive_read_failures in (1, 5, 10) or (
                        consecutive_read_failures % self.max_consecutive_read_failures == 0
                    ):
                        logger.warning(
                            f"Failed to read frame from camera "
                            f"(consecutive={consecutive_read_failures})"
                        )
                    if consecutive_read_failures >= self.max_consecutive_read_failures:
                        self._reopen_capture()
                        consecutive_read_failures = 0
                    time.sleep(0.1)
                    continue
                consecutive_read_failures = 0
                
                # Apply ROI if set
                if self.roi:
                    x, y, w, h = self.roi
                    frame = frame[y:y+h, x:x+w]
                
                with self.frame_lock:
                    self.current_frame = frame
                    
                    # Maintain buffer
                    self.frame_buffer.append(frame)
                    if len(self.frame_buffer) > self.buffer_size:
                        self.frame_buffer.pop(0)
                
                # Notify callbacks
                for callback in self.callbacks:
                    try:
                        callback(frame)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")
                
                # Calculate and log FPS periodically
                frame_count += 1
                if frame_count % 30 == 0:
                    elapsed = time.time() - start_time
                    fps = frame_count / elapsed
                    logger.debug(f"Streaming FPS: {fps:.1f}")
                
                # Control frame rate
                time.sleep(1.0 / self.fps)
                
            except Exception as e:
                logger.error(f"Streaming error: {e}")
                self._reopen_capture()
                time.sleep(0.1)
    
    def get_frame(self) -> Optional[np.ndarray]:
        """
        Get the current frame
        
        Returns:
            Optional[numpy.ndarray]: Current frame or None if not available
        """
        with self.frame_lock:
            return self.current_frame.copy() if self.current_frame is not None else None
    
    def get_buffered_frames(self, count: int = 1) -> List[np.ndarray]:
        """
        Get multiple frames from buffer
        
        Args:
            count: Number of frames to retrieve
            
        Returns:
            List of frames
        """
        with self.frame_lock:
            return self.frame_buffer[-count:] if self.frame_buffer else []
    
    def set_roi(self, x: int, y: int, width: int, height: int):
        """
        Set Region of Interest
        
        Args:
            x, y: Top-left corner coordinates
            width, height: ROI dimensions
        """
        self.roi = (x, y, width, height)
        logger.info(f"ROI set: ({x}, {y}, {width}, {height})")
    
    def clear_roi(self):
        """Clear Region of Interest"""
        self.roi = None
        logger.info("ROI cleared")
    
    def register_callback(self, callback: Callable):
        """Register frame callback function"""
        self.callbacks.append(callback)
    
    def remove_callback(self, callback: Callable):
        """Remove frame callback function"""
        if callback in self.callbacks:
            self.callbacks.remove(callback)
    
    def get_camera_info(self) -> dict:
        """Get camera information"""
        if not self.cap or not self.cap.isOpened():
            return {}
            
        info = {
            "camera_source": self.camera_source,
            "state": self.state.value,
            "streaming": self.streaming,
            "width": int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": self.cap.get(cv2.CAP_PROP_FPS),
            "brightness": self.cap.get(cv2.CAP_PROP_BRIGHTNESS),
            "contrast": self.cap.get(cv2.CAP_PROP_CONTRAST),
            "saturation": self.cap.get(cv2.CAP_PROP_SATURATION),
            "roi": self.roi
        }
        
        return info
    
    def set_camera_property(self, prop_id: int, value: float) -> bool:
        """Set camera property"""
        if not self.cap or not self.cap.isOpened():
            return False
            
        return self.cap.set(prop_id, value)
    
    def __del__(self):
        """Cleanup on destruction"""
        self.disconnect()
