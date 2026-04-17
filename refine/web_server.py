from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional, Any

import cv2
import numpy as np
import serial
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from ultralytics import YOLO

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "templates" / "refine_dashboard.html"


def _read_txt(path: str, default: str = "") -> str:
    try:
        return (BASE_DIR / path).read_text().strip()
    except Exception:
        return default


def _write_txt(path: str, value: str) -> None:
    (BASE_DIR / path).write_text(str(value).strip() + "\n")


def _safe_float(raw: Any, default: float) -> float:
    try:
        return float(str(raw).strip())
    except Exception:
        return float(default)


def _is_xy_motion_line(line: str) -> bool:
    upper = line.upper().strip()
    if not upper:
        return False
    if upper.startswith(("%", "(", ";")):
        return False
    if "Z" in upper:
        return False
    has_motion = ("G0" in upper) or ("G1" in upper)
    has_xy = ("X" in upper) or ("Y" in upper)
    return has_motion and has_xy


class RefineConfig(BaseModel):
    padx: int = 282
    pady: int = 257
    roi_radius: int = 120
    zdepth: float = 1.5
    zfeed: float = 300.0
    com: str = "/dev/ttyUSB0"
    cam: int = 2
    max_no_det_retry: int = 10
    tol_x: float = 0.03
    tol_y: float = 0.03


