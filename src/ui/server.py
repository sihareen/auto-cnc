"""
FastAPI Web Server for Auto CNC Dashboard
"""
import asyncio
import logging
import threading
import time
import json
import re
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
from pydantic import BaseModel

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
    "refine_overlay_target": None,
}

# WebSocket connections
connected_clients: List[WebSocket] = []
workflow_task: Optional[asyncio.Task] = None
stop_event = threading.Event()
current_job_id: Optional[str] = None
_telemetry_io_lock = threading.Lock()

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


REFINE_PROFILE_PRESETS: Dict[str, Dict[str, Any]] = {
    # Baseline from v2 reference (carititikpas).
    "reference": {
        "roi_radius_px": 120.0,
        "preprocess_alpha": 1.5,
        "preprocess_beta": 20.0,
        "preprocess_normalize": True,
        "detect_confidence": 0.10,
        "detect_iou": 0.50,
        "max_no_det_retry": 10,
        "retry_sleep_ms": 100,
        "max_delta_mm": 0.10,
        "tol_x_mm": 0.03,
        "tol_y_mm": 0.03,
        "search_radius_mm": 3.0,
        "timeout_ms": 600,
        "preview_overlay_interval_ms": 250,
    },
    # Faster cycle, weaker recovery for low-visibility pads.
    "fast": {
        "roi_radius_px": 100.0,
        "preprocess_alpha": 1.3,
        "preprocess_beta": 12.0,
        "preprocess_normalize": True,
        "detect_confidence": 0.18,
        "detect_iou": 0.45,
        "max_no_det_retry": 4,
        "retry_sleep_ms": 60,
        "max_delta_mm": 0.08,
        "tol_x_mm": 0.04,
        "tol_y_mm": 0.04,
        "search_radius_mm": 2.5,
        "timeout_ms": 420,
        "preview_overlay_interval_ms": 180,
    },
    # Stricter visual lock, slower but more conservative.
    "strict": {
        "roi_radius_px": 130.0,
        "preprocess_alpha": 1.7,
        "preprocess_beta": 24.0,
        "preprocess_normalize": True,
        "detect_confidence": 0.08,
        "detect_iou": 0.55,
        "max_no_det_retry": 14,
        "retry_sleep_ms": 120,
        "max_delta_mm": 0.07,
        "tol_x_mm": 0.02,
        "tol_y_mm": 0.02,
        "search_radius_mm": 2.0,
        "timeout_ms": 800,
        "preview_overlay_interval_ms": 280,
    },
}

REFINE_PROFILE = str(_get_cfg("refine.profile", "reference")).strip().lower()
REFINE_PROFILE_OVERRIDE = bool(_get_cfg("refine.profile_override", False))


def _get_refine_cfg(key: str, default: Any) -> Any:
    preset = REFINE_PROFILE_PRESETS.get(REFINE_PROFILE, REFINE_PROFILE_PRESETS["reference"])
    preset_has_key = key in preset
    preset_val = preset.get(key, default)
    if REFINE_PROFILE_OVERRIDE and preset_has_key:
        return preset_val
    # For keys not present in preset (e.g., roi_center_*), allow explicit config override.
    return _get_cfg(f"refine.{key}", preset_val)

# CNC settings
STANDBY_X = _get_cfg("standby.x", 85.0)
STANDBY_Y = _get_cfg("standby.y", -95.0)
XY_MOVE_FEED = int(_get_cfg("drill.xy_move_feed", 1000))
Z_DRILL_FEED = int(_get_cfg("drill.z_drill_feed", 300))
Z_MOVE_FEED = int(_get_cfg("drill.z_move_feed", 1000))
DRILL_REFINE_DWELL_MS = int(_get_cfg("drill.refine_dwell_ms", 800))
DRILL_REFINE_MAX_STEPS = int(_get_cfg("drill.refine_max_steps", 8))
DRILL_REFINE_JOG_FEED = int(_get_cfg("drill.refine_jog_feed", 300))
DRILL_REFINE_LOOP_SLEEP_MS = int(_get_cfg("drill.refine_loop_sleep_ms", 1000))
DRILL_REFINE_DETECT_WINDOW_MS = int(_get_cfg("drill.refine_detect_window_ms", 3000))
DRILL_REFINE_JOG_STEP_MM = float(_get_cfg("drill.refine_jog_step_mm", 0.1))
DRILL_REFINE_PIXEL_TOLERANCE = float(_get_cfg("drill.refine_pixel_tolerance", 0.1))
DETECTION_CONFIDENCE_THRESHOLD = float(_get_cfg("detection.confidence_threshold", 0.25))
DETECTION_IOU_THRESHOLD = float(_get_cfg("detection.iou_threshold", 0.45))
DETECTION_MODEL_PATH = str(_get_cfg("detection.model_path", "best.pt"))
DETECTION_MIN_POINTS = int(_get_cfg("detection.min_points", 1))
DETECTION_RETRY_COUNT = int(_get_cfg("detection.retry_count", 2))
DETECTION_RETRY_STEP = float(_get_cfg("detection.retry_threshold_step", 0.05))
CALIBRATION_AFFINE_PATH = str(_get_cfg("calibration.affine_matrix", "config/calibration_affine.json"))
CAMERA_MAIN_SOURCE = _get_cfg("camera.main_source", _get_cfg("camera.main_index", 0))
CAMERA_PREVIEW_SOURCE = _get_cfg("camera.preview_source", _get_cfg("camera.preview_index", 1))
CAMERA_WIDTH = int(_get_cfg("camera.width", 640))
CAMERA_HEIGHT = int(_get_cfg("camera.height", 480))
CAMERA_FPS = int(_get_cfg("camera.fps", 30))
CNC_PORT = str(_get_cfg("cnc.port", "/dev/ttyUSB0"))
CNC_BAUDRATE = int(_get_cfg("cnc.baudrate", 115200))
CNC_TIMEOUT = float(_get_cfg("cnc.timeout", 2.0))
WORKSPACE_MARGIN_MM = float(_get_cfg("workspace.margin_mm", 0.0))
RETRY_MOVE = int(_get_cfg("retry.move", 1))
RETRY_STATUS = int(_get_cfg("retry.status", 1))
RETRY_CAPTURE = int(_get_cfg("retry.capture", 1))
CALIBRATE_TIMEOUT_SEC = float(_get_cfg("calibration.timeout_sec", 45.0))
ALIGN_CLEARANCE_MM = float(_get_cfg("alignment.clearance_mm", _get_cfg("drill.z_clearance", 5.0)))
REFINE_ENABLED = bool(_get_cfg("refine.enabled", False))
REFINE_ROI_RADIUS_PX = float(_get_refine_cfg("roi_radius_px", 120.0))
REFINE_ROI_CENTER_X_PX = float(_get_refine_cfg("roi_center_x_px", 282.0))
REFINE_ROI_CENTER_Y_PX = float(_get_refine_cfg("roi_center_y_px", 257.0))
REFINE_MAX_DELTA_MM = float(_get_refine_cfg("max_delta_mm", 0.10))
REFINE_TOL_X_MM = float(_get_refine_cfg("tol_x_mm", 0.03))
REFINE_TOL_Y_MM = float(_get_refine_cfg("tol_y_mm", 0.03))
REFINE_SEARCH_RADIUS_MM = float(_get_refine_cfg("search_radius_mm", 3.0))
REFINE_TIMEOUT_MS = int(_get_refine_cfg("timeout_ms", 600))
REFINE_PREPROCESS_ALPHA = float(_get_refine_cfg("preprocess_alpha", 1.5))
REFINE_PREPROCESS_BETA = float(_get_refine_cfg("preprocess_beta", 20.0))
REFINE_PREPROCESS_NORMALIZE = bool(_get_refine_cfg("preprocess_normalize", True))
REFINE_DETECT_CONFIDENCE = float(_get_refine_cfg("detect_confidence", 0.10))
REFINE_DETECT_IOU = float(_get_refine_cfg("detect_iou", 0.50))
REFINE_ACCEPT_CONFIDENCE = float(_get_cfg("drill.refine_accept_confidence", 0.50))
REFINE_MAX_NO_DET_RETRY = int(_get_refine_cfg("max_no_det_retry", 10))
REFINE_RETRY_SLEEP_MS = int(_get_refine_cfg("retry_sleep_ms", 100))
REFINE_PREVIEW_OVERLAY_INTERVAL_MS = int(_get_refine_cfg("preview_overlay_interval_ms", 250))
PERF_FAST_THRESHOLD = int(_get_cfg("performance.fast_point_threshold", 60))
PERF_SLOW_THRESHOLD = int(_get_cfg("performance.slow_point_threshold", 15))
PERF_FAST_MULT = float(_get_cfg("performance.fast_xy_multiplier", 1.2))
PERF_SLOW_MULT = float(_get_cfg("performance.slow_xy_multiplier", 0.9))

_preview_overlay_last_detect_ts = 0.0
_preview_overlay_cached_dets: List[Dict[str, Any]] = []
_detector_lock = threading.Lock()

