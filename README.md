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

## Penggunaan

1. **Setup Environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Kalibrasi**
   - Place PCB di workspace
   - Jalankan proses kalibrasi untuk update matriks affine

3. **Operasi**
   - State 1: Home (Y-90 untuk clearance)
   - State 2: Visual Acquisition & Inference
   - State 3: Coordinate Transformation
   - State 4: Sequential Drilling

## State Machine

```
IDLE → HOMING → ACQUIRING → TRANSFORM → READY → DRILLING → COMPLETE
```

## Kalibrasi

Matriks affine dari `calibration_affine.json` menangani transformasi coordinates:

- **Input:** Pixel coordinates dari YOLOv7
- **Output:** G-Code coordinates (mm)
- **Reprojection error:** ~1.22mm

## Error Handling

- Zero detection: Alert operator, retry dengan threshold adjustment
- Out of bounds: Clip ke workspace bounds
- Hardware timeout: Retry dengan exponential backoff

## Requirements

- Python 3.14+
- OpenCV
- PyTorch
- PySerial
- FastAPI

## Development Plan

Lihat `PLAN.md` untuk detailed 8-phase roadmap, architecture lengkap, dan task decomposition.