# Auto CNC Drill System - Development Plan

Sistem otomatisasi CNC Drill berbasis YOLOv7 untuk deteksi dan pengeboran pad hole pada PCB.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         WEB UI LAYER                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ Dashboard  │  │ Video Stream│  │ Job Config │  │ Status     │     │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       API GATEWAY LAYER                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ /acquire    │  │ /infer      │  │ /execute    │  │ /status    │     │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
┌───────────────┐       ┌─────────────────┐       ┌─────────────────┐
│ VISION SERVICE│       │ ORCHESTRATOR     │       │ CNC CONTROLLER  │
│               │       │                 │       │                │
│ - Capture     │       │ - State Machine │       │ - GRBL Stream  │
│ - YOLOv7     │       │ - Error Handler│       │ - G-Code Gen   │
│ - Transform  │       │                 │       │ - Position    │
└───────────────┘       └─────────────────┘       └─────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        HARDWARE LAYER                                   │
│  ┌─────────────┐  ┌��────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ CNC 3-Axis  │  │  Camera 2   │  │  Spindle   │  │  Sensors   │     │
│  │ (GRBL)      │  │  (USB/IP)   │  │  (PWM)      │  │  (Home)    │     │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
```

## Input Data

| Komponen | Deskripsi |
|----------|-----------|
| **Model** | YOLOv7 untuk deteksi pad hole |
| **Kalibrasi** | Matriks Affine dari `calibration_affine.json` |
| **Hardware** | CNC 3-Axis + Camera 2 |

## Alur Kerja (Semi-Otomatis)

| State | Nama | Deskripsi |
|-------|------|-----------|
| 1 | Standby | CNC move ke standby (Z-up, XY standby) |
| 2 | Visual Acquisition | Capture frame & YOLOv7 inference |
| 3 | Coordinate Transformation | Inference result → Affine → G-Code |
| 4 | Paused at Padhole | Pause di first padhole, siap jog koreksi |
| 5 | Sequential Drilling | Eksekusi drilling bertahap (dengan jog offset jika ada) |

---

# 8-Phase Roadmap

## Phase 1: Environment Setup & Infrastructure
**Duration:** 1-2 minggu

**Tugas:**
1. Buat project structure dengan `src/core/`, `src/vision/`, `src/cnc/`, `src/ui/`
2. Setup virtual environment dengan Python 3.14
3. Install dependencies: OpenCV, NumPy, PyTorch, PySerial, Flask/FastAPI
4. Konfigurasi direktori untuk logs, jobs, calibration
5. Setup Git repository dengan .gitignore

**Deliverable:**
- Struktur project yang siap development
- requirements.txt lengkap
- Konfigurasi environment variables

---

## Phase 2: Core Hardware Abstraction
**Duration:** 2-3 minggu

**Tugas:**
1. Implementasi `GRBLController` class:
   - Serial connection management
   - Command queue dengan streaming
   - Real-time position feedback
   - Error handling untuk timeout/connection lost
2. Implementasi `CNCStateMachine`:
   - State definitions (IDLE, HOMING, MOVING, DRILLING, ERROR)
   - State transitions dengan validation
   - Emergency stop handling
3. Test hardware communication:
   - Verify GRBL responses
   - Test homing sequence
   - Test drill motion

**Deliverable:**
- `src/cnc/controller.py` - Hardware abstraction
- `src/core/fsm.py` - State machine
- Unit tests untuk controller

---

## Phase 3: Vision System Integration
**Duration:** 2-3 minggu

**Tugas:**
1. Implementasi `CameraCapture` class:
   - Video stream acquisition
   - Frame buffering untuk inference
   - ROI selection support
2. Setup YOLOv7 inference pipeline:
   - Model loading dan warmup
   - Batch inference processing
   - Output parsing (bounding boxes, confidence)
3. Optimasi untuk real-time:
   - Frame skip strategy
   - Async inference
   - GPU/CPU selection

**Deliverable:**
- `src/vision/camera.py` - Camera abstraction
- `src/vision/detector.py` - YOLOv7 wrapper
- Test deteksi pada berbagai kondisi

---

## Phase 4: Coordinate Transformation System
**Duration:** 1-2 minggu

**Tugas:**
1. Implementasi `AffineTransformer`:
   - Load matrix dari calibration_affine.json
   - Apply transformation: `dst = matrix @ src`
   - Inverse transform untuk verification
2. Implementasi point filtering:
   - Confidence threshold filtering
   - NMS (Non-Maximum Suppression)
   - Physical boundary validation
3. Error handling untuk transformasi:
   - Handle zero detection
   - Handle out-of-bounds coordinates
   - Reprocjection error verification

**Deliverable:**
- `src/vision/transformer.py` - Affine transform
- Coordinate validation utilities
- Error handling policies

---

## Phase 5: Job Orchestration & Execution
**Duration:** 2-3 minggu

**Tugas:**
1. Implementasi `DrillJobManager`:
   - Generate G-Code dari detected points
   - Sequential sorting untuk optimal path
   - Job validation sebelum execution
2. Implementasi `ExecutionController`:
   - State 1: Home/Standby (Y-90)
   - State 2: Visual Acquisition
   - State 3: Coordinate Transformation
   - State 4: Sequential Drilling
3. Progress tracking dan resume capability:
   - Save execution state
   - Resume from checkpoint

**Deliverable:**
- `src/cnc/job_manager.py` - Job orchestration
- `src/cnc/executor.py` - Execution controller
- G-Code generation utilities

---

## Phase 6: Web UI Integration
**Duration:** 2-3 minggu

**Tugas:**
1. Web Dashboard dengan Flask/FastAPI:
   - Live video feed embedding
   - Job submission interface
   - Real-time status display
2. WebSocket untuk real-time updates:
   - Machine position feedback
   - Execution progress
   - Error notifications
3. Operator controls:
   - Start/Pause/Stop buttons
   - Manual position control
   - Parameter adjustment

**Deliverable:**
- `src/ui/server.py` - Web server
- Dashboard HTML/CSS/JS
- WebSocket communication layer

---

## Phase 7: System Integration & Testing
**Duration:** 2-3 minggu

**Tugas:**
1. End-to-end integration testing:
   - Full cycle: acquire → detect → transform → drill
   - Timing measurement dan optimization
2. Error handling completeness:
   - Camera disconnection recovery
   - GRBL emergency protocols
   - Power failure resume
3. Performance tuning:
   - Optimize detection latency
   - Optimize motion speed
   - Memory leak prevention

**Deliverable:**
- Integration test suite
- Error recovery procedures
- Performance benchmarks

---

## Phase 8: Deployment & Fine-Tuning
**Duration:** 1-2 minggu

**Tugas:**
1. Production deployment:
   - Docker containerization
   - Environment configuration
   - Startup scripts
2. Logging dan monitoring:
   - Centralized logging
   - Error tracking
   - Usage analytics
3. Fine-tuning:
   - Model re-training jika diperlukan
   - Calibration refinement
   - Parameter optimization

**Deliverable:**
- Production-ready system
- Deployment documentation
- Operation manual

---

# Technical Stack Recommendation

| Layer | Technology | Justification |
|-------|------------|---------------|
| **Language** | Python 3.14 | Compatibility dengan YOLOv7, OpenCV |
| **Web Framework** | FastAPI | Async support, auto docs |
| **UI** | Vanilla JS + WebSocket | Lightweight, real-time |
| **Computer Vision** | OpenCV + PyTorch | YOLOv7 native support |
| **Hardware Control** | PySerial | GRBL native protocol |
| **State Management** | Custom FSM | Deterministic control |
| **Data Format** | JSON | Human-readable, portable |
| **Deployment** | Docker | Reproducible environment |

---

# Error Handling Strategy

| Error Category | Kondisi | Response |
|----------------|---------|----------|
| **DETECTION_ERROR** | Zero detection | Alert operator, retry dengan adjusted threshold |
| **DETECTION_ERROR** | Low confidence | Apply conservative filtering |
| **TRANSFORM_ERROR** | Out of bounds | Clip ke workspace bounds |
| **TRANSFORM_ERROR** | Invalid matrix | Re-calibration required alert |
| **HARDWARE_ERROR** | Communication timeout | Retry dengan exponential backoff |
| **HARDWARE_ERROR** | Position mismatch | Re-home required |
| **HARDWARE_ERROR** | Emergency stop | Full stop, alert operator |
| **SYSTEM_ERROR** | Camera disconnected | Pause, attempt reconnect |
| **SYSTEM_ERROR** | Power failure | Save state, wait for recovery |

---

# FSM State Diagram

```
        ┌──────────────────────────────────────────────────────────┐
        │                                                              │
        ▼                                                              │
    ┌───────┐     start     ┌──────────────┐                          │
    │ IDLE  │ ───────────► │   STANDBY    │                          │
    └───────┘              └──────┬───────┘                          │
                                  │ start                            │
                                  ▼                                  │
                          ┌──────────────┐                          │
                          │ACQUIRING    │                          │
                          │(capture &   │                          │
                          │detect)       │                          │
                          └──────┬───────┘                          │
                                 │ detection                         │
                                 ▼                                  │
                          ┌──────────────┐                          │
                          │ TRANSFORM   │                          │
                          │(pixel → mm) │                          │
                          └──────┬───────┘                          │
                                 │                                   │
                                 ▼                                   │
                    ┌────────────────────────┐                       │
                    │   PAUSED_AT_PADHOLE   │◄──── jog x/y/z       │
                    │ (bbox merah, ready    │                       │
                    │  untuk koreksi)       │                       │
                    └──────────┬────────────┘                       │
                               │ start                              │
                               ▼                                    │
                    ┌────────────────────────┐                      │
                    │      DRILLING         │                      │
                    │  (semua hole + offset)│                      │
                    └──────────┬────────────┘                      │
                               │ complete                           │
                               ▼                                    │
                    ┌────────────────────────┐                      │
                    │        HOME           │                      │
                    └──────────┬────────────┘                      │
                               │                                    │
                               └──────────────────────────────────┘