# File paths
TEMP_DIR = Path("temp")
JOB_OVERLAY_IMAGE_PATH = TEMP_DIR / "overlay.jpg"
CALIBRATE_IMAGE_PATH = TEMP_DIR / "overlay.jpg"
LAST_JOB_POINTS_PATH = Path(_get_cfg("output.last_job_points", "config/last_job_points.json"))
WORK_POINTS_PATH = Path(_get_cfg("output.work_points", "config/work_points.json"))
MAPPING_GCODE_PATH = Path(_get_cfg("output.mapping_gcode", "config/mapping_output.gcode"))
CALIB_OFFSET_PATH = Path(_get_cfg("calibration.runtime_offset", "config/calibration_runtime_offset.json"))
CAL_OFFSET_PATH = Path(_get_cfg("calibration.cal_offset", "config/cal_offset.json"))
CALIB_OFFSET_X = 0.0
CALIB_OFFSET_Y = 0.0
DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "templates" / "dashboard.html"
MAPPING_DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "templates" / "mapping_calibrate.html"
DRILL_DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "templates" / "drill.html"
JOB_LOGS_DIR = Path("logs/jobs")
GAGAL_DRILL_PATH = TEMP_DIR / "gagaldrill.txt"

# Expose temp artifacts (e.g., overlay.jpg) for dashboard preview cards.
TEMP_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/temp", StaticFiles(directory=str(TEMP_DIR)), name="temp")


def _normalize_camera_source(source: Any) -> Optional[Any]:
    if source is None:
        return None

    if isinstance(source, bool):
        return None

    if isinstance(source, int):
        return source if 0 <= source <= 9 else None

    raw = str(source).strip()
    if not raw:
        return None

    if raw.isdigit():
        idx = int(raw)
        return idx if 0 <= idx <= 9 else None

    if raw.startswith("/dev/"):
        return raw

    # Accept basename from /dev/v4l symlink dirs
    if "/" not in raw:
        by_id = Path("/dev/v4l/by-id") / raw
        by_path = Path("/dev/v4l/by-path") / raw
        if by_id.exists():
            return str(by_id)
        if by_path.exists():
            return str(by_path)

    return raw


def _is_valid_camera_source(source: Any) -> bool:
    normalized = _normalize_camera_source(source)
    if normalized is None:
        return False
    if isinstance(normalized, int):
        return 0 <= normalized <= 9
    return isinstance(normalized, str) and len(normalized) > 0


def _camera_source_key(source: Any) -> str:
    normalized = _normalize_camera_source(source)
    if normalized is None:
        return "invalid"
    if isinstance(normalized, int):
        return f"idx:{normalized}"
    try:
        return f"path:{Path(normalized).resolve()}"
    except Exception:
        return f"path:{normalized}"


