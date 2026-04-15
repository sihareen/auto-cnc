"""
FastAPI Web Server for Auto CNC Dashboard
"""
import asyncio
import logging
import threading
import json
from datetime import datetime, UTC
from time import perf_counter
from uuid import uuid4
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(title="Auto CNC Drill System", version="1.0.0")

# Global state (in production, use proper state management)
system_state = {
    "status": "IDLE",
    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
    "progress": {"current": 0, "total": 0},
    "connected": False,
    "last_error": None,
    "last_warning": None,
    "execution_state": 0,
    "calibrate_state": "idle",
    "start_state": "idle",
    "preflight": {},
}

# WebSocket connections
connected_clients: List[WebSocket] = []
workflow_task: Optional[asyncio.Task] = None
stop_event = threading.Event()
current_job_id: Optional[str] = None

# Initialize components (will be connected in main)
camera = None
preview_camera = None
detector = None
cnc_controller = None
job_manager = None
executor = None
transformer = None
pending_drill_points: List[Tuple[float, float]] = []
jog_offset: Dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0}

# Load config
CONFIG_PATH = Path("config/config.json")
_config = {}
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH, "r") as f:
            _config = json.load(f)
        logger.info(f"Loaded config from {CONFIG_PATH}")
    except Exception as e:
        logger.warning(f"Failed to load config: {e}")

def _get_cfg(path: str, default: Any = None) -> Any:
    """Get config value by dot-separated path (e.g., 'standby.x')"""
    keys = path.split(".")
    val = _config
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
    return val if val is not None else default

# CNC settings
STANDBY_X = _get_cfg("standby.x", 85.0)
STANDBY_Y = _get_cfg("standby.y", -95.0)
XY_MOVE_FEED = int(_get_cfg("drill.xy_move_feed", 1000))
Z_DRILL_FEED = int(_get_cfg("drill.z_drill_feed", 300))
Z_MOVE_FEED = int(_get_cfg("drill.z_move_feed", 1000))
DETECTION_CONFIDENCE_THRESHOLD = float(_get_cfg("detection.confidence_threshold", 0.25))
DETECTION_IOU_THRESHOLD = float(_get_cfg("detection.iou_threshold", 0.45))
DETECTION_MODEL_PATH = str(_get_cfg("detection.model_path", "best.pt"))
DETECTION_MIN_POINTS = int(_get_cfg("detection.min_points", 1))
DETECTION_RETRY_COUNT = int(_get_cfg("detection.retry_count", 2))
DETECTION_RETRY_STEP = float(_get_cfg("detection.retry_threshold_step", 0.05))
CALIBRATION_AFFINE_PATH = str(_get_cfg("calibration.affine_matrix", "config/calibration_affine.json"))
CAMERA_MAIN_INDEX = int(_get_cfg("camera.main_index", 0))
CAMERA_PREVIEW_INDEX = int(_get_cfg("camera.preview_index", 1))
CNC_PORT = str(_get_cfg("cnc.port", "/dev/ttyUSB0"))
CNC_BAUDRATE = int(_get_cfg("cnc.baudrate", 115200))
CNC_TIMEOUT = float(_get_cfg("cnc.timeout", 2.0))
WORKSPACE_MARGIN_MM = float(_get_cfg("workspace.margin_mm", 0.0))
RETRY_MOVE = int(_get_cfg("retry.move", 1))
RETRY_STATUS = int(_get_cfg("retry.status", 1))
RETRY_CAPTURE = int(_get_cfg("retry.capture", 1))
CALIBRATE_TIMEOUT_SEC = float(_get_cfg("calibration.timeout_sec", 45.0))
PERF_FAST_THRESHOLD = int(_get_cfg("performance.fast_point_threshold", 60))
PERF_SLOW_THRESHOLD = int(_get_cfg("performance.slow_point_threshold", 15))
PERF_FAST_MULT = float(_get_cfg("performance.fast_xy_multiplier", 1.2))
PERF_SLOW_MULT = float(_get_cfg("performance.slow_xy_multiplier", 0.9))

# File paths
TEMP_DIR = Path("temp")
JOB_OVERLAY_IMAGE_PATH = TEMP_DIR / "overlay.jpg"
CALIBRATE_IMAGE_PATH = TEMP_DIR / "overlay.jpg"
LAST_JOB_POINTS_PATH = Path(_get_cfg("output.last_job_points", "config/last_job_points.json"))
WORK_POINTS_PATH = Path(_get_cfg("output.work_points", "config/work_points.json"))
CALIB_OFFSET_PATH = Path(_get_cfg("calibration.runtime_offset", "config/calibration_runtime_offset.json"))
CAL_OFFSET_PATH = Path(_get_cfg("calibration.cal_offset", "config/cal_offset.json"))
CALIB_OFFSET_X = 0.0
CALIB_OFFSET_Y = 0.0
DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "templates" / "dashboard.html"
JOB_LOGS_DIR = Path("logs/jobs")

# Expose temp artifacts (e.g., overlay.jpg) for dashboard preview cards.
TEMP_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/temp", StaticFiles(directory=str(TEMP_DIR)), name="temp")


def _is_valid_camera_index(camera_index: int) -> bool:
    return 0 <= int(camera_index) <= 9


def _set_error(code: str, message: str) -> None:
    system_state["error_code"] = code
    system_state["last_error"] = message
    _log_job_event("error", code=code, message=message)


def _log_job_event(event: str, **fields: Any) -> None:
    payload = {"event": event, "job_id": current_job_id}
    payload.update(fields)
    logger.info(json.dumps(payload, sort_keys=True, default=str))
    _append_job_telemetry(event, **fields)


def _validate_startup_config() -> List[str]:
    warnings: List[str] = []
    if not Path(DETECTION_MODEL_PATH).exists():
        warnings.append(f"Detection model file not found: {DETECTION_MODEL_PATH}")
    if not Path(CALIBRATION_AFFINE_PATH).exists():
        warnings.append(f"Calibration file not found: {CALIBRATION_AFFINE_PATH}")
    if not _is_valid_camera_index(CAMERA_MAIN_INDEX):
        warnings.append(f"Invalid main camera index in config: {CAMERA_MAIN_INDEX}")
    if not _is_valid_camera_index(CAMERA_PREVIEW_INDEX):
        warnings.append(f"Invalid preview camera index in config: {CAMERA_PREVIEW_INDEX}")
    if CAMERA_MAIN_INDEX == CAMERA_PREVIEW_INDEX:
        warnings.append("Main and preview camera index should be different")
    if not CNC_PORT:
        warnings.append("CNC port empty in config")
    if CNC_BAUDRATE <= 0:
        warnings.append(f"Invalid CNC baudrate in config: {CNC_BAUDRATE}")
    if CNC_TIMEOUT <= 0:
        warnings.append(f"Invalid CNC timeout in config: {CNC_TIMEOUT}")
    return warnings


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _job_log_path(job_id: str) -> Path:
    return JOB_LOGS_DIR / f"{job_id}.json"


