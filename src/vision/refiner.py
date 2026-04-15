"""
Per-point XY refinement utilities.

This module is intentionally pure and testable. Runtime integration lives in
`src/ui/server.py`.
"""
from dataclasses import dataclass
from math import sqrt
from typing import List, Optional, Tuple


@dataclass
class RefineConfig:
    max_delta_mm: float = 0.10
    tol_x_mm: float = 0.03
    tol_y_mm: float = 0.03
    search_radius_mm: float = 3.0


@dataclass
class RefineResult:
    x: float
    y: float
    delta_x: float
    delta_y: float
    applied: bool
    status: str
    confidence: Optional[float] = None


class PointRefiner:
    """Stateless point refiner."""

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(value, hi))

    @staticmethod
    def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

    @classmethod
    def refine(
        cls,
        initial_xy: Tuple[float, float],
        candidates_mm: List[Tuple[float, float, float]],
        cfg: RefineConfig,
    ) -> RefineResult:
        """
        Pick nearest valid candidate and apply bounded correction.

        candidates_mm format: [(x_mm, y_mm, confidence), ...]
        """
        ix, iy = float(initial_xy[0]), float(initial_xy[1])
        if not candidates_mm:
            return RefineResult(ix, iy, 0.0, 0.0, False, "skipped_no_candidate", None)

        # Keep only candidates inside search radius.
        filtered = []
        for cx, cy, conf in candidates_mm:
            d = cls._distance((ix, iy), (float(cx), float(cy)))
            if d <= float(cfg.search_radius_mm):
                filtered.append((float(cx), float(cy), float(conf), d))

        if not filtered:
            return RefineResult(ix, iy, 0.0, 0.0, False, "skipped_out_of_radius", None)

        # Nearest candidate wins.
        filtered.sort(key=lambda item: item[3])
        bx, by, bconf, _ = filtered[0]

        dx_raw = bx - ix
        dy_raw = by - iy
        dx = cls._clamp(dx_raw, -cfg.max_delta_mm, cfg.max_delta_mm)
        dy = cls._clamp(dy_raw, -cfg.max_delta_mm, cfg.max_delta_mm)

        if abs(dx) <= cfg.tol_x_mm and abs(dy) <= cfg.tol_y_mm:
            return RefineResult(ix, iy, 0.0, 0.0, False, "skipped_in_tolerance", bconf)

        return RefineResult(
            x=ix + dx,
            y=iy + dy,
            delta_x=dx,
            delta_y=dy,
            applied=True,
            status="applied",
            confidence=bconf,
        )
