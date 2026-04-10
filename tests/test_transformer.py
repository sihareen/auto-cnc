"""
Unit tests for AffineTransformer
"""
import unittest
from unittest.mock import patch, mock_open
import json
import numpy as np
from src.vision.transformer import AffineTransformer, TransformError

# Mock calibration data
MOCK_CALIBRATION = {
    "type": "affine2d",
    "matrix": [
        [8.776043934764506e-13, -0.14339583333514244, 223.1791666666669],
        [0.1423325673545303, 0.019604166666669767, -152.94401002888603]
    ],
    "src_points_px": [[213.0, 120.0], [640.0, 120.0], [1066.0, 120.0]],
    "dst_points_mm": [[206.0, -119.3], [206.0, -58.9], [206.0, 2.3]],
    "reprojection_error_mm": 1.2176471587298812,
    "per_point_error_mm": [0.97, 0.59, 1.16]
}

class TestAffineTransformer(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.transformer = AffineTransformer("/fake/calibration.json")
    
    @patch('pathlib.Path.exists')
    def test_load_calibration_success(self, mock_exists):
        """Test successful calibration loading"""
        mock_exists.return_value = True
        with patch('builtins.open', mock_open(read_data=json.dumps(MOCK_CALIBRATION))):
            result = self.transformer.load_calibration()
            self.assertTrue(result)
            self.assertTrue(self.transformer.is_calibrated)
    
    @patch('pathlib.Path.exists')
    def test_load_calibration_file_not_found(self, mock_exists):
        """Test calibration file not found"""
        mock_exists.return_value = False
        # Should raise TransformError or return False
        with self.assertRaises(TransformError):
            self.transformer.load_calibration()
    
    @patch('pathlib.Path.exists')
    def test_transform_point_not_calibrated(self, mock_exists):
        """Test transformation when not calibrated"""
        mock_exists.return_value = True
        with patch('builtins.open', mock_open(read_data=json.dumps(MOCK_CALIBRATION))):
            # Don't load calibration
            with self.assertRaises(TransformError):
                self.transformer.transform_point(100, 100)
    
    @patch('pathlib.Path.exists')
    def test_transform_point(self, mock_exists):
        """Test point transformation"""
        mock_exists.return_value = True
        with patch('builtins.open', mock_open(read_data=json.dumps(MOCK_CALIBRATION))):
            self.transformer.load_calibration()
            result = self.transformer.transform_point(640, 360)
            self.assertIsNotNone(result)
            self.assertEqual(len(result), 2)
    
    @patch('pathlib.Path.exists')
    def test_workspace_bounds(self, mock_exists):
        """Test workspace bounds calculation"""
        mock_exists.return_value = True
        with patch('builtins.open', mock_open(read_data=json.dumps(MOCK_CALIBRATION))):
            self.transformer.load_calibration()
            bounds = self.transformer.workspace_bounds
            self.assertIsNotNone(bounds)
            self.assertIn('x', bounds)
            self.assertIn('y', bounds)
    
    @patch('pathlib.Path.exists')
    def test_is_within_bounds(self, mock_exists):
        """Test bounds checking"""
        mock_exists.return_value = True
        with patch('builtins.open', mock_open(read_data=json.dumps(MOCK_CALIBRATION))):
            self.transformer.load_calibration()
            # Test point outside bounds (0, 0) is definitely outside
            self.assertFalse(self.transformer.is_within_bounds(0, 0))
    
    @patch('pathlib.Path.exists')
    def test_clip_to_bounds(self, mock_exists):
        """Test clipping to bounds"""
        mock_exists.return_value = True
        with patch('builtins.open', mock_open(read_data=json.dumps(MOCK_CALIBRATION))):
            self.transformer.load_calibration()
            clipped = self.transformer.clip_to_bounds(0, 0)
            # Should be clipped to bounds
            self.assertIsNotNone(clipped)
            x, y = clipped
            self.assertGreaterEqual(x, 137.17)
    
    @patch('pathlib.Path.exists')
    def test_validate_detections(self, mock_exists):
        """Test detection validation"""
        mock_exists.return_value = True
        with patch('builtins.open', mock_open(read_data=json.dumps(MOCK_CALIBRATION))):
            self.transformer.load_calibration()
            detections = [(100, 100, 0.9), (200, 200, 0.3), (300, 300, 0.7)]
            valid = self.transformer.validate_detections(detections, min_confidence=0.5)
            # Should filter out low confidence
            self.assertEqual(len(valid), 2)
    
    @patch('pathlib.Path.exists')
    def test_transform_detections_pipeline(self, mock_exists):
        """Test complete transformation pipeline"""
        mock_exists.return_value = True
        with patch('builtins.open', mock_open(read_data=json.dumps(MOCK_CALIBRATION))):
            self.transformer.load_calibration()
            detections = [(640, 360, 0.9)]
            result = self.transformer.transform_detections(detections, min_confidence=0.5)
            self.assertIsInstance(result, list)
    
    @patch('pathlib.Path.exists')
    def test_get_calibration_info(self, mock_exists):
        """Test calibration info"""
        mock_exists.return_value = True
        with patch('builtins.open', mock_open(read_data=json.dumps(MOCK_CALIBRATION))):
            self.transformer.load_calibration()
            info = self.transformer.get_calibration_info()
            self.assertIn('reprojection_error', info)
            self.assertIn('workspace_bounds', info)

if __name__ == "__main__":
    unittest.main()