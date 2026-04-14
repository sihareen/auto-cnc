"""
FastAPI Web Server for Auto CNC Dashboard
"""
import asyncio
import logging
import threading
import json
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
    "execution_state": 0,
    "calibrate_state": "idle",
    "start_state": "idle"
}

# WebSocket connections
connected_clients: List[WebSocket] = []
workflow_task: Optional[asyncio.Task] = None
stop_event = threading.Event()

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

STANDBY_X = 85.0
STANDBY_Y = -95.0
STANDBY_Z = 0.0
TEMP_DIR = Path("temp")
JOB_OVERLAY_IMAGE_PATH = TEMP_DIR / "overlay.jpg"
CALIBRATE_IMAGE_PATH = TEMP_DIR / "calibrate.jpg"
LAST_JOB_POINTS_PATH = Path("config/last_job_points.json")
WORK_POINTS_PATH = Path("config/work_points.json")
CALIB_OFFSET_PATH = Path("config/calibration_runtime_offset.json")
CALIB_OFFSET_X = 0.0
CALIB_OFFSET_Y = 0.0
DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "templates" / "dashboard.html"

# Expose temp artifacts (e.g., overlay.jpg) for dashboard preview cards.
TEMP_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/temp", StaticFiles(directory=str(TEMP_DIR)), name="temp")


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


def _save_work_points():
    """Persist work points (pending + jog offset) to config/work_points.json."""
    try:
        WORK_POINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        work_pts = [
            (px + jog_offset["x"], py + jog_offset["y"])
            for px, py in pending_drill_points
        ]
        with open(WORK_POINTS_PATH, "w") as f:
            json.dump({
                "points": work_pts,
                "jog_offset": dict(jog_offset)
            }, f, indent=2)
        logger.info(f"Saved {len(work_pts)} work points with offset {jog_offset}")
    except Exception as e:
        logger.warning(f"Failed to save work points: {e}")


def _apply_runtime_offset(x: float, y: float) -> Tuple[float, float]:
    """Apply runtime correction offset to machine coordinates."""
    return float(x + CALIB_OFFSET_X), float(y + CALIB_OFFSET_Y)


