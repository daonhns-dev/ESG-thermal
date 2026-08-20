import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "CNN"))
sys.path.insert(0, os.path.dirname(__file__))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from heat_pipe_shape_heuristic import make_sharp_disk, edge_sharpness, extract_local_mask
from temp_anomaly_synthetic_sensitivity import find_normal_csv_paths, parse_temp_csv, make_bump
from visualize_local_anomaly_map import z_score_map

LOCAL_LABEL_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "hv_motor_raw", "labels")
LOCAL_IMAGE_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "hv_motor_raw", "csv")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "results", "heat_pipe_shape_sweep")

def evaluate_one(grid, cy, cx, radius, delta):
    rst = {}
    for name, inject_fn in [("bump", make_bump), ("disk", make_sharp_disk)]:
        g = grid + inject_fn(grid.shape, cy, cx, radius, delta)
        z = z_score_map(g, "gaussian", 45)
        mask = extract_local_mask(z > 3.0, (cy, cx))

        rst[name] = edge_sharpness(g, mask) if mask.sum() > 0 else None
    return rst


def save_comparison(grid, cy, cx, radius, delta, out_path):
    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    for row, (name, inject_fn) in enumerate([("bump", make_bump), ("disk", make_sharp_disk)]):
        g = grid + inject_fn(grid.shape, cy, cx, radius, delta)
        z = z_score_map(g, "gaussian", 45)
        mask = extract_local_mask(z > 3.0, (cy, cx))
        es = edge_sharpness(g, mask) if mask.sum() > 0 else -1

        axes[row][0].imshow(g, cmap="inferno")
        axes[row][0].set_title(f"[{name}] raw temp")
        axes[row][1].imshow(z, cmap="coolwarm", vmin=-5, vmax=5)
        axes[row][1].set_title("z-score")
        axes[row][2].imshow(g, cmap="gray")
        overlay = np.zeros((*mask.shape, 4))
        overlay[mask] = [1, 0, 0, 0.6]
        axes[row][2].imshow(overlay)
        axes[row][2].set_title(f"mask (edge_sharpness={es:.2f})")

    plt.suptitle(f"radius={radius} delta={delta}")
    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    plt.close(fig)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = np.random.default_rng(42)
    paths = find_normal_csv_paths(LOCAL_LABEL_ROOT, LOCAL_IMAGE_ROOT, limit=30)
    print(f"{len(paths)}개 프레임으로 검증")

    for radius in [8, 15, 25]:
        for delta in [5, 15, 30]:
            bump_vals, disk_vals = [], []
            saved_image = False
            for p in paths:
                grid = parse_temp_csv(p)
                if grid is None:
                    continue
                h, w = grid.shape
                if h <= 2 * radius or w <= 2 * radius:
                    continue
                cy = rng.integers(radius, h - radius)
                cx = rng.integers(radius, w - radius)

                if not saved_image:
                    out_path = os.path.join(OUT_DIR, f"r{radius}_d{delta}.png")
                    save_comparison(grid, cy, cx, radius, delta, out_path)
                    saved_image = True

                res = evaluate_one(grid, cy, cx, radius, delta)
                if res["bump"] is not None:
                    bump_vals.append(res["bump"])
                if res["disk"] is not None:
                    disk_vals.append(res["disk"])

            if not bump_vals or not disk_vals:
                print(f"radius={radius:2d} delta={delta:2d}: 표본 부족 (미검출)")
                continue
            separable = min(disk_vals) > max(bump_vals)
            print(f"radius={radius:2d} delta={delta:2d}  "
                  f"bump={np.mean(bump_vals):.2f}(n={len(bump_vals)})  "
                  f"disk={np.mean(disk_vals):.2f}(n={len(disk_vals)})  "
                  f"완전분리={'Y' if separable else 'N'}")


if __name__ == "__main__":
    main()