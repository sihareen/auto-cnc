"""
Calibration GUI tool untuk recalibrate affine transformation
antara camera pixel coordinates dan CNC machine coordinates

Usage:
    python calibrate/calibrate.py              # Interactive GUI mode
    python calibrate/calibrate.py --verify     # Verify existing calibration
    python calibrate/calibrate.py --clear      # Clear and start fresh
"""
import argparse
import cv2
import json
import numpy as np
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIN_CALIB_POINTS = 1
MAX_CALIB_POINTS = 20

class CalibrationError(Exception):
    pass

class CalibrationGUI:
    """
    Interactive GUI Calibration Tool
    """
    
    def __init__(self, calibration_path: str = "config/calibration_affine.json"):
        self.calibration_path = Path(calibration_path)
        self.src_points_px = []
        self.dst_points_mm = []
        self.matrix = None
        self.fit_mode = "unknown"
        self.camera_index = None  # Will be set when needed
        
        # Load existing if available
        self.load_existing()
    
    def _find_usb_camera(self):
        """Find USB camera 0ac8:3370"""
        import subprocess
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
    
    def load_existing(self):
        """Load existing calibration"""
        if self.calibration_path.exists():
            try:
                with open(self.calibration_path) as f:
                    data = json.load(f)
                self.matrix = np.array(data['matrix'])
                self.fit_mode = data.get("fit_mode", "affine")
                self.src_points_px = [tuple(p) for p in data['src_points_px']]
                self.dst_points_mm = [tuple(p) for p in data['dst_points_mm']]
                logger.info(f"Loaded existing calibration with {len(self.src_points_px)} points")
            except Exception as e:
                logger.warning(f"Could not load existing: {e}")
    
    def add_point_pair(self, px, mm):
        """Add calibration point pair"""
        if len(self.src_points_px) >= MAX_CALIB_POINTS:
            print(f"ERROR: Maksimal {MAX_CALIB_POINTS} titik kalibrasi")
            return
        self.src_points_px.append(px)
        self.dst_points_mm.append(mm)
        print(f"✓ Added point {len(self.src_points_px)}: pixel=({px[0]:.0f},{px[1]:.0f}) -> mm=({mm[0]},{mm[1]})")
    
    def remove_last_point(self):
        """Remove last added point"""
        if self.src_points_px:
            self.src_points_px.pop()
            self.dst_points_mm.pop()
            print(f"✗ Removed last point")
    
    def calculate_matrix(self):
        """Calculate adaptive transform: translation(1), similarity(2), affine(>=3)."""
        point_count = len(self.src_points_px)
        if point_count < MIN_CALIB_POINTS:
            print(f"ERROR: Minimal {MIN_CALIB_POINTS} titik diperlukan!")
            return None

        src = np.array(self.src_points_px, dtype=np.float64)
        dst = np.array(self.dst_points_mm, dtype=np.float64)

        if point_count == 1:
            tx = float(dst[0, 0] - src[0, 0])
            ty = float(dst[0, 1] - src[0, 1])
            self.matrix = np.array([[1.0, 0.0, tx], [0.0, 1.0, ty]], dtype=np.float64)
            self.fit_mode = "translation"
            return self.matrix

        if point_count == 2:
            v = src[1] - src[0]
            w = dst[1] - dst[0]
            denom = float(v[0] * v[0] + v[1] * v[1])
            if denom < 1e-12:
                print("ERROR: Dua titik pixel terlalu berdekatan")
                return None

            a = float((w[0] * v[0] + w[1] * v[1]) / denom)
            b = float((w[1] * v[0] - w[0] * v[1]) / denom)

            tx = float(dst[0, 0] - (a * src[0, 0] - b * src[0, 1]))
            ty = float(dst[0, 1] - (b * src[0, 0] + a * src[0, 1]))

            self.matrix = np.array([[a, -b, tx], [b, a, ty]], dtype=np.float64)
            self.fit_mode = "similarity"
            return self.matrix

        A = []
        B = []

        for (px_x, px_y), (mm_x, mm_y) in zip(self.src_points_px, self.dst_points_mm):
            A.append([px_x, px_y, 1, 0, 0, 0])
            A.append([0, 0, 0, px_x, px_y, 1])
            B.append(mm_x)
            B.append(mm_y)

        A = np.array(A)
        B = np.array(B)

        params, _, _, _ = np.linalg.lstsq(A, B, rcond=None)

        self.matrix = np.array([
            [params[0], params[1], params[2]],
            [params[3], params[4], params[5]]
        ])
        self.fit_mode = "affine"
        return self.matrix
    
    def calculate_error(self):
        """Calculate reprojection error"""
        if self.matrix is None:
            return None, []
        
        errors = []
        for (px_x, px_y), (mm_x, mm_y) in zip(self.src_points_px, self.dst_points_mm):
            px_h = np.array([px_x, px_y, 1.0])
            mm_pred = self.matrix @ px_h
            error = np.sqrt((mm_pred[0] - mm_x)**2 + (mm_pred[1] - mm_y)**2)
            errors.append(error)
        
        return np.mean(errors), errors
    
    def save(self, output_path=None):
        """Save calibration"""
        if self.matrix is None:
            print("ERROR: Matrix belum dihitung!")
            return False
        
        avg_error, per_errors = self.calculate_error()
        
        calibration_data = {
            "type": "affine2d",
            "fit_mode": self.fit_mode,
            "source": {"calibrate_markers.txt": "pixel coordinates"},
            "matrix": self.matrix.tolist(),
            "src_points_px": [[float(x), float(y)] for x, y in self.src_points_px],
            "dst_points_mm": [[float(x), float(y)] for x, y in self.dst_points_mm],
            "reprojection_error_mm": float(avg_error),
            "per_point_error_mm": [float(e) for e in per_errors]
        }
        
        output = Path(output_path) if output_path else self.calibration_path
        output.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output, 'w') as f:
            json.dump(calibration_data, f, indent=2)
        
        print(f"✓ Saved to: {output}")
        print(f"  Average error: {avg_error:.3f} mm")
        return True
    
    def run_gui(self):
        """Run interactive GUI calibration"""
        print("\n" + "="*60)
        print("CALIBRATION TOOL (GUI)")
        print("="*60)
        print("\nInstruksi:")
        print("1. Pasang PCB dengan titik referensi (marker)")
        print("2. Klik pada titik di gambar untuk dapat pixel coordinate")
        print("3. Masukkan machine coordinate (mm)")
        print("4. Ulangi untuk 1-20 titik (disarankan 9+ tersebar)")
        print("5. Tekan 'c' untuk hitung matrix")
        print("6. Tekan 's' untuk simpan")
        print("7. Tekan 'q' untuk quit")
        print("="*60 + "\n")
        
        # Open camera
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            print("ERROR: Cannot open camera")
            return
        
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        
        window_name = "Calibration Tool"
        cv2.namedWindow(window_name)
        
        def mouse_callback(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN:
                print(f"\nTitik dipilih: ({x}, {y})")
                try:
                    mm_x = float(input("  Masukkan X (mm): "))
                    mm_y = float(input("  Masukkan Y (mm): "))
                    self.add_point_pair((x, y), (mm_x, mm_y))
                    print(f"  Total titik: {len(self.src_points_px)}")
                except ValueError:
                    print("  ERROR: Invalid input")
        
        cv2.setMouseCallback(window_name, mouse_callback)
        
        print("\nKlik pada titik untuk kalibrasi...")
        
        while True:
            ret, frame = cap.read()
            if not ret:
                continue
            
            # Draw existing points
            for i, (px, mm) in enumerate(zip(self.src_points_px, self.dst_points_mm)):
                cv2.circle(frame, (int(px[0]), int(px[1])), 10, (0, 255, 0), -1)
                cv2.putText(frame, f"{i+1}", (int(px[0])+15, int(px[1])), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                cv2.putText(frame, f"({mm[0]},{mm[1]})", (int(px[0])-50, int(px[1])+25), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
            
            # Status overlay
            h, w = frame.shape[:2]
            status = f"Titik: {len(self.src_points_px)} | Matrix: {'OK' if self.matrix is not None else 'Belum'}"
            cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                       0.7, (0, 255, 0), 2)
            
            cv2.putText(frame, "Click:Add | c:Calculate | s:Save | r:Remove | q:Quit", 
                       (10, h-20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
            
            cv2.imshow(window_name, frame)
            
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('c') and len(self.src_points_px) >= MIN_CALIB_POINTS:
                matrix = self.calculate_matrix()
                if matrix is not None:
                    avg_error, _ = self.calculate_error()
                    print(f"\n✓ Matrix dihitung! Mode={self.fit_mode}, Error: {avg_error:.3f} mm")
            elif key == ord('s') and self.matrix is not None:
                self.save()
                print("✓ Disimpan!")
            elif key == ord('r'):
                self.remove_last_point()
        
        cap.release()
        cv2.destroyAllWindows()


def verify_calibration():
    """Verify existing calibration"""
    calib = CalibrationGUI()
    calib.load_existing()
    
    if calib.matrix is None:
        print("ERROR: No calibration found!")
        return
    
    print("\n" + "="*60)
    print("CALIBRATION VERIFICATION")
    print("="*60)
    
    print("\nMatrix:")
    print(calib.matrix)
    
    print(f"\nTitik: {len(calib.src_points_px)}")
    
    avg_error, per_errors = calib.calculate_error()
    print(f"\nReprojection Error:")
    print(f"  Average: {avg_error:.4f} mm")
    print(f"  Min: {min(per_errors):.4f} mm")
    print(f"  Max: {max(per_errors):.4f} mm")
    
    print("\nTest transformation:")
    test_points = [(244, 675), (630, 365), (1047, 72)]
    for px in test_points:
        px_h = np.array([px[0], px[1], 1.0])
        result = calib.matrix @ px_h
        print(f"  Pixel {px} -> Machine ({result[0]:.2f}, {result[1]:.2f})")


def main():
    parser = argparse.ArgumentParser(description='Calibration Tool (GUI)')
    parser.add_argument('--verify', action='store_true', help='Verify calibration')
    parser.add_argument('--clear', action='store_true', help='Clear and start fresh')
    parser.add_argument('--output', type=str, help='Output file')
    
    args = parser.parse_args()
    
    if args.verify:
        verify_calibration()
    else:
        calib = CalibrationGUI()
        
        if args.clear:
            calib.src_points_px = []
            calib.dst_points_mm = []
            calib.matrix = None
            calib.fit_mode = "unknown"
            print("✓ Cleared all points")
        
        # Find camera only when running GUI
        calib.camera_index = calib._find_usb_camera()
        print(f"Camera index: {calib.camera_index}")
        
        calib.run_gui()


if __name__ == "__main__":
    main()