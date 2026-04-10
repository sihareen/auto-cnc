#!/usr/bin/env python3.14
"""
Demo script for vision system components
"""
import cv2
import numpy as np
from src.vision.camera import CameraCapture
from src.vision.detector import YOLODetector, DetectionResult

def test_camera():
    """Test camera functionality"""
    print("Testing CameraCapture...")
    
    camera = CameraCapture(camera_index=0, width=640, height=480, fps=30)
    
    # Try to connect
    if camera.connect():
        print("✓ Camera connected successfully")
        
        # Get camera info
        info = camera.get_camera_info()
        print(f"Camera info: {info}")
        
        # Set ROI
        camera.set_roi(100, 100, 400, 300)
        print("✓ ROI set")
        
        camera.disconnect()
        print("✓ Camera disconnected")
    else:
        print("✗ Camera connection failed")

def test_detector():
    """Test YOLO detector functionality"""
    print("\nTesting YOLODetector...")
    
    # Create a dummy detector (won't load real model)
    detector = YOLODetector("/fake/path/model.pt", confidence_threshold=0.5)
    
    # Test device selection
    print(f"Selected device: {detector.device}")
    
    # Test preprocessing
    dummy_image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    tensor = detector.preprocess(dummy_image)
    print(f"Preprocessed tensor shape: {tensor.shape}")
    
    # Test NMS
    detections = [
        DetectionResult((100, 100, 200, 200), 0.9, 0, 'pad'),
        DetectionResult((110, 110, 210, 210), 0.8, 0, 'pad'),
        DetectionResult((300, 300, 400, 400), 0.7, 1, 'hole')
    ]
    
    filtered = detector.apply_nms(detections)
    print(f"NMS: {len(detections)} → {len(filtered)} detections")
    
    print("✓ Detector functionality tested")

def main():
    """Main demo function"""
    print("Auto CNC Vision System Demo")
    print("=" * 40)
    
    test_camera()
    test_detector()
    
    print("\n✓ Vision system demo completed!")

if __name__ == "__main__":
    main()