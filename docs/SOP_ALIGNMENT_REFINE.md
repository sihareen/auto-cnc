# SOP Operator: Mapping + Refine Drill

## 1) Pre-Start

1. Nyalakan CNC dan kamera.
2. Buka UI (`/` atau `/mapping`).
3. Klik `CHECK` dan pastikan `Preflight = OK`.

## 2) Mapping

### Opsi A: Dashboard utama (`/`)
1. START #1 -> standby.
2. START #2 -> capture + mapping output tersimpan.

### Opsi B: Halaman `/mapping`
1. Klik `MAPPING`.
2. Tunggu proses: standby -> capture -> finish.
3. Mesin otomatis `HOME` -> `IDLE`.

## 3) Calibrate (2-step)

1. Klik `CALIBRATE` (step 1), sistem pindah ke titik target hasil deteksi.
2. Jog X/Y/Z sampai tepat.
3. Klik `CALIBRATE` lagi (step 2) untuk simpan offset `x,y,z`.
4. Sistem kembali `HOME` -> `IDLE`.

## 4) Drilling (Dashboard `/`)

1. Pastikan `work_points` sudah ada.
2. Klik `REFINE DRILL`.
3. Monitor status/progress/error.
4. Selesai: mesin `HOME` -> `IDLE`.

## 5) Jika Refine Aktif

- Sistem mencoba koreksi XY lokal sebelum `Z down` di tiap titik.
- Jika refine gagal/timeout, fallback ke titik nominal (job lanjut).
- Hasil refine tercatat di telemetry.

## 6) Recovery Jika Error

1. Klik `STOP`.
2. Klik `RESET`.
3. Jika perlu: `UNLOCK` lalu `HOME`.
4. Cek `ErrCode` di UI.
