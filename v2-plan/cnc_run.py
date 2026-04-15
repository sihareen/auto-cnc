import time
import serial
from ultralytics import YOLO
import numpy as np
import cv2
import time
import os
import uuid
import urllib.request

idku=uuid.getnode()
#contents = urllib2.urlopen("http://demo.indomaker.com/raftech/ceklic.php?user="+str(uuid.getnode())).read()
#contents = urllib.request.urlopen("http://demo.indomaker.com/raftech/ceklic.php?user="+str(uuid.getnode())).read()
contents=b'ok'
#print (contents)
f = open("cam.txt", "r")
camno=f.read()

# pad center (282, 257) 212
f = open("padx.txt", "r")
angkax=f.read()
#print (angkax)
f = open("pady.txt", "r")
angkay=f.read()
f = open("zdepth.txt", "r")
zdepth=f.read()
f = open("zfeed.txt", "r")
zfeed=f.read()
f = open("com.txt", "r")
kom=f.read()
x=int(angkax)
y=int(angkay)
r=120
MODEL_PATH = "best.pt"  # 640x480 model
IMG_SIZE = (640, 480)  # 640x480
#centerx=0
#centery=0
s1 = ""
gagal=0
ROI_CENTER = (x, y)  # ROI center coordinates
ROI_RADIUS = r  # ROI radius in pixels

