"""
DrillJobManager for G-Code generation and job orchestration
"""
import json
import logging
import uuid
from typing import List, Tuple, Optional, Dict, Any
from pathlib import Path
import numpy as np

logger = logging.getLogger(__name__)

class JobError(Exception):
    """Job-related errors"""
    pass

class DrillPoint:
    """Represents a single drill point"""
    
    def __init__(self, x: float, y: float, index: int = 0):
        self.x = x
        self.y = y
        self.index = index
        self.is_drilled = False
    
    def __repr__(self):
        return f"DrillPoint({self.x:.2f}, {self.y:.2f}, idx={self.index})"
    
    def __eq__(self, other):
        return self.x == other.x and self.y == other.y
    
    def distance_to(self, other: 'DrillPoint') -> float:
        """Calculate Euclidean distance to another point"""
        return np.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)

class DrillJob:
    """Represents a complete drill job"""
    
    def __init__(self, job_id: Optional[str] = None):
        self.job_id = job_id or str(uuid.uuid4())[:8]
        self.points: List[DrillPoint] = []
        self.gcode: List[str] = []
        self.created_at = None
        self.status = "pending"  # pending, ready, running, complete, error
        self.progress = 0
        self.total_points = 0
    
    def add_point(self, x: float, y: float):
        """Add a drill point"""
        point = DrillPoint(x, y, len(self.points))
        self.points.append(point)
    
    def add_points(self, points: List[Tuple[float, float]]):
        """Add multiple drill points"""
        for x, y in points:
            self.add_point(x, y)
        self.total_points = len(self.points)
    
    def get_remaining_points(self) -> List[DrillPoint]:
        """Get points that haven't been drilled yet"""
        return [p for p in self.points if not p.is_drilled]
    
    def mark_drilled(self, index: int):
        """Mark a point as drilled"""
        if 0 <= index < len(self.points):
            self.points[index].is_drilled = True
            self.progress = sum(1 for p in self.points if p.is_drilled)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'job_id': self.job_id,
            'num_points': len(self.points),
            'total_points': self.total_points,
            'progress': self.progress,
            'status': self.status,
            'points': [(p.x, p.y) for p in self.points]
        }

class DrillJobManager:
    """
    Manages drill jobs from detected points to G-Code
    
    Features:
    - G-Code generation
    - Path optimization
    - Job validation
    - Progress tracking
    """
    
    def __init__(self, 
                 drill_depth: float = -1.5,
                 feedrate: int = 300,
                 rapid_feedrate: int = 1000,
                 clearance_height: float = 5.0):
        self.drill_depth = drill_depth
        self.feedrate = feedrate
        self.rapid_feedrate = rapid_feedrate
        self.clearance_height = clearance_height
        self.current_job: Optional[DrillJob] = None
    
    def create_job(self, points: List[Tuple[float, float]], 
                  optimize: bool = True) -> DrillJob:
        """
        Create a drill job from points
        
        Args:
            points: List of (x, y) machine coordinates
            optimize: Whether to optimize path
            
        Returns:
            DrillJob: Ready to execute
        """
        if not points:
            raise JobError("No points provided")
        
        job = DrillJob()
        job.add_points(points)
        
        if optimize:
            self._optimize_path(job)
        
        self._generate_gcode(job)
        job.status = "ready"
        self.current_job = job
        
        logger.info(f"Created job {job.job_id} with {len(points)} points")
        return job
    
    def _optimize_path(self, job: DrillJob):
        """Optimize drilling path using nearest-neighbor + lightweight 2-opt."""
        if len(job.points) <= 1:
            return
        
        points = job.points.copy()
        optimized = []
        remaining = points.copy()
        
        current = remaining.pop(0)
        optimized.append(current)
        
        while remaining:
            nearest = min(remaining, key=lambda p: current.distance_to(p))
            optimized.append(nearest)
            remaining.remove(nearest)
            current = nearest

        improved = self._two_opt(optimized, max_passes=2)
        job.points = improved
        logger.info(f"Optimized path: {len(improved)} points")

    def _path_length(self, points: List[DrillPoint]) -> float:
        if len(points) <= 1:
            return 0.0
        return float(sum(points[i].distance_to(points[i + 1]) for i in range(len(points) - 1)))

    def _two_opt(self, points: List[DrillPoint], max_passes: int = 2) -> List[DrillPoint]:
        """Short bounded 2-opt pass to reduce zig-zag motion cost."""
        if len(points) < 4:
            return points

        best = points[:]
        best_len = self._path_length(best)
        n = len(best)

        for _ in range(max_passes):
            changed = False
            for i in range(1, n - 2):
                for k in range(i + 1, n - 1):
                    if k - i < 2:
                        continue
                    candidate = best[:i] + list(reversed(best[i:k + 1])) + best[k + 1:]
                    cand_len = self._path_length(candidate)
                    if cand_len + 1e-9 < best_len:
                        best = candidate
                        best_len = cand_len
                        changed = True
            if not changed:
                break

        return best
    
    def _generate_gcode(self, job: DrillJob):
        """Generate G-Code for drill job"""
        gcode = []
        
        header = [
            "; Drill Job",
            f"; Job ID: {job.job_id}",
            f"; Created: auto-cnc",
            "",
            "G90  ; Absolute mode",
            "G21  ; mm units",
            f"F{self.rapid_feedrate} ; Set rapid feed",
            "",
        ]
        gcode.extend(header)
        
        for i, point in enumerate(job.points):
            x, y = point.x, point.y
            
            gcode.append(f"; Point {i+1}: X{x:.3f} Y{y:.3f}")
            gcode.append(f"G0 X{x:.3f} Y{y:.3f}")
            gcode.append(f"G1 Z{self.drill_depth:.3f} F{self.feedrate}")
            gcode.append(f"G0 Z{self.clearance_height}")
        
        footer = [
            "",
            f"; Job complete: {job.job_id}",
            "M2  ; End program"
        ]
        gcode.extend(footer)
        
        job.gcode = gcode
        logger.info(f"Generated {len(gcode)} lines of G-Code")
    
    def get_gcode_string(self) -> str:
        """Get G-Code as string"""
        if not self.current_job:
            raise JobError("No current job")
        return "\n".join(self.current_job.gcode)
    
    def save_gcode(self, filepath: str) -> bool:
        """Save G-Code to file"""
        if not self.current_job:
            raise JobError("No current job")
        
        try:
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'w') as f:
                f.write(self.get_gcode_string())
            logger.info(f"Saved G-Code to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to save G-Code: {e}")
            return False
    
    def validate_job(self, job: DrillJob, 
                   workspace_bounds: Dict[str, Tuple[float, float]]) -> Tuple[bool, List[str]]:
        """
        Validate job points are within workspace
        
        Returns:
            Tuple of (is_valid, warnings)
        """
        warnings = []
        
        if not job.points:
            return False, ["No points in job"]
        
        x_min, x_max = workspace_bounds.get('x', (0, 300))
        y_min, y_max = workspace_bounds.get('y', (0, 200))
        
        for point in job.points:
            if not (x_min <= point.x <= x_max):
                warnings.append(f"Point {point.index} X={point.x:.1f} out of bounds")
            if not (y_min <= point.y <= y_max):
                warnings.append(f"Point {point.index} Y={point.y:.1f} out of bounds")
        
        is_valid = len(warnings) == 0
        
        logger.info(f"Job validation: {'valid' if is_valid else 'warnings'}")
        return is_valid, warnings
    
    def get_job_status(self) -> Dict[str, Any]:
        """Get current job status"""
        if not self.current_job:
            return {'status': 'no_job'}
        return self.current_job.to_dict()
    
    def reset_job(self):
        """Reset current job"""
        self.current_job = None
        logger.info("Job reset")


