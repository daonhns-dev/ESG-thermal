"""
합성 hotspot 양성대조군(positive control) 테스트.

실제 plant 정상 PNG에 인위적으로 밝기 범프(§8-19 make_bump와 같은 원리, 다만
CSV 온도값이 아니라 픽셀 밝기 0~255에 직접 주입)를 얹어 "명백한 국소 이상"을
만들고, 학습된 AE가 이걸 탐지하는지 확인.

판정 기준:
  - AUC 높음(예 0.9+) -> 모델 자체는 국소 이상 탐지 능력 있음.
    그런데도 실제 over-heat 데이터에서 안 되면 "모델은 멀쩡, 실데이터 신호가 약함"
  - AUC 낮음(0.5대) -> GAP 말고 다른 문제가 아직 남아있다는 뜻

사용법 (thermal/image/ 에서):
    python scripts/experiments/synthetic_hotspot_ae_check.py \
        --checkpoint results/checkpoints/ae_plant/<run이름>/best.pth \
        --config configs/config_ae_plant.yaml --n 100
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import yaml
from PIL import Image
from sklearn.metrics import roc_auc_score

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.ae import ConvAutoEncoder


def make_bump(shape, cy, cx, radius, delta):
    h, w = shape
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    dist2 = (yy - cy) ** 2 + (xx - cx) ** 2
    return delta * np.exp(-dist2 / (2 * (radius**2)))


def load_model(config_path, checkpoint_path, device):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)["model"]

    model = ConvAutoEncoder(
        input_channels=cfg["input_channels"],
        latent_dim=cfg["latent_dim"],
        base_channels=cfg.get("base_channels", 32),
        depth=cfg.get("depth", 5),
        use_attention=cfg.get("use_attention", True),
        vae=cfg.get("vae", False),
        spatial_latent=cfg.get("spatial_latent", False)
    ).to(device)
    ckpt=torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    return model


def load_gray_256(path, size=256):
    return np.array(Image.open(path).convert("L").resize((size, size), Image.BILINEAR), dtype=np.float32)


def to_tensor(img, device):
    t = torch.from_numpy(img/ 255.0).float().unsqueeze(0).unsqueeze(0).to(device)
    return t


def main():
    ap = argparse.ArgumentParser() 
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--config", default="configs/config_ae_plant.yaml")
    ap.add_argument("--data_dir", default="data/plant/test/normal")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--radius", type=int, default=20)
    ap.add_argument("--delta", type=float, default=60.0, help="밝기 범프 세기 (0~255 스케일)")
    ap.add_argument("--score_mode", default="pixel_mse")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = load_model(args.config, args.checkpoint, device)

    rng = np.random.default_rng(args.seed)
    paths = sorted(Path(args.data_dir).glob("*.png"))[: args.n]
    print(f"{len(paths)} 장으로 테스트")

    scores, labels = [], []
    with torch.no_grad():
        for p in paths:
            img = load_gray_256(p)

            x = to_tensor(img, device)
            _, s = model.get_anomaly_score(x, multi_scale=True, smooth=True, score_mode=args.score_mode)
            scores.append(s.item())
            labels.append(0)  # 정상

            h, w = img.shape
            cy = rng.integers(args.radius, h - args.radius)
            cx = rng.integers(args.radius, w - args.radius)
            bumped = np.clip(img + make_bump(img.shape, cy, cx, args.radius, args.delta), 0, 255)

            xb = to_tensor(bumped, device)
            _, sb = model.get_anomaly_score(xb, multi_scale=True, smooth=True, score_mode=args.score_mode)
            scores.append(sb.item())
            labels.append(1)

    auc = roc_auc_score(labels, scores)
    print(f"\n합성 hotspot 양성대조군 AUC = {auc:.4f}")
    if auc >= 0.9:
        print("-> 모델은 명백한 국소 이상을 잘 탐지함 (실데이터에서 안 되면 데이터 신호 문제)")
    elif auc <= 0.6:
        print("-> 모델이 명백한 합성 이상조차 못 잡음 (아키텍처에 다른 문제 남아있을 가능성)")
    else:
        print("-> 애매함, radius/delta를 조정해 재확인 권장")


if __name__ == "__main__":
    main()

        
