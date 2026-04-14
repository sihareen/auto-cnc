"""
Script untuk kalibrasi dari marker file dan machine coordinates
Bisa dijalankan interaktif atau dengan file input

Usage:
    python calibrate/calibrate_from_markers.py                    # Interactive mode
    python calibrate/calibrate_from_markers.py --markers markers.txt  # Load dari file
"""
import json
import numpy as np
import argparse
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_MARKER_FILE = "calibrate_markers.txt"
MIN_CALIB_POINTS = 1
MAX_CALIB_POINTS = 20

def load_pixel_coordinates(marker_file):
    """Load pixel coordinates dari marker file"""
    markers = []
    with open(marker_file) as f:
        for line in f:
            line = line.strip()
            if line.startswith('#') or not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                x, y = float(parts[0]), float(parts[1])
                markers.append((x, y))
    return markers

def get_machine_coordinates(num_points):
    """Get machine coordinates untuk setiap titik dari user"""
    mm_coords = []
    print(f"\nMasukkan machine coordinates untuk {num_points} titik:")
    print("="*50)
    
    for i in range(num_points):
        print(f"\nTitik {i+1}:")
        while True:
            try:
                x = float(input("  X (mm): "))
                y = float(input("  Y (mm): "))
                mm_coords.append((x, y))
                break
            except ValueError:
                print("  ERROR: Invalid number, try again")
    
    return mm_coords

def save_machine_coords(mm_coords, output_file="machine_coords.txt"):
    """Simpan machine coordinates ke file"""
    with open(output_file, 'w') as f:
        f.write(f"# Machine coordinates\n")
        f.write(f"# Total: {len(mm_coords)} points\n")
        for i, (x, y) in enumerate(mm_coords):
            f.write(f"{x} {y}\n")
    print(f"Machine coordinates saved to: {output_file}")

def calculate_affine_matrix(src_points, dst_points):
    """Calculate adaptive transform: translation(1), similarity(2), affine(>=3)."""
    point_count = len(src_points)
    if point_count < MIN_CALIB_POINTS:
        raise ValueError(f"Minimal {MIN_CALIB_POINTS} titik diperlukan")

    src = np.array(src_points, dtype=np.float64)
    dst = np.array(dst_points, dtype=np.float64)

    if point_count == 1:
        tx = float(dst[0, 0] - src[0, 0])
        ty = float(dst[0, 1] - src[0, 1])
        matrix = np.array([[1.0, 0.0, tx], [0.0, 1.0, ty]], dtype=np.float64)
        return matrix, "translation"

    if point_count == 2:
        v = src[1] - src[0]
        w = dst[1] - dst[0]
        denom = float(v[0] * v[0] + v[1] * v[1])
        if denom < 1e-12:
            raise ValueError("Dua titik pixel terlalu berdekatan")

        a = float((w[0] * v[0] + w[1] * v[1]) / denom)
        b = float((w[1] * v[0] - w[0] * v[1]) / denom)
        tx = float(dst[0, 0] - (a * src[0, 0] - b * src[0, 1]))
        ty = float(dst[0, 1] - (b * src[0, 0] + a * src[0, 1]))

        matrix = np.array([[a, -b, tx], [b, a, ty]], dtype=np.float64)
        return matrix, "similarity"

    A = []
    B = []

    for (px_x, px_y), (mm_x, mm_y) in zip(src_points, dst_points):
        A.append([px_x, px_y, 1, 0, 0, 0])
        A.append([0, 0, 0, px_x, px_y, 1])
        B.append(mm_x)
        B.append(mm_y)

    A = np.array(A)
    B = np.array(B)

    params, _, _, _ = np.linalg.lstsq(A, B, rcond=None)

    matrix = np.array([
        [params[0], params[1], params[2]],
        [params[3], params[4], params[5]]
    ])

    return matrix, "affine"

def calculate_reprojection_error(matrix, src_points, dst_points):
    """Calculate reprojection error"""
    errors = []
    for (px_x, px_y), (mm_x, mm_y) in zip(src_points, dst_points):
        px_h = np.array([px_x, px_y, 1.0])
        mm_pred = matrix @ px_h
        error = np.sqrt((mm_pred[0] - mm_x)**2 + (mm_pred[1] - mm_y)**2)
        errors.append(error)
    return np.mean(errors), errors

