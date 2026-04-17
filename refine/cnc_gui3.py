import tkinter as tk
from tkinter import filedialog, ttk
import serial
import serial.tools.list_ports
import cv2
import numpy as np
import threading
import queue
import time
from PIL import Image, ImageTk
import uuid
import urllib.request

idku=uuid.getnode()
#contents = urllib2.urlopen("http://demo.indomaker.com/raftech/ceklic.php?user="+str(uuid.getnode())).read()
#contents = urllib.request.urlopen("http://demo.indomaker.com/raftech/ceklic.php?user="+str(uuid.getnode())).read()
#print (contents)
contents=b'ok'
f = open("refine/padx.txt", "r")
angkax=f.read()
#print (angkax)
f = open("refine/pady.txt", "r")
angkay=f.read()

class CNCInterface:
    def __init__(self, root):
        self.root = root
        self.root.title("CNC Interface")
        self.serial_port = None
        self.running = False
        self.paused = False
        self.status_queue = queue.Queue()
        self.camera_running = True

        # GUI Layout
        self.create_gui()
        
        # Start status update thread
        self.update_status()
        
        # Start camera thread
        self.cap = cv2.VideoCapture(2)  # Default camera
        if not self.cap.isOpened():
            self.update_status_text("Error: Could not open camera")
            self.camera_running = False
        else:
            self.update_camera()

    def create_gui(self):
        # COM Port Selection
        self.com_frame = tk.Frame(self.root)
        self.com_frame.pack(pady=5)
        
        tk.Label(self.com_frame, text="COM Port:").pack(side=tk.LEFT)
        self.com_var = tk.StringVar()
        self.com_dropdown = ttk.Combobox(self.com_frame, textvariable=self.com_var, values=self.get_com_ports())
        self.com_dropdown.pack(side=tk.LEFT, padx=5)
        
        self.connect_btn = tk.Button(self.com_frame, text="Connect", command=self.toggle_connect)
        self.connect_btn.pack(side=tk.LEFT, padx=5)
        
        self.com_status = tk.Label(self.com_frame, text="Disconnected", fg="red")
        self.com_status.pack(side=tk.LEFT, padx=5)

        # G-code Input
        self.gcode_frame = tk.Frame(self.root)
        self.gcode_frame.pack(pady=5)
        
        tk.Label(self.gcode_frame, text="G-code:").pack(side=tk.LEFT)
        self.gcode_entry = tk.Entry(self.gcode_frame, width=30)
        self.gcode_entry.pack(side=tk.LEFT, padx=5)
        tk.Button(self.gcode_frame, text="Send", command=self.send_gcode).pack(side=tk.LEFT)

        # Jog Controls
        self.jog_frame = tk.Frame(self.root)
        self.jog_frame.pack(pady=5)
        
        tk.Label(self.jog_frame, text="Jog Step (mm):").pack(side=tk.LEFT)
        self.step_var = tk.StringVar(value="1")
        ttk.Combobox(self.jog_frame, textvariable=self.step_var, values=["0.5", "1", "5", "10"]).pack(side=tk.LEFT, padx=5)
        
        tk.Label(self.jog_frame, text="Feed (mm/min):").pack(side=tk.LEFT)
        self.feed_var = tk.StringVar(value="100")
        ttk.Combobox(self.jog_frame, textvariable=self.feed_var, values=["50", "100", "500"]).pack(side=tk.LEFT, padx=5)
        
        jog_buttons = [
            ("X+", lambda: self.jog("X", self.step_var.get(), self.feed_var.get())),
            ("X-", lambda: self.jog("X", f"-{self.step_var.get()}", self.feed_var.get())),
            ("Y+", lambda: self.jog("Y", self.step_var.get(), self.feed_var.get())),
            ("Y-", lambda: self.jog("Y", f"-{self.step_var.get()}", self.feed_var.get())),
            ("Z+naik", lambda: self.jog("Z", self.step_var.get(), self.feed_var.get())),
            ("Z-turun", lambda: self.jog("Z", f"-{self.step_var.get()}", self.feed_var.get())),
        ]
        for text, cmd in jog_buttons:
            tk.Button(self.jog_frame, text=text, command=cmd).pack(side=tk.LEFT, padx=2)

        # Control Buttons
        self.control_frame = tk.Frame(self.root)
        self.control_frame.pack(pady=5)
        
        tk.Button(self.control_frame, text="Home", command=self.home).pack(side=tk.LEFT, padx=5)
        tk.Button(self.control_frame, text="Zero XY", command=self.zero_xy).pack(side=tk.LEFT, padx=5)
        tk.Button(self.control_frame, text="Zero Z", command=self.zero_z).pack(side=tk.LEFT, padx=5)
        tk.Button(self.control_frame, text="E-Stop", command=self.emergency_stop, fg="red").pack(side=tk.LEFT, padx=5)
        tk.Button(self.control_frame, text="Release (Ctrl-X)", command=self.release_ctrl_x).pack(side=tk.LEFT, padx=5)
        tk.Button(self.control_frame, text="Unlock ($X)", command=self.unlock_x).pack(side=tk.LEFT, padx=5)

        # File Controls
        self.file_frame = tk.Frame(self.root)
        self.file_frame.pack(pady=5)
        
        tk.Button(self.file_frame, text="Upload G-code", command=self.upload_gcode).pack(side=tk.LEFT, padx=5)
        self.start_btn = tk.Button(self.file_frame, text="Start", command=self.start_gcode, state=tk.DISABLED)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.pause_btn = tk.Button(self.file_frame, text="Pause", command=self.toggle_pause, state=tk.DISABLED)
        self.pause_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = tk.Button(self.file_frame, text="Stop", command=self.stop_gcode, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        tk.Button(self.file_frame, text="Goto first pad", command=self.exec_first_line).pack(side=tk.LEFT, padx=5)
        tk.Button(self.file_frame, text="Goto last pad", command=self.exec_last_line).pack(side=tk.LEFT, padx=5)
        tk.Button(self.file_frame, text="Upload PNG", command=self.upload_png).pack(side=tk.LEFT, padx=5)
        tk.Button(self.file_frame, text="Find Circle X", command=self.find_circles).pack(side=tk.LEFT, padx=5)
        tk.Button(self.file_frame, text="Find Circle Y", command=self.find_circles_y).pack(side=tk.LEFT, padx=5)

        # Status Display
        self.status_text = tk.Text(self.root, height=5, width=50)
        self.status_text.pack(pady=5)
        
        # Camera Display
        self.camera_label = tk.Label(self.root)
        self.camera_label.pack(pady=5)

    def get_com_ports(self):
        return [port.device for port in serial.tools.list_ports.comports()]

    def toggle_connect(self):
        if self.serial_port is None:
            try:
                port = self.com_var.get()
                if not port:
                    self.update_status_text("Error: No COM port selected")
                    return
                self.serial_port = serial.Serial(port, 115200, timeout=1)
                self.com_status.config(text="Connected", fg="green")
                self.connect_btn.config(text="Disconnect")
                self.update_status_text(f"Connected to {port}")
            except Exception as e:
                self.update_status_text(f"Connection error: {str(e)}")
        else:
            self.serial_port.close()
            self.serial_port = None
            self.com_status.config(text="Disconnected", fg="red")
            self.connect_btn.config(text="Connect")
            self.update_status_text("Disconnected")

    def send_gcode(self):
        if self.serial_port is None:
            self.update_status_text("Error: Not connected to CNC")
            return
        gcode = self.gcode_entry.get().strip()
        if gcode:
            try:
                self.serial_port.write(f"{gcode}\n".encode())
                self.update_status_text(f"Sent: {gcode}")
                response = self.serial_port.readline().decode().strip()
                if response:
                    self.update_status_text(f"Response: {response}")
            except Exception as e:
                self.update_status_text(f"Error sending G-code: {str(e)}")

    def jog(self, axis, step, feed):
        if self.serial_port is None:
            self.update_status_text("Error: Not connected to CNC")
            return
        gcode = f"G91 G1 {axis}{step} F{feed}"
        self.send_gcode_command(gcode)

    def home(self):
        self.send_gcode_command("$H")

    def zero_xy(self):
        self.send_gcode_command("G92 X0 Y0")

    def zero_z(self):
        self.send_gcode_command("G92 Z0")

    def emergency_stop(self):
        if self.serial_port:
            self.send_gcode_command("!")
            self.running = False
            self.paused = False
            self.update_buttons()
            self.update_status_text("Emergency Stop triggered")

    def release_ctrl_x(self):
        if self.serial_port is None:
            self.update_status_text("Error: Not connected to CNC")
            return
        try:
            self.serial_port.write(b'\x18')  # Ctrl-X for soft reset
            self.update_status_text("Sent: Ctrl-X (Soft Reset)")
            response = self.serial_port.readline().decode().strip()
            if response:
                self.update_status_text(f"Response: {response}")
        except Exception as e:
            self.update_status_text(f"Error sending Ctrl-X: {str(e)}")

    def unlock_x(self):
        self.send_gcode_command("$X")

    def exec_first_line(self):
        if not hasattr(self, 'gcode_file'):
            self.update_status_text("Error: No G-code file uploaded")
            return
        line = self.get_first_or_last_line(self.gcode_file, first=True)
        if line:
            self.send_gcode_command(line)
        else:
            self.update_status_text("Error: No valid G-code lines in file")

    def exec_last_line(self):
        if not hasattr(self, 'gcode_file'):
            self.update_status_text("Error: No G-code file uploaded")
            return
        line = self.get_first_or_last_line(self.gcode_file, first=False)
        if line:
            self.send_gcode_command(line)
        else:
            self.update_status_text("Error: No valid G-code lines in file")

    def get_first_or_last_line(self, file_path, first=True):
        with open(file_path, 'r') as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith(('%', '('))]
        if not lines:
            return None
        return lines[0] if first else lines[-1]

    def upload_png(self):
        file_path = filedialog.askopenfilename(filetypes=[("PNG files", "*.png")])
        if file_path:
            self.png_file = file_path
            self.update_status_text(f"Uploaded PNG: {file_path}")

    def find_circles(self):
        if not hasattr(self, 'png_file'):
            self.update_status_text("Error: No PNG file uploaded")
            return
        try:
            # Load image
            img = cv2.imread(self.png_file)
            if img is None:
                self.update_status_text("Error: Could not load PNG image")
                return
            height = img.shape[0]
            width = img.shape[1]
            # Convert to grayscale
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # Create a SimpleBlobDetector object
            detector = cv2.SimpleBlobDetector_create()

            # Detect blobs
            keypoints = detector.detect(gray)

            # Convert keypoints to list of tuples (x, y, size)
            circle_data = [(int(kp.pt[0]), int(kp.pt[1]), kp.size) for kp in keypoints]

            # Sort circles: primary by Y (descending), secondary by X (ascending)
            circle_data_sorted = sorted(circle_data, key=lambda x: (-x[1], x[0]))

            # Generate G-code and draw circles
            no = 0
            s1 = ""
            s2 = ""
            for x, y, size in circle_data_sorted:
                no += 1
                r = int(size / 2)
                cv2.circle(img, (x, y), r, (0, 255, 0), 2)
                if size > 10:
                    cv2.putText(img, str(no), (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
                    s1 += f"{no} G90 X{round((x * 0.0847) - 39.48)} Y{round(((height - y) * (-0.0846)) + 50.088)}\n"
                    s2 += f"G90 X{round((x * 0.0847) - 39.48)} Y{round(((height - y) * (-0.0846)) + 50.088)}\n"

            # Save G-code to file
            with open("cnc.gcode", "w") as text_file:
                text_file.write(s2)
            self.update_status_text("G-code saved to cnc.gcode")

            # Display G-code in status
            #self.update_status_text("Generated G-code:\n" + s1)

            # Save and display processed image
            cv2.imwrite("findcircle.png", img)
            self.update_status_text("Saved processed image as findcircle.png")

        except Exception as e:
            self.update_status_text(f"Error in circle detection: {str(e)}")

    def find_circles_y(self):
        if not hasattr(self, 'png_file'):
            self.update_status_text("Error: No PNG file uploaded")
            return
        try:
            # Load image
            img = cv2.imread(self.png_file)
            if img is None:
                self.update_status_text("Error: Could not load PNG image")
                return
            height = img.shape[0]
            width = img.shape[1]
            # Convert to grayscale
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # Create a SimpleBlobDetector object
            detector = cv2.SimpleBlobDetector_create()

            # Detect blobs
            keypoints = detector.detect(gray)

            # Convert keypoints to list of tuples (x, y, size)
            circle_data = [(int(kp.pt[0]), int(kp.pt[1]), kp.size) for kp in keypoints]

            # Sort circles: primary by X (ascending), secondary by Y (descending)
            circle_data_sorted = sorted(circle_data, key=lambda x: (x[0], -x[1]))

            # Generate G-code and draw circles
            no = 0
            s1 = ""
            s2 = ""
            for x, y, size in circle_data_sorted:
                no += 1
                r = int(size / 2)
                cv2.circle(img, (x, y), r, (0, 255, 0), 2)
                if size > 10:
                    cv2.putText(img, str(no), (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
                    s1 += f"{no} G90 X{round((x * 0.0847) - 39.48)} Y{round(((height - y) * (-0.0846)) + 50.088)}\n"
                    s2 += f"G90 X{round((x * 0.0847) - 39.48)} Y{round(((height - y) * (-0.0846)) + 50.088)}\n"

            # Save G-code to file
            with open("cnc.gcode", "w") as text_file:
                text_file.write(s2)
            self.update_status_text("G-code saved to cnc.gcode")

            # Display G-code in status
            #self.update_status_text("Generated G-code (Y-sorted):\n" + s1)

            # Save and display processed image
            cv2.imwrite("findcircle.png", img)
            self.update_status_text("Saved processed image as findcircle.png")

        except Exception as e:
            self.update_status_text(f"Error in circle detection (Y-sorted): {str(e)}")

    def send_gcode_command(self, gcode):
        if self.serial_port is None:
            self.update_status_text("Error: Not connected to CNC")
            return
        try:
            self.serial_port.write(f"{gcode}\n".encode())
            self.update_status_text(f"Sent: {gcode}")
            response = self.serial_port.readline().decode().strip()
            if response:
                self.update_status_text(f"Response: {response}")
        except Exception as e:
            self.update_status_text(f"Error: {str(e)}")

    def upload_gcode(self):
        file_path = filedialog.askopenfilename(filetypes=[("G-code files", "*.nc *.gcode")])
        if file_path:
            self.gcode_file = file_path
            self.update_status_text(f"Uploaded: {file_path}")
            self.start_btn.config(state=tk.NORMAL)

    def start_gcode(self):
        if not hasattr(self, 'gcode_file'):
            self.update_status_text("Error: No G-code file uploaded")
            return
        self.running = True
        self.paused = False
        self.update_buttons()
        threading.Thread(target=self.run_gcode_file, daemon=True).start()

    def run_gcode_file(self):
        try:
            with open(self.gcode_file, 'r') as f:
                for line in f:
                    if not self.running:
                        break
                    if self.paused:
                        time.sleep(0.1)
                        continue
                    line = line.strip()
                    if line and not line.startswith(('%', '(')):
                        self.send_gcode_command(line)
                        time.sleep(0.1)  # Small delay to avoid overwhelming CNC
            self.running = False
            self.paused = False
            self.update_buttons()
            self.update_status_text("G-code execution completed")
        except Exception as e:
            self.update_status_text(f"Error running G-code: {str(e)}")
            self.running = False
            self.update_buttons()

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.config(text="Resume" if self.paused else "Pause")
        self.update_status_text("Paused" if self.paused else "Resumed")

    def stop_gcode(self):
        self.running = False
        self.paused = False
        self.update_buttons()
        self.send_gcode_command("!")
        self.update_status_text("G-code execution stopped")

    def update_buttons(self):
        self.start_btn.config(state=tk.DISABLED if self.running else tk.NORMAL)
        self.pause_btn.config(state=tk.NORMAL if self.running else tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL if self.running else tk.DISABLED)

    def update_status_text(self, message):
        self.status_queue.put(message)

    def update_status(self):
        try:
            while True:
                message = self.status_queue.get_nowait()
                self.status_text.insert(tk.END, f"{message}\n")
                self.status_text.see(tk.END)
        except queue.Empty:
            pass
        self.root.after(100, self.update_status)

    def update_camera(self):
        if self.camera_running:
            ret, frame = self.cap.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame = cv2.resize(frame, (640, 480))  # Resize to 640x480
                frame = cv2.circle(frame, (int(angkax), int(angkay)), 5, (0, 0, 255), -1)
                image = Image.fromarray(frame)
                photo = ImageTk.PhotoImage(image)
                self.camera_label.config(image=photo)
                self.camera_label.image = photo
            self.root.after(33, self.update_camera)  # ~30 FPS
        else:
            self.camera_label.config(image='')

    def on_closing(self):
        self.running = False
        self.camera_running = False
        if self.cap:
            self.cap.release()
        if self.serial_port:
            self.serial_port.close()
        self.root.destroy()

if __name__ == "__main__":
    if contents.decode('utf-8')=="ok" :        
        root = tk.Tk()
        app = CNCInterface(root)
        root.protocol("WM_DELETE_WINDOW", app.on_closing)
        root.mainloop()
