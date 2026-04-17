# Auto CNC Drill System - Structure

## Entry & Runtime

- Entry: `main.py`
- Server: `src/ui/server.py`
- Port default: `8000`
- UI:
  - `/` dashboard utama
  - `/mapping` dashboard khusus mapping + calibrate

## Source Structure

```text
src/
  cnc/
    controller.py      # GRBL controller
    job_manager.py     # job orchestration & gcode utility
  core/
    config.py
    fsm.py
  vision/
    camera.py
    detector.py
    transformer.py
    refiner.py
  ui/
    server.py
    templates/
      dashboard.html
      mapping_calibrate.html
```

## WebSocket Command Aktif

- `start`
- `mapping`
- `refine_drill`
- `stop`
- `preflight`
- `home`
- `home_z`
- `standby`
- `unlock`
- `reset`
- `calibrate`
- `reset_offset`
- `camera_connect`
- `preview_camera_connect`
- `jog`

## File Runtime Utama

- `config/calibration_affine.json`
- `config/cal_offset.json`
- `config/last_job_points.json`
- `config/work_points.json`
- `config/mapping_output.gcode`
- `temp/overlay.jpg`
- `logs/jobs/*.json`

## Workflow Ringkas

### Dashboard `/`
1. START #1 -> standby
2. START #2 -> mapping output tersimpan
3. REFINE DRILL -> drilling
4. selesai -> HOME -> IDLE

### Mapping `/mapping`
1. MAPPING -> standby -> capture -> save output
2. selesai -> HOME -> IDLE
3. CALIBRATE 2-step tersedia seperti dashboard utama
