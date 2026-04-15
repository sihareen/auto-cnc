# Release Gate & Rollback Checklist

Dokumen ini dipakai sebelum rilis ke mesin produksi.

## 1. Pre-Release Gate

1. Jalankan preflight dari dashboard (`CHECK`) dan pastikan `Preflight = OK`.
2. Verifikasi kamera utama + preview connect dan stream jalan stabil > 60 detik.
3. Verifikasi CNC connect, unlock, home, standby tanpa alarm.
4. Verifikasi file wajib ada:
   - `best.pt`
   - `config/calibration_affine.json`
   - `config/config.json`
5. Jalankan smoke test alur 2-click:
   - START #1 ke standby
   - START #2 capture + drill
   - kembali standby
6. Verifikasi telemetry tercatat:
   - `logs/jobs/<job_id>.json` terbentuk
   - ada event `job_complete`/`job_failed`
   - ada event `metrics`
   - jika `refine.enabled=true`, ada event `refine_start` dan (`refine_applied` atau `refine_skipped`)
7. Verifikasi tidak ada error code kritikal setelah smoke test:
   - `MOTION_FAIL`
   - `SYSTEM_ERROR`
   - `TIMEOUT`

## 2. Functional Regression Gate

1. Tombol UI terkunci sesuai state (tidak bisa command invalid saat drilling/busy).
2. CALIBRATE 2-step:
   - step 1 detect + move target
   - step 2 simpan `cal_offset.json`
3. Soft-limit workspace aktif:
   - point out-of-bounds di-clip
   - warning tampil di UI
4. Metrics API jalan:
   - `GET /api/metrics`
   - `GET /api/metrics?date_utc=<hari ini UTC>`
5. Alignment first/last pad:
   - `GOTO FIRST` dan `GOTO LAST` berhasil move ke target
   - error `ALIGN_NO_POINTS` keluar benar saat `work_points` belum ada
6. Jika refine aktif:
   - `refine_apply_rate_pct` terbaca di `/api/metrics`
   - skip reason muncul di `top_refine_skip_status`

## 3. Rollback Plan

Jika rilis gagal:

1. Stop workflow:
   - klik `STOP`
   - klik `RESET`
2. Kembalikan versi sebelumnya:
   - checkout tag/commit terakhir stabil
3. Restore config stabil:
   - `config/config.json`
   - `config/cal_offset.json` (jika perlu)
4. Validasi cepat setelah rollback:
   - CHECK preflight
   - home + standby
   - 1 job smoke test
5. Catat insiden:
   - commit/hash yang gagal
   - error code utama
   - timestamp + operator

## 4. Go/No-Go Rule

`GO` hanya jika semua gate di atas `PASS`.  
Jika ada satu `FAIL`, status `NO-GO` dan rollback dijalankan.
