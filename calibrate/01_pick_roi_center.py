"""
Script untuk memilih ROI center (pixel) dari gambar referensi.

Usage:
    python calibrate/01_pick_roi_center.py image.jpg
    python calibrate/01_pick_roi_center.py --image image.jpg --grid 15 --output roi_center.txt
"""
import argparse
from pathlib import Path
import cv2


class ROICenterPicker:
    def __init__(self, image_path: str, output_file: str | None = None, grid_size: int = 15):
        self.image_path = Path(image_path)
        self.output_file = output_file or f"{self.image_path.stem}_roi_center.txt"
        self.grid_size = max(2, int(grid_size))
        self.img = None
        self.selected_point = None
        self.cursor = (0, 0)

    def run(self) -> bool:
        if not self.image_path.exists():
            print(f"ERROR: Image not found: {self.image_path}")
            return False

        self.img = cv2.imread(str(self.image_path))
        if self.img is None:
            print("ERROR: Cannot load image")
            return False

        h, w = self.img.shape[:2]
        window_name = f"ROI Center Picker - {self.image_path.name}"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 1000, 700)
        cv2.setMouseCallback(window_name, self.mouse_callback)

        print("\n" + "=" * 60)
        print("INSTRUKSI ROI CENTER PICKER")
        print("  - Gerakkan mouse untuk lihat koordinat pixel")
        print("  - Klik kiri untuk pilih ROI center")
        print("  - Tekan 's' untuk simpan")
        print("  - Tekan 'c' untuk clear pilihan")
        print("  - Tekan 'q' untuk keluar")
        print("=" * 60)
        print(f"Image: {self.image_path.name} ({w}x{h})")
        print(f"Grid : {self.grid_size}x{self.grid_size}\n")

        while True:
            display = self._render()
            cv2.imshow(window_name, display)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("Exited without saving")
                break
            if key == ord("c"):
                self.selected_point = None
                print("Cleared selected ROI center")
            if key == ord("s"):
                self.save_point()
                break

        cv2.destroyAllWindows()
        return True

    def _render(self):
        display = self.img.copy()
        h, w = display.shape[:2]

        # Draw grid
        step_x = (w - 1) / float(self.grid_size - 1)
        step_y = (h - 1) / float(self.grid_size - 1)

        for ci in range(self.grid_size):
            x = int(round(ci * step_x))
            color = (55, 65, 85) if (ci % 5) else (80, 110, 150)
            cv2.line(display, (x, 0), (x, h - 1), color, 1)

        for ri in range(self.grid_size):
            y = int(round(ri * step_y))
            color = (55, 65, 85) if (ri % 5) else (80, 110, 150)
            cv2.line(display, (0, y), (w - 1, y), color, 1)

        # Cursor crosshair
        cx, cy = self.cursor
        cv2.line(display, (0, cy), (w - 1, cy), (90, 90, 220), 1)
        cv2.line(display, (cx, 0), (cx, h - 1), (90, 90, 220), 1)
        cv2.circle(display, (cx, cy), 2, (90, 90, 220), -1)

        # Selected ROI center
        if self.selected_point is not None:
            sx, sy = self.selected_point
            cv2.circle(display, (sx, sy), 6, (255, 200, 0), -1)
            cv2.circle(display, (sx, sy), 30, (255, 200, 0), 2)
            cv2.putText(
                display,
                f"ROI CENTER ({sx}, {sy})",
                (max(8, sx - 80), max(20, sy - 38)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 200, 0),
                2,
            )

        info = f"cursor=({cx}, {cy})  selected={self.selected_point}  grid={self.grid_size}x{self.grid_size}"
        cv2.putText(display, info, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (80, 220, 255), 2)
        return display

    def mouse_callback(self, event, x, y, _flags, _param):
        self.cursor = (int(x), int(y))
        if event == cv2.EVENT_LBUTTONDOWN:
            self.selected_point = (int(x), int(y))
            print(f"Selected ROI center: ({x}, {y})")

    def save_point(self):
        if self.selected_point is None:
            print("No ROI center selected. Nothing to save.")
            return

        x, y = self.selected_point
        with open(self.output_file, "w", encoding="utf-8") as f:
            f.write(f"# ROI center from {self.image_path.name}\n")
            f.write(f"roi_center_x_px={x}\n")
            f.write(f"roi_center_y_px={y}\n")

        print(f"\nSaved ROI center to: {self.output_file}")
        print("Set ke config/config.json:")
        print(f'  "roi_center_x_px": {x}.0,')
        print(f'  "roi_center_y_px": {y}.0,')


def main():
    parser = argparse.ArgumentParser(description="Pick ROI center pixel from image")
    parser.add_argument("image", nargs="?", help="Image file path")
    parser.add_argument("--output", "-o", help="Output file for ROI center")
    parser.add_argument("--grid", type=int, default=15, help="Grid size (default: 15)")
    args = parser.parse_args()

    if not args.image:
        print("Usage: python calibrate/01_pick_roi_center.py <image_file>")
        print("\nContoh:")
        print("  python calibrate/01_pick_roi_center.py capture.jpg")
        print("  python calibrate/01_pick_roi_center.py capture.jpg --grid 15 --output roi_center.txt")
        return

    picker = ROICenterPicker(args.image, args.output, args.grid)
    picker.run()


if __name__ == "__main__":
    main()

