"""
EfficientAD 학습 진입점 (Algorithm 1 스켈레톤 + Teacher 채널 정규화).

논문 2단계:
  • Algorithm 3: ``distill_pdn.py`` 로 Teacher PDN 학습 → teacher_checkpoint 저장
    (또는 ``utils/efficientad_weights.py`` 로 공식 가중치 다운로드)
  • Algorithm 1: 본 스크립트에서 Teacher 로드·동결 후 Student+AE 학습

한 iteration 손실 구조:
  1) 원본 x : Teacher·Student forward → L_hard
     ImageNet P : Student forward → L_penalty = ||S(P)||²
     L_ST = L_hard + L_penalty (별도 λ 없음)
  2) augmented x_aug : forward_train → L_AE + L_STAE
  L_total = L_ST + L_AE + L_STAE (가중치 1:1:1)

S–T 경로에는 augmentation 없음. AE 분기만 ``utils/efficientad_augment`` 사용.

사용법:
  python scripts/train_efficientad.py --config configs/config_efficientad.yaml
  python scripts/train_efficientad.py --config configs/config_efficientad.yaml --skip-teacher-stats
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
import yaml
from torch.utils.data import DataLoader, Subset

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import ThermalImageDataset, create_efficientad_train_loader  # noqa: E402
from datasets.imagenet_proxy import ImageNetSubsetDataset  # noqa: E402
from models.efficientad import EfficientAD  # noqa: E402
from models.efficientad_norm import imagenet_normalize  # noqa: E402
from utils.efficientad_augment import efficientad_ae_augment  # noqa: E402
from utils.efficientad_stats import compute_teacher_output_channel_stats  # noqa: E402
from utils.losses import hard_feature_loss, pretraining_penalty, ae_loss, stae_loss  # noqa: E402


# =====================================================================
# ImageNet penalty 로더
# =====================================================================
 
def _make_imagenet_loader(cfg: dict, image_size: int, num_workers: int, pin: bool):
    """ImageNet 서브셋 로더 생성. 경로가 없으면 None 반환."""
    from torchvision import transforms
 
    imagenet_path = cfg["data"].get("imagenet_path", "")
    if not imagenet_path or not Path(imagenet_path).is_dir():
        return None
 
    gray_prob = cfg["data"].get("imagenet_gray_prob", 0.3)
    transform = transforms.Compose([
        transforms.Resize(512),
        transforms.RandomGrayscale(p=gray_prob),
        transforms.CenterCrop(image_size),
        transforms.ToTensor(),
    ])
    ds = ImageNetSubsetDataset(imagenet_path, transform=transform, gray_prob=0.0)
    if len(ds) == 0:
        return None
    return DataLoader(
        ds, batch_size=1, shuffle=True,
        num_workers=min(num_workers, 2), pin_memory=pin, drop_last=True,
    )


# =====================================================================
# Validation 로더 (분위수 정규화용)
# =====================================================================
 
def _split_train_val(full_dataset, val_ratio: float, seed: int):
    n = len(full_dataset)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    n_val = max(1, int(n * val_ratio))
    return perm[n_val:].tolist(), perm[:n_val].tolist()


# =====================================================================
# 분위수 맵 정규화 (Algorithm 1, lines 44-57)
# =====================================================================

@torch.no_grad()
def compute_map_normalization(model: EfficientAD, val_loader: DataLoader, device: torch.device, q_a: float = 0.9, q_b: float = 0.995,) -> None:
    """Validation 이미지로 local/global map 분위수 기준 계산.

    local_map_raw / global_map_raw (64×64, 정규화 전)를 직접 사용합니다.
    분위수 추정과 추론 시 적용이 동일 해상도·공간에서 이루어집니다.
    """
    model.eval()
    all_local, all_global = [], []

    for batch in val_loader:
        images = batch[0].to(device)
        out = model(images)
        # raw 64×64 map
        all_local.append(out["local_map_raw"].flatten().cpu())
        all_global.append(out["global_map_raw"].flatten().cpu())

    local_flat = torch.cat(all_local).to(device)
    global_flat = torch.cat(all_global).to(device)
    model.set_quantiles_from_maps(local_flat, global_flat, q_a, q_b)

    print(f"  Quantile: q_a_st={model.q_a_st.item():.6f}, "
          f"q_b_st={model.q_b_st.item():.6f}, "
          f"q_a_ae={model.q_a_ae.item():.6f}, "
          f"q_b_ae={model.q_b_ae.item():.6f}")


# =====================================================================
# checkpoint save/load
# =====================================================================
 
def _save_checkpoint(model: EfficientAD, optimizer, iteration: int, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "iteration": iteration,
        "teacher": model.teacher.state_dict(),
        "student": model.student.state_dict(),
        "autoencoder": model.autoencoder.state_dict(),
        "optimizer": optimizer.state_dict(),
        "teacher_feat_mu": model.teacher_feat_mu.cpu(),
        "teacher_feat_sigma": model.teacher_feat_sigma.cpu(),
        "q_a_st": model.q_a_st.cpu(),
        "q_b_st": model.q_b_st.cpu(),
        "q_a_ae": model.q_a_ae.cpu(),
        "q_b_ae": model.q_b_ae.cpu(),
        "calibrated": model.calibrated.cpu(),
    }, path)
    print(f"  Checkpoint saved: {path}")

# =====================================================================
# Main training loop (Algorithm 1)
# =====================================================================

def _brightness_roi(img: torch.Tensor, k: float, out_hw) -> torch.Tensor:
    """
    입력 열화상 밝기(=온도) 기반 고온 전경(ROI) 마스크 → feature 해상도로 다운샘플.

    Args:
        img:    (B, C, H, W) 입력 이미지 (EfficientAD 입력, [0,1] 부근)
        k:      임계 계수. 픽셀 > 이미지별 평균 + k·std → ROI(=1)
        out_hw: (h, w) 출력(feature) 해상도, 예: (64, 64)
    Returns:
        mask: (B, 1, h, w) 0/1
    """
    g = img.mean(dim=1, keepdim=True)                       # (B,1,H,W)
    B = g.shape[0]
    flat = g.reshape(B, -1)
    mean = flat.mean(dim=1).view(B, 1, 1, 1)
    std = flat.std(dim=1).view(B, 1, 1, 1)
    mask = (g > (mean + k * std)).float()
    if tuple(mask.shape[-2:]) != tuple(out_hw):
        mask = F.interpolate(mask, size=tuple(out_hw), mode="nearest")
    return mask


def train(cfg: dict, skip_teacher_stats: bool = False, roi: bool = False, roi_k: float = 0.3) -> None:
    seed = cfg.get("seed", 42)
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)

    device_s = cfg.get("device", "cpu")
    device = torch.device("cuda" if device_s == "cuda" and torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    
    m = cfg["model"]
    tr = cfg["training"]
    data_cfg = cfg["data"]
    norm_cfg = cfg["normalization"]

    # ----- model -----
    model = EfficientAD.build_default(
        variant=m.get("variant", "S"),
        in_channels=int(m.get("in_channels", 3)),
        teacher_out=int(m.get("teacher_out_channels", 384)),
        student_out=int(m.get("student_out_channels", 768)),
        with_bn=bool(m.get("with_bn", False)),
    ).to(device)

    # ----- teacher weights load -----
    ckpt_teacher = Path(tr.get("teacher_checkpoint", ""))
    if ckpt_teacher.is_file():
        sd = torch.load(ckpt_teacher, map_location=device, weights_only=False)
        if isinstance(sd, dict) and "state_dict" in sd:
            sd = sd["state_dict"]
        if isinstance(sd, dict) and "teacher" in sd:
            sd = sd["teacher"]
        if isinstance(sd, dict):
            model.teacher.load_state_dict(sd, strict=True)
        print(f"Teacher weights loaded from {ckpt_teacher}")
    else:
        print(f"Teacher weights not found at {ckpt_teacher}")

    model.teacher.eval()
    for p in model.teacher.parameters():
        p.requires_grad = False

    # ----- Data loader -----
    print("\n--- Data loader ---")
    image_size = int(data_cfg.get("image_size", 256))
    nw = int(tr.get("num_workers", 4))
    pin = device.type == "cuda"

    stats_loader = create_efficientad_train_loader(
        train_dir=data_cfg["train_dir"],
        batch_size=int(tr.get("teacher_stats_batch_size", 8)),
        image_size=image_size,
        num_workers=nw,
        shuffle=False,
        pin_memory=pin,
        train_fraction=float(data_cfg.get("train_fraction", 1.0)),
        subset_seed=seed,
    )

    # Train/validation split (train 90% / val 10%)
    full_train_dataset = stats_loader.dataset

    val_ratio = norm_cfg.get("val_ratio", 0.1)
    val_seed = norm_cfg.get("quantile_split_seed", seed)
    train_idx, val_idx = _split_train_val(full_train_dataset, val_ratio, val_seed)

    train_subset = Subset(full_train_dataset, train_idx)
    val_subset = Subset(full_train_dataset, val_idx)

    bs_train = int(tr.get("batch_size", 1))
    train_loader = DataLoader(train_subset, batch_size=bs_train, shuffle=True, num_workers=nw, pin_memory=pin, drop_last=True,)
    val_loader = DataLoader(val_subset, batch_size=8, shuffle=False, num_workers=nw, pin_memory=pin,)
    print(f"  Train: {len(train_subset)}, Val: {len(val_subset)}")

    # ImageNet penalty loader
    imagenet_loader = _make_imagenet_loader(cfg, image_size, nw, pin)
    imagenet_iter = iter(imagenet_loader) if imagenet_loader else None
    if imagenet_loader:
        print(f"  ImageNet subset: {len(imagenet_loader.dataset)} images")
    else:
        print("  ImageNet path not found -> pretraining penalty disabled")

    # ----- Teacher 채널 정규화 -----
    print("\n--- Teacher channel normalization ---")
    norm_cache = Path(tr.get("teacher_norm_cache", ""))
    if norm_cache.is_file():
        blob = torch.load(norm_cache, map_location="cpu", weights_only=False)
        model.set_teacher_feature_normalization(blob["mu"], blob["sigma"])
        print(f"  Cache loaded: {norm_cache}")
    else:
        if skip_teacher_stats:
            print(
                f"  [skip-teacher-stats] teacher_norm_cache not found: {norm_cache}\n"
                "  -> default teacher_feat_mu/sigma(0/1) 상태로 학습을 진행합니다."
            )
        else:
            mu, sigma = compute_teacher_output_channel_stats(model.teacher, stats_loader, device)
            model.set_teacher_feature_normalization(mu, sigma)
            if str(norm_cache):
                norm_cache.parent.mkdir(parents=True, exist_ok=True)
                torch.save({"mu": mu, "sigma": sigma}, norm_cache)
                print(f"  Cache saved: {norm_cache}")

    n_trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  Trainable parameters: {n_trainable}")

    # ----- Optimizer -----
    trainable_params = (list(model.student.parameters()) + list(model.autoencoder.parameters()))
    optimizer = torch.optim.Adam(trainable_params, lr=float(tr.get("lr", 1e-4)), weight_decay=float(tr.get("weight_decay", 1e-5)),)

    # ----- TensorBoard -----
    writer = None
    tb_dir = tr.get("tensorboard_dir", "")
    if cfg.get("logging", {}).get("use_tensorboard", False) and tb_dir:
        try:
            from torch.utils.tensorboard import SummaryWriter
            Path(tb_dir).mkdir(parents=True, exist_ok=True)
            writer = SummaryWriter(tb_dir)
        except ImportError:
            print("  tensorboard uninstalled, skipping TensorBoard logging")

    # ----- train loop -----
    total_iters = int(tr.get("iterations", 70000))
    p_hard = float(tr.get("p_hard", 0.999))
    lr_decay_after = int(tr.get("lr_decay_after", 66500))
    lr_decay_value = float(tr.get("lr_decay_value", 1e-5))
    log_interval = int(cfg.get("logging", {}).get("log_interval", 100))
    save_dir = Path(tr.get("save_dir", "results/checkpoints/efficientad"))
    # ROI 실험 실행은 프로덕션 체크포인트를 덮지 않도록 파일명에 접미사
    ckpt_suffix = "_roi" if roi else ""
    if roi:
        print(f"  [ROI] 학습 시점 ROI 마스킹 ON (roi_k={roi_k}) — 체크포인트 접미사 '_roi'")
    save_dir.mkdir(parents=True, exist_ok=True)
    model_ch = int(m.get("in_channels", 3))

    model.student.train()
    model.autoencoder.train()
    train_data_iter = iter(train_loader)

    print(f"\n{'='*60}")
    print(f"  Algorithm 1 학습 시작: {total_iters:,} iterations")
    print(f"  batch={bs_train}, p_hard={p_hard}, lr={tr.get('lr')}")
    print(f"  lr decay at {lr_decay_after} → {lr_decay_value}")
    print(f"{'='*60}\n")

    t0 = time.time()
    running = {"total": 0.0, "hard": 0.0, "pen": 0.0, "ae": 0.0, "stae": 0.0}

    for iteration in range(1, total_iters + 1):

        # --- lr decay ---
        if iteration == lr_decay_after + 1:
            for pg in optimizer.param_groups:
                pg["lr"] = lr_decay_value
            print(f"  [iter {iteration}] lr -> {lr_decay_value}")

        # --- train image samples ---
        try:
            batch = next(train_data_iter)
        except StopIteration:
            train_data_iter = iter(train_loader)
            batch = next(train_data_iter)
        
        images = batch[0].to(device)

        # --- augmented images for AE (Algorithm 1, lines 23-27) ---
        aug_images = efficientad_ae_augment(images)

        # --- Forward: S-T + AE ---
        out = model.forward_train(images, aug_images)
        f_t = out["f_t"]
        f_st = out["f_st"]
        f_ae = out["f_ae"]
        f_t_aug = out["f_t_aug"]
        f_stae = out["f_stae"]

        # --- ROI 마스크 (학습 시점 ROI 실험) ---
        # S-T 손실은 원본 images, AE/STAE 손실은 aug_images 기준으로 각각 마스크 생성
        # (feature 해상도로 다운샘플). roi=False면 None → 기존 동작.
        feat_hw = f_st.shape[-2:]
        roi_train = _brightness_roi(images, roi_k, feat_hw) if roi else None
        roi_aug = _brightness_roi(aug_images, roi_k, feat_hw) if roi else None

        # --- L_hard ---
        loss_hard = hard_feature_loss(f_st, f_t, p_hard=p_hard, roi_mask=roi_train)

        # --- L_penalty ---
        loss_penalty = torch.tensor(0.0, device=device)
        if imagenet_iter is not None:
            try:
                inet_img = next(imagenet_iter)
            except StopIteration:
                imagenet_iter = iter(imagenet_loader)
                inet_img = next(imagenet_iter)
            inet_img = inet_img.to(device)

            # 채널 맞추기
            if inet_img.shape[1] != model_ch:
                if model_ch == 1:
                    inet_img = inet_img.mean(dim=1, keepdim=True)
                elif model_ch == 3 and inet_img.shape[1] == 1:
                    inet_img = inet_img.repeat(1, 3, 1, 1)
            xn_inet = imagenet_normalize(inet_img)
            s_inet = model.student(xn_inet)
            loss_penalty = pretraining_penalty(s_inet)
        
        # L_ST = L_hard + L_penalty
        loss_st = loss_hard + loss_penalty

        # --- L_AE ---
        loss_ae_val = ae_loss(f_t_aug, f_ae, roi_mask=roi_aug)

        # --- L_STAE ---
        loss_stae_val = stae_loss(f_ae, f_stae, roi_mask=roi_aug)

        # --- L_total = L_ST + L_AE + L_STAE ---
        loss_total = loss_st + loss_ae_val + loss_stae_val

        # --- Backward ---
        optimizer.zero_grad()
        loss_total.backward()
        optimizer.step()

        # --- stack losses ---
        running["total"] += loss_total.item()
        running["hard"] += loss_hard.item()
        running["pen"] += loss_penalty.item()
        running["ae"] += loss_ae_val.item()
        running["stae"] += loss_stae_val.item()

        # --- logging ---
        if iteration % log_interval == 0:
            n = log_interval
            elapsed = time.time() - t0
            it_s = iteration / elapsed
            eta = (total_iters - iteration) / max(it_s, 1e-8)
            print(
                f"  [{iteration:>6d}/{total_iters}] "
                f"loss={running['total']/n:.5f} "
                f"(hard={running['hard']/n:.5f} "
                f"pen={running['pen']/n:.5f} "
                f"ae={running['ae']/n:.5f} "
                f"stae={running['stae']/n:.5f}) "
                f"lr={optimizer.param_groups[0]['lr']:.1e} "
                f"ETA={eta/60:.1f}m"
            )
            if writer:
                writer.add_scalar("loss/total", running["total"] / n, iteration)
                writer.add_scalar("loss/hard", running["hard"] / n, iteration)
                writer.add_scalar("loss/penalty", running["pen"] / n, iteration)
                writer.add_scalar("loss/ae", running["ae"] / n, iteration)
                writer.add_scalar("loss/stae", running["stae"] / n, iteration)
                writer.add_scalar("lr", optimizer.param_groups[0]["lr"], iteration)
            running = {k: 0.0 for k in running}
        
        # --- save checkpoint ---
        if iteration % 10000 == 0:
            _save_checkpoint(model, optimizer, iteration, save_dir / f"Efficientad_iter{iteration}{ckpt_suffix}.pth",)
    
    # --- finish training ---
    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  학습 완료: {total_iters:,} iterations, {elapsed/60:.1f}분")
    print(f"{'='*60}\n")

    # ----- Quantile map normalization -----
    print("\n--- Quantile map normalization ---")
    compute_map_normalization(model, val_loader, device,
        q_a=float(norm_cfg.get("q_a", 0.9)), q_b=float(norm_cfg.get("q_b", 0.995)),)

    # ----- save final checkpoint -----
    final_path = save_dir / f"efficientad{ckpt_suffix}.pth"
    _save_checkpoint(model, optimizer, total_iters, final_path)
    print(f"\n Final checkpoint saved: {final_path}")

    if writer: writer.close()


# =====================================================================
# CLI
# =====================================================================
 
def main() -> None:
    parser = argparse.ArgumentParser(description="EfficientAD Training (Algorithm 1)")
    parser.add_argument("--config", type=str, default=str(PROJECT_ROOT / "configs" / "config_efficientad.yaml"),)
    parser.add_argument("--skip-teacher-stats", action="store_true", help="Teacher 채널 정규화(mu/std) 재계산을 스킵합니다. cache 파일이 있으면 로드합니다.",)
    parser.add_argument(
        "--roi",
        action="store_true",
        help="학습 시점 ROI 마스킹 ON. S-T·AE·STAE 손실을 고온 전경(ROI) 내부에서만 계산. "
             "체크포인트는 '_roi' 접미사로 저장(프로덕션 모델 미덮어씀).",
    )
    parser.add_argument("--roi-k", type=float, default=0.3, help="ROI 임계 계수: 픽셀 > 평균 + k·std 를 ROI로 간주 (기본 0.3).",)
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    print(f"Config: {args.config}")
    train(cfg, skip_teacher_stats=bool(args.skip_teacher_stats),
          roi=bool(args.roi), roi_k=float(args.roi_k))


if __name__ == "__main__":
    main()
