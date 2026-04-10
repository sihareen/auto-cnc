"""
Unit tests for CNC controller
"""
import unittest
from unittest.mock import Mock, patch
import serial
from src.cnc.controller import GRBLController, GRBLState, ConnectionError

class TestGRBLController(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.controller = GRBLController(port="/dev/ttyTEST", baudrate=115200)
    
    @patch('serial.Serial')
    def test_connect_success(self, mock_serial):
        """Test successful connection"""
        mock_serial_instance = Mock()
        mock_serial.return_value = mock_serial_instance
        mock_serial_instance.readline.return_value = b"ok\n"
        
        result = self.controller.connect()
        self.assertTrue(result)
        self.assertTrue(self.controller.is_connected)
    
    @patch('serial.Serial')
    def test_connect_failure(self, mock_serial):
        """Test connection failure"""
        mock_serial.side_effect = serial.SerialException("Port not found")
        
        result = self.controller.connect()
        self.assertFalse(result)
        self.assertFalse(self.controller.is_connected)
    
    def test_disconnect_not_connected(self):
        """Test disconnect when not connected"""
        self.controller.disconnect()
        self.assertFalse(self.controller.is_connected)
    
    @patch('serial.Serial')
    def test_send_command_not_connected(self, mock_serial):
        """Test sending command when not connected"""
        with self.assertRaises(ConnectionError):
            self.controller._send_command("G1 X10")
    
    @patch('serial.Serial')
    def test_read_response_not_connected(self, mock_serial):
        """Test reading response when not connected"""
        with self.assertRaises(ConnectionError):
            self.controller._read_response()
    
    @patch('serial.Serial')
    def test_emergency_stop(self, mock_serial):
        """Test emergency stop functionality"""
        mock_serial_instance = Mock()
        mock_serial.return_value = mock_serial_instance
        
        self.controller.connect()
        self.controller.emergency_stop()
        
        # Should have written emergency stop character
        mock_serial_instance.write.assert_called_with(b"\x18")
    
    def test_parse_status_response_valid(self):
        """Test parsing valid status response"""
        status_str = "<Idle|MPos:10.000,20.000,5.000|FS:0,0>"
        self.controller._parse_status_response(status_str)
        
        self.assertEqual(self.controller.machine_state, GRBLState.IDLE)
        self.assertEqual(self.controller.current_position["x"], 10.0)
        self.assertEqual(self.controller.current_position["y"], 20.0)
        self.assertEqual(self.controller.current_position["z"], 5.0)
    
    def test_parse_status_response_invalid(self):
        """Test parsing invalid status response"""
        status_str = "<Invalid|BadFormat>"
        self.controller._parse_status_response(status_str)
        
        # Should not crash, position should remain unchanged
        self.assertEqual(self.controller.current_position["x"], 0.0)
    
    def test_move_to_coordinates(self):
        """Test coordinate movement command generation"""
        with patch.object(self.controller, 'queue_command') as mock_queue:
            self.controller.is_connected = True
            self.controller.move_to(x=10.0, y=20.0, feedrate=1000)
            
            mock_queue.assert_called_with("G1 X10.000 Y20.000 F1000")
    
    def test_home_axis(self):
        """Test homing command generation"""
        with patch.object(self.controller, 'queue_command') as mock_queue:
            self.controller.is_connected = True
            self.controller.home_axis("XYZ")
            
            mock_queue.assert_called_with("$HXYZ")
    
    def test_set_home_position(self):
        """Test set home position commands"""
        with patch.object(self.controller, 'queue_command') as mock_queue:
            self.controller.is_connected = True
            self.controller.set_home_position(x=100.0, y=200.0)
            
            # Should queue multiple commands
            self.assertGreaterEqual(mock_queue.call_count, 2)

class TestGRBLStateEnum(unittest.TestCase):
    
    def test_state_values(self):
        """Test GRBL state enum values"""
        self.assertEqual(GRBLState.IDLE.value, "Idle")
        self.assertEqual(GRBLState.RUN.value, "Run")
        self.assertEqual(GRBLState.HOLD.value, "Hold")
        self.assertEqual(GRBLState.ALARM.value, "Alarm")

if __name__ == "__main__":
    unittest.main()