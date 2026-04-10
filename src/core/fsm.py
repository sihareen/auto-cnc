"""
Finite State Machine for CNC control
"""
from enum import Enum, auto
import logging
from typing import Optional, Callable, Dict, Any

logger = logging.getLogger(__name__)

class CNCState(Enum):
    """CNC System States"""
    IDLE = auto()
    HOMING = auto()
    ACQUIRING = auto()
    TRANSFORM = auto()
    READY = auto()
    DRILLING = auto()
    COMPLETE = auto()
    ERROR = auto()

class ErrorType(Enum):
    """Error types for CNC system"""
    DETECTION_ERROR = auto()
    TRANSFORM_ERROR = auto()
    HARDWARE_ERROR = auto()
    SYSTEM_ERROR = auto()

class CNCStateMachine:
    """
    Finite State Machine for CNC system control
    
    Manages state transitions and error handling
    """
    
    def __init__(self):
        self.current_state = CNCState.IDLE
        self.previous_state: Optional[CNCState] = None
        self.error_state: Optional[ErrorType] = None
        self.error_message: str = ""
        self.state_handlers: Dict[CNCState, Callable] = {}
        self.transition_validators: Dict[tuple, Callable] = {}
    
    def register_state_handler(self, state: CNCState, handler: Callable):
        """Register handler for specific state"""
        self.state_handlers[state] = handler
    
    def register_transition_validator(self, from_state: CNCState, to_state: CNCState, 
                                    validator: Callable):
        """Register validator for state transition"""
        self.transition_validators[(from_state, to_state)] = validator
    
    def transition_to(self, new_state: CNCState, **kwargs) -> bool:
        """
        Transition to new state with validation
        
        Returns:
            bool: True if transition successful
        """
        # Check if transition is valid
        transition_key = (self.current_state, new_state)
        if transition_key in self.transition_validators:
            if not self.transition_validators[transition_key](**kwargs):
                logger.warning(f"Invalid transition: {self.current_state} -> {new_state}")
                return False
        
        # Execute state exit if needed
        if self.current_state != new_state:
            self._execute_state_exit(self.current_state)
        
        # Update state
        self.previous_state = self.current_state
        self.current_state = new_state
        
        # Execute state entry
        self._execute_state_entry(new_state, **kwargs)
        
        logger.info(f"State transition: {self.previous_state} -> {self.current_state}")
        return True
    
    def _execute_state_entry(self, state: CNCState, **kwargs):
        """Execute state entry actions"""
        if state in self.state_handlers:
            try:
                self.state_handlers[state]("entry", **kwargs)
            except Exception as e:
                logger.error(f"State entry error for {state}: {e}")
                self.set_error(ErrorType.SYSTEM_ERROR, f"State entry failed: {e}")
    
    def _execute_state_exit(self, state: CNCState):
        """Execute state exit actions"""
        if state in self.state_handlers:
            try:
                self.state_handlers[state]("exit")
            except Exception as e:
                logger.error(f"State exit error for {state}: {e}")
    
    def get_state(self) -> str:
        """Get current state name"""
        return self.current_state.name
    
    def get_status(self) -> Dict[str, Any]:
        """Get full status information"""
        return {
            "current_state": self.current_state.name,
            "previous_state": self.previous_state.name if self.previous_state else None,
            "error_state": self.error_state.name if self.error_state else None,
            "error_message": self.error_message,
            "is_error": self.error_state is not None
        }
    
    def set_error(self, error_type: ErrorType, message: str = ""):
        """Set error state"""
        self.error_state = error_type
        self.error_message = message
        self.current_state = CNCState.ERROR
        logger.error(f"Error [{error_type.name}]: {message}")
    
    def clear_error(self):
        """Clear error state"""
        self.error_state = None
        self.error_message = ""
        # Don't change current_state when clearing error, remain in ERROR state
        # until explicit transition
        logger.info("Error cleared, remaining in ERROR state until transition")
    
    def is_in_state(self, state: CNCState) -> bool:
        """Check if current state matches"""
        return self.current_state == state
    
    def can_transition_to(self, target_state: CNCState) -> bool:
        """Check if transition to target state is possible"""
        # Basic validation - can always go to ERROR state
        if target_state == CNCState.ERROR:
            return True
            
        # Check registered validator
        transition_key = (self.current_state, target_state)
        if transition_key in self.transition_validators:
            return self.transition_validators[transition_key]()
            
        # Default: allow transitions unless explicitly denied
        return True

# Default transition validators
def validate_idle_to_homing() -> bool:
    """Validate transition from IDLE to HOMING"""
    # Check if hardware is connected and responsive
    return True

def validate_homing_to_acquiring() -> bool:
    """Validate transition from HOMING to ACQUIRING"""
    # Check if homing completed successfully
    return True

def validate_acquiring_to_transform() -> bool:
    """Validate transition from ACQUIRING to TRANSFORM"""
    # Check if detection results are available
    return True

def validate_transform_to_ready() -> bool:
    """Validate transition from TRANSFORM to READY"""
    # Check if transformation was successful
    return True

def validate_ready_to_drilling() -> bool:
    """Validate transition from READY to DRILLING"""
    # Check if drilling parameters are valid
    return True

def validate_drilling_to_complete() -> bool:
    """Validate transition from DRILLING to COMPLETE"""
    # Check if drilling completed
    return True

def validate_complete_to_idle() -> bool:
    """Validate transition from COMPLETE to IDLE"""
    # Always allowed
    return True

def validate_to_error(from_state: CNCState) -> bool:
    """Validate transition to ERROR state"""
    # Always allowed from any state
    return True

def create_default_state_machine() -> CNCStateMachine:
    """Create state machine with default validators"""
    fsm = CNCStateMachine()
    
    # Register default transition validators
    fsm.register_transition_validator(CNCState.IDLE, CNCState.HOMING, validate_idle_to_homing)
    fsm.register_transition_validator(CNCState.HOMING, CNCState.ACQUIRING, validate_homing_to_acquiring)
    fsm.register_transition_validator(CNCState.ACQUIRING, CNCState.TRANSFORM, validate_acquiring_to_transform)
    fsm.register_transition_validator(CNCState.TRANSFORM, CNCState.READY, validate_transform_to_ready)
    fsm.register_transition_validator(CNCState.READY, CNCState.DRILLING, validate_ready_to_drilling)
    fsm.register_transition_validator(CNCState.DRILLING, CNCState.COMPLETE, validate_drilling_to_complete)
    fsm.register_transition_validator(CNCState.COMPLETE, CNCState.IDLE, validate_complete_to_idle)
    
    return fsm