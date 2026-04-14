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
        self.last_status_timestamp = 0.0

    def _wait_for_command_ack(self, command: Optional[str] = None, timeout: Optional[float] = None) -> bool:
        """
        Wait for GRBL acknowledgement for a queued command.

        Returns:
            bool: True when 'ok' is received, False for timeout/error response.
        """
        if timeout is None:
            timeout = 120.0 if (command and command.startswith("$H")) else 5.0

        end_time = time.time() + timeout
        while time.time() < end_time:
            remaining = max(0.05, end_time - time.time())
            try:
                response = self.response_queue.get(timeout=remaining)
            except queue.Empty:
                continue

            normalized = response.strip().lower()
            if normalized == "ok":
                return True
            if normalized.startswith("error") or normalized.startswith("alarm"):
                logger.error(f"GRBL command rejected: {response}")
                return False

        if command and command.startswith("$H"):
            # Some firmware/controllers may not emit a timely 'ok' for homing.
            # Downstream wait_until_idle() is used for completion truth.
            logger.warning("Timeout waiting homing ACK; continue with status-based wait")
            return True

        logger.error("Timeout waiting for GRBL acknowledgement")
        return False
    
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
            with self.lock:
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
                if not self._wait_for_command_ack(command=command):
                    logger.error(f"No ACK for command: {command}")
                self.command_queue.task_done()
                
            except queue.Empty:
                continue
            except ConnectionError:
                break

    def wait_until_idle(self, timeout: float = 30.0, poll_interval: float = 0.05) -> bool:
        """
        Wait until controller queue is empty and GRBL state is Idle.

        Returns:
            bool: True when machine appears idle before timeout.
        """
        if not self.is_connected or not self.serial_conn:
            return False

        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                with self.lock:
                    # Realtime status query in GRBL protocol.
                    self.serial_conn.write(b"?")
                    self.serial_conn.flush()
            except Exception as e:
                logger.error(f"Status polling failed: {e}")
                return False

            if self.command_queue.empty() and self.machine_state == GRBLState.IDLE:
                return True

            time.sleep(poll_interval)

        logger.error("Timeout waiting for CNC idle state")
        return False
    
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
            return

        # Queue non-status responses for command ACK tracking.
        self.response_queue.put(response)
    
    def _parse_status_response(self, status_str: str):
        """Parse GRBL status response"""
        try:
            # Example: <Idle|MPos:0.000,0.000,0.000|FS:0,0>
            parts = status_str[1:-1].split("|")
            
            # Parse machine state
            state_str = parts[0].split(":", 1)[0]
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
            self.last_status_timestamp = time.time()
            
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse status: {status_str}, error: {e}")

    def query_status_once(self, timeout: float = 1.0) -> Dict[str, Any]:
        """Request one GRBL realtime status packet and return latest status."""
        if not self.is_connected or not self.serial_conn:
            return self.get_status()

        prev_ts = self.last_status_timestamp
        try:
            with self.lock:
                self.serial_conn.write(b"?")
                self.serial_conn.flush()
        except Exception as e:
            logger.error(f"Failed to request status: {e}")
            return self.get_status()

        end_time = time.time() + timeout
        while time.time() < end_time:
            if self.last_status_timestamp > prev_ts:
                break
            time.sleep(0.02)

        return self.get_status()
    
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
               z: Optional[float] = None, feedrate: int = 1000,
               wait: bool = False, timeout: float = 30.0) -> bool:
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
        if wait:
            return self.wait_until_idle(timeout=timeout)
        return True

    def jog_relative(self,
                     dx: Optional[float] = None,
                     dy: Optional[float] = None,
                     dz: Optional[float] = None,
                     feedrate: int = 600,
                     wait: bool = False,
                     timeout: float = 30.0) -> bool:
        """Run signed relative jog using GRBL incremental mode (G91)."""
        coords = []
        if dx is not None and abs(dx) >= 1e-9:
            coords.append(f"X{dx:.3f}")
        if dy is not None and abs(dy) >= 1e-9:
            coords.append(f"Y{dy:.3f}")
        if dz is not None and abs(dz) >= 1e-9:
            coords.append(f"Z{dz:.3f}")

        if not coords:
            return False

        # Ensure feedrate is always a positive integer accepted by GRBL.
        feed = max(1, int(feedrate))

        self.queue_command("G91")
        self.queue_command(f"G1 {' '.join(coords)} F{feed}")
        self.queue_command("G90")

        if wait:
            return self.wait_until_idle(timeout=timeout)
        return True
    
    def home_axis(self, axis: str = "XYZ", wait: bool = False, timeout: float = 60.0) -> bool:
        """
        Home specified axis
        
        Args:
            axis: Axis to home (e.g., "XYZ", "Z")
            
        Returns:
            bool: True if command queued successfully
        """
        if not all(a in "XYZ" for a in axis):
            raise ValueError("Axis must be one of X, Y, Z, or combination")
        
        # Unlock GRBL before homing (required if in alarm state)
        self.queue_command("$X")
        time.sleep(0.2)
        
        # GRBL 1.1 standard homing command is `$H` (without axis suffix).
        command = "$H"
        self.queue_command(command)
        if wait:
            return self.wait_until_idle(timeout=timeout)
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

    def _drain_response_queue(self):
        """Clear pending response messages to avoid stale ACK parsing."""
        while not self.response_queue.empty():
            try:
                self.response_queue.get_nowait()
            except queue.Empty:
                break

    def unlock(self, timeout: float = 5.0) -> bool:
        """Unlock GRBL alarm/lock state using `$X`."""
        if not self.is_connected:
            return False

        self._drain_response_queue()
        self.queue_command("$X")
        return self._wait_for_command_ack(timeout=timeout)

    def recover_from_reset(self,
                           clearance_z: float = 5.0,
                           home_after_reset: bool = True,
                           home_axis: str = "XYZ") -> bool:
        """
        Recover controller after reset/stop.

        Sequence:
        1) Emergency stop and clear queued commands
        2) Optional homing
        3) Move Z to clearance height
        """
        if not self.is_connected:
            return False

        self.emergency_stop()
        time.sleep(0.2)

        if home_after_reset:
            if not self.home_axis(home_axis, wait=True, timeout=120.0):
                logger.error("GRBL homing failed during reset recovery")
                return False

        if not self.move_to(z=clearance_z, feedrate=1000, wait=True, timeout=30.0):
            logger.error("Failed to move Z to clearance during reset recovery")
            return False

        return True
