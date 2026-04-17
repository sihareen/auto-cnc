# Auto CNC Drill System

Sistem otomatisasi CNC Drill berbasis YOLOv7 untuk:
- deteksi pad-hole PCB,
- mapping koordinat pixel -> mesin (mm),
- export hasil mapping ke G-code,
- drilling seluruh pad hasil mapping.

## Release Status

**Version:** `v1.0`  
**Status:** `SELESAI (production-ready untuk workflow operator)`

## Entry Point

```bash
python main.py
# atau
uvicorn src.ui.server:app --host 127.0.0.1 --port 8000
```

## Halaman Web

- `/`: Main menu.
- `/mapping`: Mapping + calibrate workspace/offset.
- `/drill`: Drill dari `config/mapping_output.gcode`.

## Workflow Utama

### 1) Mapping -> Export G-code

Di halaman `/mapping`:
1. Klik `CHECK`.
2. Klik `MAPPING`.
3. Sistem jalan: standby -> capture -> detect -> transform -> simpan output.
4. Output utama:
- `config/last_job_points.json`
- `config/work_points.json`
- `config/mapping_output.gcode`

### 2) Drilling Hasil Mapping

#### Opsi A (direkomendasikan): runtime utama `/drill`

1. Buka `/drill`.
2. Pastikan preview camera terhubung.
3. Klik `Start Drill`.

#### Opsi B (legacy): script `refine/cnc_run.py`

- Tetap tersedia untuk workflow lama berbasis `refine/cnc.gcode`.

### 3) Calibrate Offset (UI 2-step)

Di halaman `/mapping`:
1. Klik `CALIBRATE` (step 1): detect target + move ke target.
2. Jog manual X/Y/Z sampai tepat.
3. Klik `CALIBRATE` lagi (step 2): simpan offset ke `config/cal_offset.json` (`x`,`y`,`z`).
4. Sistem kembali `HOME` -> `IDLE`.

## Kalibrasi Workspace (Offline)

Alur dasar:
1. Ambil foto board, simpan ke `calibrate/capture.jpeg`.
2. Jalankan marker picker:

```bash
python calibrate/01_add_markers.py calibrate/capture.jpeg
```

3. Jalankan hitung kalibrasi:

```bash
python calibrate/02_calibrate_from_markers.py --markers calibrate/calibrate_markers.txt
```

4. Masukkan koordinat workspace (X,Y) yang dicatat manual.
5. Hasil matrix tersimpan di `config/calibration_affine.json`.

## Tombol Inti

### Mapping (`/mapping`)

- `MAPPING`, `CALIBRATE`, `STOP`, `HOME`, `Z-HOME`, `STANDBY`, `UNLOCK`, `CHECK`, `RESET`
- Jog XY/Z
- Connect kamera

### Drill (`/drill`)

- `Start Drill`, `Stop`
- Connect preview camera

## Konfigurasi

File utama: `config/config.json`

Bagian penting:
- `cnc.*`
- `camera.main_source`, `camera.preview_source`
- `standby.*`
- `drill.*`
- `detection.*`
- `refine.*`
- `workspace.*`
- `output.*`

## Output Runtime

- `config/last_job_points.json`
- `config/work_points.json`
- `config/mapping_output.gcode`
- `config/cal_offset.json`
- `temp/overlay.jpg`
- `logs/jobs/*.json`

## Metrics API

- `GET /api/metrics`
- `GET /api/metrics?date_utc=YYYY-MM-DD`

KPI refine: `refine_started_total`, `refine_applied_total`, `refine_apply_rate_pct`, `top_refine_skip_status`.

## Dokumentasi Tambahan

- `docs/SCRIPT_USAGE.md`: daftar script + fungsi + cara pakai detail.
- `docs/DOKUMENTASI.md`: ringkasan operasional.
- `docs/STRUCTURE.md`: struktur source dan command aktif.
- `calibrate/README_Calibrate.md`: detail proses calibrate.
