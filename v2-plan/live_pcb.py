import cv2
from ultralytics import YOLO
import numpy as np
import os
from datetime import datetime

# Configuration
MODEL_PATH = "best.pt"  # 640x480 model
SAVE_FOLDER = "D:/PCB_Dataset/Live_Output_640x480"
IMG_SIZE = (640, 480)  # 640x480
f = open("cam.txt", "r")
camno=f.read()

# Create save folder
if not os.path.exists(SAVE_FOLDER):
    os.makedirs(SAVE_FOLDER)
    print(f"Created folder: {SAVE_FOLDER}")

# Load model
model = YOLO(MODEL_PATH)

# Initialize webcam
cap = cv2.VideoCapture(int(camno))
if not cap.isOpened():
    print("Error: Could not open webcam")
    exit()

# Set webcam resolution
cap.set(cv2.CAP_PROP_FRAME_WIDTH, IMG_SIZE[0])
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, IMG_SIZE[1])

# Verify resolution
width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
print(f"Webcam resolution: {width}x{height}")

while True:
    # Capture frame
    ret, frame = cap.read()
    if not ret:
        print("Error: Failed to capture frame")
        break

    # Resize to 640x480
    frame = cv2.resize(frame, IMG_SIZE)
    print(f"Frame shape: {frame.shape}")  # (480, 640, 3)

    # Preprocess frame
    preprocessed_frame = cv2.convertScaleAbs(frame, alpha=1.5, beta=20)
    preprocessed_frame = cv2.normalize(preprocessed_frame, None, 0, 255, cv2.NORM_MINMAX)

    # Run inference
    results = model(preprocessed_frame, conf=0.1, iou=0.5)
    print(f"Detections: {len(results[0].boxes)}")
    print(f"Confidence scores: {results[0].boxes.conf.tolist() if len(results[0].boxes) > 0 else 'None'}")
    print(f"Class IDs: {results[0].boxes.cls.tolist() if len(results[0].boxes) > 0 else 'None'}")

    # Draw bounding boxes
    annotated_frame = results[0].plot()
    for box in results[0].boxes:
            # Get bounding box coordinates
            x_min, y_min, x_max, y_max = box.xyxy[0].tolist()
            # Calculate center
            center_x = int((x_min + x_max) / 2)
            center_y = int((y_min + y_max) / 2)
            # Draw center point
            #centerx=0
            #centery=0
            #print("cx2=",centerx)
            #if (x_min>x-r) and (x_max<x+r) and (y_min>y-r) and (y<y+r):
               # centerx=center_x
                #centery=center_y
            cv2.circle(annotated_frame, (center_x, center_y), 5, (0, 255, 0), -1)
                # Annotate with coordinates
            cv2.putText(annotated_frame, f"({center_x}, {center_y})", (center_x + 10, center_y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
            # Print center coordinates

    # Calculate and display FPS
    inference_time = results[0].speed['inference']
    fps = 1000 / inference_time if inference_time > 0 else 0
    cv2.putText(annotated_frame, f"FPS: {fps:.1f}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)

    # Display frame
    cv2.imshow("Live PCB Pad Detection (SPACE to save, Q to quit)", annotated_frame)

    # Handle key presses
    key = cv2.waitKey(1) & 0xFF
    if key == ord(" "):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        preprocessed_path = os.path.join(SAVE_FOLDER, f"preprocessed_{timestamp}.jpg")
        annotated_path = os.path.join(SAVE_FOLDER, f"annotated_{timestamp}.jpg")
        cv2.imwrite(preprocessed_path, preprocessed_frame)
        cv2.imwrite(annotated_path, annotated_frame)
        print(f"Saved: {preprocessed_path}, {annotated_path}")
    elif key == ord("q"):
        break

# Cleanup
cap.release()
cv2.destroyAllWindows()
print("Webcam released")