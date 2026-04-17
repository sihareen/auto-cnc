# README Calibrate

Dokumen ini menjelaskan alur kalibrasi pada Auto CNC Drill System.

## Status Integrasi

Berlaku untuk runtime aktif:
- offset kalibrasi disimpan ke `config/cal_offset.json` (`x`,`y`,`z`)
- matrix affine disimpan di `config/calibration_affine.json`

## Tujuan

Kalibrasi mengubah hasil deteksi kamera (pixel) menjadi koordinat mesin (mm) dan mengunci referensi offset operator.

## Alur Calibrate di Dashboard (2-Step)

1. Klik `CALIBRATE` (step 1)
- Sistem capture + detect target
- Sistem move ke target

2. Jog manual
- Operator jog X/Y/Z hingga tepat di titik aktual

3. Klik `CALIBRATE` lagi (step 2)
- Sistem simpan offset ke `config/cal_offset.json`
- Sistem kembali `HOME` lalu `IDLE`

## Script Kalibrasi Offline

Urutan script tetap tersedia:
1. `calibrate/01_add_markers.py`
2. `calibrate/01_pick_roi_center.py` (opsional, bantu pilih pixel ROI center)
3. `calibrate/02_calibrate_from_markers.py`
4. `calibrate/03_calibrate_cli.py` (opsional)
5. `calibrate/04_calibrate.py` (verifikasi)

Contoh:

```bash
python calibrate/01_add_markers.py capture.jpg
python calibrate/01_pick_roi_center.py capture.jpg --grid 15
python calibrate/02_calibrate_from_markers.py --markers calibrate/calibrate_markers.txt
python calibrate/04_calibrate.py --verify
```

## Catatan

Setelah update matrix/offset, restart aplikasi agar runtime memakai nilai terbaru secara konsisten.
