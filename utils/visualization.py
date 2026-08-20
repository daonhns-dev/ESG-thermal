"""
시각화 유틸리티
"""

import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from typing import Dict, Optional, Tuple
import torch
import cv2


def plot_anomaly_heatmap(
    original_image: np.ndarray,
    anomaly_map: np.ndarray,
    save_path: Optional[str] = None,
    title: str = "Anomaly Detection",
    cmap: str = "jet"
):
    """
    이상 영역 히트맵 시각화
    
    Args:
        original_image: 원본 이미지 (H, W) or (H, W, C)
        anomaly_map: 이상 스코어 맵 (H, W)
        save_path: 저장 경로
        title: 그래프 제목
        cmap: 컬러맵
    """
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    try:
        # 원본 이미지
        if original_image.ndim == 2:
            axes[0].imshow(original_image, cmap='gray')
        else:
            axes[0].imshow(original_image)
        axes[0].set_title("Original Image")
        axes[0].axis('off')

        # 이상 스코어 맵
        im = axes[1].imshow(anomaly_map, cmap=cmap)
        axes[1].set_title("Anomaly Score Map")
        axes[1].axis('off')
        plt.colorbar(im, ax=axes[1])

        # 오버레이
        if original_image.ndim == 2:
            overlay = cv2.applyColorMap(
                (anomaly_map * 255).astype(np.uint8),
                cv2.COLORMAP_JET
            )
            overlay = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
            original_rgb = cv2.cvtColor(
                (original_image * 255).astype(np.uint8),
                cv2.COLOR_GRAY2RGB
            )
            blended = cv2.addWeighted(original_rgb, 0.6, overlay, 0.4, 0)
            axes[2].imshow(blended)
        else:
            axes[2].imshow(original_image)
            axes[2].imshow(anomaly_map, cmap=cmap, alpha=0.4)

        axes[2].set_title("Overlay")
        axes[2].axis('off')

        plt.suptitle(title)
        plt.tight_layout()

        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"Saved heatmap to {save_path}")
    finally:
        plt.close(fig)


def plot_reconstruction_comparison(
    originals: torch.Tensor,
    reconstructions: torch.Tensor,
    anomaly_maps: torch.Tensor,
    labels: np.ndarray,
    save_path: Optional[str] = None,
    num_samples: int = 5
):
    """
    재구성 비교 시각화
    
    Args:
        originals: 원본 이미지 (B, C, H, W)
        reconstructions: 재구성 이미지 (B, C, H, W)
        anomaly_maps: 이상 스코어 맵 (B, C, H, W)
        labels: 레이블 (B,)
        save_path: 저장 경로
        num_samples: 표시할 샘플 수
    """
    num_samples = min(num_samples, originals.size(0))
    
    fig, axes = plt.subplots(num_samples, 4, figsize=(16, 4*num_samples))
    try:
        if num_samples == 1:
            axes = axes.reshape(1, -1)

        for i in range(num_samples):
            orig = originals[i].cpu().squeeze().numpy()
            recon = reconstructions[i].cpu().squeeze().numpy()
            anom = anomaly_maps[i].cpu().squeeze().numpy()
            label = "Anomaly" if labels[i] == 1 else "Normal"

            axes[i, 0].imshow(orig, cmap='gray')
            axes[i, 0].set_title(f"{label} - Original")
            axes[i, 0].axis('off')

            axes[i, 1].imshow(recon, cmap='gray')
            axes[i, 1].set_title("Reconstruction")
            axes[i, 1].axis('off')

            diff = np.abs(orig - recon)
            axes[i, 2].imshow(diff, cmap='hot')
            axes[i, 2].set_title("Difference")
            axes[i, 2].axis('off')

            im = axes[i, 3].imshow(anom, cmap='jet')
            axes[i, 3].set_title("Anomaly Map")
            axes[i, 3].axis('off')
            plt.colorbar(im, ax=axes[i, 3])

        plt.tight_layout()

        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"Saved comparison to {save_path}")
    finally:
        plt.close(fig)


def plot_training_history(
    train_losses: list,
    val_losses: Optional[list] = None,
    save_path: Optional[str] = None
):
    """
    학습 히스토리 시각화
    
    Args:
        train_losses: 학습 손실 리스트
        val_losses: 검증 손실 리스트
        save_path: 저장 경로
    """
    fig = plt.figure(figsize=(10, 6))
    try:
        plt.plot(train_losses, label='Train Loss', linewidth=2)
        if val_losses:
            plt.plot(val_losses, label='Validation Loss', linewidth=2)

        plt.xlabel('Epoch', fontsize=12)
        plt.ylabel('Loss', fontsize=12)
        plt.title('Training History', fontsize=14)
        plt.legend(fontsize=11)
        plt.grid(True, alpha=0.3)

        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"Saved training history to {save_path}")
    finally:
        plt.close(fig)


