"""
Script untuk menambahkan marker pada gambar PCB
dan mendapatkan pixel coordinates untuk kalibrasi

Usage:
    python add_markers.py camera_capture.jpg
    python add_markers.py --image pcb.jpg --output markers.txt
"""
import cv2
import argparse
import sys
from pathlib import Path

class MarkerAdder:
    def __init__(self, image_path, output_file=None):
        self.image_path = Path(image_path)
        self.output_file = output_file or f"{self.image_path.stem}_markers.txt"
        self.markers = []
        self.img = None
        self.clone = None
        
    def run(self):
        """Run marker adder"""
        if not self.image_path.exists():
            print(f"ERROR: Image not found: {self.image_path}")
            return False
        
        # Load image
        self.img = cv2.imread(str(self.image_path))
        if self.img is None:
            print(f"ERROR: Cannot load image")
            return False
        
        self.clone = self.img.copy()
        
        h, w = self.img.shape[:2]
        print(f"\nLoaded: {self.image_path.name} ({w}x{h})")
        print(f"Resolution: {w}x{h}")
        
        # Window
        window_name = f"Marker Adder - {self.image_path.name}"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 800, 600)
        cv2.setMouseCallback(window_name, self.mouse_callback)
        
        print("\n" + "="*50)
        print("INSTRUKSI:")
        print("  - Klik untuk tambahkan marker")
        print("  - Tekan 'z' untuk undo marker terakhir")
        print("  - Tekan 's' untuk simpan")
        print("  - Tekan 'c' untuk clear semua")
        print("  - Tekan 'q' untuk keluar tanpa simpan")
        print("="*50 + "\n")
        
        while True:
            # Draw all markers
            display = self.clone.copy()
            
            # Draw markers
            for i, (x, y) in enumerate(self.markers):
                cv2.circle(display, (int(x), int(y)), 10, (0, 255, 0), -1)
                cv2.putText(display, f"{i+1}", (int(x)+15, int(y)), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                
                # Draw crosshair
                cv2.line(display, (int(x)-15, int(y)), (int(x)+15, int(y)), (0, 255, 255), 1)
                cv2.line(display, (int(x), int(y)-15), (int(x), int(y)+15), (0, 255, 255), 1)
            
            # Info bar
            info = f"Markers: {len(self.markers)} | Click:Add | z:Undo | s:Save | c:Clear | q:Quit"
            cv2.putText(display, info, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
            
            # Show instructions if no markers
            if len(self.markers) == 0:
                hint = "Klik pada titik referensi PCB untuk menambahkan marker"
                cv2.putText(display, hint, (10, h//2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            
            cv2.imshow(window_name, display)
            
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                print("Exited without saving")
                break
            elif key == ord('s'):
                self.save_markers()
                break
            elif key == ord('c'):
                self.markers = []
                self.clone = self.img.copy()
                print("Cleared all markers")
            elif key == ord('z'):
                if self.markers:
                    self.markers.pop()
                    self.clone = self.img.copy()
                    print(f"Removed last marker. Total: {len(self.markers)}")
        
        cv2.destroyAllWindows()
        return True
    
    def mouse_callback(self, event, x, y, flags, param):
        """Handle mouse click"""
        if event == cv2.EVENT_LBUTTONDOWN:
            self.markers.append((float(x), float(y)))
            print(f"Marker {len(self.markers)}: ({x}, {y})")
            
            # Draw on clone
            cv2.circle(self.clone, (x, y), 10, (0, 255, 0), -1)
            cv2.putText(self.clone, f"{len(self.markers)}", (x+15, y), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.line(self.clone, (x-15, y), (x+15, y), (0, 255, 255), 1)
            cv2.line(self.clone, (x, y-15), (x, y+15), (0, 255, 255), 1)
    
    def save_markers(self):
        """Save markers to file"""
        if not self.markers:
            print("No markers to save!")
            return
        
        with open(self.output_file, 'w') as f:
            f.write(f"# Markers from {self.image_path.name}\n")
            f.write(f"# Total: {len(self.markers)} points\n")
            f.write(f"# Format: x y (pixel coordinates)\n")
            f.write("#\n")
            
            for i, (x, y) in enumerate(self.markers):
                f.write(f"{x:.1f} {y:.1f}\n")
        
        print(f"\nSaved {len(self.markers)} markers to: {self.output_file}")
        print("\nGunakan coordinates ini untuk kalibrasi:")
        print("="*50)
        for i, (x, y) in enumerate(self.markers):
            print(f"python calibrate_cli.py --add {x:.1f} {y:.1f} <mm_x> <mm_y>")
        print("="*50)
        print("\nAtau bisa langsung lihat di file:", self.output_file)


def main():
    parser = argparse.ArgumentParser(description='Add markers to PCB image for calibration')
    parser.add_argument('image', nargs='?', help='Image file path')
    parser.add_argument('--output', '-o', help='Output file for markers')
    
    args = parser.parse_args()
    
    if not args.image:
        # Show example
        print("Usage: python add_markers.py <image_file>")
        print("\nContoh:")
        print("  python add_markers.py camera_capture.jpg")
        print("  python add_markers.py pcb.jpg --output my_markers.txt")
        print("\nFile gambar yang tersedia:")
        for f in Path('.').glob('*.jpg'):
            print(f"  - {f.name}")
        return
    
    adder = MarkerAdder(args.image, args.output)
    adder.run()


if __name__ == "__main__":
    main()