class ExecutionController:
    """
    Controls drill job execution with state machine
    
    States:
    - State 1: Home/Standby (Y-90 clearance)
    - State 2: Visual Acquisition
    - State 3: Coordinate Transformation  
    - State 4: Sequential Drilling
    """
    
    def __init__(self, cnc_controller, job_manager: DrillJobManager):
        self.cnc = cnc_controller
        self.job_manager = job_manager
        self.execution_state = 0  # 0=IDLE, 1=HOMING, 2=ACQUIRING, 3=TRANSFORM, 4=DRILLING, 5=COMPLETE
        self.state_names = ["IDLE", "HOMING", "ACQUIRING", "TRANSFORM", "DRILLING", "COMPLETE"]
    
    def get_state_name(self) -> str:
        """Get current execution state name"""
        return self.state_names[self.execution_state]
    
    def start_home(self) -> bool:
        """Start State 1: Home/Standby"""
        if self.execution_state != 0:
            logger.warning("Not in IDLE state")
            return False
        
        self.cnc.move_to(z=self.job_manager.clearance_height)
        self.execution_state = 1
        logger.info("State 1: Home/Standby")
        return True
    
    def start_acquisition(self) -> bool:
        """Start State 2: Visual Acquisition"""
        if self.execution_state != 1:
            logger.warning("Not in HOMING state")
            return False
        
        self.execution_state = 2
        logger.info("State 2: Visual Acquisition - Waiting for detection")
        return True
    
    def start_transform(self) -> bool:
        """Start State 3: Coordinate Transformation"""
        if self.execution_state != 2:
            logger.warning("Not in ACQUIRING state")
            return False
        
        self.execution_state = 3
        logger.info("State 3: Coordinate Transformation")
        return True
    
    def start_drilling(self) -> bool:
        """Start State 4: Sequential Drilling"""
        if self.execution_state != 3:
            logger.warning("Not in TRANSFORM state")
            return False
        
        if not self.job_manager.current_job:
            logger.warning("No job to execute")
            return False
        
        self.execution_state = 4
        logger.info("State 4: Sequential Drilling started")
        return True
    
    def complete(self) -> bool:
        """Complete execution"""
        self.execution_state = 5
        self.cnc.move_to(z=self.job_manager.clearance_height)
        logger.info("Execution complete")
        return True
    
    def reset(self) -> bool:
        """Reset to IDLE state"""
        self.execution_state = 0
        self.job_manager.reset_job()
        logger.info("Execution reset to IDLE")
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """Get execution status"""
        return {
            'state': self.get_state_name(),
            'execution_state': self.execution_state,
            'job': self.job_manager.get_job_status() if self.job_manager.current_job else None
        }