def _init_job_telemetry(job_id: str) -> None:
    try:
        JOB_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "job_id": job_id,
            "started_at": _now_iso(),
            "status": "running",
            "events": [],
        }
        _job_log_path(job_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to initialize job telemetry: {e}")


def _append_job_telemetry(event: str, **fields: Any) -> None:
    if not current_job_id:
        return

    path = _job_log_path(current_job_id)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = {
                "job_id": current_job_id,
                "started_at": _now_iso(),
                "status": "running",
                "events": [],
            }

        data.setdefault("events", []).append({
            "ts": _now_iso(),
            "event": event,
            **fields,
        })

        if event in {"job_complete"}:
            data["status"] = "complete"
            data["ended_at"] = _now_iso()
        elif event in {"job_failed", "job_aborted", "job_stopped"}:
            data["status"] = "failed" if event == "job_failed" else event.replace("job_", "")
            data["ended_at"] = _now_iso()

        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to append job telemetry: {e}")


def _set_last_job_summary(status: str, points: int = 0, metrics: Optional[Dict[str, float]] = None) -> None:
    system_state["last_job_summary"] = {
        "job_id": current_job_id,
        "status": status,
        "points": int(points),
        "total_ms": (metrics or {}).get("total_ms"),
        "drill_ms": (metrics or {}).get("drill_loop_ms"),
        "error_code": system_state.get("error_code"),
        "last_error": system_state.get("last_error"),
        "last_warning": system_state.get("last_warning"),
        "ts": _now_iso(),
    }


def _summarize_metrics(date_utc: Optional[str] = None) -> Dict[str, Any]:
    JOB_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(JOB_LOGS_DIR.glob("*.json"))
    jobs: List[Dict[str, Any]] = []
    error_counter: Dict[str, int] = {}
    total_ms_values: List[float] = []
    drill_ms_values: List[float] = []

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        started_at = str(data.get("started_at", ""))
        if date_utc and not started_at.startswith(date_utc):
            continue

        status = str(data.get("status", "unknown"))
        events = data.get("events", [])
        points = 0
        metrics_evt = None

        for evt in events:
            if evt.get("event") == "point_drilled":
                points = max(points, int(evt.get("point", 0)))
            if evt.get("event") == "metrics":
                metrics_evt = evt
            if evt.get("event") == "error":
                code = str(evt.get("code", "UNKNOWN"))
                error_counter[code] = error_counter.get(code, 0) + 1

        if metrics_evt:
            if isinstance(metrics_evt.get("total_ms"), (int, float)):
                total_ms_values.append(float(metrics_evt["total_ms"]))
            if isinstance(metrics_evt.get("drill_loop_ms"), (int, float)):
                drill_ms_values.append(float(metrics_evt["drill_loop_ms"]))

        jobs.append({
            "job_id": data.get("job_id"),
            "status": status,
            "started_at": started_at,
            "ended_at": data.get("ended_at"),
            "points": points,
            "metrics": metrics_evt or {},
        })

    jobs_sorted = sorted(jobs, key=lambda j: str(j.get("started_at", "")), reverse=True)
    total_jobs = len(jobs_sorted)
    completed = sum(1 for j in jobs_sorted if j.get("status") == "complete")
    failed = sum(1 for j in jobs_sorted if j.get("status") in {"failed", "aborted", "stopped"})
    success_rate = (completed / total_jobs * 100.0) if total_jobs > 0 else 0.0

    def _avg(vals: List[float]) -> Optional[float]:
        return round(sum(vals) / len(vals), 2) if vals else None

    top_errors = sorted(
        [{"code": code, "count": count} for code, count in error_counter.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]

    return {
        "date_utc": date_utc or datetime.now(UTC).date().isoformat(),
        "total_jobs": total_jobs,
        "completed_jobs": completed,
        "failed_jobs": failed,
        "success_rate_pct": round(success_rate, 2),
        "avg_total_ms": _avg(total_ms_values),
        "avg_drill_ms": _avg(drill_ms_values),
        "top_errors": top_errors,
        "recent_jobs": jobs_sorted[:10],
    }


def _resolve_workspace_bounds() -> Optional[Dict[str, Tuple[float, float]]]:
    workspace_cfg = _get_cfg("workspace", {})
    if isinstance(workspace_cfg, dict):
        try:
            x_min = workspace_cfg.get("x_min")
            x_max = workspace_cfg.get("x_max")
            y_min = workspace_cfg.get("y_min")
            y_max = workspace_cfg.get("y_max")
            if None not in (x_min, x_max, y_min, y_max):
                return {
                    "x": (float(x_min), float(x_max)),
                    "y": (float(y_min), float(y_max)),
                }
        except Exception:
            pass

    if transformer is not None and getattr(transformer, "workspace_bounds", None):
        bounds = transformer.workspace_bounds
        if isinstance(bounds, dict) and "x" in bounds and "y" in bounds:
            return {
                "x": (float(bounds["x"][0]), float(bounds["x"][1])),
                "y": (float(bounds["y"][0]), float(bounds["y"][1])),
            }

    return None


def _apply_soft_limit_xy(x: float, y: float) -> Tuple[float, float, bool]:
    bounds = _resolve_workspace_bounds()
    if not bounds:
        return float(x), float(y), False

    x_min, x_max = bounds["x"]
    y_min, y_max = bounds["y"]
    x_clipped = max(x_min + WORKSPACE_MARGIN_MM, min(float(x), x_max - WORKSPACE_MARGIN_MM))
    y_clipped = max(y_min + WORKSPACE_MARGIN_MM, min(float(y), y_max - WORKSPACE_MARGIN_MM))
    clipped = abs(x_clipped - float(x)) > 1e-9 or abs(y_clipped - float(y)) > 1e-9
    return x_clipped, y_clipped, clipped


async def _retry_async(fn, retries: int, op_name: str, *args):
    last_exc = None
    for attempt in range(retries + 1):
        try:
            result = await asyncio.to_thread(fn, *args)
            is_success = False
            if result is None:
                is_success = False
            elif isinstance(result, bool):
                is_success = result
            elif isinstance(result, np.ndarray):
                is_success = result.size > 0
            else:
                try:
                    is_success = bool(result)
                except ValueError:
                    # Numpy-like objects with ambiguous truth value.
                    is_success = True
                except Exception:
                    is_success = True

            if is_success:
                if attempt > 0:
                    _log_job_event("retry_success", op=op_name, attempt=attempt + 1)
                return result
        except Exception as exc:
            last_exc = exc
        if attempt < retries:
            _log_job_event("retry", op=op_name, attempt=attempt + 1)
    if last_exc is not None:
        raise last_exc
    return None


def _dynamic_xy_feed(total_points: int) -> int:
    base = max(100, int(XY_MOVE_FEED))
    if total_points >= PERF_FAST_THRESHOLD:
        return max(100, int(base * PERF_FAST_MULT))
    if total_points <= PERF_SLOW_THRESHOLD:
        return max(100, int(base * PERF_SLOW_MULT))
    return base


def _record_metric(metrics: Dict[str, float], key: str, t_start: float) -> None:
    metrics[key] = round((perf_counter() - t_start) * 1000.0, 2)


def _load_runtime_offset():
    """Load runtime XY correction offset from disk."""
    global CALIB_OFFSET_X, CALIB_OFFSET_Y
    if not CALIB_OFFSET_PATH.exists():
        return
    try:
        with open(CALIB_OFFSET_PATH, "r") as f:
            data = json.load(f)
        CALIB_OFFSET_X = float(data.get("offset_x", 0.0))
        CALIB_OFFSET_Y = float(data.get("offset_y", 0.0))
    except Exception as e:
        logger.warning(f"Failed to load runtime offset: {e}")


def _save_runtime_offset():
    """Persist runtime XY correction offset to disk."""
    try:
        CALIB_OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CALIB_OFFSET_PATH, "w") as f:
            json.dump({"offset_x": CALIB_OFFSET_X, "offset_y": CALIB_OFFSET_Y}, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save runtime offset: {e}")


def _save_last_job_points():
    """Persist drill points to config/last_job_points.json."""
    try:
        LAST_JOB_POINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LAST_JOB_POINTS_PATH, "w") as f:
            json.dump({"points": pending_drill_points}, f, indent=2)
        logger.info(f"Saved {len(pending_drill_points)} drill points to {LAST_JOB_POINTS_PATH}")
    except Exception as e:
        logger.warning(f"Failed to save last job points: {e}")


def _calculate_work_points(extra_offset_x: float = 0.0, extra_offset_y: float = 0.0, extra_offset_z: float = 0.0):
    """Calculate work_points = last_job_points + cal_offset + optional jog offsets."""
    try:
        # Load last_job_points
        if not LAST_JOB_POINTS_PATH.exists():
            logger.warning("last_job_points.json not found")
            return False
        
        with open(LAST_JOB_POINTS_PATH, "r") as f:
            data = json.load(f)
            last_points = data.get("points", [])
        
        # Load cal_offset
        cal_offset_x = 0.0
        cal_offset_y = 0.0
        cal_offset_z = None  # Z reference from calibrate (if exists)
        if CAL_OFFSET_PATH.exists():
            with open(CAL_OFFSET_PATH, "r") as f:
                cal_data = json.load(f)
                cal_offset_x = float(cal_data.get("x", 0.0))
                cal_offset_y = float(cal_data.get("y", 0.0))
                cal_offset_z = cal_data.get("z")  # May be None if not set
        
        total_offset_x = cal_offset_x + float(extra_offset_x)
        total_offset_y = cal_offset_y + float(extra_offset_y)
        total_offset_z = (cal_offset_z if cal_offset_z is not None else None)
        if total_offset_z is not None:
            total_offset_z = float(total_offset_z) + float(extra_offset_z)

        # Calculate work_points
        work_points = [
            (px + total_offset_x, py + total_offset_y)
            for px, py in last_points
        ]
        
        # Save to work_points.json
        WORK_POINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(WORK_POINTS_PATH, "w") as f:
            json.dump({
                "points": work_points,
                "cal_offset": {"x": total_offset_x, "y": total_offset_y, "z": total_offset_z}
            }, f, indent=2)
        
        logger.info(
            "Calculated work_points with offsets: "
            f"cal(X={cal_offset_x:.3f},Y={cal_offset_y:.3f},Z={cal_offset_z}) "
            f"jog(X={extra_offset_x:.3f},Y={extra_offset_y:.3f},Z={extra_offset_z:.3f})"
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to calculate work points: {e}")
        return False


def _save_work_points() -> bool:
    """Backwards-compatible helper used by paused jog flow."""
    return _calculate_work_points(
        jog_offset.get("x", 0.0),
        jog_offset.get("y", 0.0),
        jog_offset.get("z", 0.0),
    )


def _apply_runtime_offset(x: float, y: float) -> Tuple[float, float]:
    """Apply runtime correction offset to machine coordinates."""
    return float(x + CALIB_OFFSET_X), float(y + CALIB_OFFSET_Y)


def _apply_runtime_offset_points(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Apply runtime correction offset to list of machine coordinates."""
    return [_apply_runtime_offset(x, y) for x, y in points]


def _get_calibrated_z_reference() -> Optional[float]:
    """Load calibrated Z reference from cal_offset.json when available."""
    try:
        if not CAL_OFFSET_PATH.exists():
            return None
        with open(CAL_OFFSET_PATH, "r") as f:
            data = json.load(f)
        z_val = data.get("z")
        return float(z_val) if z_val is not None else None
    except Exception:
        return None


def connect_camera_sync(camera_index: int) -> bool:
    """Reconnect camera using user-selected index."""
    global camera
    try:
        if not _is_valid_camera_index(camera_index):
            logger.warning(f"Invalid camera index requested: {camera_index}")
            return False
        from src.vision.camera import CameraCapture

        if camera is not None:
            try:
                camera.disconnect()
            except Exception:
                pass

        cam = CameraCapture(camera_index=camera_index)
        if not cam.connect():
            return False
        cam.start_streaming()
        camera = cam
        system_state["camera_index"] = camera_index
        system_state["camera_connected"] = True
        return True
    except Exception as e:
        logger.warning(f"Camera connect failed on index {camera_index}: {e}")
        system_state["camera_connected"] = False
        return False


def connect_preview_camera_sync(camera_index: int) -> bool:
    """Reconnect preview-only camera using user-selected index."""
    global preview_camera
    try:
        if not _is_valid_camera_index(camera_index):
            logger.warning(f"Invalid preview camera index requested: {camera_index}")
            return False
        from src.vision.camera import CameraCapture

        if preview_camera is not None:
            try:
                preview_camera.disconnect()
            except Exception:
                pass

        cam = CameraCapture(camera_index=camera_index)
        if not cam.connect():
            return False
        cam.start_streaming()
        preview_camera = cam
        system_state["preview_camera_index"] = camera_index
        system_state["preview_camera_connected"] = True
        return True
    except Exception as e:
        logger.warning(f"Preview camera connect failed on index {camera_index}: {e}")
        system_state["preview_camera_connected"] = False
        return False


def manual_jog_sync(dx: float, dy: float, dz: float, feedrate: int = 600) -> bool:
    """Manual jog using GRBL signed relative moves."""
    if not (cnc_controller and cnc_controller.is_connected):
        return False

    if abs(dx) < 1e-9 and abs(dy) < 1e-9 and abs(dz) < 1e-9:
        return False

    status_before = cnc_controller.query_status_once(1.0)
    pos_before = status_before.get("position", {})
    current_x = float(pos_before.get("x", 0.0))
    current_y = float(pos_before.get("y", 0.0))
    current_z = float(pos_before.get("z", 0.0))

    ok = cnc_controller.jog_relative(dx, dy, dz, feedrate, True, 30.0)
    if ok:
        latest = cnc_controller.query_status_once(1.0)
        p = latest.get("position", {})
        system_state["position"] = {
            "x": float(p.get("x", current_x + dx)),
            "y": float(p.get("y", current_y + dy)),
            "z": float(p.get("z", current_z + dz)),
        }
    return ok


def move_to_standby_sync() -> bool:
    """Move CNC to standby coordinate using calibrated Z reference when available."""
    if not (cnc_controller and cnc_controller.is_connected):
        return False

    z_ref = _get_calibrated_z_reference()
    ok_up = True
    if z_ref is not None:
        ok_up = cnc_controller.move_to(None, None, z_ref, Z_MOVE_FEED, True, 30.0)
    ok_xy = cnc_controller.move_to(STANDBY_X, STANDBY_Y, None, XY_MOVE_FEED, True, 30.0)
    if not (ok_up and ok_xy):
        return False

    status_now = cnc_controller.query_status_once(1.0)
    pos = status_now.get("position", {})
    system_state["position"] = {
        "x": STANDBY_X,
        "y": STANDBY_Y,
        "z": float(pos.get("z", z_ref if z_ref is not None else 0.0)),
    }
    return True


def run_preflight_checks_sync() -> Dict[str, Any]:
    checks: Dict[str, Any] = {
        "ts": _now_iso(),
        "config_warnings": _validate_startup_config(),
        "model_exists": Path(DETECTION_MODEL_PATH).exists(),
        "calibration_exists": Path(CALIBRATION_AFFINE_PATH).exists(),
        "transformer_ready": bool(transformer and transformer.is_calibrated),
        "cnc_connected": bool(cnc_controller and cnc_controller.is_connected),
        "camera_connected": bool(camera is not None),
        "camera_frame_ok": False,
        "workspace_bounds": _resolve_workspace_bounds(),
    }

    if camera is not None:
        try:
            frame = camera.get_frame()
            checks["camera_frame_ok"] = frame is not None
        except Exception:
            checks["camera_frame_ok"] = False

    checks["ok"] = (
        checks["model_exists"]
        and checks["calibration_exists"]
        and checks["transformer_ready"]
        and checks["cnc_connected"]
        and checks["camera_connected"]
        and checks["camera_frame_ok"]
        and len(checks["config_warnings"]) == 0
    )
    return checks


def _save_job_overlay_image(
    frame: np.ndarray,
    detections: List[Any],
    highlighted_detection_index: Optional[int] = None,
) -> str:
    """Save single detection overlay image to temp/overlay.jpg (overwrite)."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    vis = frame.copy()
    for idx, det in enumerate(detections):
        x1, y1, x2, y2 = map(int, det.bbox)
        label = f"{det.class_name} {det.confidence:.2f}"
        is_highlighted = highlighted_detection_index is not None and idx == highlighted_detection_index
        box_color = (0, 0, 255) if is_highlighted else (0, 255, 0)
        text_color = (0, 0, 255) if is_highlighted else (0, 255, 0)
        if is_highlighted:
            label = f"FIRST_PAD {label}"

        cv2.rectangle(vis, (x1, y1), (x2, y2), box_color, 2)
        cv2.circle(vis, ((x1 + x2) // 2, (y1 + y2) // 2), 4, (0, 0, 255), -1)
        cv2.putText(vis, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, text_color, 1)

    cv2.imwrite(str(JOB_OVERLAY_IMAGE_PATH), vis)
    return str(JOB_OVERLAY_IMAGE_PATH)


def _find_first_paused_detection_index(
    detections: List[Any],
    first_machine_xy: Tuple[float, float],
    min_confidence: float = DETECTION_CONFIDENCE_THRESHOLD,
) -> Optional[int]:
    """Find detection index closest to first paused machine point."""
    if not detections or transformer is None:
        return None

    target_x, target_y = first_machine_xy
    best_idx: Optional[int] = None
    best_dist = float("inf")

    for idx, det in enumerate(detections):
        conf = float(getattr(det, "confidence", 0.0))
        if conf < min_confidence:
            continue

        x1, y1, x2, y2 = det.bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0

        transformed = transformer.transform_point(cx, cy)
        if transformed is None:
            continue

        mx, my = transformed
        if not transformer.is_within_bounds(mx, my):
            mx, my = transformer.clip_to_bounds(mx, my)

        dist = (mx - target_x) ** 2 + (my - target_y) ** 2
        if dist < best_dist:
            best_dist = dist
            best_idx = idx

    return best_idx


def _select_single_calibration_detection(detections: List[Any]) -> Optional[Any]:
    """Select one pad-hole detection with highest confidence."""
    if not detections:
        return None

    pad_like = [
        d for d in detections
        if ("pad" in str(getattr(d, "class_name", "")).lower() or
            "hole" in str(getattr(d, "class_name", "")).lower())
    ]
    candidates = pad_like if pad_like else detections
    return max(candidates, key=lambda d: float(getattr(d, "confidence", 0.0)))


def _save_calibrate_image(frame: np.ndarray, det: Any, machine_xy: Tuple[float, float]) -> str:
    """Save calibration visual result to temp/calibrate.jpg."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    vis = frame.copy()
    x1, y1, x2, y2 = map(int, det.bbox)
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    label = f"CALIBRATE {det.class_name} {det.confidence:.2f} -> X{machine_xy[0]:.2f} Y{machine_xy[1]:.2f}"
    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
    cv2.circle(vis, (cx, cy), 6, (0, 0, 255), -1)
    cv2.putText(vis, label, (max(5, x1), max(20, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    cv2.imwrite(str(CALIBRATE_IMAGE_PATH), vis)
    return str(CALIBRATE_IMAGE_PATH)


async def run_calibrate_flow() -> bool:
    """Calibrate flow: standby -> capture/detect -> keep one -> save -> move CNC to target."""
    if not (camera and detector and transformer):
        system_state["status"] = "NOT_READY"
        _set_error("NOT_READY", "Camera/detector/transformer not ready")
        return False

    if not (cnc_controller and cnc_controller.is_connected):
        system_state["status"] = "NOT_READY"
        _set_error("NOT_READY", "CNC not connected")
        return False

    # 1) Move to standby first.
    ok_standby = await asyncio.to_thread(move_to_standby_sync)
    if not ok_standby:
        system_state["status"] = "ERROR"
        _set_error("MOTION_FAIL", "Failed to move standby before calibrate")
        return False

    # 2) Capture and detect.
    frame = await _retry_async(camera.get_frame, RETRY_CAPTURE, "calibrate_capture")
    if frame is None:
        system_state["status"] = "NO_FRAME"
        _set_error("NO_FRAME", "No camera frame for calibrate")
        return False

    detections = await _retry_async(detector.detect, RETRY_CAPTURE, "calibrate_detect", frame)
    if not detections:
        system_state["status"] = "NO_POINTS"
        _set_error("NO_POINTS", "No detection found for calibrate")
        return False

    # 3) Keep one pad-hole.
    selected = _select_single_calibration_detection(detections)
    if selected is None:
        system_state["status"] = "NO_POINTS"
        _set_error("NO_POINTS", "No valid padhole detection")
        return False

    x1, y1, x2, y2 = selected.bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    machine_coords = await asyncio.to_thread(
        transformer.transform_detections,
        [(cx, cy, float(selected.confidence))],
        0.0
    )
    if not machine_coords:
        system_state["status"] = "ERROR"
        _set_error("TRANSFORM_FAIL", "Transform failed for calibrate point")
        return False

    target_x, target_y = _apply_runtime_offset(machine_coords[0][0], machine_coords[0][1])
    target_x, target_y, clipped = _apply_soft_limit_xy(target_x, target_y)
    if clipped:
        system_state["last_warning"] = "Calibrate target clipped by workspace soft-limit"

    # 4) Save temp/calibrate.jpg.
    try:
        path = await asyncio.to_thread(_save_calibrate_image, frame, selected, (target_x, target_y))
        system_state["calibrate_image"] = path
    except Exception as e:
        logger.warning(f"Failed to save calibrate image: {e}")

    # 5) Move CNC to processed coordinate.
    z_ref = _get_calibrated_z_reference()
    ok_up = True
    if z_ref is not None:
        ok_up = await _retry_async(
            cnc_controller.move_to, RETRY_MOVE, "calibrate_move_z", None, None, z_ref, Z_MOVE_FEED, True, 30.0
        )
    ok_xy = await _retry_async(
        cnc_controller.move_to, RETRY_MOVE, "calibrate_move_xy", target_x, target_y, None, XY_MOVE_FEED, True, 30.0
    )
    if not (ok_up and ok_xy):
        system_state["status"] = "ERROR"
        _set_error("MOTION_FAIL", "CNC move failed for calibrate target")
        return False

    status_now = await _retry_async(cnc_controller.query_status_once, RETRY_STATUS, "calibrate_query_status", 1.0)
    pos = status_now.get("position", {})
    system_state["position"] = {
        "x": float(pos.get("x", target_x)),
        "y": float(pos.get("y", target_y)),
        "z": float(pos.get("z", z_ref if z_ref is not None else 0.0)),
    }
    system_state["status"] = "CALIBRATE_DONE"
    system_state["calibrate_target"] = {"x": float(target_x), "y": float(target_y)}
    system_state["last_error"] = None
    system_state["error_code"] = None
    return True

def init_components():
    """Initialize system components"""
    global camera, preview_camera, detector, cnc_controller, job_manager, executor, transformer

    _load_runtime_offset()
    system_state["calibrate_offset"] = {"x": CALIB_OFFSET_X, "y": CALIB_OFFSET_Y}
    startup_warnings = _validate_startup_config()
    system_state["startup_warnings"] = startup_warnings
    for warning in startup_warnings:
        logger.warning(f"Startup validation warning: {warning}")
    
    try:
        from src.vision.transformer import AffineTransformer
        transformer = AffineTransformer(CALIBRATION_AFFINE_PATH)
        transformer.load_calibration()
        
    except Exception as e:
        logger.warning(f"Transformer init warning: {e}")
        transformer = None
    
    try:
        from src.cnc.controller import GRBLController
        cnc_controller = GRBLController(
            port=CNC_PORT,
            baudrate=CNC_BAUDRATE,
            timeout=CNC_TIMEOUT,
        )
        if cnc_controller.connect():
            system_state["connected"] = True
            logger.info("CNC connected successfully")
        else:
            system_state["connected"] = False
            logger.warning("CNC connection failed")
    except Exception as e:
        logger.warning(f"Controller init warning: {e}")
        cnc_controller = None
        system_state["connected"] = False
    
    try:
        from src.cnc.job_manager import DrillJobManager, ExecutionController
        job_manager = DrillJobManager()
        executor = ExecutionController(cnc_controller or type('MockCNC', (), {}), job_manager)
    except Exception as e:
        logger.warning(f"Job manager init warning: {e}")
        job_manager = None
        executor = None
    
    try:
        from src.vision.camera import CameraCapture
        camera = CameraCapture(camera_index=CAMERA_MAIN_INDEX)
        camera.connect()
        camera.start_streaming()
        system_state["camera_index"] = CAMERA_MAIN_INDEX
        system_state["camera_connected"] = True
        logger.info("Camera streaming started")
    except Exception as e:
        logger.warning(f"Camera init warning: {e}")
        camera = None
        system_state["camera_connected"] = False

    # Preview-only camera starts disconnected by default; user can connect from UI.
    preview_camera = None
    system_state["preview_camera_connected"] = False
    system_state["preview_camera_index"] = CAMERA_PREVIEW_INDEX
    
    try:
        from src.vision.detector import YOLODetector
        detector = YOLODetector(
            model_path=DETECTION_MODEL_PATH,
            confidence_threshold=DETECTION_CONFIDENCE_THRESHOLD,
            iou_threshold=DETECTION_IOU_THRESHOLD,
        )
        # Lazy-load model on first detect() to keep startup responsive.
        logger.info("Detector initialized (lazy model load)")
    except Exception as e:
        logger.warning(f"Detector init warning: {e}")
        detector = None
    
    import time
    time.sleep(0.5)  # Wait for camera to be ready

    # Startup flow: home machine first.
    if cnc_controller and cnc_controller.is_connected:
        system_state["status"] = "HOMING"
        homed = cnc_controller.home_axis("XYZ", True, 120.0)
        if homed:
            status_now = cnc_controller.query_status_once(1.0)
            pos = status_now.get("position", {})
            system_state["position"] = {
                "x": float(pos.get("x", 0.0)),
                "y": float(pos.get("y", 0.0)),
                "z": float(pos.get("z", 0.0)),
            }
            system_state["status"] = "IDLE"
            system_state["last_error"] = None
            logger.info("Startup homing complete")
        else:
            system_state["status"] = "ERROR"
            system_state["last_error"] = "Startup homing failed"
            logger.error("Startup homing failed")

    system_state["preflight"] = run_preflight_checks_sync()
    logger.info("Components initialized (some may be None)")

init_components()

async def run_drill_workflow():
    """Run acquire-detect-transform and pause at first drill point."""
    global current_job_id
    try:
        t_total = perf_counter()
        metrics: Dict[str, float] = {}
        current_job_id = str(uuid4())[:8]
        system_state["job_id"] = current_job_id
        system_state["last_warning"] = None
        _init_job_telemetry(current_job_id)
        _log_job_event("job_started", phase="acquire")

        if not (camera and detector and transformer):
            system_state["status"] = "NOT_READY"
            _set_error("NOT_READY", "Camera/detector/transformer not ready")
            _log_job_event("job_aborted", reason="components_not_ready")
            await broadcast_state()
            return

        # START click #2 begins acquisition manually after standby-ready phase.
        system_state["start_state"] = "capturing"

        system_state["status"] = "ACQUIRING"
        await broadcast_state()

        t = perf_counter()
        frame = await _retry_async(camera.get_frame, RETRY_CAPTURE, "camera_capture")
        _record_metric(metrics, "capture_ms", t)
        if frame is None:
            system_state["status"] = "NO_FRAME"
            _set_error("NO_FRAME", "Camera frame unavailable")
            _log_job_event("job_aborted", reason="no_frame")
            await broadcast_state()
            return

        t = perf_counter()
        detections = await asyncio.to_thread(detector.detect, frame)
        _record_metric(metrics, "detect_ms", t)
        system_state["last_detections"] = len(detections)
        _log_job_event("detections_done", count=len(detections))

        pixel_points = [
            ((d.bbox[0] + d.bbox[2]) / 2, (d.bbox[1] + d.bbox[3]) / 2, d.confidence)
            for d in detections
        ]
        t = perf_counter()
        machine_coords = []
        threshold_used = DETECTION_CONFIDENCE_THRESHOLD
        for i in range(DETECTION_RETRY_COUNT + 1):
            threshold_now = max(0.05, DETECTION_CONFIDENCE_THRESHOLD - (i * DETECTION_RETRY_STEP))
            transformed = await asyncio.to_thread(
                transformer.transform_detections, pixel_points, threshold_now
            )
            if len(transformed) >= DETECTION_MIN_POINTS:
                machine_coords = transformed
                threshold_used = threshold_now
                break
        system_state["detection_threshold_used"] = round(threshold_used, 3)
        _record_metric(metrics, "transform_ms", t)
        _log_job_event("transform_done", points=len(machine_coords), threshold=round(threshold_used, 3))
        machine_coords = _apply_runtime_offset_points(machine_coords)

        if not (machine_coords and job_manager):
            system_state["status"] = "NO_POINTS"
            _set_error("NO_POINTS", "No points after detection+transform")
            _log_job_event("job_aborted", reason="no_points_after_transform")
            await broadcast_state()
            return

        t = perf_counter()
        job = await asyncio.to_thread(job_manager.create_job, machine_coords, True)
        _record_metric(metrics, "path_plan_ms", t)
        system_state["progress"] = {"current": 0, "total": len(job.points)}
        system_state["status"] = "TRANSFORM"
        await broadcast_state()

        if not (cnc_controller and cnc_controller.is_connected):
            system_state["status"] = "SIMULATE"
            system_state["start_state"] = "idle"
            _set_error("NOT_READY", "CNC not connected")
            _log_job_event("job_aborted", reason="cnc_not_connected")
            await broadcast_state()
            return

        if stop_event.is_set():
            system_state["status"] = "STOPPED"
            system_state["start_state"] = "idle"
            _set_error("STOPPED", "Workflow stopped before drill")
            _log_job_event("job_stopped", phase="before_drill")
            await broadcast_state()
            return

        # START click #2: capture → calculate work_points → drill directly
        global pending_drill_points
        pending_drill_points = [(float(p.x), float(p.y)) for p in job.points]
        _save_last_job_points()
        
        # Calculate work_points = last_job_points + cal_offset
        ok_calc = await asyncio.to_thread(_calculate_work_points)
        if not ok_calc:
            system_state["status"] = "ERROR"
            _set_error("WORKPOINTS_FAIL", "Failed to calculate work points")
            _log_job_event("job_failed", reason="calculate_work_points_failed")
            await broadcast_state()
            return
        
        # Load work_points from file
        with open(WORK_POINTS_PATH, "r") as f:
            work_data = json.load(f)
            drill_points = work_data.get("points", [])
            ref_z_val = work_data.get("cal_offset", {}).get("z")
            ref_z = float(ref_z_val) if ref_z_val is not None else None
            ref_z_val = work_data.get("cal_offset", {}).get("z")
            ref_z = float(ref_z_val) if ref_z_val is not None else None
        
        if not drill_points:
            system_state["status"] = "NO_POINTS"
            _set_error("NO_POINTS", "Work points empty")
            _log_job_event("job_aborted", reason="work_points_empty")
            await broadcast_state()
            return

        clipped_count = 0
        safe_points: List[Tuple[float, float]] = []
        for raw_x, raw_y in drill_points:
            sx, sy, clipped = _apply_soft_limit_xy(float(raw_x), float(raw_y))
            if clipped:
                clipped_count += 1
            safe_points.append((sx, sy))
        drill_points = safe_points
        if clipped_count > 0:
            system_state["last_warning"] = f"{clipped_count} point(s) clipped by workspace soft-limit"
            _log_job_event("soft_limit_clipped", points=clipped_count)
        
        # Save overlay image
        try:
            first_x, first_y = drill_points[0]
            highlighted_idx = await asyncio.to_thread(
                _find_first_paused_detection_index,
                detections,
                (first_x, first_y),
                DETECTION_CONFIDENCE_THRESHOLD,
            )
            overlay_path = await asyncio.to_thread(
                _save_job_overlay_image,
                frame,
                detections,
                highlighted_idx,
            )
            system_state["last_capture_image"] = overlay_path
            system_state["last_detection_image"] = overlay_path
            logger.info(f"Saved job overlay image: {overlay_path}")
        except Exception as e:
            logger.warning(f"Failed to save job overlay image: {e}")

        # Directly drill without pause
        system_state["status"] = "DRILLING"
        system_state["start_state"] = "drilling"
        system_state["progress"] = {"current": 0, "total": len(drill_points)}
        await broadcast_state()

        job = await asyncio.to_thread(job_manager.create_job, drill_points, False)
        dynamic_xy_feed = _dynamic_xy_feed(len(job.points))

        # ===== EXECUTE DRILLING =====
        t_drill = perf_counter()
        for i, point in enumerate(job.points):
            if stop_event.is_set():
                system_state["status"] = "STOPPED"
                system_state["start_state"] = "idle"
                pending_drill_points = []
                _set_error("STOPPED", f"Stopped at point {i + 1}")
                _log_job_event("job_stopped", phase="drilling", point=i + 1)
                await broadcast_state()
                return

            # Z: relative drill from calibrated Z reference when available.
            z_clear = _get_cfg("drill.z_clearance", 5.0)
            z_drill_depth = _get_cfg("drill.z_depth", 1.5)
            clearance_z = (float(ref_z) + z_clear) if ref_z is not None else z_clear
            
            # Move to XY first at clearance height
            safe_x, safe_y, clipped = _apply_soft_limit_xy(point.x, point.y)
            if clipped:
                system_state["last_warning"] = f"Point {i + 1} clipped by workspace soft-limit"
                _log_job_event("soft_limit_clipped", point=i + 1)
            ok_xy = await _retry_async(
                cnc_controller.move_to, RETRY_MOVE, "move_xy", safe_x, safe_y, clearance_z, dynamic_xy_feed, True, 30.0
            )
            
            # Get current Z or use calibrated reference
            status_z = await _retry_async(cnc_controller.query_status_once, RETRY_STATUS, "query_status", 1.0)
            current_z = float(status_z.get("position", {}).get("z", 0.0))
            
            if ref_z is not None:
                target_z = float(ref_z) - z_drill_depth  # Drill from calibrated Z reference
            else:
                target_z = current_z - z_drill_depth
            
            # Drill down
            ok_down = await _retry_async(
                cnc_controller.move_to, RETRY_MOVE, "move_z_down", None, None, target_z, Z_DRILL_FEED, True, 30.0
            )
            # Move back up to clearance
            ok_up = await _retry_async(
                cnc_controller.move_to, RETRY_MOVE, "move_z_up", None, None, clearance_z, Z_MOVE_FEED, True, 30.0
            )

            if not (ok_xy and ok_down and ok_up):
                system_state["status"] = "ERROR"
                _set_error("MOTION_FAIL", f"Motion failed at point {i + 1}")
                _log_job_event("job_failed", reason="motion_failed", point=i + 1)
                await broadcast_state()
                return

            job.mark_drilled(i)
            _log_job_event("point_drilled", point=i + 1, total=len(job.points))
            system_state["progress"] = {"current": i + 1, "total": len(job.points)}
            await broadcast_state()

        _record_metric(metrics, "drill_loop_ms", t_drill)
        system_state["status"] = "COMPLETE"
        _log_job_event("job_complete", drilled=len(job.points))
        await broadcast_state()

        # End flow: return machine to STANDBY position
        system_state["status"] = "STANDBY"
        await broadcast_state()
        
        ok_standby = await asyncio.to_thread(move_to_standby_sync)
        if ok_standby:
            system_state["status"] = "IDLE"
            system_state["last_error"] = None
            system_state["error_code"] = None
        else:
            system_state["status"] = "ERROR"
            _set_error("MOTION_FAIL", "Failed to move to standby after drill")
        system_state["start_state"] = "idle"
        pending_drill_points = []
        _record_metric(metrics, "total_ms", t_total)
        system_state["last_metrics"] = metrics
        _log_job_event("metrics", **metrics)
        _set_last_job_summary("complete", points=len(job.points), metrics=metrics)
        await broadcast_state()

    except Exception as e:
        logger.exception("Workflow failed")
        system_state["status"] = "ERROR"
        _set_error("SYSTEM_ERROR", str(e))
        system_state["start_state"] = "idle"
        _log_job_event("job_failed", reason="exception", detail=str(e))
        _set_last_job_summary("failed")
        await broadcast_state()


async def continue_drill_workflow():
    """Drill using work_points from file (last_job_points + cal_offset)."""
    global pending_drill_points, current_job_id
    try:
        t_total = perf_counter()
        metrics: Dict[str, float] = {}
        if not current_job_id:
            current_job_id = str(uuid4())[:8]
            system_state["job_id"] = current_job_id
            _init_job_telemetry(current_job_id)
        _log_job_event("continue_drill_started")

        if not pending_drill_points:
            system_state["status"] = "NO_POINTS"
            _set_error("NO_POINTS", "No pending drill points")
            _log_job_event("job_aborted", reason="no_pending_points")
            await broadcast_state()
            return

        if not (cnc_controller and cnc_controller.is_connected):
            system_state["status"] = "NOT_READY"
            _set_error("NOT_READY", "CNC not connected")
            await broadcast_state()
            return

        if not job_manager:
            system_state["status"] = "NOT_READY"
            _set_error("NOT_READY", "Job manager not ready")
            await broadcast_state()
            return

        # Load work_points from file
        if not WORK_POINTS_PATH.exists():
            system_state["status"] = "NO_POINTS"
            _set_error("NO_POINTS", "No work points file")
            await broadcast_state()
            return

        with open(WORK_POINTS_PATH, "r") as f:
            work_data = json.load(f)
            drill_points = work_data.get("points", [])

        if not drill_points:
            system_state["status"] = "NO_POINTS"
            _set_error("NO_POINTS", "Work points empty")
            _log_job_event("job_aborted", reason="work_points_empty")
            await broadcast_state()
            return

        clipped_count = 0
        safe_points: List[Tuple[float, float]] = []
        for raw_x, raw_y in drill_points:
            sx, sy, clipped = _apply_soft_limit_xy(float(raw_x), float(raw_y))
            if clipped:
                clipped_count += 1
            safe_points.append((sx, sy))
        drill_points = safe_points
        if clipped_count > 0:
            system_state["last_warning"] = f"{clipped_count} point(s) clipped by workspace soft-limit"
            _log_job_event("soft_limit_clipped", points=clipped_count)

        t = perf_counter()
        job = await asyncio.to_thread(job_manager.create_job, drill_points, False)
        _record_metric(metrics, "path_plan_ms", t)
        dynamic_xy_feed = _dynamic_xy_feed(len(job.points))
        system_state["progress"] = {"current": 0, "total": len(job.points)}
        system_state["status"] = "DRILLING"
        system_state["start_state"] = "drilling"
        await broadcast_state()

        t_drill = perf_counter()
        for i, point in enumerate(job.points):
            if stop_event.is_set():
                system_state["status"] = "STOPPED"
                system_state["start_state"] = "idle"
                pending_drill_points = []
                jog_offset["x"] = 0.0
                jog_offset["y"] = 0.0
                jog_offset["z"] = 0.0
                _set_error("STOPPED", f"Stopped at point {i + 1}")
                _log_job_event("job_stopped", phase="continue_drill", point=i + 1)
                await broadcast_state()
                return

            # Z: relative drill from calibrated Z reference when available.
            z_clear = _get_cfg("drill.z_clearance", 5.0)
            z_drill_depth = _get_cfg("drill.z_depth", 1.5)
            clearance_z = (float(ref_z) + z_clear) if ref_z is not None else z_clear
            
            # Move to XY first at clearance height
            safe_x, safe_y, clipped = _apply_soft_limit_xy(point.x, point.y)
            if clipped:
                system_state["last_warning"] = f"Point {i + 1} clipped by workspace soft-limit"
                _log_job_event("soft_limit_clipped", point=i + 1)
            ok_xy = await _retry_async(
                cnc_controller.move_to, RETRY_MOVE, "continue_move_xy", safe_x, safe_y, clearance_z, dynamic_xy_feed, True, 30.0
            )
            
            # Get current Z and calculate drill depth relative to reference/current.
            status_z = await _retry_async(cnc_controller.query_status_once, RETRY_STATUS, "continue_query_status", 1.0)
            current_z = float(status_z.get("position", {}).get("z", 0.0))
            target_z = (float(ref_z) - z_drill_depth) if ref_z is not None else (current_z - z_drill_depth)
            
            # Drill down relative
            ok_down = await _retry_async(
                cnc_controller.move_to, RETRY_MOVE, "continue_move_z_down", None, None, target_z, Z_DRILL_FEED, True, 30.0
            )
            # Move back up to clearance
            ok_up = await _retry_async(
                cnc_controller.move_to, RETRY_MOVE, "continue_move_z_up", None, None, clearance_z, Z_MOVE_FEED, True, 30.0
            )

            if not (ok_xy and ok_down and ok_up):
                system_state["status"] = "ERROR"
                _set_error("MOTION_FAIL", f"Motion failed at point {i + 1}")
                _log_job_event("job_failed", reason="motion_failed", point=i + 1)
                await broadcast_state()
                return

            job.mark_drilled(i)
            _log_job_event("point_drilled", point=i + 1, total=len(job.points))
            system_state["progress"] = {"current": i + 1, "total": len(job.points)}
            await broadcast_state()

        _record_metric(metrics, "drill_loop_ms", t_drill)
        system_state["status"] = "COMPLETE"
        _log_job_event("job_complete", drilled=len(job.points))
        await broadcast_state()

        # End flow: return machine to STANDBY position
        system_state["status"] = "STANDBY"
        await broadcast_state()
        
        ok_standby = await asyncio.to_thread(move_to_standby_sync)
        if ok_standby:
            system_state["status"] = "IDLE"
            system_state["last_error"] = None
            system_state["error_code"] = None
        else:
            system_state["status"] = "ERROR"
            _set_error("MOTION_FAIL", "Failed to move to standby after drill")
        system_state["start_state"] = "idle"
        pending_drill_points = []
        _record_metric(metrics, "total_ms", t_total)
        system_state["last_metrics"] = metrics
        _log_job_event("metrics", **metrics)
        _set_last_job_summary("complete", points=len(job.points), metrics=metrics)
        await broadcast_state()

    except Exception as e:
        logger.exception("Workflow failed")
        system_state["status"] = "ERROR"
        _set_error("SYSTEM_ERROR", str(e))
        system_state["start_state"] = "idle"
        pending_drill_points = []
        _log_job_event("job_failed", reason="exception", detail=str(e))
        _set_last_job_summary("failed")
        await broadcast_state()

# ==================== WebSocket ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates"""
    global workflow_task, CALIB_OFFSET_X, CALIB_OFFSET_Y, pending_drill_points
    await websocket.accept()
    connected_clients.append(websocket)
    
    try:
        # Send initial state
        await websocket.send_json(system_state)
        
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Handle commands from UI
            cmd = message.get("command")
            
            if cmd == "start":
                if workflow_task and not workflow_task.done():
                    system_state["status"] = "BUSY"
                    await broadcast_state()
                    continue

                stop_event.clear()
                start_state = system_state.get("start_state")

                if start_state == "standby_ready":
                    workflow_task = asyncio.create_task(run_drill_workflow())
                elif start_state == "idle" or not start_state:
                    if cnc_controller and cnc_controller.is_connected:
                        system_state["status"] = "STANDBY_MOVING"
                        await broadcast_state()

                        ok_standby = await asyncio.to_thread(move_to_standby_sync)
                        if ok_standby:
                            system_state["status"] = "STANDBY_READY"
                            system_state["start_state"] = "standby_ready"
                            system_state["last_error"] = None
                        else:
                            system_state["status"] = "ERROR"
                            system_state["start_state"] = "idle"
                            system_state["last_error"] = "Failed to move standby before capture"
                    else:
                        # In simulation mode there is no standby move; allow immediate capture stage.
                        system_state["status"] = "STANDBY_READY"
                        system_state["start_state"] = "standby_ready"
                        system_state["last_error"] = None
                await broadcast_state()
                
            elif cmd == "stop":
                # Stop execution
                stop_event.set()
                if cnc_controller:
                    await asyncio.to_thread(cnc_controller.emergency_stop)
                system_state["status"] = "STOPPED"
                system_state["start_state"] = "idle"
                pending_drill_points = []
                await broadcast_state()
                
            elif cmd == "pause":
                system_state["status"] = "PAUSED"
                await broadcast_state()

            elif cmd == "preflight":
                system_state["status"] = "PRECHECK_RUNNING"
                await broadcast_state()
                checks = await asyncio.to_thread(run_preflight_checks_sync)
                system_state["preflight"] = checks
                system_state["status"] = "PRECHECK_OK" if checks.get("ok") else "PRECHECK_FAIL"
                if checks.get("ok"):
                    system_state["last_error"] = None
                else:
                    system_state["last_error"] = "Preflight failed. Check preflight details."
                await broadcast_state()

            elif cmd == "home":
                if cnc_controller and cnc_controller.is_connected:
                    system_state["status"] = "HOMING"
                    await broadcast_state()
                    homed = await asyncio.to_thread(cnc_controller.home_axis, "XYZ", True, 120.0)
                    system_state["status"] = "IDLE" if homed else "ERROR"
                    if not homed:
                        system_state["last_error"] = "Homing failed or timeout"
                else:
                    system_state["status"] = "NOT_READY"
                await broadcast_state()

            elif cmd == "standby":
                if cnc_controller and cnc_controller.is_connected:
                    system_state["status"] = "STANDBY_MOVING"
                    await broadcast_state()

                    ok_standby = await asyncio.to_thread(move_to_standby_sync)

                    if ok_standby:
                        system_state["status"] = "STANDBY"
                        system_state["last_error"] = None
                    else:
                        system_state["status"] = "ERROR"
                        system_state["last_error"] = "Standby move failed or timeout"
                else:
                    system_state["status"] = "NOT_READY"
                await broadcast_state()

            elif cmd == "reset":
                stop_event.set()
                if executor:
                    await asyncio.to_thread(executor.reset)
                pending_drill_points = []
                CALIB_OFFSET_X = 0.0
                CALIB_OFFSET_Y = 0.0
                jog_offset["x"] = 0.0
                jog_offset["y"] = 0.0
                jog_offset["z"] = 0.0
                system_state["calibrate_offset"] = {"x": 0.0, "y": 0.0}
                system_state["jog_offset"] = {"x": 0.0, "y": 0.0, "z": 0.0}
                system_state["status"] = "RESETTING"
                await broadcast_state()

                hardware_ok = True
                if cnc_controller and cnc_controller.is_connected:
                    hardware_ok = await asyncio.to_thread(
                        cnc_controller.recover_from_reset,
                        5.0,
                        False,
                        "XYZ"
                    )

                if hardware_ok:
                    if cnc_controller and cnc_controller.is_connected:
                        status = await asyncio.to_thread(cnc_controller.get_status)
                        position = status.get("position", {})
                        system_state["position"] = {
                            "x": float(position.get("x", 0.0)),
                            "y": float(position.get("y", 0.0)),
                            "z": float(position.get("z", 0.0)),
                        }
                    system_state["status"] = "IDLE"
                    system_state["last_error"] = None
                else:
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = "Reset recovery failed"

                system_state["execution_state"] = 0
                system_state["progress"] = {"current": 0, "total": 0}
                system_state["start_state"] = "idle"
                await broadcast_state()

            elif cmd == "unlock":
                if cnc_controller and cnc_controller.is_connected:
                    ok_unlock = await asyncio.to_thread(cnc_controller.unlock)
                    system_state["status"] = "IDLE" if ok_unlock else "ERROR"
                    if not ok_unlock:
                        system_state["last_error"] = "Unlock failed"
                else:
                    system_state["status"] = "NOT_READY"
                await broadcast_state()

                hardware_ok = True
                if cnc_controller and cnc_controller.is_connected:
                    hardware_ok = await asyncio.to_thread(
                        cnc_controller.recover_from_reset,
                        5.0,
                        True,
                        "XYZ"
                    )

                if hardware_ok:
                    if cnc_controller and cnc_controller.is_connected:
                        status = await asyncio.to_thread(cnc_controller.get_status)
                        position = status.get("position", {})
                        system_state["position"] = {
                            "x": float(position.get("x", 0.0)),
                            "y": float(position.get("y", 0.0)),
                            "z": float(position.get("z", 0.0)),
                        }
                    system_state["status"] = "IDLE"
                    system_state["last_error"] = None
                else:
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = "Reset recovery failed (unlock/home/clearance)"

                system_state["execution_state"] = 0
                system_state["progress"] = {"current": 0, "total": 0}
                system_state["start_state"] = "idle"
                await broadcast_state()

            elif cmd == "calibrate":
                if workflow_task and not workflow_task.done():
                    system_state["status"] = "BUSY"
                    system_state["last_error"] = "Workflow running"
                    await broadcast_state()
                    continue

                current_calibrate_state = system_state.get("calibrate_state", "idle")

                # Second click: save offset to cal_offset.json
                if current_calibrate_state == "done":
                    if cnc_controller and cnc_controller.is_connected and system_state.get("calibrate_target"):
                        status_now = await asyncio.to_thread(cnc_controller.query_status_once, 1.0)
                        pos = status_now.get("position", {})
                        actual_x = float(pos.get("x", 0.0))
                        actual_y = float(pos.get("y", 0.0))
                        actual_z = float(pos.get("z", 0.0))
                        predicted = system_state.get("calibrate_target", {})
                        pred_x = float(predicted.get("x", 0.0))
                        pred_y = float(predicted.get("y", 0.0))

                        cal_x = actual_x - pred_x
                        cal_y = actual_y - pred_y
                        cal_z = actual_z  # Save current Z as reference point

                        # Save to cal_offset.json including Z
                        try:
                            CAL_OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
                            with open(CAL_OFFSET_PATH, "w") as f:
                                json.dump({
                                    "x": cal_x, 
                                    "y": cal_y, 
                                    "z": cal_z
                                }, f, indent=2)
                            logger.info(f"Saved cal_offset: X{cal_x:.3f} Y{cal_y:.3f} Z{cal_z:.3f}")
                        except Exception as e:
                            logger.warning(f"Failed to save cal_offset: {e}")

                    system_state["status"] = "STANDBY_MOVING"
                    await broadcast_state()
                    ok_standby = await asyncio.to_thread(move_to_standby_sync)
                    if ok_standby:
                        system_state["status"] = "STANDBY"
                        system_state["calibrate_state"] = "idle"
                        system_state["last_error"] = None
                    else:
                        system_state["status"] = "ERROR"
                        system_state["last_error"] = "Failed return standby after calibrate"
                    await broadcast_state()
                    continue

                # First click: run calibrate process.
                system_state["calibrate_state"] = "running"
                system_state["status"] = "CALIBRATE_RUNNING"
                await broadcast_state()

                try:
                    ok_calibrate = await asyncio.wait_for(
                        run_calibrate_flow(),
                        timeout=CALIBRATE_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    ok_calibrate = False
                    system_state["status"] = "ERROR"
                    _set_error("TIMEOUT", f"Calibrate timeout after {CALIBRATE_TIMEOUT_SEC:.1f}s")
                if ok_calibrate:
                    system_state["calibrate_state"] = "done"
                else:
                    system_state["calibrate_state"] = "idle"
                await broadcast_state()

            elif cmd == "reset_offset":
                CALIB_OFFSET_X = 0.0
                CALIB_OFFSET_Y = 0.0
                _save_runtime_offset()
                system_state["calibrate_offset"] = {"x": CALIB_OFFSET_X, "y": CALIB_OFFSET_Y}
                system_state["last_error"] = None
                system_state["status"] = "IDLE"
                await broadcast_state()

            elif cmd == "camera_connect":
                index_raw = message.get("index", 0)
                try:
                    cam_index = int(index_raw)
                except Exception:
                    cam_index = 0

                preview_idx = int(system_state.get("preview_camera_index", -1))
                if preview_idx == cam_index and system_state.get("preview_camera_connected", False):
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = "Main camera must use different index from preview camera"
                    await broadcast_state()
                    continue

                system_state["status"] = "CAMERA_CONNECTING"
                await broadcast_state()
                ok_cam = await asyncio.to_thread(connect_camera_sync, cam_index)
                if ok_cam:
                    system_state["status"] = "IDLE"
                    system_state["last_error"] = None
                else:
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = f"Camera connect failed index {cam_index}"
                await broadcast_state()

            elif cmd == "preview_camera_connect":
                index_raw = message.get("index", 0)
                try:
                    cam_index = int(index_raw)
                except Exception:
                    cam_index = 0

                main_idx = int(system_state.get("camera_index", -1))
                if main_idx == cam_index and system_state.get("camera_connected", False):
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = "Preview camera must use different index from main camera"
                    await broadcast_state()
                    continue

                system_state["status"] = "CAMERA_CONNECTING"
                await broadcast_state()
                ok_cam = await asyncio.to_thread(connect_preview_camera_sync, cam_index)
                if ok_cam:
                    system_state["status"] = "IDLE"
                    system_state["last_error"] = None
                else:
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = f"Preview camera connect failed index {cam_index}"
                await broadcast_state()

            elif cmd == "jog":
                if not (cnc_controller and cnc_controller.is_connected):
                    system_state["status"] = "NOT_READY"
                    system_state["last_error"] = "CNC not connected"
                    await broadcast_state()
                    continue

                try:
                    dx = float(message.get("dx", 0.0))
                    dy = float(message.get("dy", 0.0))
                    dz = float(message.get("dz", 0.0))
                    feed = int(message.get("feed", 600))
                except Exception:
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = "Invalid jog payload"
                    await broadcast_state()
                    continue

                is_paused = system_state.get("start_state") == "paused_at_point"
                system_state["status"] = "JOGGING"
                await broadcast_state()
                ok_jog = await asyncio.to_thread(manual_jog_sync, dx, dy, dz, feed)
                if ok_jog:
                    if is_paused:
                        jog_offset["x"] += dx
                        jog_offset["y"] += dy
                        jog_offset["z"] += dz
                        system_state["jog_offset"] = dict(jog_offset)
                        _save_work_points()
                        logger.info(f"Jog offset accumulated: {jog_offset}")
                    system_state["status"] = "PAUSED_AT_PADHOLE" if is_paused else "IDLE"
                    system_state["last_error"] = None
                else:
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = "Manual jog failed"
                await broadcast_state()
                
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)

async def broadcast_state():
    """Broadcast state to all connected WebSocket clients"""
    for client in connected_clients:
        try:
            await client.send_json(system_state)
        except Exception as e:
            logger.error(f"Broadcast error: {e}")

# ==================== API Endpoints ====================

@app.get("/")
async def root():
    """Root HTML page"""
    try:
        html_content = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load dashboard template: {e}")
    return HTMLResponse(html_content)

@app.get("/video/stream")
async def video_stream():
    """MJPEG video stream"""
    def generate_frames():
        while True:
            try:
                if camera:
                    frame = camera.get_frame()
                    if frame is not None:
                        # Encode as JPEG
                        _, buffer = cv2.imencode('.jpg', frame)
                        frame_bytes = buffer.tobytes()
                        
                        yield (b'--frame\r\n'
                              b'Content-Type: image/jpeg\r\n\r\n'
                              + frame_bytes + b'\r\n')
                    else:
                        # Generate placeholder frame
                        frame = np.zeros((480, 640, 3), dtype=np.uint8)
                        cv2.putText(frame, 'No Camera', (200, 240), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                        _, buffer = cv2.imencode('.jpg', frame)
                        yield (b'--frame\r\n'
                              b'Content-Type: image/jpeg\r\n\r\n'
                              + buffer.tobytes() + b'\r\n')
                else:
                    # No camera - send placeholder
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(frame, 'Camera Not Connected', (150, 240), 
                              cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                    _, buffer = cv2.imencode('.jpg', frame)
                    yield (b'--frame\r\n'
                          b'Content-Type: image/jpeg\r\n\r\n'
                          + buffer.tobytes() + b'\r\n')
                        
            except Exception as e:
                logger.error(f"Video stream error: {e}")
                break
    
    return StreamingResponse(generate_frames(), 
                          media_type='multipart/x-mixed-replace; boundary=frame')


@app.get("/video/preview_stream")
async def preview_video_stream():
    """MJPEG preview-only stream from separate camera source."""
    def generate_frames():
        while True:
            try:
                if preview_camera:
                    frame = preview_camera.get_frame()
                    if frame is not None:
                        _, buffer = cv2.imencode('.jpg', frame)
                        frame_bytes = buffer.tobytes()
                        yield (b'--frame\r\n'
                              b'Content-Type: image/jpeg\r\n\r\n'
                              + frame_bytes + b'\r\n')
                    else:
                        frame = np.zeros((480, 640, 3), dtype=np.uint8)
                        cv2.putText(frame, 'Preview Camera No Frame', (120, 240),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
                        _, buffer = cv2.imencode('.jpg', frame)
                        yield (b'--frame\r\n'
                              b'Content-Type: image/jpeg\r\n\r\n'
                              + buffer.tobytes() + b'\r\n')
                else:
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(frame, 'Preview Camera Not Connected', (80, 240),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
                    _, buffer = cv2.imencode('.jpg', frame)
                    yield (b'--frame\r\n'
                          b'Content-Type: image/jpeg\r\n\r\n'
                          + buffer.tobytes() + b'\r\n')
            except Exception as e:
                logger.error(f"Preview video stream error: {e}")
                break

    return StreamingResponse(generate_frames(),
                          media_type='multipart/x-mixed-replace; boundary=frame')

# ==================== REST API ====================

@app.get("/api/status")
async def get_status():
    """Get current system status"""
    return system_state

@app.get("/api/preflight")
async def get_preflight():
    """Run preflight checks and return detail result."""
    checks = await asyncio.to_thread(run_preflight_checks_sync)
    system_state["preflight"] = checks
    return checks


@app.get("/api/metrics")
async def get_metrics(date_utc: Optional[str] = None):
    """Get summarized job metrics from telemetry logs."""
    return await asyncio.to_thread(_summarize_metrics, date_utc)

@app.post("/api/control/start")
async def start_drill():
    """Start drilling operation"""
    system_state["status"] = "STARTING"
    await broadcast_state()
    return {"status": "started"}

@app.post("/api/control/stop")
async def stop_drill():
    """Stop drilling operation"""
    if cnc_controller:
        cnc_controller.emergency_stop()
    system_state["status"] = "STOPPED"
    await broadcast_state()
    return {"status": "stopped"}

@app.post("/api/control/home")
async def home_machine():
    """Home machine"""
    system_state["status"] = "HOMING"
    await broadcast_state()
    return {"status": "homing"}

@app.post("/api/control/reset")
async def reset_system():
    """Reset system"""
    if executor:
        executor.reset()
    system_state["status"] = "IDLE"
    system_state["execution_state"] = 0
    system_state["progress"] = {"current": 0, "total": 0}
    await broadcast_state()
    return {"status": "reset"}

@app.get("/api/camera/info")
async def get_camera_info():
    """Get camera information"""
    if camera:
        return camera.get_camera_info()
    return {"error": "Camera not initialized"}

@app.get("/api/detections")
async def get_detections():
    """Get current detections"""
    if camera and detector:
        frame = camera.get_frame()
        if frame is not None and detector:
            detections = detector.detect(frame)
            return {
                "count": len(detections),
                "detections": [
                    {
                        "bbox": d.bbox,
                        "confidence": d.confidence,
                        "class_name": d.class_name
                    }
                    for d in detections
                ]
            }
    return {"count": 0, "detections": []}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
