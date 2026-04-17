# Changelog

## 2026-04-16

### Changed
- Hapus fitur UI `GOTO FIRST` dan `GOTO LAST` dari dashboard utama.
- Posisi `X/Y/Z` pada UI disinkronkan dari posisi aktual GRBL saat broadcast state.
- Tambah route page khusus mapping: `/mapping`.
- Tambah command WebSocket `mapping` untuk workflow mapping-only.
- Tambah command `home_z` dan tombol `Z-HOME` di halaman `/mapping`.
- Final motion pasca proses dibuat konsisten kembali ke `HOME` sebelum `IDLE`.

### UI /mapping
- Disederhanakan menjadi 1 webcam utama.
- Overlay preview tetap tersedia sebagai panel terpisah.
- Indikator proses interaktif untuk `MAPPING` dan `CALIBRATE`.

### Docs
- Sinkronisasi seluruh dokumen utama (`README.md`, `docs/*.md`, `calibrate/README_Calibrate.md`) dengan behavior runtime terbaru.

## 2026-04-15

### Released
- v1.0 production-ready workflow.
- Dashboard realtime + dual camera + refine integration.
- Calibrate 2-step dengan offset `x,y,z`.
- Telemetry + metrics API.
