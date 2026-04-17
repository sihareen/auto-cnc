# Dokumentasi Script dan Cara Penggunaan

Dokumen ini merangkum script utama yang dipakai di project Auto CNC, fungsi masing-masing, dan cara pakainya sesuai workflow operator.

## 1) Daftar Script dan Fungsinya

### A. Runtime Utama

1. `main.py`
- Fungsi: entry point aplikasi web FastAPI.
- Menjalankan server dashboard (`/`, `/mapping`, `/drill`) melalui `src/ui/server.py`.

2. `src/ui/server.py`
- Fungsi: pusat workflow sistem.
- Menangani command tombol UI/WebSocket: `mapping`, `refine_drill`, `calibrate`, `home`, `home_z`, `standby`, `stop`, `unlock`, `reset`, `jog`, `preflight`, `camera_connect`, `preview_camera_connect`.
- Menyimpan output mapping:
  - `config/last_job_points.json`
  - `config/work_points.json`
  - `config/mapping_output.gcode`
- Menjalankan drill dari `mapping_output.gcode` pada mode `/drill`.

### B. Modul Mesin / Vision

3. `src/cnc/controller.py`
- Fungsi: komunikasi GRBL via serial.
- Menyediakan command gerak: `move_to`, `jog_relative`, `home_axis`, `unlock`, `emergency_stop`.

4. `src/cnc/job_manager.py`
- Fungsi: manajemen titik drill dan pembentukan G-code job.
- Menyediakan optimasi urutan titik (nearest + 2-opt) untuk mode tertentu.

5. `src/vision/camera.py`
- Fungsi: manajemen kamera (connect, streaming, reconnect otomatis, ROI).

6. `src/vision/detector.py`
- Fungsi: deteksi pad-hole menggunakan YOLOv7.

7. `src/vision/transformer.py`
- Fungsi: transform koordinat pixel -> koordinat mesin (mm) menggunakan affine matrix dari file kalibrasi.

8. `src/vision/refiner.py`
- Fungsi: refine koreksi XY per titik sebelum drill (jika refine aktif).

### C. Script Calibrate Workspace

9. `calibrate/01_add_markers.py`
- Fungsi: klik marker pada gambar capture dan simpan koordinat pixel marker ke file `.txt`.

10. `calibrate/02_calibrate_from_markers.py`
- Fungsi: hitung matrix kalibrasi (pixel -> mm) dari marker + koordinat workspace manual.
- Output utama: `config/calibration_affine.json`.

11. `calibrate/03_calibrate_cli.py`
- Fungsi: utilitas CLI untuk tambah/hapus/list titik kalibrasi dan simpan matrix.
- Opsional, untuk workflow manual/headless.

12. `calibrate/04_calibrate.py`
- Fungsi: verifikasi/GUI kalibrasi tambahan.
- Opsional untuk cek ulang kualitas matrix.

13. `calibrate/01_pick_roi_center.py`
- Fungsi: bantu pilih ROI center dari gambar referensi.
- Opsional untuk tuning refine/preview ROI.

### D. Script Drill Legacy (Alternatif)

14. `refine/cnc_run.py`
- Fungsi: script drilling legacy berbasis file `refine/cnc.gcode`.
- Menjalankan alur: gerak XY -> refine visual -> drill Z per titik.
- Menyimpan titik gagal ke `refine/gagaldrill.txt`.

15. `refine/web_server.py`
- Fungsi: web server khusus mode legacy refine (terpisah dari runtime utama `src/ui/server.py`).

16. `refine/cnc_gui3.py`
- Fungsi: GUI legacy untuk jog manual, upload G-code, helper generation dari PNG.

17. `refine/live_pcb.py`
- Fungsi: live preview deteksi pad (tool bantu lama).

## 2) Cara Menggunakan (Workflow Utama)

## A. Menjalankan Aplikasi

1. Jalankan server:
```bash
python main.py
```

2. Buka halaman:
- `http://127.0.0.1:8000/` (main menu)
- `http://127.0.0.1:8000/mapping` (mapping + calibrate)
- `http://127.0.0.1:8000/drill` (drill dari hasil mapping)

## B. Mapping -> Export G-code

1. Buka `/mapping`.
2. Klik `CHECK` (preflight).
3. Klik `MAPPING`.
4. Sistem akan:
- ke standby,
- capture + detect,
- transform ke koordinat mesin,
- simpan output.

5. Pastikan file output terbentuk:
- `config/last_job_points.json`
- `config/work_points.json`
- `config/mapping_output.gcode`

## C. Drilling Hasil Mapping (Mode Runtime Utama)

### Opsi 1: via halaman `/drill`
1. Buka `/drill`.
2. Pastikan source preview camera benar.
3. Klik `Start Drill`.
4. Sumber titik drill: `config/mapping_output.gcode`.
5. Jika perlu hentikan proses: klik `Stop`.

### Opsi 2: via workflow tombol di dashboard websocket
- Gunakan command `refine_drill` setelah mapping selesai.

## D. Drilling Hasil Mapping (Mode Legacy)

1. Siapkan `refine/cnc.gcode`.
2. Jalankan:
```bash
python refine/cnc_run.py
```
3. Jika ada titik gagal refine/drill, cek:
- `refine/gagaldrill.txt`

## E. Calibrate Workspace (Sesuai Proses Anda)

1. Tentukan titik acuan secara manual di workspace, catat koordinat `X,Y` (mm).
2. Ambil foto board/capture dan simpan sebagai:
- `calibrate/capture.jpeg`

3. Jalankan marker picker:
```bash
python calibrate/01_add_markers.py calibrate/capture.jpeg
```
- Klik semua marker sesuai titik acuan.
- Simpan file marker (txt).

4. Jalankan perhitungan kalibrasi:
```bash
python calibrate/02_calibrate_from_markers.py --markers calibrate/calibrate_markers.txt
```
- Masukkan koordinat workspace `X,Y` yang sudah dicatat.
- Script akan generate/update:
  - `config/calibration_affine.json`

5. Restart aplikasi runtime setelah update kalibrasi agar nilai baru dipakai konsisten.

## F. Calibrate Offset Operasional (di UI)

1. Buka `/mapping`.
2. Klik `CALIBRATE` (step 1): sistem detect target dan move ke titik target.
3. Jog manual X/Y/Z hingga tepat.
4. Klik `CALIBRATE` lagi (step 2): simpan offset ke:
- `config/cal_offset.json` (`x`, `y`, `z`).

## 3) File Konfigurasi Penting

1. `config/config.json`
- Set CNC port, camera source, parameter mapping/drill/refine, workspace bounds.

2. `config/calibration_affine.json`
- Matrix transform pixel -> mm.

3. `config/cal_offset.json`
- Offset hasil calibrate operasional di UI.

## 4) Catatan Operasional

1. Flow utama project:
- Mapping -> export gcode -> drill semua pad.

2. Runtime utama yang direkomendasikan:
- `main.py` + `src/ui/server.py`.

3. `refine/*` adalah jalur legacy/alternatif, tetap bisa dipakai untuk kebutuhan tertentu.