def save_calibration(matrix, fit_mode, src_points, dst_points, output_file="config/calibration_affine.json"):
    """Save calibration to file"""
    avg_error, per_errors = calculate_reprojection_error(matrix, src_points, dst_points)
    
    calibration_data = {
        "type": "affine2d",
        "fit_mode": fit_mode,
        "source": {
            "roi_points": "config/roi_points.json",
            "mark_points": "calibrate_markers.txt"
        },
        "matrix": matrix.tolist(),
        "src_points_px": [[float(x), float(y)] for x, y in src_points],
        "dst_points_mm": [[float(x), float(y)] for x, y in dst_points],
        "reprojection_error_mm": float(avg_error),
        "per_point_error_mm": [float(e) for e in per_errors]
    }
    
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w') as f:
        json.dump(calibration_data, f, indent=2)
    
    return avg_error

def main():
    parser = argparse.ArgumentParser(description='Calibrate dari marker file')
    parser.add_argument('--markers', type=str, default=DEFAULT_MARKER_FILE,
                       help='Marker file (pixel coordinates)')
    parser.add_argument('--machine', type=str, 
                       help='Machine coordinates file (opsional)')
    parser.add_argument('--output', type=str, default='config/calibration_affine.json',
                       help='Output calibration file')
    
    args = parser.parse_args()
    
    # Load pixel coordinates
    marker_path = Path(args.markers)
    if not marker_path.exists():
        print(f"ERROR: Marker file tidak ditemukan: {args.markers}")
        print(f"\nPastikan file {args.markers} ada")
        return
    
    pixel_coords = load_pixel_coordinates(args.markers)
    if len(pixel_coords) > MAX_CALIB_POINTS:
        print(f"WARNING: Marker lebih dari {MAX_CALIB_POINTS}, hanya {MAX_CALIB_POINTS} pertama dipakai")
        pixel_coords = pixel_coords[:MAX_CALIB_POINTS]
    print(f"\nLoaded {len(pixel_coords)} pixel coordinates dari {args.markers}")
    
    # Load machine coordinates dari file atau input manual
    machine_coords = []
    
    if args.machine:
        # Dari file
        machine_path = Path(args.machine)
        if machine_path.exists():
            with open(machine_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('#') or not line:
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        x, y = float(parts[0]), float(parts[1])
                        machine_coords.append((x, y))
            print(f"Loaded {len(machine_coords)} machine coordinates dari {args.machine}")
        else:
            print(f"WARNING: Machine file tidak ditemukan: {args.machine}")
            machine_coords = get_machine_coordinates(len(pixel_coords))
    else:
        # Input manual
        machine_coords = get_machine_coordinates(len(pixel_coords))
    
    # Validasi jumlah titik
    if len(pixel_coords) != len(machine_coords):
        print(f"ERROR: Jumlah titik tidak cocok! {len(pixel_coords)} pixel vs {len(machine_coords)} machine")
        return

    if len(pixel_coords) < MIN_CALIB_POINTS:
        print(f"ERROR: Minimal {MIN_CALIB_POINTS} titik diperlukan")
        return
    
    print("\n" + "="*50)
    print("PIXEL -> MACHINE MAPPING:")
    print("="*50)
    for i, (px, mm) in enumerate(zip(pixel_coords, machine_coords)):
        print(f"  {i+1}. ({px[0]:.1f}, {px[1]:.1f}) -> ({mm[0]}, {mm[1]})")
    
    # Calculate matrix
    print("\nMenghitung affine matrix...")
    matrix, fit_mode = calculate_affine_matrix(pixel_coords, machine_coords)
    
    print("\nAffine Matrix:")
    print(f"  [{matrix[0,0]:.10e}, {matrix[0,1]:.10e}, {matrix[0,2]:.10e}]")
    print(f"  [{matrix[1,0]:.10e}, {matrix[1,1]:.10e}, {matrix[1,2]:.10e}]")
    print(f"Mode: {fit_mode}")
    
    # Save calibration
    avg_error = save_calibration(matrix, fit_mode, pixel_coords, machine_coords, args.output)
    
    print("\n" + "="*50)
    print("CALIBRATION COMPLETE!")
    print("="*50)
    print(f"Saved to: {args.output}")
    print(f"Average reprojection error: {avg_error:.4f} mm")
    
    # Verify dengan test
    print("\nTest transformation:")
    for i in range(min(3, len(pixel_coords))):
        px = pixel_coords[i]
        expected = machine_coords[i]
        px_h = np.array([px[0], px[1], 1.0])
        result = matrix @ px_h
        print(f"  Point {i+1}: ({px[0]:.1f}, {px[1]:.1f}) -> ({result[0]:.2f}, {result[1]:.2f})  (expected: {expected})")

if __name__ == "__main__":
    main()