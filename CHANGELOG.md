# Changelog

### [2026-04-14]
- **fix(cnc):** unlock GRBL ($X) before homing to prevent error 8
- **feat(ui):** jog-step dropdown (0.1, 0.5, 1, 5 mm) instead of manual input
- **fix(workflow):** add safe position fallback when homing fails (error 8)
- **docs:** update README dengan 3-click workflow, jog offset adjustment, dan konfigurasi file
- **feat(workflow):** simpan drill points ke config/last_job_points.json setiap capture (overwrite)
- **revert(workflow):** hapus fitur manual jog adjustment di continue_drill_workflow

### [2026-04-14]
- **feat(ui):** tambah kontrol STANDBY di dashboard dan integrasi command backend untuk safe move ke koordinat standby (Z-up lalu XY)
- **feat(cnc):** integrasikan RESET ke recovery hardware (emergency stop + unlock/home/clearance) agar tidak hanya reset state aplikasi
- **feat(workflow):** ubah alur START menjadi 2 tahap (klik 1 berhenti di padhole pertama, klik 2 lanjut drilling)
- **feat(workflow):** ubah alur operasi agar capture dilakukan setelah mesin di standby dan return ke standby setelah job selesai
- **feat(vision):** simpan otomatis gambar capture mentah dan hasil deteksi ke folder `temp/` dengan nama berbasis timestamp/job
- **feat(ui):** tambah kontrol camera source (main camera + preview camera) dengan endpoint stream terpisah
- **feat(ui):** tambah manual CNC jog control dan tombol RESET OFFSET
- **feat(ui):** ubah tema dashboard ke dark mode
- **refactor(ui):** pisahkan template HTML dashboard dari backend ke `src/ui/templates/dashboard.html`
- **refactor(calibrate):** pindahkan script kalibrasi ke folder `calibrate/` dan urutkan nama file sesuai alur:
	- `01_add_markers.py`
	- `02_calibrate_from_markers.py`
	- `03_calibrate_cli.py`
	- `04_calibrate.py`
- **feat(calibrate):** implement kalibrasi dinamis 1-20 titik dengan mode fitting adaptif:
	- 1 titik: `translation`
	- 2 titik: `similarity`
	- >=3 titik: `affine`
- **feat(calibrate):** simpan metadata `fit_mode` ke `config/calibration_affine.json`
- **docs(calibrate):** tambah `README_Calibrate.md` berisi alur penggunaan dan penjelasan proses kalibrasi end-to-end
- **fix(cnc):** ganti homing command ke `$H` untuk kompatibilitas GRBL
- **fix(cnc):** perbaiki parser state GRBL untuk token seperti `Hold:0`
- **fix(cnc):** perbaiki handling ACK/timeout homing agar lebih robust di variasi firmware/controller
- **fix(jog):** ubah jalur jog ke perintah relatif signed GRBL (`G91 -> G1 -> G90`) agar nilai negatif/positif mengikuti arah perintah
- **fix(ui):** koreksi mapping tombol MAJU/MUNDUR pada jog agar sesuai orientasi operator
- **perf(startup):** optimasi startup dengan lazy-load model detector
- **chore(cleanup):** pisahkan file non-product/non-calibration ke folder `useless/` dan rapikan berdasarkan kategori
- **chore(cleanup):** hapus runner non-GUI `run.py` dan `run_drill_workflow.py` agar fokus ke jalur GUI dashboard

### [2026-04-13]
- **feat:** add full drill workflow integration (run_drill_workflow.py)
- **refactor:** update server.py dengan full drill workflow pada command 'start'
- **feat:** integrate YOLOv7 pipeline from detect_test.py to src/vision/detector.py
- **fix:** patch torch.load for PyTorch 2.6+ compatibility
- **fix:** call cnc_controller.connect() on init (previously never connected)
- **docs:** buat STRUCTURE.md dengan dokumentasi alur sistem dan script roles

### [2026-04-10]
- **docs:** buat README.md dengan arsitektur sistem, state machine, dan usage instructions
- **docs:** buat PLAN.md dengan 8-phase roadmap lengkap, technical stack, FSM diagram, task decomposition, dan sinkronisasi Web UI-Hardware
- **docs:** buat DOKUMENTASI.md dengan penjelasan alur kerja sistem dalam bahasa mudah dipahami
- **feat:** implement Phase 1: Environment Setup (project structure, Python 3.14 venv, dependencies)
- **feat:** implement Phase 2: GRBLController dan CNCStateMachine untuk hardware control
- **feat:** implement Phase 3: CameraCapture dan YOLODetector untuk vision system
- **feat:** implement Phase 4: AffineTransformer untuk coordinate conversion
- **feat:** implement Phase 5: DrillJobManager dan ExecutionController untuk job orchestration