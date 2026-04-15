# Auto CNC Drill System

Sistem otomatisasi CNC Drill berbasis YOLOv7 untuk deteksi dan pengeboran pad hole pada PCB.

## Release Status

**Version:** `v1.0`  
**Status:** `SELESAI (Production-ready for operator workflow)`  
**Tanggal rilis:** `2026-04-15`

Scope V1 selesai:
- Dashboard operasi realtime (WebSocket) + dual camera + overlay.
- Workflow auto drilling 2-click + calibrate 2-step.
- Z reference dari hasil calibrate (`cal_offset.z`) untuk standby dan drilling.
- Preflight check + soft-limit workspace + guard tombol berbasis state.
- Telemetry per job + metrics summary API (`/api/metrics`).
- Recovery flow operator (`STOP`, `UNLOCK`, `RESET`) + release gate checklist.

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
  "retry": {"move": 1, "status": 1, "capture": 1},
  "performance": {"fast_point_threshold": 60, "fast_xy_multiplier": 1.2},
  ...
}
```

Tahap 3 runtime behavior:
- Adaptive detection threshold fallback (`detection.retry_count`, `detection.retry_threshold_step`)
- Retry untuk capture/move/status (`retry.*`)
- Adaptive XY feedrate berdasar jumlah titik (`performance.*`)
- Optional per-point refine sebelum drill (`refine.*`, default off)

## Usage

### Mode Auto (2-Click Drill)

| Click | Action |
|-------|--------|
| 1 | START → Move ke standby (X85, Y-95) |
| 2 | START → Capture → Drill semua hole → Return standby |

### Mode Calibrate (Set Offset)

| Click | Action |
|-------|--------|
| 1 | CALIBRATE → Capture, move ke target padhole |
| 2 | Jog X/Y/Z ke posisi aktual lalu CALIBRATE lagi untuk simpan offset (X, Y, Z) ke `cal_offset.json` |

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
| GOTO FIRST | Move ke titik pertama dari `work_points` untuk alignment PCB |
| GOTO LAST | Move ke titik terakhir dari `work_points` untuk alignment PCB |
| UNLOCK | Unlock GRBL |
| CHECK | Preflight check (model, calibration, camera, CNC) |
| RESET | Stop workflow + clear offsets + recover CNC |

## Files

| File | Fungsi |
|------|--------|
| `config/config.json` | System configuration |
| `config/calibration_affine.json` | Affine transformation matrix |
| `config/last_job_points.json` | Original drill points |
| `config/cal_offset.json` | X, Y, Z offset dari CALIBRATE |
| `config/work_points.json` | last_job_points + cal_offset |
| `logs/jobs/*.json` | Telemetry per job (event + metrics) |

## Metrics API

- `GET /api/metrics` → ringkasan semua job logs
- `GET /api/metrics?date_utc=YYYY-MM-DD` → ringkasan harian (UTC)
- KPI refine tersedia: `refine_started_total`, `refine_applied_total`, `refine_apply_rate_pct`, `top_refine_skip_status`

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
