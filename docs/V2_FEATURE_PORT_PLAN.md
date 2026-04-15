# V2 Feature Port Plan (From `v2-plan` to V1 Architecture)

## Tujuan

Dokumen ini mendefinisikan rencana pengembangan **V2** dengan mengambil fitur dari folder `v2-plan` dan mengintegrasikannya ke arsitektur V1 saat ini.

Fitur yang di-port:
1. **Refine-per-point sebelum bor**
2. **Alignment first/last pad**
3. **Port fitur, bukan port script mentah**

Prinsip utama:
- Reuse fondasi V1: FastAPI + WebSocket + state machine + config JSON + telemetry.
- Hindari copy-paste script monolit (`cnc_run.py`, `cnc_gui3.py`) ke runtime V1.
- Semua perilaku baru wajib configurable dan observable.

---

## Scope V2

### In Scope

1. Refine lokal per titik drilling:
- Sebelum `Z down`, jalankan deteksi lokal di ROI sekitar target point.
- Hitung delta XY kecil, lakukan koreksi XY bounded.
- Simpan hasil refine per titik ke telemetry.

2. Alignment first/last pad:
- Mode setup untuk verifikasi posisi PCB pada pad pertama dan pad terakhir.
- UI menyediakan aksi: `Goto First Pad`, `Goto Last Pad`.
- Operator dapat jog manual lalu commit offset.

3. Integrasi arsitektural:
- Endpoint/API baru di `src/ui/server.py`.
- Logic refinement di modul vision/cnc terpisah (bukan di UI template/script tunggal).
- Parameter masuk `config/config.json`.

### Out of Scope

1. Port GUI Tkinter.
2. Port IO file legacy (`padx.txt`, `pady.txt`, `cam.txt`, dst).
3. Port literal flow script lama line-by-line.

---

## Mapping Fitur Lama ke V1

### A. Refine-per-point sebelum bor

Sumber ide lama:
- `v2-plan/cnc_run.py` fungsi `carititikpas()` (ROI + deteksi + koreksi XY).

Port target V1:
- Modul baru: `src/vision/refiner.py`
- Kontrak function:
  - input: frame, initial_xy_mm, roi_px, max_iter, toleransi
  - output: refined_xy_mm, iterations, confidence, status

Integrasi flow:
1. `run_drill_workflow()` / `continue_drill_workflow()`
2. sebelum `move_z_down`
3. jalankan refiner
4. update target XY final
5. drill

Guard rail:
- Batas koreksi per titik (`max_delta_mm`)
- Timeout refine per titik
- Fallback: jika refine gagal, pakai point awal + tandai warning/error code

### B. Alignment first/last pad

Sumber ide lama:
- `v2-plan/cnc_gui3.py` fungsi `exec_first_line()` dan `exec_last_line()`.

Port target V1:
- Gunakan point hasil detect/transform dari job terakhir (`last_job_points.json`/`work_points.json`).
- Endpoint command WebSocket baru:
  - `goto_first_pad`
  - `goto_last_pad`

Perilaku:
1. Load work points
2. Move ke point pertama/terakhir (XY + clearance Z aman)
3. Operator cek visual, jog manual bila perlu
4. Optional: commit offset via CALIBRATE/offset action

### C. Port fitur, bukan script mentah

Aturan implementasi:
1. Tidak membaca/menulis file legacy txt.
2. Tidak menambah dependency GUI non-web.
3. Semua constant dipindah ke `config/config.json`.
4. Semua event baru masuk telemetry `logs/jobs/*.json`.

---

## Desain Teknis (Target)

### 1) Config baru (`config/config.json`)

Tambahkan blok:
- `refine.enabled`
- `refine.roi_radius_px`
- `refine.max_iter`
- `refine.max_delta_mm`
- `refine.tol_x_mm`
- `refine.tol_y_mm`
- `refine.timeout_ms`
- `alignment.clearance_mm`

### 2) Error Code baru

- `REFINE_FAIL`
- `ALIGN_NO_POINTS`
- `ALIGN_MOVE_FAIL`

### 3) Telemetry event baru

- `refine_start`
- `refine_iter`
- `refine_applied`
- `refine_skipped`
- `goto_first_pad`
- `goto_last_pad`

### 4) UI update (dashboard)

- Tombol baru:
  - `GOTO FIRST`
  - `GOTO LAST`
- Status ringkas refine:
  - last refined delta
  - refine success/fail count per job

---

## Rencana Eksekusi (Sprint)

## Sprint 1: Alignment First/Last Pad

Deliverable:
1. Command backend `goto_first_pad` / `goto_last_pad`
2. UI button + log feedback
3. Safety move (clearance Z + bounds check)

Acceptance:
1. Operator bisa pindah ke pad pertama/terakhir dari work points.
2. Jika work points kosong -> error `ALIGN_NO_POINTS`.

## Sprint 2: Refiner Core

Deliverable:
1. `src/vision/refiner.py` dengan API stabil
2. Unit test untuk kalkulasi delta + clamp
3. Integrasi optional (feature flag `refine.enabled`)

Acceptance:
1. Refinement berhasil memperkecil error posisi pada test scene.
2. Timeout/fallback aman tanpa stop total workflow.

## Sprint 3: Integrasi Drill + Telemetry

Deliverable:
1. Hook refine sebelum `Z down`
2. Event telemetry refine detail
3. KPI ringkas di `/api/metrics` (refine success rate)

Acceptance:
1. Job tetap selesai walau refine fail (dengan warning).
2. Semua titik punya jejak refine event (applied/skipped/fail).

## Sprint 4: Hardening + SOP

Deliverable:
1. Tuning parameter refine via config
2. SOP operator alignment + refine
3. Update `README.md` + `docs/RELEASE_GATE.md`

Acceptance:
1. Uji end-to-end di mesin real tanpa regresi alur V1.
2. Release gate pass.

---

## KPI V2

1. First-pass hit accuracy naik (dibanding V1 baseline).
2. Rework/manual drill turun.
3. Waktu setup alignment turun.
4. Failure karena misalignment turun.

---

## Risiko & Mitigasi

1. Refine menambah cycle time.
- Mitigasi: timeout ketat + max_iter kecil + skip adaptif untuk confidence tinggi.

2. Koreksi refine berlebihan (over-correct).
- Mitigasi: clamp `max_delta_mm` + threshold tolerance.

3. Operator bingung flow baru.
- Mitigasi: wizard text + SOP singkat di UI/docs.

---

## Definition of Done V2

1. Fitur `refine-per-point` aktif dan bisa di-toggle.
2. Fitur `goto first/last pad` aktif dan aman dipakai operator.
3. Seluruh fitur baru terukur via telemetry + metrics.
4. Tidak ada ketergantungan pada script legacy monolit.
