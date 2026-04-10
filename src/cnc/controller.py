"""
GRBL Controller for 3-Axis CNC control
"""
import serial
import time
import threading
import queue
import logging
from enum import Enum
from typing import Optional, Tuple, List, Dict, Any

logger = logging.getLogger(__name__)

class GRBLState(Enum):
    """GRBL machine states"""
    IDLE = "Idle"
    RUN = "Run"
    HOLD = "Hold"
    DOOR = "Door"
    HOME = "Home"
    ALARM = "Alarm"
    CHECK = "Check"

class CNCError(Exception):
    """Base CNC error class"""
    pass

class ConnectionError(CNCError):
    """Connection related errors"""
    pass

class CommandError(CNCError):
    """Command execution errors"""
    pass

class GRBLController:
    """
    Controller for GRBL-based CNC machines
    
    Features:
    - Serial connection management
    - Command queue with streaming
    - Real-time position feedback
    - Error handling and recovery
    """
    
    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 115200, 
                 timeout: float = 2.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn: Optional[serial.Serial] = None
        self.command_queue = queue.Queue()
        self.response_queue = queue.Queue()
        self.current_position: Dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.machine_state: Optional[GRBLState] = None
        self.is_connected = False
        self.is_streaming = False
        self.stream_thread: Optional[threading.Thread] = None
        self.read_thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
    
    def connect(self) -> bool:
        """
        Establish connection to GRBL controller
        
        Returns:
            bool: True if connection successful
        """
        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=self.timeout
            )
            
            # Wait for GRBL to initialize
            time.sleep(2)
            
            # Clear any existing data
            self.serial_conn.reset_input_buffer()
            self.serial_conn.reset_output_buffer()
            
            # Mark as connected before sending test command
            self.is_connected = True
            
            # Get initial status
            try:
                self._send_command("$")
                response = self._read_response()
                logger.info(f"GRBL connected: {response}")
            except ConnectionError:
                # Still consider connected if basic serial works
                logger.warning("GRBL response test failed, but serial connection established")
            
            self._start_streaming()
            return True
            
        except (serial.SerialException, OSError) as e:
            logger.error(f"Connection failed: {e}")
            self.is_connected = False
            return False
    
    def disconnect(self):
        """Disconnect from GRBL controller"""
        self._stop_streaming()
        if self.serial_conn and hasattr(self.serial_conn, 'is_open') and self.serial_conn.is_open:
            self.serial_conn.close()
        self.is_connected = False
        logger.info("GRBL disconnected")
    
    def _send_command(self, command: str) -> bool:
        """Send raw command to GRBL"""
        if not self.is_connected or not self.serial_conn:
            raise ConnectionError("Not connected to GRBL")
        
        try:
            cmd_bytes = (command + "\n").encode()
            self.serial_conn.write(cmd_bytes)
            self.serial_conn.flush()
            logger.debug(f"Sent: {command}")
            return True
            
        except (serial.SerialTimeoutException, serial.SerialException) as e:
            logger.error(f"Send command failed: {e}")
            raise ConnectionError(f"Send failed: {e}")
    
    def _read_response(self, timeout: Optional[float] = None) -> str:
        """Read response from GRBL"""
        if not self.is_connected or not self.serial_conn:
            raise ConnectionError("Not connected to GRBL")
        
        try:
            response = self.serial_conn.readline().decode().strip()
            logger.debug(f"Received: {response}")
            return response
            
        except (serial.SerialTimeoutException, serial.SerialException) as e:
            logger.error(f"Read response failed: {e}")
            raise ConnectionError(f"Read failed: {e}")
    
    def _start_streaming(self):
        """Start command streaming thread"""
        if self.is_streaming:
            return
            
        self.is_streaming = True
        self.stream_thread = threading.Thread(target=self._stream_commands, daemon=True)
        self.read_thread = threading.Thread(target=self._read_responses, daemon=True)
        self.stream_thread.start()
        self.read_thread.start()
        logger.info("Command streaming started")
    
    def _stop_streaming(self):
        """Stop command streaming"""
        self.is_streaming = False
        if self.stream_thread:
            self.stream_thread.join(timeout=1.0)
        if self.read_thread:
            self.read_thread.join(timeout=1.0)
        logger.info("Command streaming stopped")
    
    def _stream_commands(self):
        """Stream commands from queue to GRBL"""
        while self.is_streaming and self.is_connected:
            try:
                command = self.command_queue.get(timeout=0.1)
                self._send_command(command)
                self.command_queue.task_done()
                
            except queue.Empty:
                continue
            except ConnectionError:
                break
    
    def _read_responses(self):
        """Read and process responses from GRBL"""
        while self.is_streaming and self.is_connected:
            try:
                response = self._read_response(timeout=0.1)
                self._process_response(response)
                
            except ConnectionError:
                break
            except Exception as e:
                logger.error(f"Error processing response: {e}")
    
    def _process_response(self, response: str):
        """Process GRBL response"""
        if not response:
            return
            
        # Parse machine state
        if response.startswith("<"):
            self._parse_status_response(response)
        
        # Queue response for command tracking
        self.response_queue.put(response)
    
    def _parse_status_response(self, status_str: str):
        """Parse GRBL status response"""
        try:
            # Example: <Idle|MPos:0.000,0.000,0.000|FS:0,0>
            parts = status_str[1:-1].split("|")
            
            # Parse machine state
            state_str = parts[0]
            self.machine_state = GRBLState(state_str)
            
            # Parse machine position
            for part in parts:
                if part.startswith("MPos:"):
                    pos_str = part[5:]
                    coords = pos_str.split(",")
                    if len(coords) == 3:
                        self.current_position["x"] = float(coords[0])
                        self.current_position["y"] = float(coords[1])
                        self.current_position["z"] = float(coords[2])
            
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse status: {status_str}, error: {e}")
    
    def queue_command(self, command: str, block: bool = False) -> str:
        """
        Queue a command for execution
        
        Args:
            command: G-code command
            block: Wait for command completion
            
        Returns:
            str: Command ID or empty string
        """
        if not self.is_connected:
            raise ConnectionError("Not connected to GRBL")
            
        self.command_queue.put(command)
        
        if block:
            # Wait for command completion (simplified)
            time.sleep(0.1)
            
        return command
    
    def get_status(self) -> Dict[str, Any]:
        """Get current machine status"""
        return {
            "connected": self.is_connected,
            "state": self.machine_state.value if self.machine_state else None,
            "position": self.current_position,
            "queue_size": self.command_queue.qsize()
        }
    
    def emergency_stop(self):
        """Immediate emergency stop"""
        try:
            # Send Ctrl+X for emergency stop
            if self.serial_conn:
                self.serial_conn.write(b"\x18")
                self.serial_conn.flush()
                logger.warning("Emergency stop triggered")
                
        except Exception as e:
            logger.error(f"Emergency stop failed: {e}")
        finally:
            # Clear command queue
            while not self.command_queue.empty():
                try:
                    self.command_queue.get_nowait()
                    self.command_queue.task_done()
                except queue.Empty:
                    break
    
    def move_to(self, x: Optional[float] = None, y: Optional[float] = None, 
               z: Optional[float] = None, feedrate: int = 1000) -> bool:
        """
        Move to specified coordinates
        
        Args:
            x, y, z: Target coordinates
            feedrate: Movement speed
            
        Returns:
            bool: True if command queued successfully
        """
        coords = []
        if x is not None:
            coords.append(f"X{x:.3f}")
        if y is not None:
            coords.append(f"Y{y:.3f}")
        if z is not None:
            coords.append(f"Z{z:.3f}")
            
        if not coords:
            return False
            
        command = f"G1 {' '.join(coords)} F{feedrate}"
        self.queue_command(command)
        return True
    
    def home_axis(self, axis: str = "XYZ") -> bool:
        """
        Home specified axis
        
        Args:
            axis: Axis to home (e.g., "XYZ", "Z")
            
        Returns:
            bool: True if command queued successfully
        """
        if not all(a in "XYZ" for a in axis):
            raise ValueError("Axis must be one of X, Y, Z, or combination")
            
        command = f"$H{axis}"
        self.queue_command(command)
        return True
    
    def set_home_position(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> bool:
        """Set current position as home"""
        commands = [
            f"G92 X{x:.3f} Y{y:.3f} Z{z:.3f}",
            "G10 P0 L20 X0 Y0 Z0"  # Set coordinate system
        ]
        
        for cmd in commands:
            self.queue_command(cmd)
            
        return True