def carititikpas():
    model = YOLO(MODEL_PATH)
    global centerx
    global centery
    centerx=0
    centery=0
    cap = cv2.VideoCapture(int(camno)) # Set Capture Device, in case of a USB Webcam try 1, or give -1 to get a list of available devices

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, IMG_SIZE[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, IMG_SIZE[1])

    #height, width, channels = cap.shape
    #upper_left = ((int(width)//2)-(int(width) // 5), (int(height)//2)-(int(height) // 4))
    #bottom_right = ((int(width) // 2)+100, (int(height) // 2)+100)
    waktumikir=3
    x1roi=x-r
    x2roi=x+r
    y1roi=y-r
    y2roi=y+r
    #upper_left = ((int(width) // 2)-140, (int(height) // 2)-50)
    #bottom_right = ((int(width) // 2)+20, (int(height) // 2)+90)

    #start_point = (int(width)-50, int(height)-50)
    #end_point = (int(width)+50, int(height)+50)
    color = (255, 0, 0)
    thickness = 2
    waktu = 0
    
    koreksi=0 #0=false
    varx=10
    vary=10
    toleransix=0.03
    toleransiy=0.03
    titikpasx=x
    titikpasy=y
    maxcekpad=10
    cekpad=0
    
    # Create circular mask for ROI
    mask = np.zeros((IMG_SIZE[1], IMG_SIZE[0]), dtype=np.uint8)
    cv2.circle(mask, ROI_CENTER, ROI_RADIUS, 255, -1)  # White circle on black background

    while(True):
        # Capture frame-by-frame
        ret, frame = cap.read()
        if not ret:
            print("Error: Failed to capture frame")
            nilai=1
            return nilai
            #break
        frame = cv2.resize(frame, IMG_SIZE)
        preprocessed_frame = cv2.convertScaleAbs(frame, alpha=1.5, beta=20)
        preprocessed_frame = cv2.normalize(preprocessed_frame, None, 0, 255, cv2.NORM_MINMAX)
        
        # Apply circular ROI mask
        masked_frame = cv2.bitwise_and(preprocessed_frame, preprocessed_frame, mask=mask)
        
        #results = model(preprocessed_frame, conf=0.1, iou=0.5)
        results = model(masked_frame, conf=0.1, iou=0.5)
        #print(f"Detections: {len(results[0].boxes)}")
        #print(f"Confidence scores: {results[0].boxes.conf.tolist() if len(results[0].boxes) > 0 else 'None'}")
        #print(f"Class IDs: {results[0].boxes.cls.tolist() if len(results[0].boxes) > 0 else 'None'}")

        if len(results[0].boxes)<1:
            print("sini")
            nilai=1
            cekpad=cekpad+1
            if cekpad>maxcekpad:
                return nilai
        #    break
        
        # Filter detections to ensure they are within the ROI
        filtered_boxes = []
        if len(results[0].boxes) > 0:
            for box in results[0].boxes:
                # Get bounding box center
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                box_center_x = (x1 + x2) / 2
                box_center_y = (y1 + y2) / 2
                # Check if the center is within the ROI
                distance = np.sqrt((box_center_x - ROI_CENTER[0])**2 + (box_center_y - ROI_CENTER[1])**2)
                if distance <= ROI_RADIUS:
                    filtered_boxes.append(box)
        
        # Select only the detection with the highest confidence (if any)
        highest_conf = None
        if filtered_boxes:
            best_box = max(filtered_boxes, key=lambda b: b.conf.item())
            highest_conf = best_box.conf.item()
            results[0].boxes = [best_box]  # Keep only the best box for plotting
            #print(f"Highest confidence score: {highest_conf:.2f}")
        else:
            results[0].boxes = []

        # Draw bounding boxes
        annotated_frame = results[0].plot()
        #print("cx=",centerx)
        
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
            if (x_min>x-r) and (x_max<x+r) and (y_min>y-r) and (y<y+r):
                centerx=center_x
                centery=center_y
                cv2.circle(annotated_frame, (center_x, center_y), 5, (0, 255, 0), -1)
                # Annotate with coordinates
                #cv2.putText(annotated_frame, f"({center_x}, {center_y})", (center_x + 10, center_y),
                #            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
            # Print center coordinates
            #else:
                #print("sana")
            #    break
            #print(f"Pad center: ({center_x}, {center_y})")

        # Calculate and display FPS
        inference_time = results[0].speed['inference']
        fps = 1000 / inference_time if inference_time > 0 else 0
        cv2.putText(annotated_frame, f"FPS: {fps:.1f}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)

        # Display frame
        #cv2.imshow("Live PCB Pad Detection (SPACE to save, Q to quit)", frame)
        #key = cv2.waitKey(1) & 0xFF
        #if key == ord("q"):
        #    break
        
       
        time.sleep(0.1)
        #ara[waktu]=x
        #print (ara)
        waktu=waktu+1
        if waktu>=waktumikir:
                waktu=0
                
                if (abs(varx)>toleransix) or (abs(vary)>toleransiy):
                    varx=round((centerx-titikpasx)/50,2)
                    if varx>0.1:
                        varx=0.1
                    if varx<-0.1:
                        varx=-0.1
                    vary=round((centery-titikpasy)/40,2)
                    if vary<-0.1:
                        vary=-0.1
                    if vary>0.1:
                        vary=0.1
                    var1='G91 X'+str(varx)+' Y'+str(vary)
                    print (var1)
                    #var2='G91 X0.76'
                    #ser.write(b'G91 X0.76\n')
                    ser.write(var1.encode()+b'\n')
                    koreksi=koreksi+1
                else:
                    break
                    #koreksi=1
                 #   time.sleep(0.1)
                #break
                #ara=np.zeros(60).astype(int)
        # Display the resulting frame
        #cv2.putText(output, 'x: '+str(x), (10,30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        #cv2.putText(output, 'y: '+str(y), (10,60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        cv2.circle(annotated_frame, (x, y), 5, (0, 0, 255), -1)
        cv2.putText(annotated_frame, 'gagal: '+str(gagal), (10,90), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        cv2.putText(annotated_frame, 'time: '+str(waktu), (10,120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        #cv2.putText(output, 'xmod: '+str(arr1), (100,30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        #cv2.putText(output, 'xavg: '+str(round(arr2)), (240,30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        #cv2.putText(output, 'xavg: '+str(round(arr3)), (240,30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        #cv2.putText(output, 'xconf: '+str(round(arr4)), (380,30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        #cv2.putText(output, 'ymod: '+str(ary1), (100,60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        #cv2.putText(output, 'yavg: '+str(round(arr2)), (240,30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        #cv2.putText(output, 'yavg: '+str(round(ary3)), (240,60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        #cv2.putText(output, 'yconf: '+str(round(ary4)), (380,60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        #cv2.imshow('gray',gray)
        #cv2.rectangle(annotated_frame, (x-r, y-r), (x+r, y+r), (0, 255, 0), 2)
        cv2.imshow('frame',annotated_frame)
        if waktu==0:
                print ("waktu0")
                #cv2.circle(annotated_frame, (round(arr3), y), r, (0, 255, 0), 2)
                #cv2.rectangle(annotated_frame, (round(arr3) - 5, y - 5), (round(arr3) + 5, y + 5), (0, 128, 255), -1)
                #break
        #cv2.imshow('frame2',output2)
        #if cv2.waitKey(1):
        if cv2.waitKey(1) & 0xFF == 122:  #huruf z
                koreksi=0
                varx=10
                vary=10
        
        if cv2.waitKey(1) & 0xFF == 27:
         ser.close()
         break
    nilai=0
    return nilai     

if contents.decode('utf-8')=="ok" :
    ser = serial.Serial(kom,baudrate=115200)  # open serial port
    #carititikpas()
    file1 = open('cnc.gcode', 'r')
    Lines = file1.readlines()
    #print (Lines)
    #print (Lines[0]) 
    #print (len(Lines))

    jmlbaris=len(Lines)

    count = 0
    # Strips the newline character
    perintah=""
    for line in Lines:
        count += 1
        print("Line{}: {}".format(count, line.strip()))
        
        perintah=line.strip()
        #run gcode from file (line by line)
        ser.write(line.strip().encode()+b'\n')
        
        # wait until 'idle'
        data = ser.read(4)
        #print (data)
        time.sleep (0.2)
        ser.write(b'?\n')     # write a string
        data = ser.read(4)
        #print (data)

        time.sleep (0.2)
    # wait until 'idle'
        while (data.decode()!='<Idl'):
            ser.write(b'?\n')     # write a string
            data = ser.read(4)
            #print (data)
            #time.sleep(0.2)
            if (data.decode()=='<Idl'):
                print ("cnc idle")
                break
        
            time.sleep(0.2)
            ser.flushInput()
            ser.flushOutput()

        
        # refine
        b=carititikpas()
        #print ("nilai= ",b)
        # bor
        if b==0:
            gcode_command="G01 Z-"+str(zdepth)+" F"+str(zfeed)+"\n"
            ser.write(gcode_command.encode())
            time.sleep (0.1)
            #ser.write(b'G01 Z{zdepth} F200\n')
            #ser.write(f'G01 Z{zdepth} F200\n'.encode())
            gcode_command="G01 Z"+str(zdepth)+" F"+str(zfeed)+"\n"
            ser.write(gcode_command.encode())
            time.sleep (0.1)
            ser.write(b'F500\n')
        else:
            gagal += 1
            s1 +=f"{perintah}\n"
        
        
        
        #ser.write(b'G91 X1\n')     # write a string
        #ser.write(line.strip().encode()+b'\n')
        #print (line.strip().encode())
        #data = ser.read(2)
        #print (data)    
        time.sleep(1)
        
    ser.close()
    with open("gagaldrill.txt", "w") as text_file:
        text_file.write(s1)