# Auto CNC Drill System - Structure Documentation

## Overview

Sistem otomatisasi CNC Drill berbasis YOLOv7 untuk deteksi dan pengeboran pad hole pada PCB.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AUTO CNC DRILL SYSTEM                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐               │
│   │   CAMERA     │      │   YOLOv7     │      │   AFFINE     │               │
│   │  (video4)    │ ───► │  (best.pt)   │ ───► │ TRANSFORM    │               │
│   │  0ac8:3370   │      │  Detection   │      │  Calibration │               │
│   └──────────────┘      └──────────────┘      └──────┬───────┘               │
│                                                     │                        │
│                                                     ▼                        │
│   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐               │
│   │    CNC       │◄──── │  GRBL CTRL  │◄──── │  DRILL JOB  │               │
│   │ (ttyUSB0)    │      │  Controller │      │  MANAGER    │               │
│   └──────────────┘      └──────────────┘      └──────────────┘               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Hardware Connections

| Hardware | Device Path | Description |
|----------|-------------|-------------|
| USB Camera | `/dev/video4` | 0ac8:3370, 640x480 |
| GRBL CNC | `/dev/ttyUSB0` | 115200 baud |

---

## Scripts & Their Roles

### Entry Points

#### `main.py`
- **Fungsi**: Entry point utama untuk web server
- **Menggunakan**: FastAPI + Uvicorn
- **Port**: 8000
- **Route**: `/` → Dashboard HTML
- **WebSocket**: `/ws` → Real-time control

```bash
python main.py
# atau
python -m uvicorn src.ui.server:app --host 0.0.0.0 --port 8000
```

#### `run_drill_workflow.py`
- **Fungsi**: CLI untuk full drill workflow (tanpa web UI)
- **Mode**: `--simulate` (tanpa CNC) atau real CNC

```bash
# Simulation mode
python run_drill_workflow.py --simulate

# Real CNC
python run_drill_workflow.py --camera 4 --port /dev/ttyUSB0
```

---

### Source Code Structure (`src/`)

```
src/
├── cnc/
│   ├── controller.py    # GRBL serial communication
│   └── job_manager.py    # G-Code generation & job orchestration
├── core/
│   ├── config.py        # Configuration management
│   └── fsm.py           # Finite State Machine
├── vision/
│   ├── camera.py        # Camera capture (CV2)
│   ├── detector.py      # YOLOv7 object detection
│   └── transformer.py   # Affine coordinate transform
└── ui/
    └── server.py        # FastAPI web server
```

---

## Component Details

### 1. Vision System (`src/vision/`)

#### `camera.py` - CameraCapture
```python
camera = CameraCapture(camera_index=4)
camera.connect()
camera.start_streaming()
frame = camera.get_frame()  # Returns numpy array (720, 1280, 3)
```

#### `detector.py` - YOLODetector
```python
detector = YOLODetector(model_path="best.pt")
detector.load_model()
detections = detector.detect(frame)

# DetectionResult:
#   - bbox: (x1, y1, x2, y2)
#   - confidence: float
#   - class_name: str (hole_middle_1, dll)
```

#### `transformer.py` - AffineTransformer
```python
transformer = AffineTransformer("config/calibration_affine.json")
transformer.load_calibration()
machine_coords = transformer.transform_detections(pixel_points)
# Input: [(cx, cy, confidence), ...]  pixel coordinates
# Output: [(x_mm, y_mm), ...] machine coordinates
```

---

### 2. CNC System (`src/cnc/`)

#### `controller.py` - GRBLController
```python
cnc = GRBLController(port="/dev/ttyUSB0")
cnc.connect()
cnc.is_connected  # True/False
cnc.get_status()  # {position, state, ...}
cnc.move_to(x=100, y=-50, z=5, feedrate=1000)
cnc.home_axis("XYZ")
cnc.emergency_stop()
```

#### `job_manager.py` - DrillJobManager
```python
job_manager = DrillJobManager()
job = job_manager.create_job([(x1,y1), (x2,y2), ...])
job.gcode  # List of G-Code commands
job_manager.save_gcode("output.ngc")
```

