# README Calibrate

Dokumen ini menjelaskan urutan penggunaan script kalibrasi dan bagaimana proses kalibrasi bekerja pada sistem Auto CNC.

## Tujuan Kalibrasi
Kalibrasi dipakai untuk mengubah titik deteksi kamera (pixel) menjadi koordinat mesin CNC (mm) agar posisi drilling akurat.

Output utama kalibrasi disimpan ke:
- `config/calibration_affine.json`

File ini dipakai langsung oleh product code saat runtime.

## Urutan Script Kalibrasi
Script sudah diberi nomor agar urut pemakaiannya jelas.

1. `calibrate/01_add_markers.py`
2. `calibrate/02_calibrate_from_markers.py`
3. `calibrate/03_calibrate_cli.py` (opsional/manual)
4. `calibrate/04_calibrate.py` (GUI alternatif + verifikasi)

## Alur Rekomendasi (Cepat)
### 1) Ambil titik marker dari gambar
```bash
python calibrate/01_add_markers.py capture.jpg
```
- Klik titik referensi pada gambar.
- Simpan hasil marker (pixel coordinates).

### 2) Masukkan koordinat mesin untuk tiap marker, lalu hitung
```bash
python calibrate/02_calibrate_from_markers.py --markers calibrate/calibrate_markers.txt
```
- Script meminta input `X(mm)` dan `Y(mm)` untuk setiap titik pixel.
- Script menghitung matriks kalibrasi otomatis.
- Hasil langsung disimpan ke `config/calibration_affine.json`.

### 3) Verifikasi hasil (opsional, disarankan)
```bash
python calibrate/04_calibrate.py --verify
```
- Cek error reprojection dan hasil transform contoh titik.

## Alur Manual (Alternatif)
Jika ingin input titik satu per satu secara manual:

```bash
python calibrate/03_calibrate_cli.py --add <px_x> <px_y> <mm_x> <mm_y>
python calibrate/03_calibrate_cli.py --calculate
python calibrate/03_calibrate_cli.py --save
python calibrate/03_calibrate_cli.py --verify
```

## Logika Dinamis Jumlah Titik (1-20)
Sistem kalibrasi mendukung jumlah titik dinamis dengan mode fitting berikut:

- 1 titik: `translation`
- 2 titik: `similarity` (scale + rotation + translation)
- 3 atau lebih titik: `affine` (least-squares)

Batas jumlah titik:
- Minimal: 1
- Maksimal: 20

Catatan praktik terbaik:
- Disarankan gunakan 9-20 titik.
- Sebarkan titik di seluruh area kerja (pojok, tepi, tengah).
- Hindari titik menumpuk di satu area saja.

## Struktur Data Kalibrasi
File `config/calibration_affine.json` menyimpan:
- `matrix` (2x3 transform)
- `fit_mode` (`translation`, `similarity`, `affine`)
- `src_points_px`
- `dst_points_mm`
- `reprojection_error_mm`
- `per_point_error_mm`

## Integrasi ke Product Code
Setelah file kalibrasi tersimpan, runtime product akan membaca nilai terbaru dari `config/calibration_affine.json`.

Agar update dipakai penuh, restart aplikasi setelah kalibrasi.

## Catatan Tambahan
Selain matriks utama, sistem bisa punya offset runtime di `config/calibration_runtime_offset.json` untuk koreksi operator saat proses produksi.