def _apply_runtime_offset_points(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Apply runtime correction offset to list of machine coordinates."""
    return [_apply_runtime_offset(x, y) for x, y in points]


def connect_camera_sync(camera_index: int) -> bool:
    """Reconnect camera using user-selected index."""
    global camera
    try:
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
    """Move CNC to standby coordinate safely (raise Z first)."""
    if not (cnc_controller and cnc_controller.is_connected):
        return False

    ok_up = cnc_controller.move_to(None, None, STANDBY_Z, 1000, True, 30.0)
    ok_xy = cnc_controller.move_to(STANDBY_X, STANDBY_Y, None, 1000, True, 30.0)
    if not (ok_up and ok_xy):
        return False

    system_state["position"] = {
        "x": STANDBY_X,
        "y": STANDBY_Y,
        "z": STANDBY_Z,
    }
    return True


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
    min_confidence: float = 0.25,
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
        system_state["last_error"] = "Camera/detector/transformer not ready"
        return False

    if not (cnc_controller and cnc_controller.is_connected):
        system_state["status"] = "NOT_READY"
        system_state["last_error"] = "CNC not connected"
        return False

    # 1) Move to standby first.
    ok_standby = await asyncio.to_thread(move_to_standby_sync)
    if not ok_standby:
        system_state["status"] = "ERROR"
        system_state["last_error"] = "Failed to move standby before calibrate"
        return False

    # 2) Capture and detect.
    frame = await asyncio.to_thread(camera.get_frame)
    if frame is None:
        system_state["status"] = "NO_FRAME"
        system_state["last_error"] = "No camera frame for calibrate"
        return False

    detections = await asyncio.to_thread(detector.detect, frame)
    if not detections:
        system_state["status"] = "NO_POINTS"
        system_state["last_error"] = "No detection found for calibrate"
        return False

    # 3) Keep one pad-hole.
    selected = _select_single_calibration_detection(detections)
    if selected is None:
        system_state["status"] = "NO_POINTS"
        system_state["last_error"] = "No valid padhole detection"
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
        system_state["last_error"] = "Transform failed for calibrate point"
        return False

    target_x, target_y = _apply_runtime_offset(machine_coords[0][0], machine_coords[0][1])

    # 4) Save temp/calibrate.jpg.
    try:
        path = await asyncio.to_thread(_save_calibrate_image, frame, selected, (target_x, target_y))
        system_state["calibrate_image"] = path
    except Exception as e:
        logger.warning(f"Failed to save calibrate image: {e}")

    # 5) Move CNC to processed coordinate.
    ok_up = await asyncio.to_thread(cnc_controller.move_to, None, None, STANDBY_Z, 1000, True, 30.0)
    ok_xy = await asyncio.to_thread(cnc_controller.move_to, target_x, target_y, None, 1000, True, 30.0)
    if not (ok_up and ok_xy):
        system_state["status"] = "ERROR"
        system_state["last_error"] = "CNC move failed for calibrate target"
        return False

    system_state["position"] = {"x": float(target_x), "y": float(target_y), "z": STANDBY_Z}
    system_state["status"] = "CALIBRATE_DONE"
    system_state["calibrate_target"] = {"x": float(target_x), "y": float(target_y)}
    system_state["last_error"] = None
    return True

def init_components():
    """Initialize system components"""
    global camera, preview_camera, detector, cnc_controller, job_manager, executor, transformer

    _load_runtime_offset()
    system_state["calibrate_offset"] = {"x": CALIB_OFFSET_X, "y": CALIB_OFFSET_Y}
    
    try:
        from src.vision.transformer import AffineTransformer
        transformer = AffineTransformer("config/calibration_affine.json")
        transformer.load_calibration()
        
    except Exception as e:
        logger.warning(f"Transformer init warning: {e}")
        transformer = None
    
    try:
        from src.cnc.controller import GRBLController
        cnc_controller = GRBLController()
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
        camera = CameraCapture(camera_index=4)
        camera.connect()
        camera.start_streaming()
        system_state["camera_index"] = 4
        system_state["camera_connected"] = True
        logger.info("Camera streaming started")
    except Exception as e:
        logger.warning(f"Camera init warning: {e}")
        camera = None
        system_state["camera_connected"] = False

    # Preview-only camera starts disconnected by default; user can connect from UI.
    preview_camera = None
    system_state["preview_camera_connected"] = False
    system_state["preview_camera_index"] = 0
    
    try:
        from src.vision.detector import YOLODetector
        detector = YOLODetector(model_path="best.pt", confidence_threshold=0.25, iou_threshold=0.45)
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

    logger.info("Components initialized (some may be None)")

init_components()

async def run_drill_workflow():
    """Run acquire-detect-transform and pause at first drill point."""
    try:
        if not (camera and detector and transformer):
            system_state["status"] = "NOT_READY"
            await broadcast_state()
            return

        # START click #2 begins acquisition manually after standby-ready phase.
        system_state["start_state"] = "capturing"

        system_state["status"] = "ACQUIRING"
        await broadcast_state()

        frame = await asyncio.to_thread(camera.get_frame)
        if frame is None:
            system_state["status"] = "NO_FRAME"
            await broadcast_state()
            return

        detections = await asyncio.to_thread(detector.detect, frame)
        system_state["last_detections"] = len(detections)

        pixel_points = [
            ((d.bbox[0] + d.bbox[2]) / 2, (d.bbox[1] + d.bbox[3]) / 2, d.confidence)
            for d in detections
        ]
        machine_coords = await asyncio.to_thread(
            transformer.transform_detections, pixel_points, 0.25
        )
        machine_coords = _apply_runtime_offset_points(machine_coords)

        if not (machine_coords and job_manager):
            system_state["status"] = "NO_POINTS"
            await broadcast_state()
            return

        job = await asyncio.to_thread(job_manager.create_job, machine_coords, True)
        system_state["progress"] = {"current": 0, "total": len(job.points)}
        system_state["status"] = "TRANSFORM"
        await broadcast_state()

        if not (cnc_controller and cnc_controller.is_connected):
            system_state["status"] = "SIMULATE"
            system_state["start_state"] = "idle"
            await broadcast_state()
            return

        if stop_event.is_set():
            system_state["status"] = "STOPPED"
            system_state["start_state"] = "idle"
            await broadcast_state()
            return

        # START phase-1: stop at first padhole, wait second START to continue drilling.
        global pending_drill_points
        pending_drill_points = [(float(p.x), float(p.y)) for p in job.points]
        _save_last_job_points()
        first_x, first_y = pending_drill_points[0]
        ok_first = await asyncio.to_thread(cnc_controller.move_to, first_x, first_y, 5.0, 1000, True, 30.0)
        if not ok_first:
            system_state["status"] = "ERROR"
            system_state["last_error"] = "Failed to move first padhole"
            await broadcast_state()
            return

        try:
            highlighted_idx = await asyncio.to_thread(
                _find_first_paused_detection_index,
                detections,
                (first_x, first_y),
                0.25,
            )
            overlay_path = await asyncio.to_thread(
                _save_job_overlay_image,
                frame,
                detections,
                highlighted_idx,
            )
            system_state["last_capture_image"] = overlay_path
            system_state["last_detection_image"] = overlay_path
            logger.info(
                f"Saved job overlay image: {overlay_path} (highlight_idx={highlighted_idx})"
            )
        except Exception as e:
            logger.warning(f"Failed to save job overlay image: {e}")

        system_state["position"] = {"x": first_x, "y": first_y, "z": 5.0}
        system_state["status"] = "PAUSED_AT_PADHOLE"
        system_state["start_state"] = "paused_at_point"
        system_state["last_error"] = None
        _save_work_points()
        await broadcast_state()
        return

    except Exception as e:
        logger.exception("Workflow failed")
        system_state["status"] = "ERROR"
        system_state["last_error"] = str(e)
        system_state["start_state"] = "idle"
        await broadcast_state()


async def continue_drill_workflow():
    """Continue full drilling after operator confirms with second START click."""
    global pending_drill_points, jog_offset
    try:
        if not pending_drill_points:
            system_state["status"] = "NO_POINTS"
            system_state["last_error"] = "No pending drill points"
            await broadcast_state()
            return

        if not (cnc_controller and cnc_controller.is_connected):
            system_state["status"] = "NOT_READY"
            system_state["last_error"] = "CNC not connected"
            await broadcast_state()
            return

        if not job_manager:
            system_state["status"] = "NOT_READY"
            system_state["last_error"] = "Job manager not ready"
            await broadcast_state()
            return

        applied_offset = dict(jog_offset)
        work_points = [
            (px + applied_offset["x"], py + applied_offset["y"])
            for px, py in pending_drill_points
        ]
        logger.info(f"Work points with jog offset {applied_offset}: {work_points}")

        job = await asyncio.to_thread(job_manager.create_job, work_points, False)
        system_state["progress"] = {"current": 0, "total": len(job.points)}
        system_state["status"] = "DRILLING"
        system_state["start_state"] = "drilling"
        await broadcast_state()

        for i, point in enumerate(job.points):
            if stop_event.is_set():
                system_state["status"] = "STOPPED"
                system_state["start_state"] = "idle"
                pending_drill_points = []
                jog_offset["x"] = 0.0
                jog_offset["y"] = 0.0
                jog_offset["z"] = 0.0
                await broadcast_state()
                return

            z_clear = 5.0 + applied_offset["z"]
            z_drill = -1.5 + applied_offset["z"]
            ok_xy = await asyncio.to_thread(
                cnc_controller.move_to, point.x, point.y, z_clear, 1000, True, 30.0
            )
            ok_down = await asyncio.to_thread(
                cnc_controller.move_to, None, None, z_drill, 300, True, 30.0
            )
            ok_up = await asyncio.to_thread(
                cnc_controller.move_to, None, None, z_clear, 1000, True, 30.0
            )

            if not (ok_xy and ok_down and ok_up):
                system_state["status"] = "ERROR"
                system_state["last_error"] = f"Motion failed at point {i + 1}"
                await broadcast_state()
                return

            job.mark_drilled(i)
            system_state["progress"] = {"current": i + 1, "total": len(job.points)}
            await broadcast_state()

        await asyncio.to_thread(cnc_controller.move_to, None, None, 10.0, 1000, True, 30.0)
        system_state["status"] = "COMPLETE"
        await broadcast_state()

        # End flow: return machine to STANDBY position
        system_state["status"] = "STANDBY"
        await broadcast_state()
        
        ok_standby = await asyncio.to_thread(cnc_controller.move_to, STANDBY_X, STANDBY_Y, 10.0, 1000, True, 30.0)
        if ok_standby:
            system_state["position"] = {"x": STANDBY_X, "y": STANDBY_Y, "z": 10.0}
            system_state["status"] = "IDLE"
            system_state["last_error"] = None
        else:
            system_state["status"] = "ERROR"
            system_state["last_error"] = "Failed to move to standby after drill"
        system_state["start_state"] = "idle"
        pending_drill_points = []
        await broadcast_state()

    except Exception as e:
        logger.exception("Workflow failed")
        system_state["status"] = "ERROR"
        system_state["last_error"] = str(e)
        system_state["start_state"] = "idle"
        pending_drill_points = []
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

                if start_state == "paused_at_point" and pending_drill_points:
                    workflow_task = asyncio.create_task(continue_drill_workflow())
                elif start_state == "standby_ready":
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

                # Second click behavior: return to standby and reset button state.
                if current_calibrate_state == "done":
                    # Operator may jog manually to true drill point; store residual as runtime offset.
                    if cnc_controller and cnc_controller.is_connected and system_state.get("calibrate_target"):
                        status_now = await asyncio.to_thread(cnc_controller.query_status_once, 1.0)
                        pos = status_now.get("position", {})
                        actual_x = float(pos.get("x", 0.0))
                        actual_y = float(pos.get("y", 0.0))
                        predicted = system_state.get("calibrate_target", {})
                        pred_x = float(predicted.get("x", 0.0))
                        pred_y = float(predicted.get("y", 0.0))

                        residual_x = actual_x - pred_x
                        residual_y = actual_y - pred_y

                        CALIB_OFFSET_X += residual_x
                        CALIB_OFFSET_Y += residual_y
                        _save_runtime_offset()
                        system_state["calibrate_offset"] = {"x": CALIB_OFFSET_X, "y": CALIB_OFFSET_Y}

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

                # First click behavior: run calibrate process.
                system_state["calibrate_state"] = "running"
                system_state["status"] = "CALIBRATE_RUNNING"
                await broadcast_state()

                ok_calibrate = await run_calibrate_flow()
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
