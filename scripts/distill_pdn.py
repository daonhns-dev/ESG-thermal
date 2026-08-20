"""
PDN 증류 (Algorithm 3) — 열화상 도메인 적응 구현.

논문 원문과의 차이:
  - 논문: ImageNet 이미지로 증류 (자연 이미지 도메인)
  - 여기: data/train의 열화상 정상 이미지로 증류 (열화상 도메인 적응)

파이프라인:
  1) python scripts/distill_pdn.py --config configs/config_efficientad.yaml → teacher_distilled_thermal.pth 저장
  2) config에서 teacher_checkpoint 경로를 thermal 버전으로 교체
  3) results/checkpoints/efficientad/teacher_feat_norm.pt 삭제 (캐시 무효화)
  4) python scripts/train_efficientad.py --config configs/config_efficientad.yaml

Backbone 특징 추출:
  - Wide ResNet 101-2 (ImageNet 사전학습, 동결)
  - layer1 출력: (B, 256, 64, 64) — PDN 출력(64×64)과 공간 해상도 일치
  - 고정 직교 projection: 256ch → teacher_out_channels(384)
  - Backbone 동결: 학습 중 업데이트 없음

손실:
  L = MSE(PDN(x), Backbone(x))
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import yaml
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.efficientad_norm import imagenet_normalize  # noqa: E402
from models.pdn import PatchDescriptionNetwork  # noqa: E402


# =====================================================================
# Backbone: WRN-101-2 layer1 + 고정 projection
# =====================================================================

class BackboneFeatureExtractor(nn.Module):
    """
    Wide ResNet 101-2 (ImageNet 사전학습) → layer1 특징 추출.

    입력 256×256 기준:
      conv1 → maxpool → layer1 : (B, 256, 64, 64)  ← PDN 출력과 공간 해상도 일치

    고정 직교 projection (512 또는 256 → out_channels):
      - 학습 중 파라미터 업데이트 없음
      - seed로 재현 가능한 초기화
    """

    def __init__(self, out_channels: int = 384, seed: int = 42):
        super().__init__()
        import torchvision.models as tvm

        try:
            wrn = tvm.wide_resnet101_2(
                weights=tvm.Wide_ResNet101_2_Weights.IMAGENET1K_V2
            )
        except AttributeError:
            wrn = tvm.wide_resnet101_2(pretrained=True)

        # layer1 출력: (B, 256, 64, 64) for 256×256 input
        self.extractor = nn.Sequential(
            wrn.conv1, wrn.bn1, wrn.relu, wrn.maxpool,
            wrn.layer1,
        )
        backbone_ch = 256  # layer1 output channels

        # 고정 직교 projection: backbone_ch → out_channels
        # Conv2d 1×1로 구현, 한 번만 초기화 후 동결
        self.proj = nn.Conv2d(backbone_ch, out_channels, kernel_size=1, bias=False)
        _rng = torch.random.get_rng_state()
        torch.manual_seed(seed)
        nn.init.orthogonal_(self.proj.weight.view(out_channels, backbone_ch))
        torch.random.set_rng_state(_rng)

        for p in self.parameters():
            p.requires_grad = False
        self.eval()

    @torch.no_grad()
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feat = self.extractor(x)   # (B, 256, 64, 64)
        return self.proj(feat)     # (B, out_channels, 64, 64)


# =====================================================================
# Dataset: 열화상 → gray2rgb
# =====================================================================

class ThermalDistillDataset(Dataset):
    """
    열화상 이미지를 gray2rgb로 로드.
    gray_prob: 무작위로 grayscale 변환 적용 확률 (Algorithm 3 line 6).
               열화상은 이미 grayscale이라 큰 차이 없으나 다양성 확보용.
    """

    def __init__(self, root_dir: str, image_size: int = 256, gray_prob: float = 0.5,):
        root = Path(root_dir)
        exts = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}

        paths = []
        for d in root.rglob("*"):
            if d.is_file() and d.suffix.lower() in exts:
                paths.append(d)

        self.paths = sorted(paths)
        self.base_transform = transforms.Compose([
            transforms.Resize((image_size, image_size)),
            transforms.ToTensor(),
        ])
        self.gray_prob = gray_prob

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, idx: int) -> torch.Tensor:
        img = Image.open(self.paths[idx]).convert("L").convert("RGB")  # gray2rgb
        if random.random() < self.gray_prob:
            img = img.convert("L").convert("RGB")  # 명시적 grayscale 유지
        return self.base_transform(img)


# =====================================================================
# 증류 학습 루프
# =====================================================================

def train_distillation(cfg: dict) -> None:
    seed = int(cfg.get("seed", 42))
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)

    device_str = cfg.get("device", "cpu")
    device = torch.device("cuda" if device_str == "cuda" and torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    d = cfg["distillation"]
    m = cfg["model"]
    data_cfg = cfg["data"]
    log_cfg = cfg.get("logging", {})

    total_iters  = int(d.get("iterations", 60000))
    batch_size   = int(d.get("batch_size", 16))
    lr           = float(d.get("learning_rate", 1e-4))
    wd           = float(d.get("weight_decay", 1e-5))
    gray_prob    = float(d.get("gray_prob", 0.5))
    save_path    = Path(d.get(
        "save_path",
        "results/checkpoints/efficientad/teacher_distilled_thermal.pth",
    ))
    variant      = m.get("variant", "S")
    out_ch       = int(m.get("teacher_out_channels", 384))
    in_ch        = int(m.get("in_channels", 3))
    image_size   = int(data_cfg.get("image_size", 256))
    nw           = int(cfg.get("training", {}).get("num_workers", 4))
    log_interval = int(log_cfg.get("log_interval", 500))
    pin          = device.type == "cuda"

    # ----- Backbone (동결) -----
    print("\nLoading Wide ResNet 101-2 backbone (ImageNet pretrained)...")
    backbone = BackboneFeatureExtractor(out_channels=out_ch, seed=seed).to(device)
    backbone.eval()
    print(f"  Backbone output: ({out_ch}, 64, 64)  [고정 직교 projection 256→{out_ch}]")

    # ----- Teacher PDN (학습 대상) -----
    teacher = PatchDescriptionNetwork(variant=variant, out_channels=out_ch, in_channels=in_ch,).to(device)
    teacher.train()
    n_params = sum(p.numel() for p in teacher.parameters())
    print(f"  Teacher PDN-{variant} params: {n_params:,}")

    # ----- Optimizer -----
    optimizer = torch.optim.Adam(teacher.parameters(), lr=lr, weight_decay=wd)

    # ----- Dataset -----
    print(f"\n데이터: {data_cfg['train_dir']}  (gray_prob={gray_prob})")
    dataset = ThermalDistillDataset(
        root_dir=data_cfg["train_dir"],
        image_size=image_size,
        gray_prob=gray_prob,
    )
    if len(dataset) == 0:
        raise RuntimeError(
            f"데이터셋이 비어 있습니다: {data_cfg['train_dir']}\n"
            "data/train/normal/ 하위에 이미지가 있는지 확인하세요."
        )
    loader = DataLoader(
        dataset, batch_size=batch_size, shuffle=True,
        num_workers=nw, pin_memory=pin, drop_last=True,
    )
    print(f"  이미지: {len(dataset)}장, batch={batch_size}, "
          f"epoch당 {len(loader)}배치")

    # ----- 학습 루프 -----
    print(f"\n{'='*60}")
    print(f"  Algorithm 3 (열화상 도메인 Teacher 증류)")
    print(f"  {total_iters:,} iterations | lr={lr} | variant={variant}")
    print(f"{'='*60}\n")

    data_iter = iter(loader)
    running_loss = 0.0
    t0 = time.time()

    for iteration in range(1, total_iters + 1):
        try:
            images = next(data_iter)
        except StopIteration:
            data_iter = iter(loader)
            images = next(data_iter)

        # images: (B, 3, H, W) — gray2rgb, [0,1]
        images = images.to(device)
        xn = imagenet_normalize(images)  

        # Backbone 특징 
        with torch.no_grad():
            target = backbone(xn)  # (B, out_ch, 64, 64)

        # Teacher PDN 예측
        pred = teacher(xn)  # (B, out_ch, H', W')

        # 공간 크기 불일치 시 align (안전 장치)
        if pred.shape[-2:] != target.shape[-2:]:
            pred = F.interpolate(pred, size=target.shape[-2:], mode="bilinear", align_corners=False)

        # MSE 손실
        loss = F.mse_loss(pred, target)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        running_loss += loss.item()

        if iteration % log_interval == 0:
            elapsed = time.time() - t0
            it_s = iteration / max(elapsed, 1e-8)
            eta = (total_iters - iteration) / max(it_s, 1e-8)
            avg = running_loss / log_interval
            print(
                f"  [{iteration:>6d}/{total_iters}] "
                f"loss={avg:.6f}  "
                f"lr={lr:.1e}  "
                f"ETA={eta/60:.1f}m"
            )
            running_loss = 0.0

    # ----- 저장 -----
    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  증류 완료: {total_iters:,} iterations, {elapsed/60:.1f}분")
    print(f"{'='*60}\n")

    save_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(teacher.state_dict(), save_path)
    print(f"Teacher PDN 저장: {save_path}")

    # ----- 다음 단계 안내 -----
    norm_cache = Path(
        cfg.get("training", {}).get(
            "teacher_norm_cache",
            "results/checkpoints/efficientad/teacher_feat_norm.pt",
        )
    )
    print(f"""
