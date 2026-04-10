"""
Unit tests for vision components
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
import numpy as np
import cv2
from src.vision.camera import CameraCapture, CameraState, CameraError
from src.vision.detector import YOLODetector, DetectionResult

class TestCameraCapture(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.camera = CameraCapture(camera_index=0, width=640, height=480, fps=30)
    
    @patch('cv2.VideoCapture')
    def test_connect_success(self, mock_video_capture):
        """Test successful camera connection"""
        mock_cap = Mock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.side_effect = [640, 480, 30.0]  # width, height, fps
        mock_video_capture.return_value = mock_cap
        
        result = self.camera.connect()
        self.assertTrue(result)
        self.assertEqual(self.camera.state, CameraState.CONNECTED)
    
    @patch('cv2.VideoCapture')
    def test_connect_failure(self, mock_video_capture):
        """Test camera connection failure"""
        mock_cap = Mock()
        mock_cap.isOpened.return_value = False
        mock_video_capture.return_value = mock_cap
        
        result = self.camera.connect()
        self.assertFalse(result)
        self.assertEqual(self.camera.state, CameraState.ERROR)
    
    def test_disconnect_not_connected(self):
        """Test disconnect when not connected"""
        self.camera.disconnect()
        self.assertEqual(self.camera.state, CameraState.DISCONNECTED)
    
    def test_set_roi(self):
        """Test ROI setting"""
        self.camera.set_roi(100, 100, 200, 200)
        self.assertEqual(self.camera.roi, (100, 100, 200, 200))
    
    def test_clear_roi(self):
        """Test ROI clearing"""
        self.camera.set_roi(100, 100, 200, 200)
        self.camera.clear_roi()
        self.assertIsNone(self.camera.roi)
    
    def test_get_frame_not_connected(self):
        """Test getting frame when not connected"""
        frame = self.camera.get_frame()
        self.assertIsNone(frame)
    
    def test_register_callback(self):
        """Test callback registration"""
        callback = Mock()
        self.camera.register_callback(callback)
        self.assertIn(callback, self.camera.callbacks)
    
    def test_remove_callback(self):
        """Test callback removal"""
        callback = Mock()
        self.camera.register_callback(callback)
        self.camera.remove_callback(callback)
        self.assertNotIn(callback, self.camera.callbacks)

class TestYOLODetector(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.detector = YOLODetector(
            model_path="/fake/path/model.pt",
            confidence_threshold=0.5,
            iou_threshold=0.5
        )
    
    def test_device_selection_cuda(self):
        """Test CUDA device selection"""
        with patch('torch.cuda.is_available', return_value=True):
            detector = YOLODetector("/fake/path/model.pt", device="auto")
            self.assertEqual(detector.device, "cuda")
    
    def test_device_selection_cpu(self):
        """Test CPU device selection"""
        with patch('torch.cuda.is_available', return_value=False):
            with patch('torch.backends.mps.is_available', return_value=False):
                detector = YOLODetector("/fake/path/model.pt", device="auto")
                self.assertEqual(detector.device, "cpu")
    
    @patch('torch.hub.load')
    def test_load_model_success(self, mock_hub_load):
        """Test successful model loading"""
        mock_model = Mock()
        mock_model.names = ['pad', 'hole']
        mock_hub_load.return_value = mock_model
        
        with patch('pathlib.Path.exists', return_value=True):
            result = self.detector.load_model()
            self.assertTrue(result)
            self.assertIsNotNone(self.detector.model)
    
    def test_load_model_file_not_found(self):
        """Test model loading with non-existent file"""
        with patch('pathlib.Path.exists', return_value=False):
            result = self.detector.load_model()
            self.assertFalse(result)
            self.assertIsNone(self.detector.model)
    
    def test_preprocess(self):
        """Test image preprocessing"""
        # Create test image
        test_image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        
        with patch('torch.cuda.is_available', return_value=False):
            detector = YOLODetector("/fake/path/model.pt", device="cpu")
            tensor = detector.preprocess(test_image)
            
            self.assertEqual(tensor.shape, (1, 3, 640, 640))
            self.assertEqual(tensor.device.type, "cpu")
    
    def test_postprocess_empty_predictions(self):
        """Test postprocessing with empty predictions"""
        results = self.detector.postprocess(None, (480, 640))
        self.assertEqual(results, [])
    
    def test_apply_nms(self):
        """Test NMS application"""
        # Create test detections
        detections = [
            DetectionResult((100, 100, 200, 200), 0.9, 0, 'pad'),
            DetectionResult((110, 110, 210, 210), 0.8, 0, 'pad'),  # Overlapping
            DetectionResult((300, 300, 400, 400), 0.7, 1, 'hole')  # Not overlapping
        ]
        
        filtered = self.detector.apply_nms(detections)
        # Should keep highest confidence overlapping and non-overlapping
        self.assertLessEqual(len(filtered), len(detections))
    
    def test_set_confidence_threshold(self):
        """Test confidence threshold setting"""
        self.detector.set_confidence_threshold(0.7)
        self.assertEqual(self.detector.confidence_threshold, 0.7)
    
    def test_set_iou_threshold(self):
        """Test IoU threshold setting"""
        self.detector.set_iou_threshold(0.3)
        self.assertEqual(self.detector.iou_threshold, 0.3)

class TestDetectionResult(unittest.TestCase):
    
    def test_detection_result_creation(self):
        """Test DetectionResult creation"""
        bbox = (100, 100, 200, 200)
        confidence = 0.85
        class_id = 0
        class_name = "pad"
        
        result = DetectionResult(bbox, confidence, class_id, class_name)
        
        self.assertEqual(result.bbox, bbox)
        self.assertEqual(result.confidence, confidence)
        self.assertEqual(result.class_id, class_id)
        self.assertEqual(result.class_name, class_name)
    
    def test_detection_result_repr(self):
        """Test DetectionResult string representation"""
        result = DetectionResult((100, 100, 200, 200), 0.85, 0, "pad")
        repr_str = repr(result)
        
        self.assertIn("DetectionResult", repr_str)
        self.assertIn("confidence=0.85", repr_str)
        self.assertIn("class_name='pad'", repr_str)

if __name__ == "__main__":
    unittest.main()