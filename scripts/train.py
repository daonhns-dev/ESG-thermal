"""
AutoEncoder 학습 스크립트 - Optimization
"""

from __future__ import annotations

import os
# oneDNN/absl 로그 억제 (TensorBoard 등 로드 전에 설정)
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

import argparse
import yaml
from pathlib import Path
from datetime import datetime
import sys
import torch
import torch.nn as nn
import torch.optim as optim
# SummaryWriter는 사용 시점에 지연 import (tensorboard 미설치 대응)
from tqdm import tqdm
import numpy as np
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.ae import ConvAutoEncoder, SimpleAutoEncoder, ThermalRGBConvAE, MultiModalAE, PerceptualLoss
from datasets.dataset import create_dataloaders, ThermalRGBPairDataset, get_transforms, get_transforms_rgb
from torch.utils.data import DataLoader
from utils.metrics import compute_anomaly_scores, compute_metrics, print_metrics
from utils.visualization import plot_training_history, plot_reconstruction_comparison


class Trainer:
    """AutoEncoder training/eval class"""

    def __init__(self, config_path: str, eval_checkpoint: Optional[str] = None):
        """
        Args:
            config_path: configuration file path
            eval_checkpoint: (--eval_only 시) 로드할 체크포인트. best.pth 경로 또는 run 폴더 경로.
        """
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # ── Device ──────────────────────────────────────────────────────
        want_cuda = (self.config.get('device', 'cpu').lower() == 'cuda')
        cuda_ok = torch.cuda.is_available()
        self.device = 'cuda' if (want_cuda and cuda_ok) else 'cpu'
        print(f"Using device: {self.device}")
        if want_cuda and not cuda_ok:
            print("  (config has device: cuda but CUDA is not available → using CPU. Install PyTorch with CUDA or check GPU/driver.)")

        # ── AMP scaler (CUDA 전용) ──────────────────────────────────────
        self.use_amp = self.config['training'].get('use_amp', True) and self.device == 'cuda'
        self.scaler = torch.amp.GradScaler(device="cuda") if self.use_amp else None
        print(f"AMP (Mixed Precision): {'enabled' if self.use_amp else 'disabled'}")

        self.set_seed(self.config['seed'])

        # ── Model ───────────────────────────────────────────────────────
        self.model = self.build_model().to(self.device)

        # ── PerceptualLoss (torchvision 유무 자동 감지) ───────────────────
        perceptual_w = self.config['training']['loss'].get('perceptual_weight', 0.0)
        if perceptual_w > 0:
            self.perceptual_loss_fn: Optional[PerceptualLoss] = PerceptualLoss().to(self.device)
            status = "enabled" if self.perceptual_loss_fn else "disabled (torchvision not found)"
            print(f"PerceptualLoss: {status}")
        else:
            self.perceptual_loss_fn = None

        # loss 가중치를 모델 클래스 변수에 반영
        loss_cfg = self.config['training']['loss']
        # BaseAutoEncoder 계열 모델에만 손실 가중치 적용
        for m in [self.model] if not isinstance(self.model, MultiModalAE) else [self.model]:
            if hasattr(m, "mse_weight"):
                m.mse_weight = loss_cfg.get('mse_weight', 0.4)
            if hasattr(m, "ssim_weight"):
                m.ssim_weight = loss_cfg.get('ssim_weight', 0.4)
            if hasattr(m, "perceptual_weight"):
                m.perceptual_weight = loss_cfg.get('perceptual_weight', 0.2)

        # ── Optimizer / Scheduler ────────────────────────────────────
        self.optimizer = self.build_optimizer()
        self.scheduler = self.build_scheduler()

        # ── DataLoader ───────────────────────────────────────────────
        self.train_loader, self.val_loader, self.test_loader = self.build_dataloaders()

        # ── Run ID / Output 디렉토리 구조 ─────────────────────────────
        #   예: ae_ConvAutoEncoder_20250311-112030
        model_name = self.config['model']['name']
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_name = f"ae_{model_name}_{timestamp}"
        base_ckpt_dir = Path(self.config['training']['save_dir'])

        # --eval_only --checkpoint 지정 시: 해당 run의 best.pth 사용, 시각화도 그 run 폴더에
        self.eval_checkpoint_path: Optional[Path] = None
        if eval_checkpoint:
            p = Path(eval_checkpoint).resolve()
            if p.is_file():
                self.eval_checkpoint_path = p
                self.save_dir = p.parent
                self.run_name = p.parent.name
            elif p.is_dir():
                self.eval_checkpoint_path = p / "best.pth"
                self.save_dir = p
                self.run_name = p.name
            else:
                self.eval_checkpoint_path = base_ckpt_dir / eval_checkpoint / "best.pth"
                self.save_dir = base_ckpt_dir / eval_checkpoint
                self.run_name = eval_checkpoint
            print(f"Eval-only: using checkpoint from run {self.run_name}")
        else:
            self.save_dir = base_ckpt_dir / self.run_name

        self.save_dir.mkdir(parents=True, exist_ok=True)

        # ── 학습 상태 ─────────────────────────────────────────────────
        self.train_losses: list[float] = []
        self.val_losses: list[float] = []
        self.best_loss = float('inf')
        self.patience_counter = 0
        
        # ── TensorBoard (미설치 시 자동 skip) ────────────────────────
        self.writer = None
        if self.config['logging']['use_tensorboard']:
            try:
                from torch.utils.tensorboard import SummaryWriter
                base_log_dir = Path(self.config['logging']['tensorboard_dir'])
                log_dir = base_log_dir / self.run_name
                self.writer = SummaryWriter(str(log_dir))
                print(f"TensorBoard logging to: {log_dir}")
            except ImportError:
                print("  tensorboard 미설치 — TensorBoard 로깅 건너뜀")

    # ────────────────────────────────────────────────────────────────────
    # Setup helpers
    # ────────────────────────────────────────────────────────────────────

    def set_seed(self, seed: int) -> None:
        torch.manual_seed(seed)
        np.random.seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed(seed)
    
    def build_model(self) -> nn.Module:
        """Create model"""
        cfg = self.config['model']
        name = cfg['name']

        # 공통 파라미터
        latent_dim = cfg['latent_dim']
        base_channels = cfg.get('base_channels', 32)
        depth = cfg.get('depth', 5)
        use_attention = cfg.get('use_attention', True)
        vae = cfg.get('vae', False)
        spatial_latent = cfg.get('spatial_latent', False)

        if name == 'ConvAutoEncoder':
            model = ConvAutoEncoder(
                input_channels=cfg['input_channels'],
                latent_dim=latent_dim,
                base_channels=base_channels,
                depth=depth,
                use_attention=use_attention,
                vae=vae,
                spatial_latent=spatial_latent,
            )
        elif name == 'SimpleAutoEncoder':
            model = SimpleAutoEncoder(
                input_channels=cfg['input_channels'],
                latent_dim=latent_dim,
            )
        elif name == 'ThermalRGBConvAE':
            model = ThermalRGBConvAE(
                latent_dim=latent_dim,
                base_channels=base_channels,
                depth=depth,
                vae=vae,
                use_attention=use_attention,
            )
        elif name == 'MultiModalAE':
            model = MultiModalAE(
                image_channels=cfg.get('image_channels', 4),
                csv_dim=cfg.get('csv_dim', 10),
                latent_dim=latent_dim,
                base_channels=base_channels,
                depth=depth,
                fusion_type=cfg.get('fusion_type', 'gate'),
                use_attention=use_attention,
                vae=vae,
            )
        else:
            raise ValueError(f"Unknown model: {name}")
        
        print(f"Model: {name}  |  params: {sum(p.numel() for p in model.parameters()):,}")
        
        return model
    
    def build_optimizer(self) -> optim.Optimizer:
        """Create optimizer"""
        name = self.config['training']['optimizer']
        lr = self.config['training']['learning_rate']
        weight_decay = self.config['training'].get('weight_decay', 1e-4)

        if name == 'Adam':
            return optim.Adam(self.model.parameters(), lr=lr)
        elif name == 'AdamW':
            return optim.AdamW(self.model.parameters(), lr=lr, weight_decay=weight_decay)
        elif name == 'SGD':
            return optim.SGD(self.model.parameters(), lr=lr, momentum=0.9, weight_decay=weight_decay)
        else:
            raise ValueError(f"Unknown optimizer: {name}")
    
    def build_scheduler(self):
        """Create scheduler"""
        name = self.config['training']['scheduler']
        params = self.config['training'].get('scheduler_params', {})
        epochs = self.config['training']['epochs']
        
        if name == 'ReduceLROnPlateau':
            return optim.lr_scheduler.ReduceLROnPlateau(
                self.optimizer, mode='min', patience=params.get('patience', 5), factor=params.get('factor', 0.5)
            )
        elif name == 'CosineAnnealing':
            return optim.lr_scheduler.CosineAnnealingLR(
                self.optimizer, T_max=epochs, eta_min=params.get('eta_min', 1e-6)
            )
        return None
    
    def build_dataloaders(self):
        cfg = self.config['data']
        batch_size = cfg.get('batch_size', 32)
        num_workers = cfg.get('num_workers', 4)
        image_size = cfg.get('image_size', 256)
        
        # Phase 2: Thermal + RGB
        if 'thermal_dir' in cfg and 'rgb_dir' in cfg:
            train_ds = ThermalRGBPairDataset(
                thermal_dir=cfg['thermal_dir'],
                rgb_dir=cfg['rgb_dir'],
                transform_thermal=get_transforms(image_size, is_train=True),
                transform_rgb=get_transforms_rgb(image_size, is_train=True),
                is_train=True
            )
            test_ds = ThermalRGBPairDataset(
                thermal_dir=cfg['thermal_dir'],
                rgb_dir=cfg['rgb_dir'],
                transform_thermal=get_transforms(image_size, is_train=False),
                transform_rgb=get_transforms_rgb(image_size, is_train=False),
                is_train=False
            )
            pin_memory = self.device == "cuda"
            train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=num_workers, pin_memory=pin_memory)
            test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False, num_workers=num_workers, pin_memory=pin_memory)
            # ThermalRGBPairDataset은 아직 val_dir 미지원 — 항상 test_loader로 validate (TODO: 필요 시 val 분리 추가)
            return train_loader, None, test_loader
        
        # Phase 1: Thermal only
        pin_memory = self.device == "cuda"
        train_fraction = cfg.get('train_fraction', 1.0)
        test_fraction = cfg.get('test_fraction', 1.0)
        subset_seed = self.config.get('seed')
        train_loader, val_loader, test_loader = create_dataloaders(
            train_dir=cfg['train_dir'],
            test_dir=cfg['test_dir'],
            val_dir=cfg.get('val_dir'),
            batch_size=batch_size,
            image_size=image_size,
            num_workers=num_workers,
            shuffle=cfg.get('shuffle', True),
            pin_memory=pin_memory,
            train_fraction=train_fraction,
            test_fraction=test_fraction,
            subset_seed=subset_seed,
        )
        if val_loader is None:
            print("⚠️ val_dir 미설정 — validate()가 test_loader를 val로 재사용합니다.")
        if train_fraction < 1.0 or test_fraction < 1.0:
            print(f"Data subset: train_fraction={train_fraction}, test_fraction={test_fraction} -> train batches={len(train_loader)}, test batches={len(test_loader)}")
        return train_loader, val_loader, test_loader

    # ────────────────────────────────────────────────────────────────────
    # Loss helper
    # ────────────────────────────────────────────────────────────────────

    def _compute_loss(self, images: torch.Tensor, recon: torch.Tensor, epoch: int = 0) -> torch.Tensor:
        """
        모델 타입에 따라 적절한 compute_loss 호출.
        perceptual_loss_fn과 kl_weight를 일관되게 전달
        """
        kl_weight = self.config['model'].get('vae_kl_weight', 1e-4)

        if isinstance(self.model, (ConvAutoEncoder, ThermalRGBConvAE)):
            return self.model.compute_loss(
                images, recon, perceptual_loss_fn=self.perceptual_loss_fn, kl_weight=kl_weight, epoch=epoch,
            )
        elif isinstance(self.model, MultiModalAE):
            return self.model.compute_loss(
                images, recon, perceptual_loss_fn=self.perceptual_loss_fn, epoch=epoch,
            )
        else:
            return self.model.compute_loss(images, recon, epoch=epoch)

    # ────────────────────────────────────────────────────────────────────
    # Train / Validate
    # ────────────────────────────────────────────────────────────────────

    def train_epoch(self, epoch: int) -> float:
        """Train for one epoch"""
        self.model.train()
        total_loss = 0.0
        grad_clip = self.config['training'].get('grad_clip', 1.0)
        log_interval = self.config['logging']['log_interval']

        pbar = tqdm(self.train_loader, desc=f"Epoch {epoch + 1}")
        for batch_idx, (images, _, _) in enumerate(pbar):
            images = images.to(self.device)
            
            # ── Forward (AMP) ────────────────────────────────────────
            with torch.amp.autocast(device_type="cuda", enabled=self.use_amp):
                recon, _ = self.model(images)
                loss = self._compute_loss(images, recon)
            
            # ── Backward ─────────────────────────────────────────────
            self.optimizer.zero_grad()

            if self.scaler:
                self.scaler.scale(loss).backward()
                if grad_clip > 0:
                    self.scaler.unscale_(self.optimizer)
                    nn.utils.clip_grad_norm_(self.model.parameters(), grad_clip)
                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                loss.backward()
                if grad_clip > 0:
                    nn.utils.clip_grad_norm_(self.model.parameters(), grad_clip)
                self.optimizer.step()
            
            total_loss += loss.item()
            pbar.set_postfix({'loss': f"{loss.item():.5f}"})

            # TensorBoard
            if self.writer and batch_idx % log_interval == 0:
                step = epoch * len(self.train_loader) + batch_idx
                self.writer.add_scalar('Train/BatchLoss', loss.item(), step)
        
        return total_loss / len(self.train_loader)
    
    def validate(self, epoch: int = 0) -> float:
        self.model.eval()
        total_loss = 0.0
        
        with torch.no_grad():
            loader = self.val_loader if self.val_loader is not None else self.test_loader
            for images, _, _ in loader:
                images = images.to(self.device)
                with torch.amp.autocast(device_type="cuda", enabled=self.use_amp):
                    recon, _ = self.model(images)
                    loss = self._compute_loss(images, recon, epoch=epoch)
                total_loss += loss.item()
        
        return total_loss / len(loader)

    # ────────────────────────────────────────────────────────────────────
    # Checkpoint
    # ────────────────────────────────────────────────────────────────────

    def save_checkpoint(self, epoch: int, is_best: bool = False):
        checkpoint = {
            'epoch':               epoch,
            'model_state_dict':    self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'train_losses':        self.train_losses,
            'val_losses':          self.val_losses,
            'config':              self.config
        }
        
        if (epoch + 1) % self.config['training']['save_every'] == 0:
            path = self.save_dir / f"checkpoint_epoch_{epoch+1}.pth"
            torch.save(checkpoint, path)
            print(f"  Saved checkpoint: {path}")
        
        if is_best:
            path = self.save_dir / "best.pth"
            torch.save(checkpoint, path)
            print(f"  Saved best model: {path}")

    # ────────────────────────────────────────────────────────────────────
    # Main train loop
    # ────────────────────────────────────────────────────────────────────
    
    def train(self):
        print("\n" + "="*50)
        print("Starting Training")
        print("="*50)
        
        epochs = self.config['training']['epochs']
        early_stopping = self.config['training']['early_stopping']
        
        for epoch in range(epochs):
            train_loss = self.train_epoch(epoch)
            self.train_losses.append(train_loss)
            
            val_loss = self.validate(epoch=epoch)
            self.val_losses.append(val_loss)
            
            warmup_epochs = getattr(self.model, 'ssim_warmup_epochs', 10)
            warmup_ratio = min(1.0, epoch / max(warmup_epochs, 1))
            effective_ssim_w = getattr(self.model, 'ssim_weight', 0.2) * warmup_ratio

            print(f"\nEpoch {epoch + 1}/{epochs}")
            print(f"  Train Loss: {train_loss:.6f}")
            print(f"  Val   Loss: {val_loss:.6f}")
            print(f"  SSIM weight (effective): {effective_ssim_w:.4f}  "  
                  f"[warmup {min(epoch, warmup_epochs)}/{warmup_epochs}]")
            
            # TensorBoard (epoch)
            if self.writer:
                self.writer.add_scalar('Train/EpochLoss', train_loss, epoch)
                self.writer.add_scalar('Val/Loss', val_loss, epoch)
                self.writer.add_scalar('Learning Rate', self.optimizer.param_groups[0]['lr'], epoch)
                self.writer.add_scalar('Loss/SSIM_weight_effective', effective_ssim_w, epoch)

            # Scheduler step
            if self.scheduler:
                if isinstance(self.scheduler, optim.lr_scheduler.ReduceLROnPlateau):
                    self.scheduler.step(val_loss)
                else:
                    self.scheduler.step()

            # Best model / Early stopping
            # SSIM warmup 동안은 손실 식이 에폭마다 바뀌어 수치 비교가 무의미함 → warmup 끝난 뒤부터만 적용
            past_warmup = (epoch >= warmup_epochs)
            if past_warmup:
                is_best = val_loss < self.best_loss - early_stopping['min_delta']
                if is_best:
                    self.best_loss = val_loss
                    self.patience_counter = 0
                    print(f"  ★ New best loss: {self.best_loss:.6f}")
                else:
                    self.patience_counter += 1
                self.save_checkpoint(epoch, is_best)

                if self.patience_counter >= early_stopping['patience']:
                    print(f"\nEarly stopping at epoch {epoch + 1}")
                    break
            else:
                # warmup 중: best 갱신 안 함. 체크포인트는 save_every 기준으로만 저장
                self.save_checkpoint(epoch, is_best=False)
                if epoch == 0:
                    print("  (SSIM warmup 중: Val loss는 에폭마다 손실 식이 달라 비교 불가 → warmup 끝난 뒤부터 best/early stop 적용)")

        plot_training_history(self.train_losses, self.val_losses, str(self.save_dir / "training_history.png"))
        
        print("\n" + "="*50)
        print("Training Completed!")
        print(f"Best validation loss: {self.best_loss:.6f}")
        print("="*50)
        
        if self.writer:
            self.writer.close()

    # ────────────────────────────────────────────────────────────────────
    # Evaluation
    # ────────────────────────────────────────────────────────────────────

    def evaluate(self) -> dict:
        """Final evaluation"""
        print("\n" + "="*50)
        print("Evaluating Model")
        print("="*50)

        best_path = self.eval_checkpoint_path if self.eval_checkpoint_path is not None else self.save_dir / "best.pth"
        if best_path.exists():
            checkpoint = torch.load(best_path, map_location=self.device)
            self.model.load_state_dict(checkpoint['model_state_dict'])
            print(f"Loaded best model from {best_path}")
        else:
            print(f"WARNING: Checkpoint not found at {best_path}")
            print("         Evaluating with current (untrained) weights → AUC will be ~0.5. Use --checkpoint for eval_only.")

        eval_cfg = self.config['evaluation']
        multi_scale = eval_cfg.get('multi_scale', True)
        smooth = eval_cfg.get('anomaly_smooth', True)
        score_mode = eval_cfg.get('anomaly_score_mode', 'pixel_mse')
        blur_kernel_size = eval_cfg.get('blur_kernel_size', 5)
        blur_sigma = eval_cfg.get('blur_sigma', 1.5)

        print(f"\nComputing anomaly scores (mode={score_mode}, multi_scale={multi_scale}, smooth={smooth})...")
        scores, labels = compute_anomaly_scores(
            self.model,
            self.test_loader,
            self.device,
            multi_scale=multi_scale,
            anomaly_smooth=smooth,
            anomaly_score_mode=score_mode,
            blur_kernel_size=blur_kernel_size,
            blur_sigma=blur_sigma,
        )
        
        metrics = compute_metrics(scores, labels)
        print_metrics(metrics)
        print("  (Threshold: F1 최대화 기준 자동 산출 — score_mode 변경 시 스케일이 달라지므로 재탐색됨)\n")

        # Visualization (run 별 폴더에 저장)
        base_vis_dir = Path(eval_cfg['visualize']['save_dir'])
        vis_dir = base_vis_dir / self.run_name
        vis_dir.mkdir(parents=True, exist_ok=True)
        
        from utils.visualization import plot_roc_curve, plot_score_distribution
        
        plot_roc_curve(labels, scores, str(vis_dir / "roc_curve.png"))
        
        plot_score_distribution(
            scores[labels == 0],
            scores[labels == 1],
            metrics['threshold'],
            str(vis_dir / "score_distribution.png")
        )

        # 재구성 샘플 시각화 (evaluation과 동일한 score_mode 사용)
        self.model.eval()
        with torch.no_grad():
            for images, batch_labels, _ in self.test_loader:
                images = images.to(self.device)
                recon, _ = self.model(images)
                anomaly_maps, _ = self.model.get_anomaly_score(
                    images,
                    multi_scale=multi_scale,
                    smooth=smooth,
                    score_mode=score_mode,
                    blur_kernel_size=blur_kernel_size,
                    blur_sigma=blur_sigma,
                )
                plot_reconstruction_comparison(
                    images[:5], recon[:5], anomaly_maps[:5], batch_labels[:5].numpy(),
                    str(vis_dir / "reconstruction_comparison.png"),
                    num_samples=5
                )
                break
        
        print(f"\nVisualization saved to: {vis_dir}")
        return metrics

# ────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Train AutoEncoder for Thermal Anomaly Detection"
    )
    parser.add_argument(
        '--config', type=str, default='configs/config_ae.yaml',
        help='Path to YAML config file'
    )
    parser.add_argument(
        '--eval_only', action='store_true', help='Skip training, run evaluation only'
    )
    parser.add_argument(
        '--checkpoint', type=str, default=None,
        help='(--eval_only 시) 사용할 체크포인트. best.pth 전체 경로 또는 run 폴더 경로 예: results/checkpoints/ae/ae_ConvAutoEncoder_20260312-134521'
    )
    args = parser.parse_args()

    eval_ckpt = args.checkpoint if args.eval_only else None
    trainer = Trainer(args.config, eval_checkpoint=eval_ckpt)
    
    if args.eval_only:
        trainer.evaluate()
    else:
        trainer.train()
        trainer.evaluate()


if __name__ == "__main__":
    main()

