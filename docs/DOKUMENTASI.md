# Dokumentasi Operasional

## Gambaran Umum

Sistem melakukan:
1. Capture PCB dari kamera.
2. Deteksi padhole (YOLOv7).
3. Transform pixel -> koordinat mesin (affine).
4. Simpan titik kerja.
5. Drilling terkontrol (opsional refine per titik).

## Dua Mode UI

### 1. Dashboard Utama (`/`)
- Untuk operasi lengkap: mapping + refine drill.
- Flow standar:
  - `CHECK`
  - `START` (standby)
  - `START` (capture + mapping)
  - `REFINE DRILL`
  - selesai -> `HOME` -> `IDLE`

### 2. Mapping + Calibrate (`/mapping`)
- Fokus mapping output tanpa drilling.
- Flow `MAPPING`:
  - standby -> capture -> simpan output -> HOME -> IDLE
- Flow `CALIBRATE` sama seperti dashboard utama (2-step).

## Calibrate 2-Step

1. Klik `CALIBRATE` (step 1): sistem detect target dan move ke titik target.
2. Operator jog ke posisi aktual.
3. Klik `CALIBRATE` lagi (step 2): simpan offset ke `config/cal_offset.json`.
4. Sistem selesai ke `HOME` lalu `IDLE`.

## Output File

- `config/last_job_points.json`
- `config/work_points.json`
- `config/mapping_output.gcode`
- `config/cal_offset.json`
- `logs/jobs/*.json`

## Status Posisi

`X (mm), Y (mm), Z (mm)` pada UI diambil dari status aktual GRBL (`query_status_once`), bukan estimasi statis.
