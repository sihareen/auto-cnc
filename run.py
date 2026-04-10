#!/usr/bin/env python3.14
"""
Auto CNC Drill System - Startup Script
"""
import sys
import os
import argparse
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def setup_environment():
    """Setup environment and validate"""
    logger.info("Setting up environment...")
    
    # Check required files
    required = ['config/calibration_affine.json']
    for path in required:
        if not Path(path).exists():
            logger.warning(f"Missing: {path}")
    
    # Create directories
    for d in ['logs', 'jobs', 'calibration']:
        Path(d).mkdir(exist_ok=True)
    
    logger.info("Environment setup complete")

def initialize_components():
    """Initialize all system components"""
    logger.info("Initializing components...")
    
    components = {}
    
    # Transformer
    try:
        from src.vision.transformer import AffineTransformer
        transformer = AffineTransformer('config/calibration_affine.json')
        transformer.load_calibration()
        components['transformer'] = transformer
        logger.info("  Transformer: OK")
    except Exception as e:
        logger.warning(f"  Transformer: FAILED ({e})")
    
    # Camera
    try:
        from src.vision.camera import CameraCapture
        camera = CameraCapture(camera_index=0)
        components['camera'] = camera
        logger.info("  Camera: OK")
    except Exception as e:
        logger.warning(f"  Camera: FAILED ({e})")
    
    # CNC Controller
    try:
        from src.cnc.controller import GRBLController
        cnc = GRBLController()
        components['cnc'] = cnc
        logger.info("  CNC: OK")
    except Exception as e:
        logger.warning(f"  CNC: FAILED ({e})")
    
    # Job Manager
    try:
        from src.cnc.job_manager import DrillJobManager, ExecutionController
        job_mgr = DrillJobManager()
        executor = ExecutionController(cnc if 'cnc' in components else type('Mock', (), {}), job_mgr)
        components['job_manager'] = job_mgr
        components['executor'] = executor
        logger.info("  Job Manager: OK")
    except Exception as e:
        logger.warning(f"  Job Manager: FAILED ({e})")
    
    logger.info("Component initialization complete")
    return components

def start_server(args):
    """Start web server"""
    import uvicorn
    from src.ui.server import app
    
    logger.info(f"Starting server on {args.host}:{args.port}")
    
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info"
    )

def run_diagnostic(args):
    """Run system diagnostic"""
    logger.info("Running diagnostics...")
    
    # Test each component
    from src.vision.transformer import AffineTransformer
    t = AffineTransformer('config/calibration_affine.json')
    t.load_calibration()
    
    print(f"\nCalibration Info:")
    print(f"  Reprojection error: {t.reprojection_error:.3f}mm")
    print(f"  Workspace: {t.workspace_bounds}")
    print(f"  Matrix shape: {t.matrix.shape}")
    
    # Test transformation
    result = t.transform_point(640, 360)
    print(f"\nTest transform (640, 360) -> {result}")
    
    print("\nDiagnostics complete!")

def main():
    parser = argparse.ArgumentParser(description='Auto CNC Drill System')
    subparsers = parser.add_subparsers()
    
    # Server command
    server_parser = subparsers.add_command('server', help='Start web server')
    server_parser.add_argument('--host', default='0.0.0.0', help='Host')
    server_parser.add_argument('--port', type=int, default=8000, help='Port')
    
    # Diagnostic command
    diag_parser = subparsers.add_command('diag', help='Run diagnostics')
    
    args = parser.parse_args()
    
    setup_environment()
    
    if hasattr(args, 'port'):
        start_server(args)
    elif args.func == 'diag' if hasattr(args, 'func') else False:
        run_diagnostic(args)
    else:
        # Default: start server
        initialize_components()
        start_server(argparse.Namespace(host='0.0.0.0', port=8000))

if __name__ == "__main__":
    main()