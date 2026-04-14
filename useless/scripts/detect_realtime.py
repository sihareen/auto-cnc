import sys
import cv2
import torch
import numpy as np
import subprocess

# ===============================
# TAMBAHKAN PATH YOLOv7
# ===============================
YOLOV7_PATH = "/home/hreen/yolov7"
sys.path.append(YOLOV7_PATH)

from models.experimental import attempt_load
from utils.general import non_max_suppression, scale_coords
from utils.torch_utils import select_device

# ===============================
# DETECTION CONFIG
# ===============================
CONF_THRES_DEFAULT = 0.72
IOU_THRES = 0.45
CONF_STEP = 0.02
CONF_MIN = 0.10
CONF_MAX = 0.95
MIN_BOX_W = 8
MIN_BOX_H = 8
MIN_BOX_AREA = 120

# ===============================
# CAMERA DETECTION
# ===============================
def find_usb_camera():
    """Find USB camera 0ac8:3370"""
    for index in range(10):
        try:
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                result = subprocess.run(
                    ['v4l2-ctl', '-d', str(index), '--info'],
                    capture_output=True, text=True, timeout=1
                )
                if '0ac8' in result.stdout or 'USB 2.0 Camera' in result.stdout:
                    cap.release()
                    return index
                cap.release()
        except:
            pass
    return 0

# ===============================
# LOAD MODEL
# ===============================
def load_model(weights):
    device = select_device('')  # auto CPU/GPU
    model = attempt_load(weights, map_location=device)
    model.eval()
    return model, device

# ===============================
# DETECTION FUNCTION
# ===============================
def detect(frame, model, device, conf_thres=CONF_THRES_DEFAULT, iou_thres=IOU_THRES):
    img = cv2.resize(frame, (640, 640))
    
    img = img[:, :, ::-1].transpose(2, 0, 1)  # BGR → RGB
    img = np.ascontiguousarray(img)
    
    img = torch.from_numpy(img).to(device).float() / 255.0
    img = img.unsqueeze(0)
    
    with torch.no_grad():
        pred = model(img)[0]
    pred = non_max_suppression(pred, conf_thres, iou_thres)
    
    return pred

# ===============================
# MAIN
# ===============================
def run_detection(weights_path):
    model, device = load_model(weights_path)
    conf_thres = CONF_THRES_DEFAULT
    
    cam_index = find_usb_camera()
    print(f"[INFO] Camera index: {cam_index}")
    print(f"[INFO] Confidence threshold: {conf_thres:.2f} (press '+' / '-' to adjust)")
    
    cap = cv2.VideoCapture(cam_index)
    
    if not cap.isOpened():
        print("[ERROR] Camera not found")
        return
    
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    
    switched_to_cpu = False

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        try:
            preds = detect(frame, model, device, conf_thres=conf_thres, iou_thres=IOU_THRES)
            # Guard: invalid outputs (NaN/Inf) usually indicate unstable precision/runtime.
            if any(det is not None and (torch.isnan(det).any() or torch.isinf(det).any()) for det in preds):
                raise RuntimeError("Invalid prediction values (NaN/Inf)")
        except torch.cuda.OutOfMemoryError:
            if not switched_to_cpu:
                print("[WARN] CUDA OOM. Switching inference to CPU.")
                model = model.float().to("cpu")
                device = torch.device("cpu")
                switched_to_cpu = True
                torch.cuda.empty_cache()
                preds = detect(frame, model, device, conf_thres=conf_thres, iou_thres=IOU_THRES)
            else:
                raise
        except RuntimeError as e:
            if not switched_to_cpu and "Invalid prediction values" in str(e):
                print("[WARN] Invalid CUDA prediction values. Switching inference to CPU.")
                model = model.float().to("cpu")
                device = torch.device("cpu")
                switched_to_cpu = True
                torch.cuda.empty_cache()
                preds = detect(frame, model, device, conf_thres=conf_thres, iou_thres=IOU_THRES)
            else:
                raise

        visible_boxes = 0
        
        for det in preds:
            if det is not None and len(det):
                det[:, :4] = scale_coords((640, 640), det[:, :4], frame.shape).round()
                
                for *xyxy, conf, cls in det:
                    x1, y1, x2, y2 = map(int, xyxy)
                    w = max(0, x2 - x1)
                    h = max(0, y2 - y1)
                    if w < MIN_BOX_W or h < MIN_BOX_H or (w * h) < MIN_BOX_AREA:
                        continue
                    
                    label = f"{int(cls)} {conf:.2f}"
                    visible_boxes += 1
                    
                    # Bounding box
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0,255,0), 2)
                    
                    # Text
                    cv2.putText(frame, label, (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                (0,255,0), 2)
                    
                    # Center point (PENTING buat CNC)
                    cx = int((x1 + x2) / 2)
                    cy = int((y1 + y2) / 2)
                    cv2.circle(frame, (cx, cy), 5, (0,0,255), -1)

        status_text = f"conf={conf_thres:.2f} boxes={visible_boxes}"
        cv2.putText(frame, status_text, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (50, 255, 255), 2)
        
        cv2.imshow("YOLOv7 PCB Detection", frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key in (ord('+'), ord('=')):
            conf_thres = min(CONF_MAX, conf_thres + CONF_STEP)
            print(f"[INFO] Confidence threshold -> {conf_thres:.2f}")
        elif key in (ord('-'), ord('_')):
            conf_thres = max(CONF_MIN, conf_thres - CONF_STEP)
            print(f"[INFO] Confidence threshold -> {conf_thres:.2f}")
    
    cap.release()
    cv2.destroyAllWindows()

# ===============================
# ENTRY
# ===============================
if __name__ == "__main__":
    run_detection("best.pt")
