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

## Konfigurasi

Edit `config/config.json` untuk configure:

```json
{
  "cnc": {"port": "/dev/ttyUSB0", "baudrate": 115200},
  "camera": {"main_index": 4, "preview_index": 0},
  "standby": {"x": 85.0, "y": -95.0},
  "drill": {"z_depth": 1.5, "z_clearance": 5.0},
  ...
}
```

## Usage

### Mode Auto (2-Click Drill)

| Click | Action |
|-------|--------|
| 1 | START → Move ke standby (X85, Y-95) |
| 2 | START → Capture → Drill semua hole → Return standby |

### Mode Calibrate (Set Offset)

| Click | Action |
|-------|--------|
| 1 | CALIBRATE → Capture, move ke padhole → PAUSE |
| 2 | (Optional) Jog X/Y/Z ke posisi actual |
| 2 | CALIBRATE → Simpan offset (X, Y, Z) ke `cal_offset.json` |

**Z Calibration:**
- Jog spindle turun ke surface PCB
- Jog X/Y ke target hole
- CALIBRATE → Simpan posisi sebagai reference
- Saat drill, Z relatif dari posisi yang sudah di-reference

### Button Functions

| Button | Fungsi |
|--------|--------|
| START | 2-click drill workflow |
| STOP | Stop execution |
| CALIBRATE | Set offset (X, Y, Z) |
| HOME | CNC homing (X, Y, Z) |
| STANDBY | Move ke posisi standby |
| UNLOCK | Unlock GRBL |
| RESET | Stop workflow + clear offsets + recover CNC |

## Files

| File | Fungsi |
|------|--------|
| `config/config.json` | System configuration |
| `config/calibration_affine.json` | Affine transformation matrix |
| `config/last_job_points.json` | Original drill points |
| `config/cal_offset.json` | X, Y, Z offset dari CALIBRATE |
| `config/work_points.json` | last_job_points + cal_offset |

## State Machine

```
IDLE → STANDBY → STANDBY_READY → ACQUIRING → DRILLING → STANDBY
                     ↓
              CALIBRATE (PAUSE)
```

## Error Handling

- Zero detection: Alert operator, retry dengan threshold adjustment
- Out of bounds: Clip ke workspace bounds
- Hardware timeout: Retry dengan exponential backoff

## Development Plan

Lihat `PLAN.md` untuk detailed 8-phase roadmap, architecture lengkap, dan task decomposition.