def _list_camera_sources() -> List[Dict[str, str]]:
    """
    Discover camera sources from /dev/v4l symlinks, prioritizing by-id.
    Returns list of {value, label}.
    """
    sources: List[Dict[str, str]] = []
    seen: set[str] = set()

    def _add(path_obj: Path, prefix: str) -> None:
        try:
            resolved = str(path_obj.resolve())
        except Exception:
            resolved = str(path_obj)
        key = str(path_obj)
        if key in seen:
            return
        seen.add(key)
        sources.append({
            "value": key,
            "label": f"{prefix}: {path_obj.name} -> {Path(resolved).name}",
        })

    by_id_dir = Path("/dev/v4l/by-id")
    if by_id_dir.exists():
        for p in sorted(by_id_dir.iterdir()):
            if p.is_symlink() or p.exists():
                _add(p, "by-id")

    # Always keep configured sources selectable even if device disconnected.
    for cfg_src in (CAMERA_MAIN_SOURCE, CAMERA_PREVIEW_SOURCE):
        norm = _normalize_camera_source(cfg_src)
        if isinstance(norm, str):
            if norm not in seen:
                seen.add(norm)
                sources.append({
                    "value": norm,
                    "label": f"config: {Path(norm).name}",
                })

    return sources


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
    if REFINE_PROFILE not in REFINE_PROFILE_PRESETS:
        warnings.append(
            f"Invalid refine.profile in config: {REFINE_PROFILE}. "
            f"Use one of {sorted(REFINE_PROFILE_PRESETS.keys())}"
        )
    if not Path(DETECTION_MODEL_PATH).exists():
        warnings.append(f"Detection model file not found: {DETECTION_MODEL_PATH}")
    if not Path(CALIBRATION_AFFINE_PATH).exists():
        warnings.append(f"Calibration file not found: {CALIBRATION_AFFINE_PATH}")
    if not _is_valid_camera_source(CAMERA_MAIN_SOURCE):
        warnings.append(f"Invalid main camera source in config: {CAMERA_MAIN_SOURCE}")
    if not _is_valid_camera_source(CAMERA_PREVIEW_SOURCE):
        warnings.append(f"Invalid preview camera source in config: {CAMERA_PREVIEW_SOURCE}")
    if _camera_source_key(CAMERA_MAIN_SOURCE) == _camera_source_key(CAMERA_PREVIEW_SOURCE):
        warnings.append("Main and preview camera source should be different")
    if not CNC_PORT:
        warnings.append("CNC port empty in config")
    if CNC_BAUDRATE <= 0:
        warnings.append(f"Invalid CNC baudrate in config: {CNC_BAUDRATE}")
    if CNC_TIMEOUT <= 0:
        warnings.append(f"Invalid CNC timeout in config: {CNC_TIMEOUT}")
    if ALIGN_CLEARANCE_MM <= 0:
        warnings.append(f"Invalid alignment.clearance_mm in config: {ALIGN_CLEARANCE_MM}")
    if DETECTION_RETRY_COUNT < 0:
        warnings.append(f"Invalid detection.retry_count in config: {DETECTION_RETRY_COUNT}")
    if DETECTION_RETRY_STEP <= 0:
        warnings.append(f"Invalid detection.retry_threshold_step in config: {DETECTION_RETRY_STEP}")
    if RETRY_MOVE < 0 or RETRY_STATUS < 0 or RETRY_CAPTURE < 0:
        warnings.append(
            "Invalid retry config (retry.move/retry.status/retry.capture must be >= 0)"
        )
    if CALIBRATE_TIMEOUT_SEC <= 0:
        warnings.append(f"Invalid calibration.timeout_sec in config: {CALIBRATE_TIMEOUT_SEC}")
    if PERF_SLOW_THRESHOLD < 0 or PERF_FAST_THRESHOLD < 0:
        warnings.append(
            "Invalid performance thresholds (performance.slow_point_threshold/fast_point_threshold must be >= 0)"
        )
    if PERF_SLOW_MULT <= 0 or PERF_FAST_MULT <= 0:
        warnings.append(
            "Invalid performance multipliers (performance.slow_xy_multiplier/fast_xy_multiplier must be > 0)"
        )
    if REFINE_ROI_RADIUS_PX <= 0:
        warnings.append(f"Invalid refine.roi_radius_px in config: {REFINE_ROI_RADIUS_PX}")
    if REFINE_MAX_DELTA_MM <= 0:
        warnings.append(f"Invalid refine.max_delta_mm in config: {REFINE_MAX_DELTA_MM}")
    if REFINE_TOL_X_MM < 0 or REFINE_TOL_Y_MM < 0:
        warnings.append("Invalid refine tolerances (refine.tol_x_mm/refine.tol_y_mm must be >= 0)")
    if REFINE_SEARCH_RADIUS_MM <= 0:
        warnings.append(f"Invalid refine.search_radius_mm in config: {REFINE_SEARCH_RADIUS_MM}")
    if REFINE_TIMEOUT_MS <= 0:
        warnings.append(f"Invalid refine.timeout_ms in config: {REFINE_TIMEOUT_MS}")
    if REFINE_PREPROCESS_ALPHA <= 0:
        warnings.append(f"Invalid refine.preprocess_alpha in config: {REFINE_PREPROCESS_ALPHA}")
    if not (0.0 <= REFINE_DETECT_CONFIDENCE <= 1.0):
        warnings.append(f"Invalid refine.detect_confidence in config: {REFINE_DETECT_CONFIDENCE}")
    if not (0.0 <= REFINE_DETECT_IOU <= 1.0):
        warnings.append(f"Invalid refine.detect_iou in config: {REFINE_DETECT_IOU}")
    if REFINE_MAX_NO_DET_RETRY < 0:
        warnings.append(f"Invalid refine.max_no_det_retry in config: {REFINE_MAX_NO_DET_RETRY}")
    if REFINE_RETRY_SLEEP_MS < 0:
        warnings.append(f"Invalid refine.retry_sleep_ms in config: {REFINE_RETRY_SLEEP_MS}")
    return warnings


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_json_file(path: Path, default: Any) -> Any:
    """Read JSON file with fallback to default."""
    try:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_text_atomic(path: Path, content: str) -> bool:
    """Atomic text write to reduce partial/corrupt file risk."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        tmp_path.replace(path)
        return True
    except Exception:
        return False


def _write_json_file_atomic(path: Path, payload: Any) -> bool:
    """Atomic JSON write."""
    try:
        data = json.dumps(payload, indent=2)
        return _write_text_atomic(path, data)
    except Exception:
        return False


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
        ok = _write_json_file_atomic(_job_log_path(job_id), payload)
        if not ok:
            raise RuntimeError("atomic write failed")
    except Exception as e:
        logger.warning(f"Failed to initialize job telemetry: {e}")


def _append_job_telemetry(event: str, **fields: Any) -> None:
    if not current_job_id:
        return

    path = _job_log_path(current_job_id)
    try:
        with _telemetry_io_lock:
            data = _read_json_file(path, {
                "job_id": current_job_id,
                "started_at": _now_iso(),
                "status": "running",
                "events": [],
            })
            if not isinstance(data, dict):
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

            ok = _write_json_file_atomic(path, data)
            if not ok:
                raise RuntimeError("atomic write failed")
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
    refine_skip_counter: Dict[str, int] = {}
    total_ms_values: List[float] = []
    drill_ms_values: List[float] = []
    refine_started_total = 0
    refine_applied_total = 0
    refine_skipped_total = 0

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
        refine_started = 0
        refine_applied = 0
        refine_skipped = 0

        for evt in events:
            if evt.get("event") == "point_drilled":
                points = max(points, int(evt.get("point", 0)))
            if evt.get("event") == "metrics":
                metrics_evt = evt
            if evt.get("event") == "error":
                code = str(evt.get("code", "UNKNOWN"))
                error_counter[code] = error_counter.get(code, 0) + 1
            if evt.get("event") == "refine_start":
                refine_started += 1
            if evt.get("event") == "refine_applied":
                refine_applied += 1
            if evt.get("event") == "refine_skipped":
                refine_skipped += 1
                skip_status = str(evt.get("status", "unknown"))
                refine_skip_counter[skip_status] = refine_skip_counter.get(skip_status, 0) + 1

        refine_started_total += refine_started
        refine_applied_total += refine_applied
        refine_skipped_total += refine_skipped

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
            "refine": {
                "started": refine_started,
                "applied": refine_applied,
                "skipped": refine_skipped,
            },
        })

    jobs_sorted = sorted(jobs, key=lambda j: str(j.get("started_at", "")), reverse=True)
    total_jobs = len(jobs_sorted)
    completed = sum(1 for j in jobs_sorted if j.get("status") == "complete")
    failed = sum(1 for j in jobs_sorted if j.get("status") in {"failed", "aborted", "stopped"})
    success_rate = (completed / total_jobs * 100.0) if total_jobs > 0 else 0.0

    def _avg(vals: List[float]) -> Optional[float]:
        return round(sum(vals) / len(vals), 2) if vals else None

    refine_apply_rate = (
        (refine_applied_total / refine_started_total * 100.0)
        if refine_started_total > 0 else 0.0
    )

    top_refine_skip_status = sorted(
        [{"status": status, "count": count} for status, count in refine_skip_counter.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]

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
        "refine_started_total": refine_started_total,
        "refine_applied_total": refine_applied_total,
        "refine_skipped_total": refine_skipped_total,
        "refine_apply_rate_pct": round(refine_apply_rate, 2),
        "top_refine_skip_status": top_refine_skip_status,
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


def _detect_with_detector_profile(frame: np.ndarray, confidence: float, iou: float):
    """
    Run detector with temporary confidence/iou profile under lock.
    """
    if detector is None:
        return []
    with _detector_lock:
        old_conf = getattr(detector, "confidence_threshold", None)
        old_iou = getattr(detector, "iou_threshold", None)
        try:
            if old_conf is not None:
                detector.confidence_threshold = float(confidence)
            if old_iou is not None:
                detector.iou_threshold = float(iou)
            return detector.detect(frame)
        finally:
            if old_conf is not None:
                detector.confidence_threshold = old_conf
            if old_iou is not None:
                detector.iou_threshold = old_iou


def _preprocess_refine_frame(frame: np.ndarray) -> np.ndarray:
    """
    Reference-aligned preprocess:
    - contrast/brightness via convertScaleAbs
    - optional normalize
    """
    out = cv2.convertScaleAbs(frame, alpha=REFINE_PREPROCESS_ALPHA, beta=REFINE_PREPROCESS_BETA)
    if REFINE_PREPROCESS_NORMALIZE:
        out = cv2.normalize(out, None, 0, 255, cv2.NORM_MINMAX)
    return out


def _apply_refine_roi_mask(frame: np.ndarray, center_xy: Optional[Tuple[float, float]]) -> np.ndarray:
    """
    Apply circular ROI mask centered at target projection.
    """
    h, w = frame.shape[:2]
    if center_xy is None:
        # Match refine/cnc_run.py: fixed ROI center in preview frame pixels.
        cx = int(round(REFINE_ROI_CENTER_X_PX))
        cy = int(round(REFINE_ROI_CENTER_Y_PX))
    else:
        cx = int(round(center_xy[0]))
        cy = int(round(center_xy[1]))
    cx = max(0, min(w - 1, cx))
    cy = max(0, min(h - 1, cy))

    radius_px = max(4, int(round(REFINE_ROI_RADIUS_PX)))
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.circle(mask, (cx, cy), radius_px, 255, -1)
    return cv2.bitwise_and(frame, frame, mask=mask)


def _run_refine_detection(frame: np.ndarray, center_xy: Optional[Tuple[float, float]]):
    """
    Full refine detection pipeline aligned with reference:
    preprocess -> circular ROI mask -> YOLO detect -> keep boxes inside ROI.
    """
    pre = _preprocess_refine_frame(frame)
    masked = _apply_refine_roi_mask(pre, center_xy)
    raw = _detect_with_detector_profile(masked, REFINE_DETECT_CONFIDENCE, REFINE_DETECT_IOU)

    if center_xy is None:
        return raw

    cx, cy = float(center_xy[0]), float(center_xy[1])
    radius_px = float(max(4, int(round(REFINE_ROI_RADIUS_PX))))
    filtered = []
    for det in raw:
        x1, y1, x2, y2 = det.bbox
        bx = (x1 + x2) / 2.0
        by = (y1 + y2) / 2.0
        d = ((bx - cx) ** 2 + (by - cy) ** 2) ** 0.5
        if d <= radius_px:
            filtered.append(det)
    if not filtered:
        return filtered

    # Reference behavior: keep highest-confidence detection inside ROI.
    best = max(filtered, key=lambda d: float(getattr(d, "confidence", 0.0)))
    return [best]


def _runtime_refine_point_sync(initial_x: float, initial_y: float) -> Dict[str, Any]:
    """
    Runtime per-point refine. Safe fallback by design:
    on any issue, returns original point with non-applied status.
    """
    if not REFINE_ENABLED:
        return {
            "x": float(initial_x),
            "y": float(initial_y),
            "delta_x": 0.0,
            "delta_y": 0.0,
            "applied": False,
            "status": "disabled",
            "confidence": None,
            "pixel_dx": None,
            "pixel_dy": None,
        }
    if not (preview_camera and detector and transformer):
        return {
            "x": float(initial_x),
            "y": float(initial_y),
            "delta_x": 0.0,
            "delta_y": 0.0,
            "applied": False,
            "status": "skipped_preview_not_ready",
            "confidence": None,
            "pixel_dx": None,
            "pixel_dy": None,
        }

    try:
        from src.vision.refiner import PointRefiner, RefineConfig
    except Exception:
        return {
            "x": float(initial_x),
            "y": float(initial_y),
            "delta_x": 0.0,
            "delta_y": 0.0,
            "applied": False,
            "status": "skipped_refiner_import_error",
            "confidence": None,
            "pixel_dx": None,
            "pixel_dy": None,
        }

    frame = preview_camera.get_frame()
    if frame is None:
        return {
            "x": float(initial_x),
            "y": float(initial_y),
            "delta_x": 0.0,
            "delta_y": 0.0,
            "applied": False,
            "status": "skipped_preview_no_frame",
            "confidence": None,
            "pixel_dx": None,
            "pixel_dy": None,
        }

    # Match refine/cnc_run.py: use static ROI center, not dynamic per-point projection.
    roi_center = (float(REFINE_ROI_CENTER_X_PX), float(REFINE_ROI_CENTER_Y_PX))
    detections = []
    for attempt in range(REFINE_MAX_NO_DET_RETRY + 1):
        detections = _run_refine_detection(frame, roi_center)
        if detections:
            break
        if attempt < REFINE_MAX_NO_DET_RETRY:
            sleep_sec = max(0.0, REFINE_RETRY_SLEEP_MS / 1000.0)
            if sleep_sec > 0:
                time.sleep(sleep_sec)
            frame = preview_camera.get_frame()
            if frame is None:
                break
    if not detections:
        return {
            "x": float(initial_x),
            "y": float(initial_y),
            "delta_x": 0.0,
            "delta_y": 0.0,
            "applied": False,
            "status": "skipped_no_detection",
            "confidence": None,
            "pixel_dx": None,
            "pixel_dy": None,
        }

    # Optional ROI filter in pixel-space around current machine point.
    candidates_mm: List[Tuple[float, float, float]] = []
    best_pixel_dx: Optional[float] = None
    best_pixel_dy: Optional[float] = None
    for det in detections:
        conf = float(getattr(det, "confidence", 0.0))
        x1, y1, x2, y2 = det.bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0

        if roi_center is not None:
            rx, ry = roi_center
            if ((cx - rx) ** 2 + (cy - ry) ** 2) ** 0.5 > REFINE_ROI_RADIUS_PX:
                continue

        transformed = transformer.transform_point(cx, cy)
        if transformed is None:
            continue
        mx, my = transformed
        if transformer and not transformer.is_within_bounds(mx, my):
            mx, my = transformer.clip_to_bounds(mx, my)
        candidates_mm.append((float(mx), float(my), conf))
        if roi_center is not None and best_pixel_dx is None:
            best_pixel_dx = float(cx - roi_center[0])
            best_pixel_dy = float(cy - roi_center[1])

    cfg = RefineConfig(
        max_delta_mm=REFINE_MAX_DELTA_MM,
        tol_x_mm=REFINE_TOL_X_MM,
        tol_y_mm=REFINE_TOL_Y_MM,
        search_radius_mm=REFINE_SEARCH_RADIUS_MM,
    )
    result = PointRefiner.refine((float(initial_x), float(initial_y)), candidates_mm, cfg)
    return {
        "x": result.x,
        "y": result.y,
        "delta_x": result.delta_x,
        "delta_y": result.delta_y,
        "applied": result.applied,
        "status": result.status,
        "confidence": result.confidence,
        "pixel_dx": best_pixel_dx,
        "pixel_dy": best_pixel_dy,
    }


def _get_preview_overlay_detections(frame: np.ndarray) -> List[Dict[str, Any]]:
    """
    Run preview detections with light caching to avoid heavy per-frame inference.
    """
    global _preview_overlay_last_detect_ts, _preview_overlay_cached_dets

    if detector is None:
        return []

    now = perf_counter()
    interval_sec = max(0.05, REFINE_PREVIEW_OVERLAY_INTERVAL_MS / 1000.0)
    if (now - _preview_overlay_last_detect_ts) < interval_sec:
        return _preview_overlay_cached_dets

    # Match refine/cnc_run.py: fixed ROI center for overlay detection scope.
    overlay_center = (float(REFINE_ROI_CENTER_X_PX), float(REFINE_ROI_CENTER_Y_PX))

    try:
        raw = _run_refine_detection(frame, overlay_center)
        parsed: List[Dict[str, Any]] = []
        for det in raw:
            x1, y1, x2, y2 = det.bbox
            parsed.append({
                "bbox": (float(x1), float(y1), float(x2), float(y2)),
                "confidence": float(getattr(det, "confidence", 0.0)),
                "class_name": str(getattr(det, "class_name", "pad")),
            })
        _preview_overlay_cached_dets = parsed
        _preview_overlay_last_detect_ts = now
    except Exception:
        # Keep previous cached detections if latest detect fails.
        pass

    return _preview_overlay_cached_dets


def _draw_preview_refine_overlay(frame: np.ndarray) -> np.ndarray:
    """
    Draw refine ROI and pad detections onto preview camera frame.
    """
    vis = frame.copy()
    detections = _get_preview_overlay_detections(frame)
    roi_target = system_state.get("refine_overlay_target")

    # Match refine/cnc_run.py: always show fixed ROI center in preview.
    roi_center: Optional[Tuple[int, int]] = (
        int(round(REFINE_ROI_CENTER_X_PX)),
        int(round(REFINE_ROI_CENTER_Y_PX)),
    )

    radius_px = max(4, int(round(REFINE_ROI_RADIUS_PX)))
    in_roi = 0
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        cx = int(round((x1 + x2) / 2.0))
        cy = int(round((y1 + y2) / 2.0))
        is_in_roi = False
        if roi_center is not None:
            dx = cx - roi_center[0]
            dy = cy - roi_center[1]
            is_in_roi = (dx * dx + dy * dy) <= (radius_px * radius_px)
        if is_in_roi:
            in_roi += 1

        box_color = (0, 255, 255) if is_in_roi else (0, 255, 0)
        cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), box_color, 2)
        cv2.circle(vis, (cx, cy), 3, box_color, -1)
        conf = float(det.get("confidence", 0.0))
        label = f"PAD {conf:.2f}"
        cv2.putText(
            vis,
            label,
            (int(x1), max(16, int(y1) - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            box_color,
            1,
        )

    if roi_center is not None:
        cv2.circle(vis, roi_center, radius_px, (255, 200, 0), 2)
        cv2.circle(vis, roi_center, 4, (255, 200, 0), -1)
        point_no = roi_target.get("point") if isinstance(roi_target, dict) else None
        txt = f"ROI p{point_no}" if point_no is not None else "ROI"
        cv2.putText(
            vis,
            txt,
            (max(8, roi_center[0] - 24), max(18, roi_center[1] - radius_px - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 200, 0),
            1,
        )

    cv2.putText(
        vis,
        f"det={len(detections)} in_roi={in_roi}",
        (10, 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (80, 220, 255),
        2,
    )
    return vis


def _load_runtime_offset():
    """Load runtime XY correction offset from disk."""
    global CALIB_OFFSET_X, CALIB_OFFSET_Y
    try:
        data = _read_json_file(CALIB_OFFSET_PATH, {})
        CALIB_OFFSET_X = float(data.get("offset_x", 0.0))
        CALIB_OFFSET_Y = float(data.get("offset_y", 0.0))
    except Exception as e:
        logger.warning(f"Failed to load runtime offset: {e}")


def _save_runtime_offset():
    """Persist runtime XY correction offset to disk."""
    try:
        ok = _write_json_file_atomic(
            CALIB_OFFSET_PATH,
            {"offset_x": CALIB_OFFSET_X, "offset_y": CALIB_OFFSET_Y},
        )
        if not ok:
            raise RuntimeError("atomic write failed")
    except Exception as e:
        logger.warning(f"Failed to save runtime offset: {e}")


def _reset_gagaldrill_file() -> None:
    """Reset temp/gagaldrill.txt before drill run."""
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        GAGAL_DRILL_PATH.write_text("", encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed reset gagaldrill file: {e}")


def _append_gagaldrill_line(line: str) -> None:
    """Append one failed drill entry to temp/gagaldrill.txt."""
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        with open(GAGAL_DRILL_PATH, "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except Exception as e:
        logger.warning(f"Failed append gagaldrill file: {e}")


def _save_last_job_points():
    """Persist drill points to config/last_job_points.json."""
    try:
        ok = _write_json_file_atomic(LAST_JOB_POINTS_PATH, {"points": pending_drill_points})
        if not ok:
            raise RuntimeError("atomic write failed")
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
        
        data = _read_json_file(LAST_JOB_POINTS_PATH, {})
        last_points = data.get("points", [])
        
        # Load cal_offset
        cal_offset_x = 0.0
        cal_offset_y = 0.0
        cal_offset_z = None  # Z reference from calibrate (if exists)
        if CAL_OFFSET_PATH.exists():
            cal_data = _read_json_file(CAL_OFFSET_PATH, {})
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
        ok = _write_json_file_atomic(
            WORK_POINTS_PATH,
            {
                "points": work_points,
                "cal_offset": {"x": total_offset_x, "y": total_offset_y, "z": total_offset_z},
            },
        )
        if not ok:
            raise RuntimeError("atomic write failed")
        
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


def _save_mapping_gcode(work_points: List[Tuple[float, float]], ref_z: Optional[float]) -> Optional[str]:
    """
    Save drilling G-code generated from mapping work points.
    """
    if not work_points:
        return None
    try:
        xy_feed = int(_get_cfg("drill.xy_move_feed", 1000))

        z_note = f"ref_z={float(ref_z):.4f}" if ref_z is not None else "ref_z=none"

        lines: List[str] = []
        lines.append("; Auto-generated from mapping work_points")
        lines.append(f"; points={len(work_points)} {z_note}")
        lines.append("G21 ; mm")
        lines.append("G90 ; absolute")
        lines.append(f"G1 F{xy_feed}")

        for idx, (px, py) in enumerate(work_points, start=1):
            lines.append(f"; Point {idx}")
            # Keep mapping output as XY-only nominal points for refine-drill stage.
            lines.append(f"G90 X{float(px):.4f} Y{float(py):.4f}")

        lines.append("M2")

        ok = _write_text_atomic(MAPPING_GCODE_PATH, "\n".join(lines) + "\n")
        if not ok:
            raise RuntimeError("atomic write failed")
        return str(MAPPING_GCODE_PATH)
    except Exception as e:
        logger.warning(f"Failed to save mapping gcode: {e}")
        return None


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
        data = _read_json_file(CAL_OFFSET_PATH, {})
        z_val = data.get("z")
        return float(z_val) if z_val is not None else None
    except Exception:
        return None


def connect_camera_sync(camera_source: Any) -> bool:
    """Reconnect camera using user-selected source (index or /dev/v4l path)."""
    global camera
    try:
        normalized_source = _normalize_camera_source(camera_source)
        if not _is_valid_camera_source(normalized_source):
            logger.warning(f"Invalid camera source requested: {camera_source}")
            return False
        from src.vision.camera import CameraCapture

        if camera is not None:
            try:
                camera.disconnect()
            except Exception:
                pass

        cam = CameraCapture(
            camera_source=normalized_source,
            width=CAMERA_WIDTH,
            height=CAMERA_HEIGHT,
            fps=CAMERA_FPS,
        )
        if not cam.connect():
            return False
        cam.start_streaming()
        camera = cam
        system_state["camera_source"] = normalized_source
        system_state["camera_connected"] = True
        return True
    except Exception as e:
        logger.warning(f"Camera connect failed on source {camera_source}: {e}")
        system_state["camera_connected"] = False
        return False


def connect_preview_camera_sync(camera_source: Any) -> bool:
    """Reconnect preview-only camera using source (index or /dev/v4l path)."""
    global preview_camera
    try:
        normalized_source = _normalize_camera_source(camera_source)
        if not _is_valid_camera_source(normalized_source):
            logger.warning(f"Invalid preview camera source requested: {camera_source}")
            return False
        from src.vision.camera import CameraCapture

        if preview_camera is not None:
            try:
                preview_camera.disconnect()
            except Exception:
                pass

        cam = CameraCapture(
            camera_source=normalized_source,
            width=CAMERA_WIDTH,
            height=CAMERA_HEIGHT,
            fps=CAMERA_FPS,
        )
        if not cam.connect():
            return False
        cam.start_streaming()
        preview_camera = cam
        system_state["preview_camera_source"] = normalized_source
        system_state["preview_camera_connected"] = True
        return True
    except Exception as e:
        logger.warning(f"Preview camera connect failed on source {camera_source}: {e}")
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


def auto_refine_jog_sync(
    point_idx: int,
    initial_x: float,
    initial_y: float,
    detect_window_ms: int,
    jog_step_mm: float,
    jog_feed: int,
) -> Dict[str, Any]:
    """
    Refine flow aligned to cnc_run.py behavior:
    1) Try detect pad for detect_window_ms.
    2) If detected but offset exists, jog iteratively with no timeout until aligned.
    3) If no detection in detect window, skip point.
    """
    result: Dict[str, Any] = {
        "status": "skipped",
        "steps": 0,
        "total_dx": 0.0,
        "total_dy": 0.0,
        "last_refine_status": None,
        "last_dx": 0.0,
        "last_dy": 0.0,
        "last_pixel_dx": None,
        "last_pixel_dy": None,
        "last_confidence": None,
        "final_x": float(initial_x),
        "final_y": float(initial_y),
    }

    if not REFINE_ENABLED:
        result["status"] = "disabled"
        return result
    if not (cnc_controller and cnc_controller.is_connected and preview_camera and detector and transformer):
        result["status"] = "not_ready"
        return result

    detect_window_sec = max(0.0, float(detect_window_ms) / 1000.0)
    if detect_window_sec <= 0.0:
        result["status"] = "detect_window_zero"
        return result

    steps_done = 0
    total_dx = 0.0
    total_dy = 0.0
    last_refine_status = None
    t_detect_deadline = perf_counter() + detect_window_sec
    detected_once = False

    # Phase 1: limited detection window.
    while perf_counter() < t_detect_deadline:
        if stop_event.is_set():
            result["status"] = "stopped"
            return result

        status_now = cnc_controller.query_status_once(1.0)
        pos = status_now.get("position", {})
        cur_x = float(pos.get("x", initial_x))
        cur_y = float(pos.get("y", initial_y))

        refine_result = _runtime_refine_point_sync(cur_x, cur_y)
        dx = float(refine_result.get("delta_x", 0.0))
        dy = float(refine_result.get("delta_y", 0.0))
        conf = refine_result.get("confidence")
        pixel_dx = refine_result.get("pixel_dx")
        pixel_dy = refine_result.get("pixel_dy")
        applied = bool(refine_result.get("applied", False))
        refine_status = str(refine_result.get("status", "unknown"))
        last_refine_status = refine_status
        result["last_dx"] = dx
        result["last_dy"] = dy
        result["last_pixel_dx"] = float(pixel_dx) if pixel_dx is not None else None
        result["last_pixel_dy"] = float(pixel_dy) if pixel_dy is not None else None
        result["last_confidence"] = float(conf) if conf is not None else None

        system_state["last_refine"] = {
            "point": point_idx,
            "status": refine_status,
            "delta_x": dx,
            "delta_y": dy,
            "pixel_dx": result["last_pixel_dx"],
            "pixel_dy": result["last_pixel_dy"],
            "confidence": result["last_confidence"],
        }

        if refine_status not in (
            "skipped_no_detection",
            "skipped_no_candidate",
            "skipped_out_of_radius",
            "skipped_preview_no_frame",
        ):
            detected_once = True
            break

        sleep_s = max(0.0, DRILL_REFINE_LOOP_SLEEP_MS / 1000.0)
        if sleep_s > 0:
            time.sleep(sleep_s)

    if not detected_once:
        result["status"] = "no_detection_window"
        result["last_refine_status"] = last_refine_status
        return result

    # Phase 2: no time limit, keep jogging until aligned or stopped.
    while True:
        if stop_event.is_set():
            result["status"] = "stopped"
            return result

        status_now = cnc_controller.query_status_once(1.0)
        pos = status_now.get("position", {})
        cur_x = float(pos.get("x", initial_x))
        cur_y = float(pos.get("y", initial_y))

        refine_result = _runtime_refine_point_sync(cur_x, cur_y)
        dx = float(refine_result.get("delta_x", 0.0))
        dy = float(refine_result.get("delta_y", 0.0))
        conf = refine_result.get("confidence")
        pixel_dx = refine_result.get("pixel_dx")
        pixel_dy = refine_result.get("pixel_dy")
        applied = bool(refine_result.get("applied", False))
        refine_status = str(refine_result.get("status", "unknown"))
        last_refine_status = refine_status
        result["last_dx"] = dx
        result["last_dy"] = dy
        result["last_pixel_dx"] = float(pixel_dx) if pixel_dx is not None else None
        result["last_pixel_dy"] = float(pixel_dy) if pixel_dy is not None else None
        result["last_confidence"] = float(conf) if conf is not None else None

        system_state["last_refine"] = {
            "point": point_idx,
            "status": refine_status,
            "delta_x": dx,
            "delta_y": dy,
            "pixel_dx": result["last_pixel_dx"],
            "pixel_dy": result["last_pixel_dy"],
            "confidence": result["last_confidence"],
        }

        pixel_ok = (
            result["last_pixel_dx"] is not None
            and result["last_pixel_dy"] is not None
            and abs(float(result["last_pixel_dx"])) <= DRILL_REFINE_PIXEL_TOLERANCE
            and abs(float(result["last_pixel_dy"])) <= DRILL_REFINE_PIXEL_TOLERANCE
        )
        if pixel_ok:
            result["status"] = "aligned"
            break

        if not applied:
            # keep trying (no timeout), as requested.
            sleep_s = max(0.0, DRILL_REFINE_LOOP_SLEEP_MS / 1000.0)
            if sleep_s > 0:
                time.sleep(sleep_s)
            continue

        step = max(0.001, abs(float(jog_step_mm)))
        jog_dx = 0.0 if abs(dx) <= REFINE_TOL_X_MM else (step if dx > 0 else -step)
        jog_dy = 0.0 if abs(dy) <= REFINE_TOL_Y_MM else (step if dy > 0 else -step)
        ok_jog = cnc_controller.jog_relative(jog_dx, jog_dy, 0.0, jog_feed, True, 30.0)
        if not ok_jog:
            result["status"] = "jog_failed"
            break

        steps_done += 1
        total_dx += jog_dx
        total_dy += jog_dy
        _log_job_event(
            "refine_jog",
            point=point_idx,
            step=steps_done,
            dx=round(jog_dx, 4),
            dy=round(jog_dy, 4),
            status=refine_status,
        )
        sleep_s = max(0.0, DRILL_REFINE_LOOP_SLEEP_MS / 1000.0)
        if sleep_s > 0:
            time.sleep(sleep_s)

    status_final = cnc_controller.query_status_once(1.0)
    pos_final = status_final.get("position", {})
    result["steps"] = steps_done
    result["total_dx"] = round(total_dx, 6)
    result["total_dy"] = round(total_dy, 6)
    result["last_refine_status"] = last_refine_status
    result["final_x"] = float(pos_final.get("x", initial_x))
    result["final_y"] = float(pos_final.get("y", initial_y))
    if result["status"] == "skipped":
        result["status"] = "timeout" if steps_done > 0 else (last_refine_status or "timeout")
    return result


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


def _load_work_points() -> List[Tuple[float, float]]:
    if not WORK_POINTS_PATH.exists():
        return []
    try:
        with open(WORK_POINTS_PATH, "r") as f:
            data = json.load(f)
        points = data.get("points", [])
        if not isinstance(points, list):
            return []
        normalized: List[Tuple[float, float]] = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            normalized.append((float(point[0]), float(point[1])))
        return normalized
    except Exception:
        return []


def _load_points_from_mapping_gcode() -> List[Tuple[float, float]]:
    """
    Load XY drill points from mapping_output.gcode.
    Expected pattern per point: motion line containing both X and Y.
    """
    if not MAPPING_GCODE_PATH.exists():
        return []

    points: List[Tuple[float, float]] = []
    try:
        lines = MAPPING_GCODE_PATH.read_text(encoding="utf-8").splitlines()
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if line.startswith((";", "(", "%")):
                continue

            up = line.upper()
            if ("X" not in up) or ("Y" not in up):
                continue

            # Accept legacy and current formats:
            # - G0 X.. Y..
            # - G1 X.. Y..
            # - G90 X.. Y..
            if ("G0" not in up) and ("G1" not in up) and ("G90" not in up):
                continue

            mx = re.search(r"[Xx]\s*([-+]?\d*\.?\d+)", line)
            my = re.search(r"[Yy]\s*([-+]?\d*\.?\d+)", line)
            if not (mx and my):
                continue
            points.append((float(mx.group(1)), float(my.group(1))))
        return points
    except Exception as e:
        logger.warning(f"Failed parse mapping gcode: {e}")
        return []


def _load_drill_gcode_xy_lines() -> List[Tuple[str, float, float]]:
    """
    Load executable per-point G-code lines (with X and Y) from mapping_output.gcode.
    Returns list of (raw_line, x, y).
    """
    if not MAPPING_GCODE_PATH.exists():
        return []

    rows: List[Tuple[str, float, float]] = []
    try:
        lines = MAPPING_GCODE_PATH.read_text(encoding="utf-8").splitlines()
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if line.startswith((";", "(", "%")):
                continue

            up = line.upper()
            if ("X" not in up) or ("Y" not in up):
                continue
            if ("G0" not in up) and ("G1" not in up) and ("G90" not in up):
                continue

            mx = re.search(r"[Xx]\s*([-+]?\d*\.?\d+)", line)
            my = re.search(r"[Yy]\s*([-+]?\d*\.?\d+)", line)
            if not (mx and my):
                continue
            rows.append((line, float(mx.group(1)), float(my.group(1))))
        return rows
    except Exception as e:
        logger.warning(f"Failed parse mapping gcode lines: {e}")
        return []


def _run_gcode_line_sync(command: str, timeout: float = 30.0) -> bool:
    """Queue one G-code line and wait until controller returns Idle."""
    if not (cnc_controller and cnc_controller.is_connected):
        return False
    try:
        cnc_controller.queue_command(command)
        return cnc_controller.wait_until_idle(timeout=timeout)
    except Exception as e:
        logger.warning(f"Run gcode line failed ({command}): {e}")
        return False


def move_to_alignment_point_sync(which: str) -> bool:
    """
    Move to first/last work point with safe clearance Z.
    `which` must be 'first' or 'last'.
    """
    if not (cnc_controller and cnc_controller.is_connected):
        return False

    points = _load_work_points()
    if not points:
        return False

    target = points[0] if which == "first" else points[-1]
    tx, ty, clipped = _apply_soft_limit_xy(target[0], target[1])
    if clipped:
        system_state["last_warning"] = f"Alignment target ({which}) clipped by workspace soft-limit"

    z_ref = _get_calibrated_z_reference()
    clearance_z = (float(z_ref) + ALIGN_CLEARANCE_MM) if z_ref is not None else ALIGN_CLEARANCE_MM

    ok_up = cnc_controller.move_to(None, None, clearance_z, Z_MOVE_FEED, True, 30.0)
    ok_xy = cnc_controller.move_to(tx, ty, None, XY_MOVE_FEED, True, 30.0)
    if not (ok_up and ok_xy):
        return False

    status_now = cnc_controller.query_status_once(1.0)
    pos = status_now.get("position", {})
    system_state["position"] = {
        "x": float(pos.get("x", tx)),
        "y": float(pos.get("y", ty)),
        "z": float(pos.get("z", clearance_z)),
    }
    system_state["alignment_target"] = {"which": which, "x": float(tx), "y": float(ty)}
    return True


def _sync_position_from_cnc() -> None:
    """Sync system_state position from actual GRBL reported coordinates."""
    if not (cnc_controller and cnc_controller.is_connected):
        return
    try:
        status_now = cnc_controller.query_status_once(1.0)
        pos = status_now.get("position", {})
        system_state["position"] = {
            "x": float(pos.get("x", 0.0)),
            "y": float(pos.get("y", 0.0)),
            "z": float(pos.get("z", 0.0)),
        }
    except Exception as e:
        logger.warning(f"Failed sync real CNC position: {e}")


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
        "refine_camera_connected": bool(preview_camera is not None),
        "refine_camera_frame_ok": False,
        "workspace_bounds": _resolve_workspace_bounds(),
    }

    if camera is not None:
        try:
            frame = camera.get_frame()
            checks["camera_frame_ok"] = frame is not None
        except Exception:
            checks["camera_frame_ok"] = False

    if preview_camera is not None:
        try:
            frame = preview_camera.get_frame()
            checks["refine_camera_frame_ok"] = frame is not None
        except Exception:
            checks["refine_camera_frame_ok"] = False

    refine_ready = True
    if REFINE_ENABLED:
        refine_ready = checks["refine_camera_connected"] and checks["refine_camera_frame_ok"]

    checks["ok"] = (
        checks["model_exists"]
        and checks["calibration_exists"]
        and checks["transformer_ready"]
        and checks["cnc_connected"]
        and checks["camera_connected"]
        and checks["camera_frame_ok"]
        and refine_ready
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
    system_state["camera_source"] = _normalize_camera_source(CAMERA_MAIN_SOURCE)
    system_state["preview_camera_source"] = _normalize_camera_source(CAMERA_PREVIEW_SOURCE)
    system_state["refine_profile"] = REFINE_PROFILE
    system_state["refine_profile_override"] = REFINE_PROFILE_OVERRIDE
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
        main_source = _normalize_camera_source(CAMERA_MAIN_SOURCE)
        camera = CameraCapture(
            camera_source=main_source,
            width=CAMERA_WIDTH,
            height=CAMERA_HEIGHT,
            fps=CAMERA_FPS,
        )
        camera.connect()
        camera.start_streaming()
        system_state["camera_source"] = main_source
        system_state["camera_connected"] = True
        logger.info("Camera streaming started")
    except Exception as e:
        logger.warning(f"Camera init warning: {e}")
        camera = None
        system_state["camera_connected"] = False

    # Preview-only camera starts disconnected by default; user can connect from UI.
    preview_camera = None
    system_state["preview_camera_connected"] = False
    system_state["preview_camera_source"] = _normalize_camera_source(CAMERA_PREVIEW_SOURCE)
    
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

        # START click #2: capture → mapping only (no drilling).
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
        work_data = _read_json_file(WORK_POINTS_PATH, {})
        drill_points = work_data.get("points", [])
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

        mapping_gcode_path = await asyncio.to_thread(_save_mapping_gcode, drill_points, ref_z)
        if mapping_gcode_path:
            system_state["last_mapping_gcode"] = mapping_gcode_path
            _log_job_event("mapping_gcode_saved", path=mapping_gcode_path, points=len(drill_points))
        else:
            system_state["last_mapping_gcode"] = None
            _log_job_event("mapping_gcode_failed")
        
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

        system_state["status"] = "MAPPING_DONE"
        system_state["start_state"] = "mapped_ready"
        system_state["progress"] = {"current": len(drill_points), "total": len(drill_points)}
        _record_metric(metrics, "total_ms", t_total)
        system_state["last_metrics"] = metrics
        _log_job_event("mapping_complete", points=len(drill_points))
        _log_job_event("metrics", **metrics)
        _set_last_job_summary("mapped", points=len(drill_points), metrics=metrics)
        await broadcast_state()
        return

    except Exception as e:
        logger.exception("Workflow failed")
        system_state["status"] = "ERROR"
        system_state["refine_overlay_target"] = None
        _set_error("SYSTEM_ERROR", str(e))
        system_state["start_state"] = "idle"
        _log_job_event("job_failed", reason="exception", detail=str(e))
        _set_last_job_summary("failed")
        await broadcast_state()


async def continue_drill_workflow():
    """Refine-drill using points parsed from mapping_output.gcode."""
    global pending_drill_points, current_job_id
    try:
        t_total = perf_counter()
        metrics: Dict[str, float] = {}
        if not current_job_id:
            current_job_id = str(uuid4())[:8]
            system_state["job_id"] = current_job_id
            _init_job_telemetry(current_job_id)
        _log_job_event("refine_drill_started")
        await asyncio.to_thread(_reset_gagaldrill_file)

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

        # Load drill points from mapping_output.gcode
        if not MAPPING_GCODE_PATH.exists():
            system_state["status"] = "NO_POINTS"
            _set_error("NO_POINTS", "No mapping gcode file")
            await broadcast_state()
            return

        gcode_rows = await asyncio.to_thread(_load_drill_gcode_xy_lines)
        z_drill_depth = float(_get_cfg("drill.z_depth", 1.5))
        z_drill_feed = int(_get_cfg("drill.z_drill_feed", 300))
        xy_feed = int(_get_cfg("drill.xy_move_feed", 1000))

        if not gcode_rows:
            system_state["status"] = "NO_POINTS"
            _set_error("NO_POINTS", "Mapping gcode has no XY drill points")
            _log_job_event("job_aborted", reason="mapping_gcode_empty")
            await broadcast_state()
            return

        clipped_count = 0
        safe_rows: List[Tuple[str, float, float]] = []
        for raw_line, raw_x, raw_y in gcode_rows:
            sx, sy, clipped = _apply_soft_limit_xy(float(raw_x), float(raw_y))
            if clipped:
                clipped_count += 1
            safe_rows.append((f"G90 X{sx:.4f} Y{sy:.4f}", sx, sy))
        gcode_rows = safe_rows
        if clipped_count > 0:
            system_state["last_warning"] = f"{clipped_count} point(s) clipped by workspace soft-limit"
            _log_job_event("soft_limit_clipped", points=clipped_count)

        t = perf_counter()
        job = await asyncio.to_thread(job_manager.create_job, [(x, y) for _, x, y in gcode_rows], False)
        _record_metric(metrics, "path_plan_ms", t)
        dynamic_xy_feed = _dynamic_xy_feed(len(gcode_rows))
        system_state["progress"] = {"current": 0, "total": len(gcode_rows)}
        system_state["status"] = "DRILLING"
        system_state["start_state"] = "drilling"
        await broadcast_state()

        # Align with refine/cnc_run.py: set absolute mode + XY feed once.
        ok_setup_mode = await _retry_async(_run_gcode_line_sync, RETRY_MOVE, "drill_gcode_setup_g90", "G90", 10.0)
        ok_setup_feed = await _retry_async(_run_gcode_line_sync, RETRY_MOVE, "drill_gcode_setup_feed", f"G1 F{max(1, dynamic_xy_feed if dynamic_xy_feed > 0 else xy_feed)}", 10.0)
        if not (ok_setup_mode and ok_setup_feed):
            system_state["status"] = "ERROR"
            _set_error("MOTION_FAIL", "Failed setup G-code mode/feed before drill")
            await broadcast_state()
            return

        t_drill = perf_counter()
        for i, (xy_line, safe_x, safe_y) in enumerate(gcode_rows):
            if stop_event.is_set():
                system_state["status"] = "STOPPED"
                system_state["start_state"] = "idle"
                system_state["refine_overlay_target"] = None
                pending_drill_points = []
                jog_offset["x"] = 0.0
                jog_offset["y"] = 0.0
                jog_offset["z"] = 0.0
                _set_error("STOPPED", f"Stopped at point {i + 1}")
                _log_job_event("job_stopped", phase="continue_drill", point=i + 1)
                await broadcast_state()
                return

            # Match refine/cnc_run.py: execute XY G-code line first.
            ok_xy = await _retry_async(_run_gcode_line_sync, RETRY_MOVE, "continue_gcode_xy", xy_line, 30.0)
            if not ok_xy:
                system_state["status"] = "ERROR"
                system_state["refine_overlay_target"] = None
                _set_error("MOTION_FAIL", f"G-code XY failed at point {i + 1}")
                await asyncio.to_thread(_append_gagaldrill_line, xy_line)
                _log_job_event("job_failed", reason="gcode_xy_failed", point=i + 1, line=xy_line)
                await broadcast_state()
                return

            if REFINE_ENABLED:
                system_state["status"] = "DRILL_SETTLING"
                system_state["refine_overlay_target"] = {
                    "point": i + 1,
                    "x": safe_x,
                    "y": safe_y,
                }
                await broadcast_state()
                _log_job_event("refine_start", point=i + 1, dwell_ms=DRILL_REFINE_DWELL_MS)
                refine_loop_result = await asyncio.to_thread(
                    auto_refine_jog_sync,
                    i + 1,
                    safe_x,
                    safe_y,
                    DRILL_REFINE_DETECT_WINDOW_MS,
                    DRILL_REFINE_JOG_STEP_MM,
                    DRILL_REFINE_JOG_FEED,
                )
                _log_job_event(
                    "refine_done",
                    point=i + 1,
                    status=refine_loop_result.get("status"),
                    steps=refine_loop_result.get("steps"),
                    total_dx=refine_loop_result.get("total_dx"),
                    total_dy=refine_loop_result.get("total_dy"),
                )
                if refine_loop_result.get("status") in ("jog_failed", "stopped"):
                    system_state["status"] = "ERROR" if refine_loop_result.get("status") == "jog_failed" else "STOPPED"
                    system_state["start_state"] = "idle"
                    system_state["refine_overlay_target"] = None
                    _set_error("REFINE_FAIL", f"Refine failed at point {i + 1}: {refine_loop_result.get('status')}")
                    await broadcast_state()
                    return

                last_dx = float(refine_loop_result.get("last_dx", 0.0))
                last_dy = float(refine_loop_result.get("last_dy", 0.0))
                last_conf = refine_loop_result.get("last_confidence")
                conf_ok = (last_conf is not None) and (float(last_conf) > REFINE_ACCEPT_CONFIDENCE)
                center_ok = str(refine_loop_result.get("status")) == "aligned"
                if not (conf_ok and center_ok):
                    await asyncio.to_thread(_append_gagaldrill_line, xy_line)
                    _log_job_event("point_skipped_refine_not_valid", point=i + 1, line=xy_line)
                    system_state["last_warning"] = f"Point {i + 1} skipped: refine not valid"
                    job.mark_drilled(i)
                    system_state["progress"] = {"current": i + 1, "total": len(gcode_rows)}
                    system_state["status"] = "DRILLING"
                    await broadcast_state()
                    continue
                system_state["status"] = "DRILLING"
                await broadcast_state()

            # Match refine/cnc_run.py: drill relative down then up.
            ok_down = await _retry_async(
                cnc_controller.jog_relative, RETRY_MOVE, "continue_jog_z_down", 0.0, 0.0, -abs(z_drill_depth), z_drill_feed, True, 30.0
            )
            ok_up = await _retry_async(
                cnc_controller.jog_relative, RETRY_MOVE, "continue_jog_z_up", 0.0, 0.0, abs(z_drill_depth), z_drill_feed, True, 30.0
            )

            if not (ok_down and ok_up):
                system_state["status"] = "ERROR"
                system_state["refine_overlay_target"] = None
                _set_error("MOTION_FAIL", f"Drill Z failed at point {i + 1}")
                await asyncio.to_thread(_append_gagaldrill_line, xy_line)
                _log_job_event("job_failed", reason="drill_z_failed", point=i + 1, line=xy_line)
                await broadcast_state()
                return

            job.mark_drilled(i)
            _log_job_event("point_drilled", point=i + 1, total=len(gcode_rows), line=xy_line)
            system_state["progress"] = {"current": i + 1, "total": len(gcode_rows)}
            await broadcast_state()

        _record_metric(metrics, "drill_loop_ms", t_drill)
        system_state["status"] = "COMPLETE"
        _log_job_event("job_complete", drilled=len(gcode_rows))
        await broadcast_state()

        # End flow: return machine to HOME position
        system_state["status"] = "HOMING"
        await broadcast_state()

        ok_home = await asyncio.to_thread(cnc_controller.home_axis, "XYZ", True, 120.0)
        if ok_home:
            system_state["status"] = "IDLE"
            system_state["last_error"] = None
            system_state["error_code"] = None
        else:
            system_state["status"] = "ERROR"
            _set_error("MOTION_FAIL", "Failed to HOME after drill")
        system_state["start_state"] = "idle"
        system_state["refine_overlay_target"] = None
        pending_drill_points = []
        _record_metric(metrics, "total_ms", t_total)
        system_state["last_metrics"] = metrics
        _log_job_event("metrics", **metrics)
        _set_last_job_summary("complete", points=len(gcode_rows), metrics=metrics)
        await broadcast_state()

    except Exception as e:
        logger.exception("Workflow failed")
        system_state["status"] = "ERROR"
        system_state["refine_overlay_target"] = None
        _set_error("SYSTEM_ERROR", str(e))
        system_state["start_state"] = "idle"
        pending_drill_points = []
        _log_job_event("job_failed", reason="exception", detail=str(e))
        _set_last_job_summary("failed")
        await broadcast_state()


async def run_mapping_only_workflow():
    """
    Mapping-only flow:
    standby -> capture/detect/transform/save outputs -> HOME -> IDLE.
    """
    try:
        if not (cnc_controller and cnc_controller.is_connected):
            system_state["status"] = "NOT_READY"
            _set_error("NOT_READY", "CNC not connected")
            await broadcast_state()
            return

        system_state["status"] = "STANDBY_MOVING"
        system_state["start_state"] = "idle"
        await broadcast_state()

        ok_standby = await asyncio.to_thread(move_to_standby_sync)
        if not ok_standby:
            system_state["status"] = "ERROR"
            _set_error("MOTION_FAIL", "Failed to move standby before mapping")
            await broadcast_state()
            return

        system_state["status"] = "STANDBY_READY"
        system_state["start_state"] = "standby_ready"
        await broadcast_state()

        await run_drill_workflow()
        if system_state.get("status") != "MAPPING_DONE":
            return

        system_state["status"] = "HOMING"
        await broadcast_state()
        homed = await asyncio.to_thread(cnc_controller.home_axis, "XYZ", True, 120.0)
        if homed:
            system_state["status"] = "IDLE"
            system_state["start_state"] = "idle"
            system_state["last_error"] = None
            system_state["error_code"] = None
        else:
            system_state["status"] = "ERROR"
            _set_error("MOTION_FAIL", "Failed to HOME after mapping")
            system_state["start_state"] = "idle"
        await broadcast_state()
    except Exception as e:
        logger.exception("Mapping-only workflow failed")
        system_state["status"] = "ERROR"
        _set_error("SYSTEM_ERROR", str(e))
        system_state["start_state"] = "idle"
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
                elif start_state == "mapped_ready":
                    system_state["status"] = "MAPPING_DONE"
                    system_state["last_warning"] = "Mapping ready. Use REFINE DRILL to execute drilling."
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

            elif cmd == "mapping":
                if workflow_task and not workflow_task.done():
                    system_state["status"] = "BUSY"
                    await broadcast_state()
                    continue
                stop_event.clear()
                workflow_task = asyncio.create_task(run_mapping_only_workflow())
                system_state["status"] = "MAPPING_STARTING"
                await broadcast_state()

            elif cmd == "refine_drill":
                if workflow_task and not workflow_task.done():
                    system_state["status"] = "BUSY"
                    await broadcast_state()
                    continue
                stop_event.clear()
                workflow_task = asyncio.create_task(continue_drill_workflow())
                system_state["status"] = "REFINE_DRILL_STARTING"
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

            elif cmd == "home_z":
                if cnc_controller and cnc_controller.is_connected:
                    system_state["status"] = "HOMING_Z"
                    await broadcast_state()
                    ok_z = await asyncio.to_thread(cnc_controller.move_to, None, None, 0.0, Z_MOVE_FEED, True, 60.0)
                    system_state["status"] = "IDLE" if ok_z else "ERROR"
                    if not ok_z:
                        system_state["last_error"] = "Z-HOME failed or timeout"
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
                            ok = _write_json_file_atomic(
                                CAL_OFFSET_PATH,
                                {
                                    "x": cal_x,
                                    "y": cal_y,
                                    "z": cal_z,
                                },
                            )
                            if not ok:
                                raise RuntimeError("atomic write failed")
                            logger.info(f"Saved cal_offset: X{cal_x:.3f} Y{cal_y:.3f} Z{cal_z:.3f}")
                        except Exception as e:
                            logger.warning(f"Failed to save cal_offset: {e}")

                    system_state["status"] = "HOMING"
                    await broadcast_state()
                    ok_home = await asyncio.to_thread(cnc_controller.home_axis, "XYZ", True, 120.0)
                    if ok_home:
                        system_state["status"] = "IDLE"
                        system_state["calibrate_state"] = "idle"
                        system_state["last_error"] = None
                    else:
                        system_state["status"] = "ERROR"
                        system_state["last_error"] = "Failed return HOME after calibrate"
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
                raw_source = message.get("source", message.get("index", CAMERA_MAIN_SOURCE))
                if raw_source in (None, ""):
                    raw_source = system_state.get("camera_source", CAMERA_MAIN_SOURCE)
                cam_source = _normalize_camera_source(raw_source)
                if not _is_valid_camera_source(cam_source):
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = f"Invalid main camera source: {raw_source}"
                    await broadcast_state()
                    continue

                preview_source = system_state.get("preview_camera_source")
                if (
                    system_state.get("preview_camera_connected", False)
                    and _camera_source_key(preview_source) == _camera_source_key(cam_source)
                ):
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = "Main camera must use different source from preview camera"
                    await broadcast_state()
                    continue

                system_state["status"] = "CAMERA_CONNECTING"
                await broadcast_state()
                ok_cam = await asyncio.to_thread(connect_camera_sync, cam_source)
                if ok_cam:
                    system_state["status"] = "IDLE"
                    system_state["last_error"] = None
                else:
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = f"Camera connect failed source {cam_source}"
                await broadcast_state()

            elif cmd == "preview_camera_connect":
                raw_source = message.get("source", message.get("index", CAMERA_PREVIEW_SOURCE))
                if raw_source in (None, ""):
                    raw_source = system_state.get("preview_camera_source", CAMERA_PREVIEW_SOURCE)
                cam_source = _normalize_camera_source(raw_source)
                if not _is_valid_camera_source(cam_source):
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = f"Invalid preview camera source: {raw_source}"
                    await broadcast_state()
                    continue

                main_source = system_state.get("camera_source")
                if (
                    system_state.get("camera_connected", False)
                    and _camera_source_key(main_source) == _camera_source_key(cam_source)
                ):
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = "Preview camera must use different source from main camera"
                    await broadcast_state()
                    continue

                system_state["status"] = "CAMERA_CONNECTING"
                await broadcast_state()
                ok_cam = await asyncio.to_thread(connect_preview_camera_sync, cam_source)
                if ok_cam:
                    system_state["status"] = "IDLE"
                    system_state["last_error"] = None
                else:
                    system_state["status"] = "ERROR"
                    system_state["last_error"] = f"Preview camera connect failed source {cam_source}"
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
    except RuntimeError as e:
        # Starlette may raise RuntimeError when socket is already closed.
        if "WebSocket is not connected" in str(e):
            logger.info("WebSocket disconnected before receive loop completed")
        else:
            logger.exception(f"WebSocket runtime error: {e}")
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)

async def broadcast_state():
    """Broadcast state to all connected WebSocket clients"""
    await asyncio.to_thread(_sync_position_from_cnc)
    dead_clients: List[WebSocket] = []
    for client in list(connected_clients):
        try:
            await client.send_json(system_state)
        except Exception as e:
            logger.error(f"Broadcast error: {e}")
            dead_clients.append(client)
    for client in dead_clients:
        if client in connected_clients:
            connected_clients.remove(client)

# ==================== API Endpoints ====================

@app.get("/")
async def root():
    """Root HTML page"""
    try:
        html_content = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load dashboard template: {e}")
    return HTMLResponse(html_content)


@app.get("/mapping")
async def mapping_page():
    """Dedicated page for Mapping + Calibrate."""
    try:
        html_content = MAPPING_DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load mapping template: {e}")
    return HTMLResponse(html_content)


@app.get("/drill")
async def drill_page():
    """Dedicated page for refine-drill execution from mapping_output.gcode."""
    try:
        html_content = DRILL_DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load drill template: {e}")
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
    """MJPEG preview stream with refine ROI + pad detection overlay."""
    def generate_frames():
        while True:
            try:
                if preview_camera:
                    frame = preview_camera.get_frame()
                    if frame is not None:
                        frame_vis = _draw_preview_refine_overlay(frame)
                        _, buffer = cv2.imencode('.jpg', frame_vis)
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


@app.post("/api/drill/start")
async def api_drill_start():
    """Start drill-only workflow using config/mapping_output.gcode."""
    global workflow_task
    if workflow_task and not workflow_task.done():
        raise HTTPException(status_code=409, detail="Workflow already running")

    # Ensure refine preview camera is connected for /drill flow.
    if preview_camera is None:
        preferred_source = _normalize_camera_source(system_state.get("preview_camera_source", CAMERA_PREVIEW_SOURCE))
        if not _is_valid_camera_source(preferred_source):
            preferred_source = _normalize_camera_source(CAMERA_PREVIEW_SOURCE)
        if (
            system_state.get("camera_connected", False)
            and _camera_source_key(system_state.get("camera_source")) == _camera_source_key(preferred_source)
        ):
            preferred_source = _normalize_camera_source(2)
        ok_preview = await asyncio.to_thread(connect_preview_camera_sync, preferred_source)
        if not ok_preview:
            fallback_source = _normalize_camera_source(2)
            ok_preview = await asyncio.to_thread(connect_preview_camera_sync, fallback_source)
        if not ok_preview:
            raise HTTPException(status_code=500, detail="Preview camera connect failed for drill mode")

    stop_event.clear()
    system_state["status"] = "REFINE_DRILL_STARTING"
    system_state["start_state"] = "drilling"
    await broadcast_state()
    workflow_task = asyncio.create_task(continue_drill_workflow())
    return {"ok": True, "status": "started"}


@app.post("/api/drill/stop")
async def api_drill_stop():
    """Stop active drill-only workflow."""
    stop_event.set()
    if cnc_controller:
        await asyncio.to_thread(cnc_controller.emergency_stop)
    system_state["status"] = "STOPPED"
    system_state["start_state"] = "idle"
    await broadcast_state()
    return {"ok": True, "status": "stopped"}


class DrillCameraConnectRequest(BaseModel):
    source: Any


@app.post("/api/drill/preview/connect")
async def api_drill_preview_connect(payload: DrillCameraConnectRequest):
    """
    Connect /drill preview camera to selected source (index or /dev/v4l path).
    """
    source = _normalize_camera_source(payload.source)
    if not _is_valid_camera_source(source):
        raise HTTPException(status_code=400, detail=f"Invalid preview source: {payload.source}")

    if (
        system_state.get("camera_connected", False)
        and _camera_source_key(system_state.get("camera_source")) == _camera_source_key(source)
    ):
        raise HTTPException(status_code=400, detail="Preview camera must be different from main camera")

    ok = await asyncio.to_thread(connect_preview_camera_sync, source)
    if not ok:
        raise HTTPException(status_code=500, detail=f"Failed to connect preview camera source: {source}")

    system_state["status"] = "IDLE"
    system_state["last_error"] = None
    await broadcast_state()
    return {"ok": True, "preview_source": system_state.get("preview_camera_source")}


@app.get("/api/drill/settings")
async def api_drill_settings():
    """Get current drill page runtime settings."""
    return {
        "refine_enabled": REFINE_ENABLED,
        "refine_dwell_ms": DRILL_REFINE_DWELL_MS,
        "refine_max_steps": DRILL_REFINE_MAX_STEPS,
        "refine_jog_feed": DRILL_REFINE_JOG_FEED,
        "refine_jog_step_mm": DRILL_REFINE_JOG_STEP_MM,
        "refine_pixel_tolerance": DRILL_REFINE_PIXEL_TOLERANCE,
        "refine_loop_sleep_ms": DRILL_REFINE_LOOP_SLEEP_MS,
        "refine_detect_window_ms": DRILL_REFINE_DETECT_WINDOW_MS,
        "refine_accept_confidence": REFINE_ACCEPT_CONFIDENCE,
        "mapping_gcode_path": str(MAPPING_GCODE_PATH),
    }

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


@app.get("/api/camera/sources")
async def get_camera_sources():
    """List available camera sources (prefer /dev/v4l/by-id)."""
    return {
        "sources": await asyncio.to_thread(_list_camera_sources),
        "main_source": system_state.get("camera_source"),
        "preview_source": system_state.get("preview_camera_source"),
    }

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
