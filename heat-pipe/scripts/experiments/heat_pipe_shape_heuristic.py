import numpy as np
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "CNN"))
from temp_anomaly_synthetic_sensitivity import find_normal_csv_paths, parse_temp_csv, make_bump, compute_background, LABEL_ROOT, IMAGE_ROOT
from visualize_local_anomaly_map import z_score_map
from scipy.ndimage import binary_erosion, label


def make_sharp_disk(shape, cy, cx, radius, delta):
    h, w = shape
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    mask = (yy - cy) ** 2 + (xx - cx) ** 2 <= radius ** 2
    return np.where(mask, delta, 0.0)


def edge_sharpness(grid, mask):
    if mask.sum() == 0:
        return 0.0
    eroded = binary_erosion(mask)
    boundary = mask & ~eroded
    if boundary.sum() == 0:
        return 0.0
    gy, gx = np.gradient(grid)
    grad_mag = np.sqrt(gy**2 + gx**2)
    return float(grad_mag[boundary].mean())


def circularity(mask):
    area = mask.sum()
    if area == 0:
        return 0.0
    eroded = binary_erosion(mask)
    boundary = mask & ~eroded
    perimeter = boundary.sum()
    if perimeter == 0:
        return 0.0
    return float(4 * np.pi * area / (perimeter**2))


def extract_local_mask(full_mask, center):
    labeled, _ = label(full_mask)
    cy, cx = center
    target = labeled[cy, cx]
    if target == 0:
        return np.zeros_like(full_mask, dtype=bool)
    return labeled == target

def main():
    LOCAL_LABEL_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "hv_motor_raw", "labels")
    LOCAL_IMAGE_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "hv_motor_raw", "csv")
    paths = find_normal_csv_paths(LOCAL_LABEL_ROOT, LOCAL_IMAGE_ROOT, limit=5)
    grid = parse_temp_csv(paths[0])
    h, w = grid.shape
    cy, cx = h // 2, w // 2
    radius, delta = 15, 15.0

    bump_grid = grid + make_bump(grid.shape, cy, cx, radius, delta)
    disk_grid = grid + make_sharp_disk(grid.shape, cy, cx, radius, delta)

    for name, g in [("bump", bump_grid), ("disk", disk_grid)]:
        z = z_score_map(g, "gaussian", 45)
        mask = z > 3.0
        mask = extract_local_mask(mask, (cy, cx))
        print(f"{name}: edge_sharpness={edge_sharpness(g, mask):.3f} circularity={circularity(mask):.3f}  pixels={mask.sum()}")


if __name__ == "__main__":
    main()
