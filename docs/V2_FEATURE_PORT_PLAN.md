# V2 Feature Port Plan - Status Update

Dokumen ini mencatat status porting fitur V2 ke arsitektur V1.

## Scope yang Sudah Masuk Runtime

1. **Refine-per-point sebelum drilling**
- Sudah terintegrasi di flow `refine_drill`.
- Modul core: `src/vision/refiner.py`.

2. **Mapping output pipeline**
- Simpan:
  - `config/last_job_points.json`
  - `config/work_points.json`
  - `config/mapping_output.gcode`

3. **Halaman khusus Mapping + Calibrate**
- Route: `/mapping`
- Fokus mapping workflow + calibrate 2-step.

## Perubahan Scope Terbaru

- Fitur alignment tombol `GOTO FIRST`/`GOTO LAST` **tidak dipakai lagi** di UI aktif.
- Final motion setelah proses utama dibuat konsisten kembali ke `HOME` sebelum `IDLE`.

## Command WebSocket Aktif Terkait V2

- `mapping`
- `calibrate`
- `refine_drill`
- `home_z`

## Catatan

Prinsip tetap: port fitur ke arsitektur modular V1, bukan mem-port script monolit lama secara mentah.
