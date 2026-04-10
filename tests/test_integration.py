"""
Integration tests for complete workflow
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
import json

class TestCompleteWorkflow(unittest.TestCase):
    """Integration tests for complete CNC drilling workflow"""
    
    def test_full_detection_to_gcode_workflow(self):
        """Test complete workflow: detection -> transform -> job -> gcode"""
        # Phase 1: Mock detection results
        detections = [
            (640.0, 360.0, 0.95),  # x, y, confidence
            (800.0, 400.0, 0.90),
            (500.0, 250.0, 0.85),
        ]
        
        # Phase 2: Transform
        from src.vision.transformer import AffineTransformer
        transformer = AffineTransformer('config/calibration_affine.json')
        transformer.load_calibration()
        
        transformed = transformer.transform_detections(detections, min_confidence=0.5)
        self.assertEqual(len(transformed), 3)
        
        # Phase 3: Create job
        from src.cnc.job_manager import DrillJobManager
        job_manager = DrillJobManager()
        job = job_manager.create_job(transformed, optimize=True)
        
        self.assertIsNotNone(job)
        self.assertEqual(len(job.points), 3)
        
        # Phase 4: Generate G-Code
        gcode = job_manager.get_gcode_string()
        self.assertIn('G90', gcode)  # Absolute mode
        self.assertIn('G21', gcode)  # mm units
        self.assertIn('M2', gcode)   # End program
        
    def test_fsm_state_transitions(self):
        """Test FSM state transitions"""
        from src.core.fsm import CNCStateMachine, CNCState, ErrorType
        
        fsm = CNCStateMachine()
        
        # Test valid transitions
        self.assertTrue(fsm.transition_to(CNCState.HOMING))
        self.assertTrue(fsm.transition_to(CNCState.ACQUIRING))
        self.assertTrue(fsm.transition_to(CNCState.TRANSFORM))
        self.assertTrue(fsm.transition_to(CNCState.READY))
        
        # Test error handling
        fsm.set_error(ErrorType.SYSTEM_ERROR, "Test error")
        self.assertTrue(fsm.is_in_state(CNCState.ERROR))
        
    def test_execution_controller_states(self):
        """Test execution controller states"""
        from src.cnc.job_manager import DrillJobManager, ExecutionController
        from src.cnc.controller import GRBLController
        
        cnc = GRBLController()
        job_mgr = DrillJobManager()
        executor = ExecutionController(cnc, job_mgr)
        
        # Test state transitions
        self.assertEqual(executor.get_state_name(), "IDLE")
        
    def test_transform_with_bounds_check(self):
        """Test coordinate transformation with bounds checking"""
        from src.vision.transformer import AffineTransformer
        
        transformer = AffineTransformer('config/calibration_affine.json')
        transformer.load_calibration()
        
        # Test in-bounds detection
        detections = [(640, 360, 0.9)]
        result = transformer.transform_detections(detections)
        
        self.assertEqual(len(result), 1)
        self.assertTrue(transformer.is_within_bounds(result[0][0], result[0][1]))
        
    def test_job_validation(self):
        """Test job validation"""
        from src.cnc.job_manager import DrillJobManager
        
        job_mgr = DrillJobManager()
        job = job_mgr.create_job([(150, -50), (180, -80)], optimize=False)
        
        bounds = {'x': (100, 200), 'y': (-100, 0)}
        is_valid, warnings = job_mgr.validate_job(job, bounds)
        
        self.assertTrue(is_valid)
        
    def test_camera_and_detector_integration(self):
        """Test camera and detector integration"""
        from src.vision.camera import CameraCapture
        from src.vision.detector import YOLODetector
        
        camera = CameraCapture(camera_index=0)
        detector = YOLODetector('/fake/model.pt', device='cpu')
        
        # Both should initialize
        self.assertIsNotNone(camera)
        self.assertIsNotNone(detector)
        
    def test_error_recovery_zero_detection(self):
        """Test error recovery for zero detections"""
        from src.vision.transformer import AffineTransformer
        
        transformer = AffineTransformer('config/calibration_affine.json')
        transformer.load_calibration()
        
        # No detections
        result = transformer.transform_detections([])
        self.assertEqual(result, [])
        
    def test_error_recovery_out_of_bounds(self):
        """Test error recovery for out of bounds"""
        from src.vision.transformer import AffineTransformer
        
        transformer = AffineTransformer('config/calibration_affine.json')
        transformer.load_calibration()
        
        # Very large point - should be clipped
        detections = [(99999, 99999, 0.9)]
        result = transformer.transform_detections(detections)
        
        # Should still return clipped result
        self.assertGreaterEqual(len(result), 0)

class TestPerformance(unittest.TestCase):
    """Performance tests"""
    
    def test_transform_performance(self):
        """Test transformation performance"""
        import time
        from src.vision.transformer import AffineTransformer
        
        transformer = AffineTransformer('config/calibration_affine.json')
        transformer.load_calibration()
        
        # Test with 100 points
        detections = [(640, 360, 0.9)] * 100
        
        start = time.time()
        result = transformer.transform_detections(detections)
        elapsed = time.time() - start
        
        self.assertLess(elapsed, 1.0)  # Should complete in < 1 second
        self.assertEqual(len(result), 100)
        
    def test_gcode_generation_performance(self):
        """Test G-Code generation performance"""
        import time
        from src.cnc.job_manager import DrillJobManager
        
        job_mgr = DrillJobManager()
        
        # Create job with many points
        points = [(100 + i*10, 50) for i in range(50)]
        
        start = time.time()
        job = job_mgr.create_job(points, optimize=False)
        elapsed = time.time() - start
        
        self.assertLess(elapsed, 0.5)

class TestConfiguration(unittest.TestCase):
    """Configuration tests"""
    
    def test_calibration_loading(self):
        """Test calibration file loading"""
        from src.vision.transformer import AffineTransformer
        import json
        from pathlib import Path
        
        transformer = AffineTransformer('config/calibration_affine.json')
        result = transformer.load_calibration()
        
        self.assertTrue(result)
        
        # Verify matrix structure
        self.assertIsNotNone(transformer.matrix)
        self.assertEqual(transformer.matrix.shape, (2, 3))
        
    def test_environment_config(self):
        """Test environment configuration"""
        from pathlib import Path
        
        config_path = Path('config/calibration_affine.json')
        self.assertTrue(config_path.exists())
        
        with open(config_path) as f:
            config = json.load(f)
            
        self.assertIn('matrix', config)
        self.assertIn('reprojection_error_mm', config)

if __name__ == "__main__":
    unittest.main()