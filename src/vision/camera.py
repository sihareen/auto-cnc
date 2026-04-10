"""
Camera capture and management for vision system
"""
import cv2
import threading
import time
import logging
from typing import Optional, Tuple, List, Callable
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
    - Frame buffering for inference
    - ROI selection and cropping
    - Real-time frame rate control
    """
    
    def __init__(self, camera_index: int = 0, 
                 width: int = 1920, 
                 height: int = 1080, 
                 fps: int = 30):
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.cap: Optional[cv2.VideoCapture] = None
        self.state = CameraState.DISCONNECTED
        self.frame_buffer = []
        self.buffer_size = 5  # Number of frames to buffer
        self.current_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()
        self.streaming = False
        self.stream_thread: Optional[threading.Thread] = None
        self.callbacks: List[Callable] = []
        self.roi: Optional[Tuple[int, int, int, int]] = None  # x, y, w, h
        
    def connect(self, max_attempts: int = 3) -> bool:
        """
        Connect to camera with retry logic
        
        Args:
            max_attempts: Maximum connection attempts
            
        Returns:
            bool: True if connection successful
        """
        self.state = CameraState.CONNECTING
        logger.info(f"Connecting to camera {self.camera_index}...")
        
        for attempt in range(max_attempts):
            try:
                self.cap = cv2.VideoCapture(self.camera_index)
                
                if not self.cap.isOpened():
                    raise CameraError(f"Camera {self.camera_index} not opened")
                
                # Set camera properties
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                self.cap.set(cv2.CAP_PROP_FPS, self.fps)
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimal buffer
                
                # Verify settings
                actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                
                if actual_width != self.width or actual_height != self.height:
                    logger.warning(f"Camera resolution mismatch: "
                                 f"requested {self.width}x{self.height}, "
                                 f"got {actual_width}x{actual_height}")
                
                self.state = CameraState.CONNECTED
                logger.info(f"Camera {self.camera_index} connected successfully")
                return True
                
            except Exception as e:
                logger.error(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt == max_attempts - 1:
                    self.state = CameraState.ERROR
                    logger.error(f"Failed to connect to camera {self.camera_index}")
                    return False
                
                time.sleep(1)  # Wait before retry
    
    def disconnect(self):
        """Disconnect from camera"""
        self.stop_streaming()
        
        if self.cap and self.cap.isOpened():
            self.cap.release()
        
        self.cap = None
        self.state = CameraState.DISCONNECTED
        logger.info(f"Camera {self.camera_index} disconnected")
    
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
        
        while self.streaming and self.cap and self.cap.isOpened():
            try:
                ret, frame = self.cap.read()
                
                if not ret:
                    logger.error("Failed to read frame from camera")
                    time.sleep(0.1)
                    continue
                
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
            "camera_index": self.camera_index,
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