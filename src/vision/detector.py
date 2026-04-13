"""
YOLOv7 object detector for pad hole detection
Uses official YOLOv7 pipeline from yolov7/ folder
"""
import sys
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

import cv2
import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent.parent
YOLOV7_PATH = ROOT / "yolov7"
sys.path.insert(0, str(YOLOV7_PATH))

import torch
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs.setdefault('weights_only', False)
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

from models.experimental import attempt_load
from utils.datasets import letterbox
from utils.general import check_img_size, non_max_suppression, scale_coords
from utils.torch_utils import select_device


class DetectionResult:
    def __init__(self, 
                 bbox: Tuple[float, float, float, float],
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
    def __init__(self, 
                 model_path: str,
                 confidence_threshold: float = 0.25,
                 iou_threshold: float = 0.45,
                 img_size: int = 640,
                 device: str = ""):
        self.model_path = Path(model_path)
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.img_size = img_size
        self.device_str = device
        self.device = None
        self.model = None
        self.stride = None
        self.class_names = []
        
    def load_model(self) -> bool:
        try:
            if not self.model_path.exists():
                raise FileNotFoundError(f"Model not found: {self.model_path}")
            
            device = select_device(self.device_str)
            self.device = device
            self.model = attempt_load(self.model_path, map_location=device)
            self.stride = int(self.model.stride.max())
            self.img_size = check_img_size(self.img_size, s=self.stride)
            self.model.eval()
            
            self.class_names = self.model.module.names if hasattr(self.model, "module") else self.model.names
            
            return True
        except Exception as e:
            print(f"[ERROR] Failed to load model: {e}")
            return False
    
    def preprocess(self, im0: np.ndarray) -> Tuple[torch.Tensor, np.ndarray]:
        img = letterbox(im0, self.img_size, stride=self.stride)[0]
        img = img[:, :, ::-1].transpose(2, 0, 1)
        img = np.ascontiguousarray(img)
        img = torch.from_numpy(img).to(self.device).float() / 255.0
        if img.ndimension() == 3:
            img = img.unsqueeze(0)
        return img, im0
    
    def detect(self, image: np.ndarray) -> List[DetectionResult]:
        if self.model is None:
            if not self.load_model():
                return []
        
        img, im0 = self.preprocess(image)
        
        with torch.no_grad():
            pred = self.model(img, augment=False)[0]
        pred = non_max_suppression(pred, self.confidence_threshold, self.iou_threshold, classes=None, agnostic=False)
        
        results = []
        for det in pred:
            if not len(det):
                continue
            
            det[:, :4] = scale_coords(img.shape[2:], det[:, :4], im0.shape).round()
            
            for *xyxy, conf, cls in reversed(det):
                class_id = int(cls)
                class_name = self.class_names[class_id] if class_id < len(self.class_names) else str(class_id)
                results.append(DetectionResult(
                    bbox=(float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])),
                    confidence=float(conf),
                    class_id=class_id,
                    class_name=class_name
                ))
        
        return results
    
    def detect_with_vis(self, image: np.ndarray, output_path: str = None):
        from utils.plots import plot_one_box
        
        results = self.detect(image)
        
        for det in results:
            label = f"{det.class_name} {det.confidence:.2f}"
            plot_one_box(det.bbox, image, label=label, color=(0, 255, 0), line_thickness=1)
        
        if output_path:
            cv2.imwrite(output_path, image)
        
        return results
    
    def get_model_info(self) -> Dict[str, Any]:
        return {
            "model_path": str(self.model_path),
            "device": str(self.device if self.device else "none"),
            "confidence_threshold": self.confidence_threshold,
            "iou_threshold": self.iou_threshold,
            "img_size": self.img_size,
            "class_names": self.class_names,
            "model_loaded": self.model is not None
        }