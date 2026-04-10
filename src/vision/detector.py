"""
YOLOv7 object detector for pad hole detection
"""
import logging
import time
from typing import List, Tuple, Optional, Dict, Any
import numpy as np
import torch
import cv2
from pathlib import Path

logger = logging.getLogger(__name__)

class DetectionResult:
    """Detection result container"""
    
    def __init__(self, 
                 bbox: Tuple[float, float, float, float],  # x1, y1, x2, y2
                 confidence: float,
                 class_id: int,
                 class_name: str):
        self.bbox = bbox
        self.confidence = confidence
        self.class_id = class_id
        self.class_name = class_name
        
    def __repr__(self):
        return (f"DetectionResult(bbox={self.bbox}, confidence={self.confidence:.3f}, "
                f"class_id={self.class_id}, class_name='{self.class_name}')")

class YOLODetector:
    """
    YOLOv7 object detector for pad hole detection
    
    Features:
    - Model loading and warmup
    - Batch inference processing
    - Confidence threshold filtering
    - NMS (Non-Maximum Suppression)
    - GPU/CPU device selection
    """
    
    def __init__(self, 
                 model_path: str,
                 confidence_threshold: float = 0.5,
                 iou_threshold: float = 0.5,
                 device: str = "auto"):
        self.model_path = Path(model_path)
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.device = self._select_device(device)
        self.model: Optional[torch.nn.Module] = None
        self.class_names: List[str] = []
        self.input_size: Tuple[int, int] = (640, 640)  # YOLO default input size
        self.warmup_complete = False
        
    def _select_device(self, device: str) -> str:
        """Select computation device"""
        if device == "auto":
            if torch.cuda.is_available():
                return "cuda"
            elif torch.backends.mps.is_available():
                return "mps"
            else:
                return "cpu"
        return device
    
    def load_model(self) -> bool:
        """Load YOLOv7 model"""
        try:
            if not self.model_path.exists():
                raise FileNotFoundError(f"Model file not found: {self.model_path}")
            
            logger.info(f"Loading YOLOv7 model from {self.model_path}...")
            
            # Load model (simplified - would use actual YOLOv7 loading)
            # In practice, this would use the specific YOLOv7 implementation
            self.model = torch.hub.load('WongKinYiu/yolov7', 'custom', 
                                      path_or_model=str(self.model_path),
                                      trust_repo=True)
            
            self.model.to(self.device)
            self.model.conf = self.confidence_threshold
            self.model.iou = self.iou_threshold
            
            # Get class names
            if hasattr(self.model, 'names'):
                self.class_names = self.model.names
            else:
                # Default class names for pad detection
                self.class_names = ['pad', 'hole', 'component']
            
            logger.info(f"Model loaded on device: {self.device}")
            logger.info(f"Class names: {self.class_names}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            return False
    
    def warmup(self, warmup_iterations: int = 10):
        """Warmup model for stable inference"""
        if not self.model:
            raise RuntimeError("Model not loaded")
            
        logger.info("Warming up model...")
        
        # Create dummy input
        dummy_input = torch.randn(1, 3, self.input_size[0], self.input_size[1]).to(self.device)
        
        # Warmup iterations
        with torch.no_grad():
            for _ in range(warmup_iterations):
                _ = self.model(dummy_input)
        
        self.warmup_complete = True
        logger.info("Model warmup complete")
    
    def preprocess(self, image: np.ndarray) -> torch.Tensor:
        """Preprocess image for YOLO inference"""
        # Resize to model input size
        resized = cv2.resize(image, self.input_size)
        
        # Convert to tensor and normalize
        tensor = torch.from_numpy(resized).float()
        tensor = tensor.permute(2, 0, 1)  # HWC to CHW
        tensor = tensor / 255.0  # Normalize to [0, 1]
        
        # Add batch dimension
        tensor = tensor.unsqueeze(0)
        
        return tensor.to(self.device)
    
    def postprocess(self, 
                   predictions: torch.Tensor, 
                   original_shape: Tuple[int, int]) -> List[DetectionResult]:
        """Postprocess model predictions"""
        results = []
        
        if predictions is None or len(predictions) == 0:
            return results
        
        # Convert predictions to detection results
        for pred in predictions[0]:  # First batch
            if len(pred) >= 6:  # x1, y1, x2, y2, confidence, class
                x1, y1, x2, y2, conf, cls = pred[:6]
                
                if conf < self.confidence_threshold:
                    continue
                
                # Convert to original image coordinates
                orig_h, orig_w = original_shape
                scale_x = orig_w / self.input_size[0]
                scale_y = orig_h / self.input_size[1]
                
                x1 = int(x1 * scale_x)
                y1 = int(y1 * scale_y)
                x2 = int(x2 * scale_x)
                y2 = int(y2 * scale_y)
                
                class_id = int(cls)
                class_name = self.class_names[class_id] if class_id < len(self.class_names) else str(class_id)
                
                results.append(DetectionResult(
                    bbox=(x1, y1, x2, y2),
                    confidence=float(conf),
                    class_id=class_id,
                    class_name=class_name
                ))
        
        return results
    
    def detect(self, image: np.ndarray) -> List[DetectionResult]:
        """
        Detect objects in single image
        
        Args:
            image: Input image (BGR format)
            
        Returns:
            List of detection results
        """
        if not self.model:
            raise RuntimeError("Model not loaded")
        
        if not self.warmup_complete:
            logger.warning("Model not warmed up - performance may be suboptimal")
        
        original_shape = image.shape[:2]  # Height, Width
        
        try:
            # Preprocess
            input_tensor = self.preprocess(image)
            
            # Inference
            with torch.no_grad():
                start_time = time.time()
                predictions = self.model(input_tensor)
                inference_time = time.time() - start_time
            
            # Postprocess
            results = self.postprocess(predictions, original_shape)
            
            logger.debug(f"Detection completed: {len(results)} objects, "
                       f"inference time: {inference_time:.3f}s")
            
            return results
            
        except Exception as e:
            logger.error(f"Detection failed: {e}")
            return []
    
    def detect_batch(self, images: List[np.ndarray]) -> List[List[DetectionResult]]:
        """
        Detect objects in batch of images
        
        Args:
            images: List of input images
            
        Returns:
            List of detection results for each image
        """
        if not self.model:
            raise RuntimeError("Model not loaded")
        
        batch_results = []
        
        for image in images:
            results = self.detect(image)
            batch_results.append(results)
        
        return batch_results
    
    def apply_nms(self, results: List[DetectionResult]) -> List[DetectionResult]:
        """Apply Non-Maximum Suppression"""
        if not results:
            return []
        
        # Simple NMS implementation
        if len(results) <= 1:
            return results
        
        # Sort by confidence (descending)
        results.sort(key=lambda x: x.confidence, reverse=True)
        
        filtered_results = []
        
        while results:
            # Take the highest confidence detection
            best = results.pop(0)
            filtered_results.append(best)
            
            # Remove overlapping detections
            results = [
                det for det in results 
                if self._iou(best.bbox, det.bbox) < self.iou_threshold
            ]
        
        logger.debug(f"NMS: {len(results)} → {len(filtered_results)} detections")
        
        return filtered_results
    
    def _iou(self, box1: Tuple[float, float, float, float], 
             box2: Tuple[float, float, float, float]) -> float:
        """Calculate Intersection over Union"""
        x1_1, y1_1, x2_1, y2_1 = box1
        x1_2, y1_2, x2_2, y2_2 = box2
        
        # Calculate intersection area
        x_left = max(x1_1, x1_2)
        y_top = max(y1_1, y1_2)
        x_right = min(x2_1, x2_2)
        y_bottom = min(y2_1, y2_2)
        
        if x_right < x_left or y_bottom < y_top:
            return 0.0
        
        intersection = (x_right - x_left) * (y_bottom - y_top)
        
        # Calculate union area
        area1 = (x2_1 - x1_1) * (y2_1 - y1_1)
        area2 = (x2_2 - x1_2) * (y2_2 - y1_2)
        union = area1 + area2 - intersection
        
        return intersection / union if union > 0 else 0.0
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get model information"""
        return {
            "model_path": str(self.model_path),
            "device": self.device,
            "confidence_threshold": self.confidence_threshold,
            "iou_threshold": self.iou_threshold,
            "input_size": self.input_size,
            "class_names": self.class_names,
            "warmup_complete": self.warmup_complete,
            "model_loaded": self.model is not None
        }
    
    def set_confidence_threshold(self, threshold: float):
        """Set confidence threshold"""
        self.confidence_threshold = max(0.0, min(1.0, threshold))
        if self.model:
            self.model.conf = self.confidence_threshold
        logger.info(f"Confidence threshold set to: {self.confidence_threshold}")
    
    def set_iou_threshold(self, threshold: float):
        """Set IoU threshold"""
        self.iou_threshold = max(0.0, min(1.0, threshold))
        if self.model:
            self.model.iou = self.iou_threshold
        logger.info(f"IoU threshold set to: {self.iou_threshold}")
    
    def __del__(self):
        """Cleanup"""
        if self.model:
            del self.model