```

---

# Task Decomposition

## Utama Tasks

| ID | Task | Dependencies | Priority |
|----|------|---------------|----------|
| T1 | Setup project structure | - | High |
| T2 | Implement GRBLController | T1 | High |
| T3 | Implement FSM | T2 | High |
| T4 | Integration camera | T1 | High |
| T5 | Setup YOLOv7 inference | T4 | High |
| T6 | Implement AffineTransformer | T1 | High |
| T7 | Implement DrillJobManager | T2, T5, T6 | High |
| T8 | Implement ExecutionController | T3, T7 | High |
| T9 | Build Web UI | T1 | Medium |
| T10 | Integration testing | T8, T9 | Medium |
| T11 | Deployment setup | T10 | Low |

## Error Handling Tasks

| ID | Task | Priority |
|----|------|----------|
| E1 | Handle zero detection | High |
| E2 | Handle camera disconnection | High |
| E3 | Handle GRBL timeout | High |
| E4 | Handle out-of-bounds | Medium |
| E5 | Power failure recovery | Medium |

---

# Sinkronisasi Web UI dan Hardware

## Communication Flow

```
┌─────────┐     WebSocket      ┌─────────┐
│  Web UI │ ◄────────────────► │ Server  │
└─────────┘                    └────┬────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
             ┌────────────┐   ┌────────────┐   ┌────────────┐
             │  /status  │   │  /control  │   │  /video   │
             └────────────┘   └────────────┘   └────────────┘
```

## Events

| Event | Direction | Payload |
|-------|-----------|---------|
| `machine:position` | Server → UI | `{x, y, z, state}` |
| `machine:state` | Server → UI | `{state, progress}` |
| `machine:error` | Server → UI | `{code, message}` |
| `control:start` | UI → Server | `{job_id}` |
| `control:stop` | UI → Server | `{}` |
| `control:pause` | UI → Server | `{}` |

## Real-Time Updates

- Position feedback: 10Hz
- State changes: on-event
- Video stream: MJPEG streaming via HTTP