class RefineRunner:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.status = "IDLE"
        self.total_lines = 0
        self.current_line = 0
        self.failed_count = 0
        self.last_error = ""
        self.logs: list[str] = []
        self.preview_frame: Optional[np.ndarray] = None
        self.config = RefineConfig(
            padx=int(_read_txt("padx.txt", "282") or 282),
            pady=int(_read_txt("pady.txt", "257") or 257),
            zdepth=_safe_float(_read_txt("zdepth.txt", "1.5"), 1.5),
            zfeed=_safe_float(_read_txt("zfeed.txt", "300"), 300.0),
            com=_read_txt("com.txt", "/dev/ttyUSB0") or "/dev/ttyUSB0",
            cam=int(_read_txt("cam.txt", "2") or 2),
        )
        self.model: Optional[YOLO] = None

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.logs.insert(0, line)
        self.logs = self.logs[:300]

    def get_state(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "status": self.status,
                "current_line": self.current_line,
                "total_lines": self.total_lines,
                "failed_count": self.failed_count,
                "last_error": self.last_error,
                "logs": self.logs[:100],
                "config": self.config.model_dump(),
            }

    def update_config(self, cfg: RefineConfig) -> None:
        with self.lock:
            self.config = cfg
        _write_txt("padx.txt", str(cfg.padx))
        _write_txt("pady.txt", str(cfg.pady))
        _write_txt("zdepth.txt", str(cfg.zdepth))
        _write_txt("zfeed.txt", str(cfg.zfeed))
        _write_txt("com.txt", cfg.com)
        _write_txt("cam.txt", str(cfg.cam))

    def start(self) -> None:
        with self.lock:
            if self.running:
                raise RuntimeError("Job already running")
            self.stop_event.clear()
            self.running = True
            self.status = "STARTING"
            self.current_line = 0
            self.failed_count = 0
            self.last_error = ""

        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._log("Stop requested")

    def _send_and_wait_idle(self, ser: serial.Serial, cmd: str, timeout: float = 20.0) -> bool:
        ser.write((cmd.strip() + "\n").encode())
        ser.flush()
        end = time.time() + timeout
        while time.time() < end:
            ser.write(b"?\n")
            ser.flush()
            raw = ser.readline().decode(errors="ignore").strip()
            if raw.startswith("<Idle"):
                return True
            if raw.lower().startswith("error") or raw.lower().startswith("alarm"):
                return False
            time.sleep(0.05)
        return False

    def _refine_point(self, ser: serial.Serial, cap: cv2.VideoCapture) -> bool:
        cfg = self.config
        x, y, r = cfg.padx, cfg.pady, cfg.roi_radius

        if self.model is None:
            self.model = YOLO(str(BASE_DIR / "best.pt"))

        varx = 10.0
        vary = 10.0
        checks = 0

        mask = np.zeros((480, 640), dtype=np.uint8)
        cv2.circle(mask, (x, y), r, 255, -1)

        while not self.stop_event.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                checks += 1
                if checks > cfg.max_no_det_retry:
                    return False
                continue

            frame = cv2.resize(frame, (640, 480))
            pre = cv2.convertScaleAbs(frame, alpha=1.5, beta=20)
            pre = cv2.normalize(pre, None, 0, 255, cv2.NORM_MINMAX)
            masked = cv2.bitwise_and(pre, pre, mask=mask)

            results = self.model(masked, conf=0.1, iou=0.5, verbose=False)
            boxes = results[0].boxes

            best_center = None
            best_conf = -1.0
            for box in boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                d = float(np.hypot(cx - x, cy - y))
                if d > r:
                    continue
                conf = float(box.conf.item())
                if conf > best_conf:
                    best_conf = conf
                    best_center = (cx, cy)

            view = results[0].plot()
            cv2.circle(view, (x, y), 5, (0, 0, 255), -1)

            if best_center is None:
                checks += 1
                with self.lock:
                    self.preview_frame = view
                if checks > cfg.max_no_det_retry:
                    return False
                time.sleep(0.1)
                continue

            cx, cy = best_center
            varx = round((cx - x) / 50.0, 2)
            vary = round((cy - y) / 40.0, 2)
            varx = max(-0.1, min(0.1, varx))
            vary = max(-0.1, min(0.1, vary))

            cv2.circle(view, (int(cx), int(cy)), 5, (0, 255, 0), -1)
            cv2.putText(view, f"dx={varx:.2f} dy={vary:.2f}", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
            with self.lock:
                self.preview_frame = view

            if abs(varx) <= cfg.tol_x and abs(vary) <= cfg.tol_y:
                return True

            cmd = f"G91 X{varx} Y{vary}"
            if not self._send_and_wait_idle(ser, cmd, timeout=10.0):
                return False
            time.sleep(0.05)

        return False

    def _run(self) -> None:
        ser = None
        cap = None
        failed_lines: list[str] = []

        try:
            cfg = self.config
            gcode_path = BASE_DIR / "cnc.gcode"
            if not gcode_path.exists():
                raise RuntimeError(f"G-code not found: {gcode_path}")

            lines = [ln.strip() for ln in gcode_path.read_text().splitlines() if _is_xy_motion_line(ln)]
            with self.lock:
                self.total_lines = len(lines)
                self.status = "RUNNING"

            self._log(f"Connect serial: {cfg.com}")
            ser = serial.Serial(cfg.com, baudrate=115200, timeout=1)
            time.sleep(0.2)
            cap = cv2.VideoCapture(int(cfg.cam))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            if not cap.isOpened():
                raise RuntimeError(f"Camera open failed: {cfg.cam}")

            zdepth = abs(float(cfg.zdepth))
            zfeed = float(cfg.zfeed)

            for idx, cmd_xy in enumerate(lines, start=1):
                if self.stop_event.is_set():
                    self._log("Stopped by user")
                    break

                with self.lock:
                    self.current_line = idx

                self._log(f"Move XY [{idx}/{len(lines)}]: {cmd_xy}")
                if not self._send_and_wait_idle(ser, cmd_xy, timeout=30.0):
                    failed_lines.append(cmd_xy)
                    self.failed_count += 1
                    self._log(f"XY move fail: {cmd_xy}")
                    continue

                ok_refine = self._refine_point(ser, cap)
                if not ok_refine:
                    failed_lines.append(cmd_xy)
                    self.failed_count += 1
                    self._log("Refine fail, skip drill")
                    continue

                self._log("Refine ok, drilling")
                self._send_and_wait_idle(ser, "G91", timeout=5.0)
                self._send_and_wait_idle(ser, f"G01 Z-{zdepth:.4f} F{zfeed:.1f}", timeout=10.0)
                self._send_and_wait_idle(ser, f"G01 Z{zdepth:.4f} F{zfeed:.1f}", timeout=10.0)
                self._send_and_wait_idle(ser, "G90", timeout=5.0)

            (BASE_DIR / "gagaldrill.txt").write_text("\n".join(failed_lines) + ("\n" if failed_lines else ""))

            with self.lock:
                self.status = "STOPPED" if self.stop_event.is_set() else "DONE"
                self.last_error = ""
            self._log(f"Job finished. Failed lines: {len(failed_lines)}")

        except Exception as e:
            with self.lock:
                self.status = "ERROR"
                self.last_error = str(e)
            self._log(f"ERROR: {e}")
        finally:
            if cap is not None:
                cap.release()
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            with self.lock:
                self.running = False


runner = RefineRunner()
app = FastAPI(title="Refine CNC Web", version="1.0")


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    if not TEMPLATE_PATH.exists():
        raise HTTPException(status_code=500, detail="Template not found")
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@app.get("/api/state")
def state() -> JSONResponse:
    return JSONResponse(runner.get_state())


@app.post("/api/config")
def set_config(cfg: RefineConfig) -> JSONResponse:
    if runner.running:
        raise HTTPException(status_code=400, detail="Cannot update config while running")
    runner.update_config(cfg)
    runner._log("Config updated")
    return JSONResponse({"ok": True})


@app.post("/api/start")
def start() -> JSONResponse:
    try:
        runner.start()
        runner._log("Job started")
        return JSONResponse({"ok": True})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/stop")
def stop() -> JSONResponse:
    runner.stop()
    return JSONResponse({"ok": True})


@app.get("/video/stream")
def video_stream():
    def gen():
        while True:
            frame = runner.preview_frame
            if frame is None:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(frame, "No preview", (220, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            ok, buf = cv2.imencode('.jpg', frame)
            if not ok:
                time.sleep(0.1)
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
            )
            time.sleep(0.1)

    return StreamingResponse(gen(), media_type='multipart/x-mixed-replace; boundary=frame')
