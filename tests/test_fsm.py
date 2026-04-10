"""
Unit tests for Finite State Machine
"""
import unittest
from unittest.mock import Mock
from src.core.fsm import CNCStateMachine, CNCState, ErrorType

class TestCNCStateMachine(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.fsm = CNCStateMachine()
    
    def test_initial_state(self):
        """Test initial state is IDLE"""
        self.assertEqual(self.fsm.current_state, CNCState.IDLE)
        self.assertIsNone(self.fsm.previous_state)
    
    def test_transition_to_new_state(self):
        """Test basic state transition"""
        result = self.fsm.transition_to(CNCState.HOMING)
        self.assertTrue(result)
        self.assertEqual(self.fsm.current_state, CNCState.HOMING)
        self.assertEqual(self.fsm.previous_state, CNCState.IDLE)
    
    def test_transition_to_same_state(self):
        """Test transition to same state"""
        result = self.fsm.transition_to(CNCState.IDLE)
        self.assertTrue(result)
        self.assertEqual(self.fsm.current_state, CNCState.IDLE)
    
    def test_transition_with_validator(self):
        """Test transition with validator"""
        # Register validator that rejects transition
        validator = Mock(return_value=False)
        self.fsm.register_transition_validator(CNCState.IDLE, CNCState.HOMING, validator)
        
        result = self.fsm.transition_to(CNCState.HOMING)
        self.assertFalse(result)
        self.assertEqual(self.fsm.current_state, CNCState.IDLE)
    
    def test_error_state_transition(self):
        """Test transition to ERROR state"""
        self.fsm.set_error(ErrorType.HARDWARE_ERROR, "Test error")
        
        self.assertEqual(self.fsm.current_state, CNCState.ERROR)
        self.assertEqual(self.fsm.error_state, ErrorType.HARDWARE_ERROR)
        self.assertEqual(self.fsm.error_message, "Test error")
    
    def test_clear_error(self):
        """Test clearing error state"""
        self.fsm.transition_to(CNCState.HOMING)
        self.fsm.set_error(ErrorType.SYSTEM_ERROR, "Test")
        self.fsm.clear_error()
        
        # After clearing error, should remain in ERROR state
        # actual transition would be handled separately
        self.assertEqual(self.fsm.current_state, CNCState.ERROR)
        self.assertIsNone(self.fsm.error_state)
        self.assertEqual(self.fsm.error_message, "")
    
    def test_get_status(self):
        """Test status information"""
        status = self.fsm.get_status()
        
        self.assertEqual(status["current_state"], "IDLE")
        self.assertIsNone(status["previous_state"])
        self.assertIsNone(status["error_state"])
        self.assertFalse(status["is_error"])
    
    def test_is_in_state(self):
        """Test state checking"""
        self.assertTrue(self.fsm.is_in_state(CNCState.IDLE))
        self.assertFalse(self.fsm.is_in_state(CNCState.HOMING))
    
    def test_can_transition_to(self):
        """Test transition possibility checking"""
        # Should be able to transition to ERROR from any state
        self.assertTrue(self.fsm.can_transition_to(CNCState.ERROR))
        
        # Default should allow transition (no validators registered)
        self.assertTrue(self.fsm.can_transition_to(CNCState.HOMING))
    
    def test_state_handler_registration(self):
        """Test state handler registration"""
        handler = Mock()
        self.fsm.register_state_handler(CNCState.HOMING, handler)
        
        self.assertIn(CNCState.HOMING, self.fsm.state_handlers)
        self.assertEqual(self.fsm.state_handlers[CNCState.HOMING], handler)
    
    def test_state_handler_execution(self):
        """Test state handler execution"""
        handler = Mock()
        self.fsm.register_state_handler(CNCState.HOMING, handler)
        
        self.fsm.transition_to(CNCState.HOMING)
        
        # Handler should be called with "entry" action
        handler.assert_called_once_with("entry")
    
    def test_default_validators(self):
        """Test default validator functions"""
        from src.core.fsm import (
            validate_idle_to_homing, validate_homing_to_acquiring,
            validate_acquiring_to_transform, validate_transform_to_ready,
            validate_ready_to_drilling, validate_drilling_to_complete,
            validate_complete_to_idle, validate_to_error
        )
        
        # All default validators should return True
        self.assertTrue(validate_idle_to_homing())
        self.assertTrue(validate_homing_to_acquiring())
        self.assertTrue(validate_acquiring_to_transform())
        self.assertTrue(validate_transform_to_ready())
        self.assertTrue(validate_ready_to_drilling())
        self.assertTrue(validate_drilling_to_complete())
        self.assertTrue(validate_complete_to_idle())
        self.assertTrue(validate_to_error(CNCState.IDLE))

if __name__ == "__main__":
    unittest.main()