다음 단계:
  1) config에서 teacher_checkpoint 경로 변경:
       teacher_checkpoint: "{save_path}"
  2) Teacher 통계 캐시 삭제 (재계산 필요):
       del {norm_cache}
  3) 재학습:
       python scripts/train_efficientad.py --config configs/config_efficientad.yaml
""")


# =====================================================================
# CLI
# =====================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PDN Teacher 증류 (Algorithm 3) — 열화상 도메인 적응"
    )
    parser.add_argument(
        "--config", type=str,
        default=str(PROJECT_ROOT / "configs" / "config_efficientad.yaml"),
    )
    parser.add_argument(
        "--save_path", type=str, default=None,
        help="저장 경로 override (미지정 시 config distillation.save_path 사용)",
    )
    parser.add_argument(
        "--iterations", type=int, default=None,
        help="iteration 수 override (파이프라인 체크용, 예: 500)",
    )
    parser.add_argument(
        "--batch_size", type=int, default=None,
        help="batch size override (파이프라인 체크용, 예: 2)",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if args.save_path:
        cfg.setdefault("distillation", {})["save_path"] = args.save_path
    if args.iterations:
        cfg.setdefault("distillation", {})["iterations"] = args.iterations
    if args.batch_size:
        cfg.setdefault("distillation", {})["batch_size"] = args.batch_size

    print(f"Config: {args.config}")
    train_distillation(cfg)


if __name__ == "__main__":
    main()
