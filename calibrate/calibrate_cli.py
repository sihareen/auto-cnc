"""
Calibration tool berbasis CLI (tanpa GUI)
Untuk sistem headless atau remote

Usage:
    python calibrate/calibrate_cli.py --add           # Tambah titik kalibrasi
    python calibrate/calibrate_cli.py --calculate    # Hitung matrix
    python calibrate/calibrate_cli.py --save         # Simpan hasil
    python calibrate/calibrate_cli.py --verify        # Verifikasi
"""
import argparse
import json
import numpy as np
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIN_CALIB_POINTS = 1
MAX_CALIB_POINTS = 20

class CLICalibrator:
    def __init__(self, calibration_path: str = "config/calibration_affine.json"):
        self.calibration_path = Path(calibration_path)
        self.src_points_px = []
        self.dst_points_mm = []
        self.matrix = None
        self.fit_mode = "unknown"
        self.load_existing()
    
    def load_existing(self):
        """Load existing calibration"""
        if self.calibration_path.exists():
            with open(self.calibration_path) as f:
                data = json.load(f)
            self.matrix = np.array(data['matrix'])
            self.fit_mode = data.get("fit_mode", "affine")
            self.src_points_px = [tuple(p) for p in data['src_points_px']]
            self.dst_points_mm = [tuple(p) for p in data['dst_points_mm']]
            logger.info(f"Loaded existing calibration with {len(self.src_points_px)} points")
    
    def add_point(self, px_x: float, px_y: float, mm_x: float, mm_y: float):
        """Add calibration point"""
        if len(self.src_points_px) >= MAX_CALIB_POINTS:
            print(f"ERROR: Maksimal {MAX_CALIB_POINTS} titik kalibrasi")
            return
        self.src_points_px.append((px_x, px_y))
        self.dst_points_mm.append((mm_x, mm_y))
        print(f"✓ Added point {len(self.src_points_px)}: pixel=({px_x},{px_y}) -> mm=({mm_x},{mm_y})")
    
    def remove_point(self, index: int = -1):
        """Remove last or specific point"""
        if self.src_points_px:
            removed_px = self.src_points_px.pop(index if index >= 0 else len(self.src_points_px)-1)
            removed_mm = self.dst_points_mm.pop()
            print(f"✗ Removed point: {removed_px} -> {removed_mm}")
    
    def list_points(self):
        """List all calibration points"""
        print(f"\n{'='*60}")
        print(f"CALIBRATION POINTS ({len(self.src_points_px)} total)")
        print(f"{'='*60}")
        print(f"{'No':<5} {'Pixel X':<10} {'Pixel Y':<10} {'Machine X':<12} {'Machine Y':<12}")
        print(f"{'-'*5} {'-'*10} {'-'*10} {'-'*12} {'-'*12}")
        for i, (px, mm) in enumerate(zip(self.src_points_px, self.dst_points_mm)):
            print(f"{i+1:<5} {px[0]:<10.1f} {px[1]:<10.1f} {mm[0]:<12.1f} {mm[1]:<12.1f}")
        print(f"{'='*60}")
    
    def calculate_matrix(self):
        """Calculate adaptive transform: translation(1), similarity(2), affine(>=3)."""
        point_count = len(self.src_points_px)
        if point_count < MIN_CALIB_POINTS:
            print(f"ERROR: Minimal {MIN_CALIB_POINTS} titik diperlukan!")
            return False

        src = np.array(self.src_points_px, dtype=np.float64)
        dst = np.array(self.dst_points_mm, dtype=np.float64)

        if point_count == 1:
            tx = float(dst[0, 0] - src[0, 0])
            ty = float(dst[0, 1] - src[0, 1])
            self.matrix = np.array([[1.0, 0.0, tx], [0.0, 1.0, ty]], dtype=np.float64)
            self.fit_mode = "translation"
        elif point_count == 2:
            v = src[1] - src[0]
            w = dst[1] - dst[0]
            denom = float(v[0] * v[0] + v[1] * v[1])
            if denom < 1e-12:
                print("ERROR: Dua titik pixel terlalu berdekatan")
                return False

            a = float((w[0] * v[0] + w[1] * v[1]) / denom)
            b = float((w[1] * v[0] - w[0] * v[1]) / denom)

            tx = float(dst[0, 0] - (a * src[0, 0] - b * src[0, 1]))
            ty = float(dst[0, 1] - (b * src[0, 0] + a * src[0, 1]))

            self.matrix = np.array([[a, -b, tx], [b, a, ty]], dtype=np.float64)
            self.fit_mode = "similarity"
        else:
            # Build system of equations
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

        # Calculate error
        avg_error, per_errors = self.calculate_error()

        print(f"\n{'='*60}")
        print(f"CALIBRATION MATRIX CALCULATED")
        print(f"{'='*60}")
        print(f"\nMode: {self.fit_mode}")
        print(f"Matrix (2x3):")
        print(f"  [{self.matrix[0,0]:.10e}, {self.matrix[0,1]:.10e}, {self.matrix[0,2]:.10e}]")
        print(f"  [{self.matrix[1,0]:.10e}, {self.matrix[1,1]:.10e}, {self.matrix[1,2]:.10e}]")
        print(f"\nReprojection Error:")
        print(f"  Average: {avg_error:.4f} mm")
        print(f"  Min: {min(per_errors):.4f} mm")
        print(f"  Max: {max(per_errors):.4f} mm")
        print(f"{'='*60}")

        return True
    
    def calculate_error(self):
        """Calculate reprojection error"""
        per_errors = []
        for (px_x, px_y), (mm_x, mm_y) in zip(self.src_points_px, self.dst_points_mm):
            px_h = np.array([px_x, px_y, 1.0])
            mm_pred = self.matrix @ px_h
            error = np.sqrt((mm_pred[0] - mm_x)**2 + (mm_pred[1] - mm_y)**2)
            per_errors.append(error)
        return np.mean(per_errors), per_errors
    
    def transform_point(self, px_x: float, px_y: float):
        """Transform pixel to machine coordinates"""
        if self.matrix is None:
            print("ERROR: Matrix belum dihitung!")
            return None
        
        px_h = np.array([px_x, px_y, 1.0])
        mm = self.matrix @ px_h
        return mm[0], mm[1]
    
    def save(self, output_path: str = None):
        """Save calibration to file"""
        if self.matrix is None:
            print("ERROR: Matrix belum dihitung! Jalankan --calculate dulu")
            return False
        
        avg_error, per_errors = self.calculate_error()
        
        calibration_data = {
            "type": "affine2d",
            "fit_mode": self.fit_mode,
            "source": {
                "roi_points": "config/roi_points.json",
                "mark_points": "config/markROI.txt"
            },
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
        
        print(f"\n✓ Calibration saved to: {output}")
        print(f"  Average error: {avg_error:.4f} mm")
        return True
    
    def verify(self):
        """Verify current calibration"""
        if self.matrix is None:
            print("ERROR: Tidak ada calibration loaded!")
            return
        
        print(f"\n{'='*60}")
        print(f"CALIBRATION VERIFICATION")
        print(f"{'='*60}")
        
        print(f"\nMatrix:")
        print(self.matrix)
        
        print(f"\nPoints: {len(self.src_points_px)}")
        
        # Show some test transformations
        print(f"\nTest transformations:")
        if len(self.src_points_px) >= 3:
            test_indices = [0, len(self.src_points_px)//2, len(self.src_points_px)-1]
            for i in test_indices:
                px = self.src_points_px[i]
                expected = self.dst_points_mm[i]
                actual = self.transform_point(px[0], px[1])
                print(f"  Point {i+1}: Pixel {px} -> Machine {actual} (expected: {expected})")
        
        avg_error, per_errors = self.calculate_error()
        print(f"\nError: {avg_error:.4f} mm (min: {min(per_errors):.4f}, max: {max(per_errors):.4f})")
        print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description='CLI Calibration Tool')
    parser.add_argument('--add', nargs=4, metavar=('PX', 'PY', 'MM_X', 'MM_Y'), 
                       type=float, help='Tambah titik: px_x px_y mm_x mm_y')
    parser.add_argument('--from-markers', type=str, help='Load dari marker file (pixel coords saja)')
    parser.add_argument('--remove', action='store_true', help='Hapus titik terakhir')
    parser.add_argument('--list', action='store_true', help='List semua titik')
    parser.add_argument('--calculate', action='store_true', help='Hitung matrix')
    parser.add_argument('--save', action='store_true', help='Simpan calibration')
    parser.add_argument('--verify', action='store_true', help='Verifikasi calibration')
    parser.add_argument('--clear', action='store_true', help='Clear semua titik')
    parser.add_argument('--output', type=str, help='Output file path')
    
    args = parser.parse_args()
    
    calib = CLICalibrator()
    
    # Handle loading from marker file
    if args.from_markers:
        marker_file = Path(args.from_markers)
        if marker_file.exists():
            with open(marker_file) as f:
                lines = f.readlines()
            
            print(f"\nLoaded {len(lines)} markers from {marker_file}")
            print("Catat coordinate mm untuk setiap titik:")
            print("="*50)
            
            # Show each marker and prompt for mm coordinate
            for i, line in enumerate(lines):
                line = line.strip()
                if line.startswith('#') or not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    px_x, px_y = float(parts[0]), float(parts[1])
                    print(f"Titik {i+1}: Pixel ({px_x}, {px_y}) -> Masukkan mm:")
                    
                    # Get mm coords from user
                    try:
                        if len(calib.src_points_px) >= MAX_CALIB_POINTS:
                            print(f"    -> Maksimal {MAX_CALIB_POINTS} titik, sisanya diabaikan")
                            break
                        mm_x = float(input("    X (mm): "))
                        mm_y = float(input("    Y (mm): "))
                        calib.add_point(px_x, px_y, mm_x, mm_y)
                    except ValueError:
                        print("    -> Input invalid, skipped")
            
            print("\nSemua titik berhasil dimuat!")
            print("Lanjutkan dengan --calculate")
        else:
            print(f"ERROR: File tidak ditemukan: {marker_file}")
        return
    
    if args.list:
        calib.list_points()
    
    elif args.add:
        calib.add_point(*args.add)
    
    elif args.remove:
        calib.remove_point()
    
    elif args.clear:
        calib.src_points_px = []
        calib.dst_points_mm = []
        calib.matrix = None
        print("✓ Cleared all calibration points")
    
    elif args.calculate:
        calib.list_points()
        calib.calculate_matrix()
    
    elif args.save:
        calib.save(args.output)
    
    elif args.verify:
        calib.verify()
    
    else:
        # Default: show help and current status
        print("\n" + "="*60)
        print("AUTO CNC CALIBRATION TOOL")
        print("="*60)
        print("\nCurrent status:")
        print(f"  Points: {len(calib.src_points_px)}")
        print(f"  Allowed points: {MIN_CALIB_POINTS}-{MAX_CALIB_POINTS}")
        print(f"  Matrix: {'Calculated' if calib.matrix is not None else 'Not calculated'}")
        
        if calib.matrix is not None:
            avg_error, _ = calib.calculate_error()
            print(f"  Error: {avg_error:.4f} mm")
        
        print("\nUsage:")
        print("  python calibrate/calibrate_cli.py --add 640 360 171.5 -54.8   # Tambah titik")
        print("  python calibrate/calibrate_cli.py --list                        # Lihat titik")
        print("  python calibrate/calibrate_cli.py --calculate                  # Hitung matrix")
        print("  python calibrate/calibrate_cli.py --save                        # Simpan")
        print("  python calibrate/calibrate_cli.py --verify                      # Verifikasi")
        print("  python calibrate/calibrate_cli.py --clear                      # Clear semua")
        print("="*60)


if __name__ == "__main__":
    main()