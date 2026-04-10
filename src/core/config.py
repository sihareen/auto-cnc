"""
Configuration management for Auto CNC System
"""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)

@dataclass
class CNCConfig:
    """CNC configuration"""
    port: str = "/dev/ttyUSB0"
    baudrate: int = 115200
    timeout: float = 2.0
    retry_attempts: int = 3
    
@dataclass
class CameraConfig:
    """Camera configuration"""
    index: int = 0
    width: int = 1920
    height: int = 1080
    fps: int = 30
    
@dataclass
class YOLOConfig:
    """YOLO configuration"""
    model_path: str = "models/best.pt"
    confidence_threshold: float = 0.5
    iou_threshold: float = 0.5
    device: str = "auto"
    
@dataclass
class JobConfig:
    """Job configuration"""
    drill_depth: float = -1.5
    feedrate: int = 300
    rapid_feedrate: int = 1000
    clearance_height: float = 5.0
    
@dataclass  
class WebConfig:
    """Web server configuration"""
    host: str = "0.0.0.0"
    port: int = 8000
    
@dataclass
class AppConfig:
    """Application configuration"""
    cnc: CNCConfig = None
    camera: CameraConfig = None
    yolo: YOLOConfig = None
    job: JobConfig = None
    web: WebConfig = None

class ConfigManager:
    """Configuration manager"""
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.config_file = self.config_dir / "app_config.json"
        self.config: AppConfig = AppConfig()
        
    def load(self) -> bool:
        """Load configuration"""
        try:
            if self.config_file.exists():
                with open(self.config_file) as f:
                    data = json.load(f)
                    
                self.config.cnc = CNCConfig(**data.get('cnc', {}))
                self.config.camera = CameraConfig(**data.get('camera', {}))
                self.config.yolo = YOLOConfig(**data.get('yolo', {}))
                self.config.job = JobConfig(**data.get('job', {}))
                self.config.web = WebConfig(**data.get('web', {}))
                
                logger.info(f"Loaded config from {self.config_file}")
                return True
            else:
                self._set_defaults()
                self.save()
                logger.info("Created default configuration")
                return True
                
        except Exception as e:
            logger.error(f"Config load failed: {e}")
            self._set_defaults()
            return False
    
    def _set_defaults(self):
        """Set default values"""
        self.config.cnc = CNCConfig()
        self.config.camera = CameraConfig()
        self.config.yolo = YOLOConfig()
        self.config.job = JobConfig()
        self.config.web = WebConfig()
    
    def save(self) -> bool:
        """Save configuration"""
        try:
            self.config_dir.mkdir(exist_ok=True)
            
            data = {
                'cnc': asdict(self.config.cnc),
                'camera': asdict(self.config.camera),
                'yolo': asdict(self.config.yolo),
                'job': asdict(self.config.job),
                'web': asdict(self.config.web)
            }
            
            with open(self.config_file, 'w') as f:
                json.dump(data, f, indent=2)
                
            logger.info(f"Saved config to {self.config_file}")
            return True
            
        except Exception as e:
            logger.error(f"Config save failed: {e}")
            return False
    
    def get(self) -> AppConfig:
        """Get configuration"""
        return self.config