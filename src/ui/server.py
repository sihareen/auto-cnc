"""
FastAPI Web Server for Auto CNC Dashboard
"""
import logging
import threading
import json
from typing import Dict, Any, Optional, List
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(title="Auto CNC Drill System", version="1.0.0")

# Global state (in production, use proper state management)
system_state = {
    "status": "IDLE",
    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
    "progress": {"current": 0, "total": 0},
    "connected": False,
    "last_error": None,
    "execution_state": 0
}

# WebSocket connections
connected_clients: List[WebSocket] = []

# Initialize components (will be connected in main)
camera = None
detector = None
cnc_controller = None
job_manager = None
executor = None
transformer = None

def init_components():
    """Initialize system components"""
    global camera, detector, cnc_controller, job_manager, executor, transformer
    
    try:
        from src.vision.transformer import AffineTransformer
        transformer = AffineTransformer("config/calibration_affine.json")
        transformer.load_calibration()
        
    except Exception as e:
        logger.warning(f"Transformer init warning: {e}")
        transformer = None
    
    try:
        from src.cnc.controller import GRBLController
        cnc_controller = GRBLController()
    except Exception as e:
        logger.warning(f"Controller init warning: {e}")
        cnc_controller = None
    
    try:
        from src.cnc.job_manager import DrillJobManager, ExecutionController
        job_manager = DrillJobManager()
        executor = ExecutionController(cnc_controller or type('MockCNC', (), {}), job_manager)
    except Exception as e:
        logger.warning(f"Job manager init warning: {e}")
        job_manager = None
        executor = None
    
    try:
        from src.vision.camera import CameraCapture
        camera = CameraCapture(camera_index=4)
        camera.connect()
        camera.start_streaming()
        logger.info("Camera streaming started")
    except Exception as e:
        logger.warning(f"Camera init warning: {e}")
        camera = None
    
    try:
        from src.vision.detector import YOLODetector
        detector = YOLODetector(model_path="best.pt", confidence_threshold=0.25, iou_threshold=0.45)
        detector.load_model()
        logger.info("Detector loaded successfully")
    except Exception as e:
        logger.warning(f"Detector init warning: {e}")
        detector = None
    
    import time
    time.sleep(0.5)  # Wait for camera to be ready
    logger.info("Components initialized (some may be None)")

init_components()

# ==================== WebSocket ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates"""
    await websocket.accept()
    connected_clients.append(websocket)
    
    try:
        # Send initial state
        await websocket.send_json(system_state)
        
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Handle commands from UI
            cmd = message.get("command")
            
            if cmd == "start":
                system_state["status"] = "ACQUIRING"
                await broadcast_state()
                
                if camera and detector and transformer:
                    frame = camera.get_frame()
                    if frame is not None:
                        detections = detector.detect(frame)
                        system_state["last_detections"] = len(detections)
                        
                        pixel_points = [
                            ((d.bbox[0]+d.bbox[2])/2, (d.bbox[1]+d.bbox[3])/2, d.confidence)
                            for d in detections
                        ]
                        machine_coords = transformer.transform_detections(pixel_points, min_confidence=0.25)
                        
                        if machine_coords and job_manager:
                            job = job_manager.create_job(machine_coords, optimize=True)
                            system_state["progress"] = {"current": 0, "total": len(job.points)}
                            system_state["status"] = "TRANSFORM"
                            await broadcast_state()
                            
                            if cnc_controller and cnc_controller.is_connected:
                                cnc_controller.home_axis("XYZ")
                                system_state["status"] = "DRILLING"
                                await broadcast_state()
                                
                                for i, point in enumerate(job.points):
                                    cnc_controller.move_to(x=point.x, y=point.y, z=5.0, feedrate=1000)
                                    cnc_controller.move_to(z=-1.5, feedrate=300)
                                    cnc_controller.move_to(z=5.0, feedrate=1000)
                                    job.mark_drilled(i)
                                    system_state["progress"] = {"current": i+1, "total": len(job.points)}
                                    await broadcast_state()
                                
                                cnc_controller.move_to(z=10.0)
                                system_state["status"] = "COMPLETE"
                            else:
                                system_state["status"] = "SIMULATE"
                        else:
                            system_state["status"] = "NO_POINTS"
                    else:
                        system_state["status"] = "NO_FRAME"
                else:
                    system_state["status"] = "NOT_READY"
                
                await broadcast_state()
                
            elif cmd == "stop":
                # Stop execution
                if cnc_controller:
                    cnc_controller.emergency_stop()
                system_state["status"] = "STOPPED"
                await broadcast_state()
                
            elif cmd == "pause":
                system_state["status"] = "PAUSED"
                await broadcast_state()
                
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)

