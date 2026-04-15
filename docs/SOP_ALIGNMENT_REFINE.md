# SOP Operator: Alignment + Refine (V2)

SOP ini dipakai operator untuk setup PCB dan drilling dengan fitur:
- `GOTO FIRST` / `GOTO LAST` (alignment)
- refine per titik sebelum bor (opsional, via `refine.enabled`)

## 1. Pre-Start

1. Nyalakan CNC dan kamera.
2. Buka dashboard.
3. Klik `CHECK` dan pastikan `Preflight = OK`.
4. Pastikan `work_points` sudah tersedia (jalankan capture job minimal sekali jika belum ada).

## 2. Alignment PCB

1. Klik `GOTO FIRST`.
2. Cek apakah posisi spindle tepat pada pad pertama.
3. Klik `GOTO LAST`.
4. Cek apakah posisi spindle tepat pada pad terakhir.
5. Jika meleset:
   - lakukan jog manual kecil
   - jalankan `CALIBRATE` 2-step untuk update `cal_offset`
   - ulang `GOTO FIRST` dan `GOTO LAST`

## 3. Run Drilling

1. Klik `START` pertama (standby).
2. Klik `START` kedua (capture + drill).
3. Monitor:
   - `Status`
   - `ErrCode`
   - `Warning`
   - progress

## 4. Refine Behavior

Jika `refine.enabled = true`:
- Sebelum setiap titik bor, sistem mencoba refine XY lokal.
- Jika refine sukses: posisi XY disesuaikan kecil sebelum `Z down`.
- Jika refine gagal/timeout: fallback ke titik nominal (job tetap lanjut) dan warning tercatat.

## 5. Post-Run Checks

1. Buka metrics:
   - `GET /api/metrics`
2. Pantau KPI refine:
   - `refine_apply_rate_pct`
   - `top_refine_skip_status`
3. Jika `refine_apply_rate_pct` sangat rendah:
   - evaluasi lighting/focus kamera
   - evaluasi `refine.roi_radius_px`, `refine.search_radius_mm`, `refine.timeout_ms`

## 6. Tindakan Jika Error

1. Klik `STOP`.
2. Klik `RESET`.
3. Jika perlu: `UNLOCK` lalu `HOME`.
4. Cek `ErrCode`:
   - `ALIGN_NO_POINTS`: jalankan capture untuk generate `work_points`
   - `ALIGN_MOVE_FAIL`: cek batas workspace + koneksi CNC
   - `MOTION_FAIL`: cek mekanik/speed/feed
   - `TIMEOUT`: cek kamera/serial load

## 7. Parameter Tuning Cepat (Engineer)

File: `config/config.json`

1. Alignment:
- `alignment.clearance_mm`

2. Refine:
- `refine.enabled`
- `refine.roi_radius_px`
- `refine.max_delta_mm`
- `refine.tol_x_mm`
- `refine.tol_y_mm`
- `refine.search_radius_mm`
- `refine.timeout_ms`

3. Safety:
- `workspace.*`

Catatan:
- Ubah parameter bertahap.
- Simpan baseline nilai stabil sebelum tuning.
