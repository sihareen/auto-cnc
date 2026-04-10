"""
Affine transformation for coordinate conversion between camera and CNC
"""
import json
import logging
import numpy as np
from typing import List, Tuple, Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

class TransformError(Exception):
    """Transformation-related errors"""
    pass

class AffineTransformer:
    """
    Affine transformation for coordinate conversion
    
    Converts pixel coordinates from camera to CNC machine coordinates
    using affine transformation matrix from calibration
    """
    
    def __init__(self, calibration_path: str = "config/calibration_affine.json"):
        self.calibration_path = Path(calibration_path)
        self.matrix: Optional[np.ndarray] = None
        self.src_points: Optional[np.ndarray] = None
        self.dst_points: Optional[np.ndarray] = None
        self.reprojection_error: Optional[float] = None
        self.per_point_errors: Optional[np.ndarray] = None
        self.workspace_bounds: Optional[Dict[str, Tuple[float, float]]] = None
        self.is_calibrated = False
    
    def load_calibration(self) -> bool:
        """
        Load affine transformation matrix from calibration file
        
        Returns:
            bool: True if calibration loaded successfully
        """
        try:
            if not self.calibration_path.exists():
                raise TransformError(f"Calibration file not found: {self.calibration_path}")
            
            with open(self.calibration_path, 'r') as f:
                calibration_data = json.load(f)
            
            matrix_data = calibration_data.get('matrix', [])
            if not matrix_data or len(matrix_data) != 2 or len(matrix_data[0]) != 3:
                raise TransformError("Invalid matrix format in calibration file")
            
            self.matrix = np.array(matrix_data, dtype=np.float64)
            
            self.src_points = np.array(calibration_data.get('src_points_px', []), dtype=np.float64)
            self.dst_points = np.array(calibration_data.get('dst_points_mm', []), dtype=np.float64)
            
            self.reprojection_error = calibration_data.get('reprojection_error_mm')
            self.per_point_errors = np.array(calibration_data.get('per_point_error_mm', []))
            
            self._calculate_workspace_bounds()
            
            self.is_calibrated = True
            logger.info(f"Calibration loaded successfully from {self.calibration_path}")
            logger.info(f"Reprojection error: {self.reprojection_error:.3f} mm")
            
            return True
            
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to load calibration: {e}")
            self.is_calibrated = False
            return False
    
    def _calculate_workspace_bounds(self):
        """Calculate workspace bounds from destination points"""
        if self.dst_points is None or len(self.dst_points) == 0:
            return
        
        x_coords = self.dst_points[:, 0]
        y_coords = self.dst_points[:, 1]
        
        self.workspace_bounds = {
            'x': (float(np.min(x_coords)), float(np.max(x_coords))),
            'y': (float(np.min(y_coords)), float(np.max(y_coords))),
            'z': (0.0, 0.0)
        }
    
    def transform_point(self, pixel_x: float, pixel_y: float) -> Optional[Tuple[float, float]]:
        """Transform single point from pixel to machine coordinates"""
        if not self.is_calibrated or self.matrix is None:
            raise TransformError("Transformer not calibrated")
        
        try:
            pixel_coords = np.array([pixel_x, pixel_y, 1.0])
            machine_coords = self.matrix @ pixel_coords
            return float(machine_coords[0]), float(machine_coords[1])
        except Exception as e:
            logger.error(f"Point transformation failed: {e}")
            return None
    
    def transform_points(self, points: List[Tuple[float, float]]) -> List[Optional[Tuple[float, float]]]:
        """Transform multiple points"""
        if not self.is_calibrated:
            raise TransformError("Transformer not calibrated")
        
        results = []
        for x, y in points:
            transformed = self.transform_point(x, y)
            results.append(transformed)
        return results
    
    def inverse_transform(self, machine_x: float, machine_y: float) -> Optional[Tuple[float, float]]:
        """Inverse transform from machine to pixel coordinates"""
        if not self.is_calibrated or self.matrix is None:
            raise TransformError("Transformer not calibrated")
        
        try:
            linear_part = self.matrix[:, :2]
            translation = self.matrix[:, 2]
            machine_vec = np.array([machine_x, machine_y])
            pixel_vec = np.linalg.inv(linear_part) @ (machine_vec - translation)
            return float(pixel_vec[0]), float(pixel_vec[1])
        except np.linalg.LinAlgError as e:
            logger.error(f"Inverse transformation failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Inverse transformation failed: {e}")
            return None
    
    def is_within_bounds(self, x: float, y: float, margin: float = 5.0) -> bool:
        """Check if coordinates are within workspace bounds"""
        if self.workspace_bounds is None:
            return True
        
        x_min, x_max = self.workspace_bounds['x']
        y_min, y_max = self.workspace_bounds['y']
        
        return (x_min - margin <= x <= x_max + margin and 
                y_min - margin <= y <= y_max + margin)
    
    def clip_to_bounds(self, x: float, y: float, margin: float = 5.0) -> Tuple[float, float]:
        """Clip coordinates to workspace bounds"""
        if self.workspace_bounds is None:
            return x, y
        
        x_min, x_max = self.workspace_bounds['x']
        y_min, y_max = self.workspace_bounds['y']
        
        x_clipped = max(x_min + margin, min(x, x_max - margin))
        y_clipped = max(y_min + margin, min(y, y_max - margin))
        
        return x_clipped, y_clipped
    
    def validate_detections(self, detections: List[Tuple[float, float, float]], 
                      min_confidence: float = 0.5) -> List[Tuple[float, float]]:
        """Validate and filter detections by confidence"""
        if not detections:
            logger.warning("No detections to validate")
            return []
        
        filtered = [(x, y) for x, y, conf in detections if conf >= min_confidence]
        
        if not filtered:
            logger.warning(f"All detections below confidence threshold {min_confidence}")
        
        logger.info(f"Validation: {len(detections)} -> {len(filtered)} detections")
        return filtered
    
    def transform_detections(self, detections: List[Tuple[float, float, float]], 
                         min_confidence: float = 0.5) -> List[Tuple[float, float]]:
        """Complete transformation pipeline for detections"""
        if not self.is_calibrated:
            raise TransformError("Transformer not calibrated")
        
        valid_pixels = self.validate_detections(detections, min_confidence)
        
        if not valid_pixels:
            return []
        
        machine_coords = []
        out_of_bounds = []
        
        for pixel_x, pixel_y in valid_pixels:
            transformed = self.transform_point(pixel_x, pixel_y)
            
            if transformed is None:
                logger.warning(f"Transformation failed for point ({pixel_x}, {pixel_y})")
                continue
            
            machine_x, machine_y = transformed
            
            if not self.is_within_bounds(machine_x, machine_y):
                logger.warning(f"Point out of bounds: ({machine_x:.1f}, {machine_y:.1f}) mm")
                out_of_bounds.append((machine_x, machine_y))
                machine_x, machine_y = self.clip_to_bounds(machine_x, machine_y)
            
            machine_coords.append((machine_x, machine_y))
        
        if out_of_bounds:
            logger.warning(f"{len(out_of_bounds)} points clipped to bounds")
        
        logger.info(f"Transformation complete: {len(valid_pixels)} -> {len(machine_coords)} points")
        return machine_coords
    
    def verify_calibration(self, test_points: Optional[List[Tuple[float, float]]] = None) -> Dict[str, Any]:
        """Verify calibration quality"""
        if not self.is_calibrated:
            raise TransformError("Transformer not calibrated")
        
        results = {
            'reprojection_error': self.reprojection_error,
            'per_point_errors': self.per_point_errors.tolist() if self.per_point_errors is not None else [],
            'workspace_bounds': self.workspace_bounds,
            'test_results': []
        }
        
        if test_points is None and self.src_points is not None:
            test_points = [tuple(point) for point in self.src_points]
        
        if test_points:
            for i, (src_x, src_y) in enumerate(test_points):
                dst_point = self.transform_point(src_x, src_y)
                if dst_point:
                    dst_x, dst_y = dst_point
                    src_reconstructed = self.inverse_transform(dst_x, dst_y)
                    if src_reconstructed:
                        src_rec_x, src_rec_y = src_reconstructed
                        error = np.sqrt((src_x - src_rec_x)**2 + (src_y - src_rec_y)**2)
                        results['test_results'].append({
                            'point_index': i,
                            'src_original': (src_x, src_y),
                            'dst_transformed': (dst_x, dst_y),
                            'src_reconstructed': (src_rec_x, src_rec_y),
                            'reconstruction_error': error
                        })
        return results
    
    def get_calibration_info(self) -> Dict[str, Any]:
        """Get calibration information"""
        return {
            'is_calibrated': self.is_calibrated,
            'calibration_path': str(self.calibration_path),
            'matrix': self.matrix.tolist() if self.matrix is not None else None,
            'reprojection_error': self.reprojection_error,
            'workspace_bounds': self.workspace_bounds,
            'num_calibration_points': len(self.src_points) if self.src_points is not None else 0
        }