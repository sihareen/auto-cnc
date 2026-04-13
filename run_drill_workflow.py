#!/usr/bin/env python3
"""
Full Auto CNC Drill Workflow
Complete pipeline: Camera → Detect → Transform → Drill
"""
import argparse
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.vision.camera import CameraCapture
from src.vision.detector import YOLODetector
from src.vision.transformer import AffineTransformer
from src.cnc.controller import GRBLController
from src.cnc.job_manager import DrillJobManager, ExecutionController


class AutoCNCSystem:
    def __init__(self, 
                 camera_index: int = 4,
                 model_path: str = "best.pt",
                 calibration_path: str = "config/calibration_affine.json",
                 cnc_port: str = "/dev/ttyUSB0"):
        
        logger.info("Initializing Auto CNC System...")
        
        self.camera = None
        self.detector = None
        self.transformer = None
        self.cnc = None
        self.job_manager = None
        self.executor = None
        
        self.camera_index = camera_index
        self.model_path = model_path
        self.calibration_path = calibration_path
        self.cnc_port = cnc_port
        
    def initialize_vision(self) -> bool:
        logger.info("=== Initializing Vision System ===")
        
        logger.info(f"Connecting to camera index {self.camera_index}...")
        try:
            self.camera = CameraCapture(camera_index=self.camera_index)
            if self.camera.connect():
                self.camera.start_streaming()
                time.sleep(0.5)
                frame = self.camera.get_frame()
                if frame is not None:
                    logger.info(f"Camera connected: {frame.shape}")
                else:
                    logger.warning("Camera connected but no frame available")
            else:
                logger.warning("Camera connection failed")
        except Exception as e:
            logger.error(f"Camera init failed: {e}")
            return False
        
        logger.info(f"Loading YOLOv7 model: {self.model_path}")
        try:
            self.detector = YOLODetector(
                model_path=self.model_path,
                confidence_threshold=0.25,
                iou_threshold=0.45
            )
            if self.detector.load_model():
                logger.info("YOLOv7 model loaded successfully")
            else:
                logger.error("YOLOv7 model load failed")
                return False
        except Exception as e:
            logger.error(f"Detector init failed: {e}")
            return False
        
        logger.info(f"Loading calibration: {self.calibration_path}")
        try:
            self.transformer = AffineTransformer(self.calibration_path)
            if self.transformer.load_calibration():
                info = self.transformer.get_calibration_info()
                logger.info(f"Calibration loaded: error={info['reprojection_error']:.3f}mm")
                logger.info(f"Workspace bounds: {info['workspace_bounds']}")
            else:
                logger.error("Calibration load failed")
                return False
        except Exception as e:
            logger.error(f"Transformer init failed: {e}")
            return False
        
        return True
    
    def initialize_cnc(self) -> bool:
        logger.info("=== Initializing CNC System ===")
        
        try:
            self.cnc = GRBLController(port=self.cnc_port)
            if self.cnc.connect():
                logger.info(f"CNC connected on {self.cnc_port}")
                status = self.cnc.get_status()
                logger.info(f"CNC Status: {status}")
            else:
                logger.warning("CNC connection failed - running in simulation mode")
                self.cnc = None
        except Exception as e:
            logger.warning(f"CNC init failed: {e} - running in simulation mode")
            self.cnc = None
        
        self.job_manager = DrillJobManager()
        self.executor = ExecutionController(
            cnc_controller=self.cnc or type('MockCNC', (), {})(),
            job_manager=self.job_manager
        )
        
        return True
    
    def initialize(self) -> bool:
        vision_ok = self.initialize_vision()
        cnc_ok = self.initialize_cnc()
        return vision_ok
    
    def capture_and_detect(self, save_path: str = None) -> list:
        logger.info("=== Capture and Detection ===")
        
        if not self.camera:
            logger.error("Camera not initialized")
            return []
        
        if self.camera.state.value == "disconnected":
            self.camera.connect()
        
        frame = self.camera.get_frame()
        if frame is None:
            logger.error("Frame capture failed - no frame available")
            return []
        
        logger.info(f"Captured frame: {frame.shape}")
        
        if save_path:
            cv2.imwrite(save_path, frame)
            logger.info(f"Saved frame to {save_path}")
        
        detections = self.detector.detect(frame)
        logger.info(f"Detected {len(detections)} objects")
        
        for i, det in enumerate(detections[:5]):
            logger.info(f"  [{i}] {det.class_name}: conf={det.confidence:.3f}, bbox={det.bbox}")
        
        return detections
    
    def transform_to_machine_coords(self, detections: list) -> list:
        logger.info("=== Coordinate Transformation ===")
        
        if not self.transformer or not self.transformer.is_calibrated:
            logger.error("Transformer not calibrated")
            return []
        
        pixel_points = []
        for det in detections:
            x1, y1, x2, y2 = det.bbox
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            pixel_points.append((cx, cy, det.confidence))
        
        if not pixel_points:
            logger.warning("No detections to transform")
            return []
        
        machine_coords = self.transformer.transform_detections(
            pixel_points, 
            min_confidence=0.25
        )
        
        logger.info(f"Transformed {len(machine_coords)} points to machine coordinates")
        for i, (x, y) in enumerate(machine_coords[:5]):
            logger.info(f"  [{i}] ({x:.2f}, {y:.2f}) mm")
        
        return machine_coords
    
    def create_and_execute_job(self, machine_coords: list, simulate: bool = False):
        logger.info("=== Drill Job Execution ===")
        
        if not machine_coords:
            logger.error("No coordinates to drill")
            return False
        
        job = self.job_manager.create_job(machine_coords, optimize=True)
        logger.info(f"Created job {job.job_id} with {len(job.points)} points")
        
        if not simulate and self.cnc and self.cnc.is_connected:
            logger.info("Executing drill job on real CNC...")
            
            self.cnc.home_axis("XYZ")
            time.sleep(0.5)
            
            for i, point in enumerate(job.points):
                logger.info(f"Drilling point {i+1}/{len(job.points)}: ({point.x:.2f}, {point.y:.2f}) mm")
                
                self.cnc.move_to(x=point.x, y=point.y, z=5.0, feedrate=1000)
                time.sleep(0.1)
                
                self.cnc.move_to(z=-1.5, feedrate=300)
                time.sleep(0.1)
                
                self.cnc.move_to(z=5.0, feedrate=1000)
                time.sleep(0.05)
                
                job.mark_drilled(i)
                
            self.cnc.move_to(z=10.0)
            logger.info("Drill job complete!")
            
        else:
            logger.info("Simulation mode - would drill:")
            for i, point in enumerate(job.points):
                logger.info(f"  [{i}] ({point.x:.2f}, {point.y:.2f}) mm")
        
        return True
    
    def run_full_workflow(self, capture_path: str = "capture.jpg", simulate: bool = False):
        logger.info("=" * 50)
        logger.info("STARTING FULL AUTO CNC WORKFLOW")
        logger.info("=" * 50)
        
        detections = self.capture_and_detect(save_path=capture_path)
        
        if not detections:
            logger.error("No detections - aborting")
            return False
        
        machine_coords = self.transform_to_machine_coords(detections)
        
        if not machine_coords:
            logger.error("No valid coordinates - aborting")
            return False
        
        self.create_and_execute_job(machine_coords, simulate=simulate)
        
        logger.info("=" * 50)
        logger.info("WORKFLOW COMPLETE")
        logger.info("=" * 50)
        return True


def main():
    parser = argparse.ArgumentParser(description="Auto CNC Full Workflow")
    parser.add_argument("--camera", type=int, default=4, help="Camera index")
    parser.add_argument("--model", type=str, default="best.pt", help="YOLO model path")
    parser.add_argument("--calib", type=str, default="config/calibration_affine.json", help="Calibration file")
    parser.add_argument("--port", type=str, default="/dev/ttyUSB0", help="CNC serial port")
    parser.add_argument("--simulate", action="store_true", help="Simulation mode (no CNC)")
    parser.add_argument("--capture", type=str, default="capture.jpg", help="Save capture path")
    args = parser.parse_args()
    
    system = AutoCNCSystem(
        camera_index=args.camera,
        model_path=args.model,
        calibration_path=args.calib,
        cnc_port=args.port
    )
    
    if not system.initialize():
        logger.error("System initialization failed")
        return 1
    
    system.run_full_workflow(capture_path=args.capture, simulate=args.simulate)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
