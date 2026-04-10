"""
Unit tests for DrillJobManager and ExecutionController
"""
import unittest
from unittest.mock import Mock, patch
from src.cnc.job_manager import DrillJobManager, DrillJob, DrillPoint, ExecutionController, JobError

class TestDrillPoint(unittest.TestCase):
    
    def test_create_point(self):
        """Test drill point creation"""
        point = DrillPoint(10.0, 20.0, 0)
        self.assertEqual(point.x, 10.0)
        self.assertEqual(point.y, 20.0)
        self.assertEqual(point.index, 0)
        self.assertFalse(point.is_drilled)
    
    def test_distance_to(self):
        """Test distance calculation"""
        p1 = DrillPoint(0, 0, 0)
        p2 = DrillPoint(3, 4, 1)
        self.assertAlmostEqual(p1.distance_to(p2), 5.0)

class TestDrillJob(unittest.TestCase):
    
    def setUp(self):
        self.job = DrillJob("test123")
    
    def test_create_job(self):
        """Test job creation"""
        self.assertEqual(self.job.job_id, "test123")
        self.assertEqual(len(self.job.points), 0)
        self.assertEqual(self.job.status, "pending")
    
    def test_add_point(self):
        """Test adding single point"""
        self.job.add_point(10.0, 20.0)
        self.assertEqual(len(self.job.points), 1)
    
    def test_add_multiple_points(self):
        """Test adding multiple points"""
        points = [(10, 20), (30, 40), (50, 60)]
        self.job.add_points(points)
        self.assertEqual(len(self.job.points), 3)
    
    def test_mark_drilled(self):
        """Test marking point as drilled"""
        self.job.add_point(10, 20)
        self.job.mark_drilled(0)
        self.assertTrue(self.job.points[0].is_drilled)
    
    def test_get_remaining_points(self):
        """Test getting remaining points"""
        self.job.add_points([(10, 20), (30, 40)])
        self.job.mark_drilled(0)
        remaining = self.job.get_remaining_points()
        self.assertEqual(len(remaining), 1)

class TestDrillJobManager(unittest.TestCase):
    
    def setUp(self):
        self.manager = DrillJobManager(drill_depth=-1.5, feedrate=300)
    
    def test_create_job(self):
        """Test job creation"""
        points = [(10, 20), (30, 40)]
        job = self.manager.create_job(points, optimize=False)
        
        self.assertIsNotNone(job)
        self.assertEqual(job.status, "ready")
        self.assertEqual(len(job.points), 2)
    
    def test_create_job_empty(self):
        """Test creating job with no points"""
        with self.assertRaises(JobError):
            self.manager.create_job([])
    
    def test_generate_gcode(self):
        """Test G-Code generation"""
        points = [(100, 50), (200, 100)]
        job = self.manager.create_job(points, optimize=False)
        
        gcode = self.manager.get_gcode_string()
        self.assertIn("G90", gcode)
        self.assertIn("G21", gcode)
        self.assertIn("G1 Z-1.500", gcode)
        self.assertIn("M2", gcode)
    
    def test_validate_job_valid(self):
        """Test job validation with valid points"""
        points = [(150, -50), (180, -80)]
        job = self.manager.create_job(points, optimize=False)
        
        bounds = {'x': (100, 200), 'y': (-100, 0)}
        is_valid, warnings = self.manager.validate_job(job, bounds)
        
        self.assertTrue(is_valid)
        self.assertEqual(len(warnings), 0)
    
    def test_validate_job_out_of_bounds(self):
        """Test job validation with out of bounds points"""
        points = [(500, 500)]
        job = self.manager.create_job(points, optimize=False)
        
        bounds = {'x': (100, 200), 'y': (-100, 0)}
        is_valid, warnings = self.manager.validate_job(job, bounds)
        
        self.assertFalse(is_valid)
        self.assertGreater(len(warnings), 0)
    
    def test_path_optimization(self):
        """Test path optimization"""
        points = [(0, 0), (100, 100), (50, 50)]
        job = self.manager.create_job(points, optimize=True)
        
        # Should be optimized to minimize travel
        self.assertEqual(len(job.points), 3)
    
    def test_get_job_status(self):
        """Test job status"""
        points = [(100, 50)]
        self.manager.create_job(points, optimize=False)
        
        status = self.manager.get_job_status()
        self.assertEqual(status['status'], 'ready')
        self.assertEqual(status['num_points'], 1)

class TestExecutionController(unittest.TestCase):
    
    def setUp(self):
        self.cnc = Mock()
        self.job_manager = DrillJobManager()
        self.controller = ExecutionController(self.cnc, self.job_manager)
    
    def test_initial_state(self):
        """Test initial state is IDLE"""
        self.assertEqual(self.controller.get_state_name(), "IDLE")
        self.assertEqual(self.controller.execution_state, 0)
    
    def test_start_home(self):
        """Test State 1: Home/Standby"""
        result = self.controller.start_home()
        self.assertTrue(result)
        self.assertEqual(self.controller.get_state_name(), "HOMING")
    
    def test_start_acquisition(self):
        """Test State 2: Visual Acquisition"""
        self.controller.start_home()
        result = self.controller.start_acquisition()
        self.assertTrue(result)
        self.assertEqual(self.controller.get_state_name(), "ACQUIRING")
    
    def test_start_transform(self):
        """Test State 3: Coordinate Transformation"""
        self.controller.start_home()
        self.controller.start_acquisition()
        result = self.controller.start_transform()
        self.assertTrue(result)
        self.assertEqual(self.controller.get_state_name(), "TRANSFORM")
    
    def test_start_drilling(self):
        """Test State 4: Sequential Drilling"""
        self.job_manager.create_job([(100, 50)], optimize=False)
        self.controller.start_home()
        self.controller.start_acquisition()
        self.controller.start_transform()
        result = self.controller.start_drilling()
        self.assertTrue(result)
        self.assertEqual(self.controller.get_state_name(), "DRILLING")
    
    def test_invalid_state_transition(self):
        """Test invalid state transition"""
        # Try to go to ACQUIRING without HOMING
        result = self.controller.start_acquisition()
        self.assertFalse(result)
    
    def test_complete(self):
        """Test completion"""
        self.controller.execution_state = 4
        self.controller.complete()
        self.assertEqual(self.controller.get_state_name(), "COMPLETE")
    
    def test_reset(self):
        """Test reset"""
        self.controller.execution_state = 4
        self.controller.reset()
        self.assertEqual(self.controller.get_state_name(), "IDLE")
    
    def test_get_status(self):
        """Test status"""
        status = self.controller.get_status()
        self.assertEqual(status['state'], "IDLE")
        self.assertIn('execution_state', status)

if __name__ == "__main__":
    unittest.main()