def plot_roc_curve(
    labels: np.ndarray,
    scores: np.ndarray,
    save_path: Optional[str] = None
):
    """
    ROC Curve 시각화
    
    Args:
        labels: 실제 레이블
        scores: 예측 스코어
        save_path: 저장 경로
    """
    from sklearn.metrics import roc_curve, auc
    
    fpr, tpr, _ = roc_curve(labels, scores)
    roc_auc = auc(fpr, tpr)

    fig = plt.figure(figsize=(8, 8))
    try:
        plt.plot(fpr, tpr, color='darkorange', lw=2,
                 label=f'ROC curve (AUC = {roc_auc:.4f})')
        plt.plot([0, 1], [0, 1], color='navy', lw=2, linestyle='--', label='Random')

        plt.xlim([0.0, 1.0])
        plt.ylim([0.0, 1.05])
        plt.xlabel('False Positive Rate', fontsize=12)
        plt.ylabel('True Positive Rate', fontsize=12)
        plt.title('Receiver Operating Characteristic (ROC)', fontsize=14)
        plt.legend(loc="lower right", fontsize=11)
        plt.grid(True, alpha=0.3)

        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"Saved ROC curve to {save_path}")
    finally:
        plt.close(fig)


def plot_score_distribution(
    normal_scores: np.ndarray,
    anomaly_scores: np.ndarray,
    threshold: float,
    save_path: Optional[str] = None
):
    """
    이상 스코어 분포 시각화
    
    Args:
        normal_scores: 정상 샘플 스코어
        anomaly_scores: 이상 샘플 스코어
        threshold: 임계값
        save_path: 저장 경로
    """
    fig = plt.figure(figsize=(10, 6))
    try:
        plt.hist(normal_scores, bins=50, alpha=0.6, label='Normal', color='blue')
        plt.hist(anomaly_scores, bins=50, alpha=0.6, label='Anomaly', color='red')
        plt.axvline(threshold, color='green', linestyle='--', linewidth=2,
                    label=f'Threshold = {threshold:.4f}')

        plt.xlabel('Anomaly Score', fontsize=12)
        plt.ylabel('Frequency', fontsize=12)
        plt.title('Anomaly Score Distribution', fontsize=14)
        plt.legend(fontsize=11)
        plt.grid(True, alpha=0.3)

        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"Saved score distribution to {save_path}")
    finally:
        plt.close(fig)


def plot_efficientad_maps(
    original_image: np.ndarray,
    maps: Dict[str, np.ndarray],
    save_path: Optional[str] = None,
    cmap: str = "jet",
):
    """
    EfficientAD local / global / combined 이상 맵 시각화.

    Args:
        original_image: (H, W) 또는 (H, W, C)
        maps: 'local_map', 'global_map', 'combined_map' 등 (H, W) 배열
        save_path: 저장 경로
        cmap: 히트맵 컬러맵
    """
    keys = [k for k in ("local_map", "global_map", "combined_map") if k in maps]
    if not keys:
        raise ValueError("maps 에 local_map / global_map / combined_map 중 하나 이상 필요합니다.")
    n = len(keys) + 1
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 5))
    try:
        if n == 1:
            axes = [axes]

        if original_image.ndim == 2:
            axes[0].imshow(original_image, cmap="gray")
        else:
            axes[0].imshow(original_image)
        axes[0].set_title("Original")
        axes[0].axis("off")

        # 모든 맵을 동일 vmin/vmax로 정규화해 색상 스케일 일관성 보장
        all_vals = [np.asarray(maps[k]).ravel() for k in keys]
        vmin = float(np.concatenate(all_vals).min())
        vmax = float(np.concatenate(all_vals).max())

        for i, key in enumerate(keys, start=1):
            m = np.asarray(maps[key])
            if m.ndim == 3 and m.shape[0] == 1:
                m = m.squeeze(0)
            im = axes[i].imshow(m, cmap=cmap, vmin=vmin, vmax=vmax)
            axes[i].set_title(key.replace("_", " ").title())
            axes[i].axis("off")
            plt.colorbar(im, ax=axes[i], fraction=0.046)

        plt.tight_layout()
        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            print(f"Saved EfficientAD maps to {save_path}")
    finally:
        plt.close(fig)


if __name__ == "__main__":
    # 시각화 테스트
    print("=== Visualization Test ===")
    
    # 더미 데이터 생성
    np.random.seed(42)
    
    # 이미지
    original = np.random.rand(256, 256)
    anomaly_map = np.random.rand(256, 256) * 0.5
    # 이상 영역 추가
    anomaly_map[100:150, 100:150] = 1.0
    
    # 히트맵
    plot_anomaly_heatmap(
        original,
        anomaly_map,
        save_path="test_heatmap.png",
        title="Test Anomaly Heatmap"
    )
    
    # 학습 히스토리
    train_losses = [1.0 - 0.01*i + np.random.rand()*0.1 for i in range(50)]
    val_losses = [1.0 - 0.008*i + np.random.rand()*0.15 for i in range(50)]
    plot_training_history(train_losses, val_losses, "test_history.png")
    
    # 스코어 분포
    normal_scores = np.random.normal(0.02, 0.01, 100)
    anomaly_scores = np.random.normal(0.08, 0.02, 50)
    plot_score_distribution(
        normal_scores,
        anomaly_scores,
        threshold=0.05,
        save_path="test_distribution.png"
    )
    
    # ROC 곡선
    labels = np.concatenate([np.zeros(100), np.ones(50)])
    scores = np.concatenate([normal_scores, anomaly_scores])
    plot_roc_curve(labels, scores, "test_roc.png")
    
    print("\n✓ Visualization test completed!")
    print("Generated files: test_heatmap.png, test_history.png, test_distribution.png, test_roc.png")