---

### 3. Web Server (`src/ui/server.py`)

#### Endpoints

| Method | Endpoint | Fungsi |
|--------|----------|--------|
| `GET` | `/` | Dashboard HTML |
| `GET` | `/video/stream` | MJPEG video stream |
| `WS` | `/ws` | WebSocket real-time control |

#### WebSocket Commands

```javascript
// Connect
const ws = new WebSocket('ws://localhost:8000/ws');

// Start workflow
ws.send(JSON.stringify({command: 'start'}));

// Stop (emergency)
ws.send(JSON.stringify({command: 'stop'}));

// Custom G-Code
ws.send(JSON.stringify({command: 'gcode', data: 'G0 X100 Y0'}));

// Get status
ws.send(JSON.stringify({command: 'status'}));
```

---

## Workflow Execution

### Full Drill Workflow

```
1. CAMERA CAPTURE
   └─► get_frame() → numpy array (720x1280)

2. YOLOv7 DETECTION
   └─► detector.detect(frame) → List[DetectionResult]
       └─► 121 objects detected (hole_middle_1, dll)

3. COORDINATE TRANSFORM
   └─► transform_detections(pixel_points)
       └─► Affine matrix from calibration
       └─► 121 machine coordinates (mm)

4. JOB CREATION
   └─► job_manager.create_job(coords)
       └─► Path optimization (nearest neighbor)
       └─► G-Code generation

5. CNC EXECUTION
   └─► For each point:
       ├─► cnc.move_to(x, y, z=5, feedrate=1000)
       ├─► cnc.move_to(z=-1.5, feedrate=300)  # Drill down
       └─► cnc.move_to(z=5, feedrate=1000)    # Retract
```

### State Machine States

```
IDLE → HOMING → ACQUIRING → TRANSFORM → DRILLING → COMPLETE
         ↓         ↓           ↓           ↓
       (done)   (detection) (transform)  (drilling)
```

---

## File Dependencies

### Required Files

| File | Purpose |
|------|---------|
| `best.pt` | YOLOv7 trained model |
| `config/calibration_affine.json` | Affine transformation matrix |
| `yolov7/` | YOLOv7 source code (models, utils) |

### Calibration File Structure (`config/calibration_affine.json`)

```json
{
  "matrix": [[a, b, tx], [c, d, ty]],
  "src_points_px": [[x1,y1], [x2,y2], ...],
  "dst_points_mm": [[x1,y1], [x2,y2], ...],
  "reprojection_error_mm": 0.729
}
```

---

## Python Environment

### Required Environment
```bash
/home/hreen/.python-envs/general/bin/python
```

### Key Dependencies
- `torch` (CUDA support)
- `opencv-python` (cv2)
- `numpy`
- `fastapi` + `uvicorn`
- `websockets`

---

## Quick Reference

### Run Web Server
```bash
/home/hreen/.python-envs/general/bin/python main.py
# Access: http://localhost:8000
```

### Run CLI Workflow
```bash
/home/hreen/.python-envs/general/bin/python run_drill_workflow.py --simulate
```

### Check Hardware
```bash
# Camera
ls -la /dev/video*

# CNC Serial
ls -la /dev/ttyUSB*
```

### Test Detection Only
```python
from src.vision.detector import YOLODetector
import cv2

detector = YOLODetector('best.pt')
detector.load_model()
img = cv2.imread('capture.jpg')
results = detector.detect(img)
print(f"Detected: {len(results)}")
```

---

## Notes

1. **Camera Index**: Default camera_index=4 (`/dev/video4`)
2. **Serial Port**: Default `/dev/ttyUSB0`, baud 115200
3. **Calibration Error**: Target < 1mm (current: 0.729mm)
4. **Detection Threshold**: confidence=0.25, iou=0.45
5. **Drill Depth**: -1.5mm (Z axis)
6. **Clearance Height**: 5.0mm (Z axis)