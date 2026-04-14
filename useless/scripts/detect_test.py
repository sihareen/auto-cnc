"""
Image detection test using YOLOv7 pipeline aligned with yolov7/detect.py.

Example:
    python detect_test.py --capture.py
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import torch


ROOT = Path(__file__).resolve().parent
YOLOV7_PATH = ROOT / "yolov7"
sys.path.insert(0, str(YOLOV7_PATH))

from models.experimental import attempt_load
from utils.datasets import letterbox
from utils.general import check_img_size, non_max_suppression, scale_coords
from utils.plots import plot_one_box
from utils.torch_utils import select_device

# Hardcoded configuration
DEFAULT_IMAGE = "capture.jpg"
WEIGHTS_PATH = "best.pt"
IMG_SIZE = 640
CONF_THRES = 0.25
IOU_THRES = 0.45
DEVICE = ""


def load_model(weights: str, device_str: str = "", img_size: int = 640):
    """Load YOLOv7 model similarly to yolov7/detect.py."""
    device = select_device(device_str)
    model = attempt_load(weights, map_location=device)
    stride = int(model.stride.max())
    img_size = check_img_size(img_size, s=stride)
    model.eval()
    return model, device, stride, img_size


def preprocess_bgr(im0: np.ndarray, img_size: int, stride: int, device: torch.device):
    """Letterbox + CHW + normalize, following yolov7/detect.py flow."""
    img = letterbox(im0, img_size, stride=stride)[0]
    img = img[:, :, ::-1].transpose(2, 0, 1)  # BGR to RGB, HWC to CHW
    img = np.ascontiguousarray(img)
    img = torch.from_numpy(img).to(device).float() / 255.0
    if img.ndimension() == 3:
        img = img.unsqueeze(0)
    return img


def run_image_detection(
    image_path: str,
    output_path: str,
):
    model, device, stride, img_size = load_model(WEIGHTS_PATH, device_str=DEVICE, img_size=IMG_SIZE)
    names = model.module.names if hasattr(model, "module") else model.names

    im0 = cv2.imread(image_path)
    if im0 is None:
        raise FileNotFoundError(f"Image not found: {image_path}")

    img = preprocess_bgr(im0, img_size, stride, device)

    with torch.no_grad():
        pred = model(img, augment=False)[0]
    pred = non_max_suppression(pred, CONF_THRES, IOU_THRES, classes=None, agnostic=False)

    det_count = 0
    for det in pred:
        if not len(det):
            continue

        det[:, :4] = scale_coords(img.shape[2:], det[:, :4], im0.shape).round()
        det_count += len(det)

        for *xyxy, conf, cls in reversed(det):
            label = f"{names[int(cls)]} {conf:.2f}" if names else f"{int(cls)} {conf:.2f}"
            plot_one_box(xyxy, im0, label=label, color=(0, 255, 0), line_thickness=1)

    cv2.imwrite(output_path, im0)
    print(f"[INFO] Image: {image_path}")
    print(f"[INFO] Weights: {WEIGHTS_PATH}")
    print(f"[INFO] Device: {device}")
    print(f"[INFO] Threshold: conf={CONF_THRES:.2f}, iou={IOU_THRES:.2f}")
    print(f"[INFO] Detections: {det_count}")
    print(f"[INFO] Saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="YOLOv7 image detection test (hardcoded config)")
    parser.add_argument(
        "--capture.py",
        "--capture",
        dest="use_capture",
        action="store_true",
        help="Use capture.jpg as input image",
    )
    args = parser.parse_args()

    image_path = DEFAULT_IMAGE
    input_name = Path(image_path).stem
    output_path = f"output_{input_name}.jpg"
    run_image_detection(image_path=image_path, output_path=output_path)


if __name__ == "__main__":
    main()
