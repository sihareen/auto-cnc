# Changelog

### [2026-04-13]
- **feat:** add full drill workflow integration (run_drill_workflow.py)
- **refactor:** update server.py dengan full drill workflow pada command 'start'
- **feat:** integrate YOLOv7 pipeline from detect_test.py to src/vision/detector.py
- **fix:** patch torch.load for PyTorch 2.6+ compatibility
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