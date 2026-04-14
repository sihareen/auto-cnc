# Auto CNC Drill System

Sistem otomatisasi CNC Drill berbasis YOLOv7 untuk deteksi dan pengeboran pad hole pada PCB.

## Arsitektur

```
Web UI → API Gateway → Vision Service / CNC Controller → Hardware
```

### Komponen

| Komponen | Fungsi |
|----------|--------|
| `src/vision/` | Camera capture & YOLOv7 inference |
| `src/cnc/` | GRBL controller & job execution |
| `src/ui/` | Web dashboard |
| `src/core/` | State machine & orchestration |

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requirements: Python 3.14+, OpenCV, PyTorch, PySerial, FastAPI

## Usage

### 3-Click Drill Workflow

1. **START** → Machine move ke standby position (Z-up, XY standby)
2. **START** → Capture image, YOLOv7 detect padholes, move ke first padhole (bbox merah), **PAUSE**
3. **Jog Manual** → Adjust posisi drill dengan jog control (x, y, z) sampai tepat di target
4. **START** → Drill semua hole dengan work coordinate (original + jog offset), return HOME

### Jog Offset Adjustment

Saat status `PAUSED_AT_PADHOLE`, setiap jog movement (x/y/z) diakumulasi sebagai offset. Offset ini:
- Ditambahkan ke semua drill points (x, y)
- Ditambahkan ke Z depth dan clearance (z)

Offset tersimpan di `config/work_points.json`.

### Kalibrasi

1. Place PCB di workspace
2. Klik **CALIBRATE** → system detect padhole dan move ke posisi kalkulasi
3. Jog manual ke posisi actual drill point
4. Klik **CALIBRATE** lagi → residual offset disimpan ke `config/calibration_runtime_offset.json`

## State Machine

```
IDLE → STANDBY → STANDBY_READY → ACQUIRING → TRANSFORM → PAUSED_AT_PADHOLE → DRILLING → HOME
```

## Konfigurasi

| File | Fungsi |
|------|--------|
| `config/calibration_affine.json` | Affine transformation matrix (pixel → machine coords) |
| `config/calibration_runtime_offset.json` | Runtime XY offset dari kalibrasi |
| `config/last_job_points.json` | Last captured drill points |
| `config/work_points.json` | Work points dengan jog offset |

## Error Handling

- Zero detection: Alert operator, retry dengan threshold adjustment
- Out of bounds: Clip ke workspace bounds
- Hardware timeout: Retry dengan exponential backoff

## Development Plan

Lihat `PLAN.md` untuk detailed 8-phase roadmap, architecture lengkap, dan task decomposition.