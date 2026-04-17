# Release Gate & Rollback Checklist

## 1) Pre-Release Gate

1. Jalankan `CHECK` dari UI, hasil `Preflight = OK`.
2. Verifikasi main camera stream stabil (>60 detik).
3. Verifikasi CNC connect, unlock, home, standby tanpa alarm.
4. Verifikasi file wajib ada:
   - `best.pt`
   - `config/calibration_affine.json`
   - `config/config.json`
5. Jalankan smoke test dashboard utama:
   - START #1 -> standby
   - START #2 -> mapping done
   - REFINE DRILL -> complete
   - akhir proses HOME -> IDLE
6. Jalankan smoke test page `/mapping`:
   - MAPPING -> output tersimpan
   - akhir proses HOME -> IDLE
   - CALIBRATE 2-step sukses simpan `cal_offset.json`
7. Verifikasi telemetry:
   - `logs/jobs/<job_id>.json` terbentuk
   - event `metrics` ada
   - event job selesai/failed tercatat

## 2) Functional Regression Gate

1. Guard tombol aktif sesuai state busy/idle.
2. Status posisi X/Y/Z update dari posisi GRBL aktual.
3. Soft-limit workspace aktif (point clipping + warning).
4. Metrics API valid:
   - `GET /api/metrics`
   - `GET /api/metrics?date_utc=<UTC date>`
5. Jika refine aktif:
   - `refine_apply_rate_pct` terhitung
   - skip reason tercatat di `top_refine_skip_status`

## 3) Rollback Plan

1. Klik `STOP` lalu `RESET`.
2. Checkout commit/tag stabil sebelumnya.
3. Restore config stabil:
   - `config/config.json`
   - `config/cal_offset.json` (jika perlu)
4. Validasi cepat pasca rollback:
   - CHECK preflight
   - HOME
   - 1 job smoke test
5. Catat incident:
   - commit/hash gagal
   - error code utama
   - timestamp + operator

## 4) Go/No-Go

`GO` hanya bila semua gate `PASS`.  
Jika ada satu `FAIL`, status `NO-GO` dan rollback dijalankan.