async def broadcast_state():
    """Broadcast state to all connected WebSocket clients"""
    for client in connected_clients:
        try:
            await client.send_json(system_state)
        except Exception as e:
            logger.error(f"Broadcast error: {e}")

# ==================== API Endpoints ====================

@app.get("/")
async def root():
    """Root HTML page"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Auto CNC Drill System</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #1a1a2e; color: #eee; min-height: 100vh;
            }
            .header {
                background: #16213e; padding: 20px; text-align: center;
                border-bottom: 2px solid #0f3460;
            }
            .header h1 { color: #e94560; font-size: 24px; }
            .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
            .grid {
                display: grid; grid-template-columns: 2fr 1fr; gap: 20px;
            }
            .video-container {
                background: #16213e; border-radius: 10px; overflow: hidden;
                aspect-ratio: 16/9;
            }
            .video-container img {
                width: 100%; height: 100%; object-fit: contain;
            }
            .sidebar { display: flex; flex-direction: column; gap: 20px; }
            .panel {
                background: #16213e; border-radius: 10px; padding: 20px;
            }
            .panel h2 {
                color: #e94560; font-size: 16px; margin-bottom: 15px;
                border-bottom: 1px solid #0f3460; padding-bottom: 10px;
            }
            .status-grid {
                display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
            }
            .status-item {
                background: #0f3460; padding: 15px; border-radius: 8px; text-align: center;
            }
            .status-item .label { font-size: 12px; color: #888; }
            .status-item .value { font-size: 20px; font-weight: bold; color: #e94560; }
            .controls {
                display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
            }
            .btn {
                padding: 15px 20px; border: none; border-radius: 8px;
                font-size: 14px; font-weight: bold; cursor: pointer;
                transition: all 0.2s;
            }
            .btn-primary { background: #e94560; color: white; }
            .btn-primary:hover { background: #ff6b6b; }
            .btn-secondary { background: #0f3460; color: white; }
            .btn-secondary:hover { background: #16213e; }
            .btn-danger { background: #dc3545; color: white; }
            .btn-danger:hover { background: #ff6b6b; }
            .btn:disabled { opacity: 0.5; cursor: not-allowed; }
            .progress-bar {
                background: #0f3460; height: 20px; border-radius: 10px; overflow: hidden;
            }
            .progress-fill {
                background: linear-gradient(90deg, #e94560, #ff6b6b);
                height: 100%; transition: width 0.3s;
            }
            .log-container {
                background: #0f3460; border-radius: 8px; padding: 15px;
                max-height: 200px; overflow-y: auto;
                font-family: monospace; font-size: 12px;
            }
            .log-entry { margin: 5px 0; color: #888; }
            .log-entry.error { color: #ff6b6b; }
            .log-entry.success { color: #51cf66; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Auto CNC Drill System</h1>
        </div>
        <div class="container">
            <div class="grid">
                <div class="video-container">
                    <img id="video" src="/video/stream" alt="Video Feed">
                </div>
                <div class="sidebar">
                    <div class="panel">
                        <h2>Machine Status</h2>
                        <div class="status-grid">
                            <div class="status-item">
                                <div class="label">Status</div>
                                <div class="value" id="status">IDLE</div>
                            </div>
                            <div class="status-item">
                                <div class="label">State</div>
                                <div class="value" id="exec-state">0</div>
                            </div>
                            <div class="status-item">
                                <div class="label">X (mm)</div>
                                <div class="value" id="pos-x">0.0</div>
                            </div>
                            <div class="status-item">
                                <div class="label">Y (mm)</div>
                                <div class="value" id="pos-y">0.0</div>
                            </div>
                        </div>
                    </div>
                    <div class="panel">
                        <h2>Progress</h2>
                        <div class="progress-bar">
                            <div class="progress-fill" id="progress-fill" style="width: 0%"></div>
                        </div>
                        <p style="text-align: center; margin-top: 10px;">
                            <span id="progress-current">0</span> / <span id="progress-total">0</span>
                        </p>
                    </div>
                    <div class="panel">
                        <h2>Controls</h2>
                        <div class="controls">
                            <button class="btn btn-primary" id="btn-start">START</button>
                            <button class="btn btn-danger" id="btn-stop">STOP</button>
                            <button class="btn btn-secondary" id="btn-home">HOME</button>
                            <button class="btn btn-secondary" id="btn-reset">RESET</button>
                        </div>
                    </div>
                </div>
            </div>
            <div class="panel" style="margin-top: 20px;">
                <h2>System Log</h2>
                <div class="log-container" id="log"></div>
            </div>
        </div>
        <script>
            const ws = new WebSocket(`ws://${location.host}/ws`);
            
            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                updateUI(data);
            };
            
            ws.onerror = function(error) {
                log('WebSocket error', 'error');
            };
            
            function updateUI(data) {
                document.getElementById('status').textContent = data.status || 'IDLE';
                document.getElementById('exec-state').textContent = data.execution_state || '0';
                document.getElementById('pos-x').textContent = (data.position?.x || 0).toFixed(1);
                document.getElementById('pos-y').textContent = (data.position?.y || 0).toFixed(1);
                
                const progress = data.progress || {};
                const total = progress.total || 1;
                const current = progress.current || 0;
                const percent = total > 0 ? (current / total) * 100 : 0;
                
                document.getElementById('progress-fill').style.width = percent + '%';
                document.getElementById('progress-current').textContent = current;
                document.getElementById('progress-total').textContent = total;
            }
            
            function log(message, type = 'info') {
                const logDiv = document.getElementById('log');
                const entry = document.createElement('div');
                entry.className = 'log-entry ' + type;
                entry.textContent = new Date().toLocaleTimeString() + ' ' + message;
                logDiv.insertBefore(entry, logDiv.firstChild);
            }
            
            document.getElementById('btn-start').onclick = function() {
                ws.send(JSON.stringify({command: 'start'}));
                log('Start command sent');
            };
            
            document.getElementById('btn-stop').onclick = function() {
                ws.send(JSON.stringify({command: 'stop'}));
                log('Stop command sent');
            };
            
            document.getElementById('btn-home').onclick = function() {
                ws.send(JSON.stringify({command: 'home'}));
                log('Home command sent');
            };
            
            document.getElementById('btn-reset').onclick = function() {
                ws.send(JSON.stringify({command: 'reset'}));
                log('Reset command sent');
            };
            
            log('Dashboard loaded');
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html_content)

@app.get("/video/stream")
async def video_stream():
    """MJPEG video stream"""
    def generate_frames():
        while True:
            try:
                if camera:
                    frame = camera.get_frame()
                    if frame is not None:
                        # Encode as JPEG
                        _, buffer = cv2.imencode('.jpg', frame)
                        frame_bytes = buffer.tobytes()
                        
                        yield (b'--frame\r\n'
                              b'Content-Type: image/jpeg\r\n\r\n'
                              + frame_bytes + b'\r\n')
                    else:
                        # Generate placeholder frame
                        frame = np.zeros((480, 640, 3), dtype=np.uint8)
                        cv2.putText(frame, 'No Camera', (200, 240), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                        _, buffer = cv2.imencode('.jpg', frame)
                        yield (b'--frame\r\n'
                              b'Content-Type: image/jpeg\r\n\r\n'
                              + buffer.tobytes() + b'\r\n')
                else:
                    # No camera - send placeholder
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(frame, 'Camera Not Connected', (150, 240), 
                              cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                    _, buffer = cv2.imencode('.jpg', frame)
                    yield (b'--frame\r\n'
                          b'Content-Type: image/jpeg\r\n\r\n'
                          + buffer.tobytes() + b'\r\n')
                        
            except Exception as e:
                logger.error(f"Video stream error: {e}")
                break
    
    return StreamingResponse(generate_frames(), 
                          media_type='multipart/x-mixed-replace; boundary=frame')

# ==================== REST API ====================

@app.get("/api/status")
async def get_status():
    """Get current system status"""
    return system_state

@app.post("/api/control/start")
async def start_drill():
    """Start drilling operation"""
    system_state["status"] = "STARTING"
    await broadcast_state()
    return {"status": "started"}

@app.post("/api/control/stop")
async def stop_drill():
    """Stop drilling operation"""
    if cnc_controller:
        cnc_controller.emergency_stop()
    system_state["status"] = "STOPPED"
    await broadcast_state()
    return {"status": "stopped"}

@app.post("/api/control/home")
async def home_machine():
    """Home machine"""
    system_state["status"] = "HOMING"
    await broadcast_state()
    return {"status": "homing"}

@app.post("/api/control/reset")
async def reset_system():
    """Reset system"""
    if executor:
        executor.reset()
    system_state["status"] = "IDLE"
    system_state["execution_state"] = 0
    system_state["progress"] = {"current": 0, "total": 0}
    await broadcast_state()
    return {"status": "reset"}

@app.get("/api/camera/info")
async def get_camera_info():
    """Get camera information"""
    if camera:
        return camera.get_camera_info()
    return {"error": "Camera not initialized"}

@app.get("/api/detections")
async def get_detections():
    """Get current detections"""
    if camera and detector:
        frame = camera.get_frame()
        if frame is not None and detector:
            detections = detector.detect(frame)
            return {
                "count": len(detections),
                "detections": [
                    {
                        "bbox": d.bbox,
                        "confidence": d.confidence,
                        "class_name": d.class_name
                    }
                    for d in detections
                ]
            }
    return {"count": 0, "detections": []}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)