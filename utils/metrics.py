"""
이상탐지 평가 메트릭
"""

import numpy as np
import torch
from sklearn.metrics import (
    roc_auc_score,
    roc_curve,
    precision_recall_curve,
    f1_score,
    precision_score,
    recall_score,
    accuracy_score,
    confusion_matrix
)
from typing import Tuple, Dict


def compute_anomaly_scores(
    model,
    dataloader,
    device: str = 'cuda',
    multi_scale: bool = True,
    anomaly_smooth: bool = True,
    anomaly_score_mode: str = 'pixel_mse',
    blur_kernel_size: int = 5,
    blur_sigma: float = 1.5,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    모델로부터 이상 스코어 계산

    Args:
        model: 학습된 모델
        dataloader: 데이터로더
        device: 디바이스
        multi_scale: 멀티스케일 오차 합산 여부
        anomaly_smooth: anomaly map 가우시안 스무딩 여부
        anomaly_score_mode: pixel_mse | temperature_weighted | blur_then_diff
        blur_kernel_size: blur_then_diff 시 Gaussian 커널 크기
        blur_sigma: blur_then_diff 시 Gaussian sigma

    Returns:
        scores: 이상 스코어 배열
        labels: 실제 레이블 배열 (0: normal, 1: anomaly)
    """
    model.eval()
    model.to(device)

    all_scores = []
    all_labels = []

    with torch.no_grad():
        for images, labels, _ in dataloader:
            images = images.to(device)
            _, scores = model.get_anomaly_score(
                images,
                multi_scale=multi_scale,
                smooth=anomaly_smooth,
                score_mode=anomaly_score_mode,
                blur_kernel_size=blur_kernel_size,
                blur_sigma=blur_sigma,
            )
            all_scores.extend(scores.cpu().numpy())
            all_labels.extend(labels.numpy())

    return np.array(all_scores), np.array(all_labels)


def find_optimal_threshold(scores: np.ndarray, labels: np.ndarray) -> Tuple[float, float]:
    """
    최적 임계값 찾기 (F1 score 최대화)
    
    Args:
        scores: 이상 스코어
        labels: 실제 레이블
    
    Returns:
        best_threshold: 최적 임계값
        best_f1: 최적 F1 스코어
    """
    precision, recall, thresholds = precision_recall_curve(labels, scores)
    f1_scores = 2 * precision * recall / (precision + recall + 1e-10)
    
    best_idx = np.argmax(f1_scores)
    best_threshold = thresholds[best_idx] if best_idx < len(thresholds) else thresholds[-1]
    best_f1 = f1_scores[best_idx]
    
    return best_threshold, best_f1


def compute_metrics(scores: np.ndarray, labels: np.ndarray, threshold: float = None) -> Dict[str, float]:
    """
    종합 평가 메트릭 계산
    
    Args:
        scores: 이상 스코어
        labels: 실제 레이블
        threshold: 임계값 (None이면 자동 계산)
    
    Returns:
        metrics: 메트릭 딕셔너리
    """
    metrics = {}
    
    # AUC
    if len(np.unique(labels)) > 1:
        metrics['auc'] = roc_auc_score(labels, scores)
    else:
        metrics['auc'] = 0.0
    
    # 임계값 설정
    if threshold is None:
        threshold, _ = find_optimal_threshold(scores, labels)
    
    # 예측값
    predictions = (scores >= threshold).astype(int)
    
    # 분류 메트릭
    metrics['threshold'] = threshold
    metrics['accuracy'] = accuracy_score(labels, predictions)
    metrics['precision'] = precision_score(labels, predictions, zero_division=0)
    metrics['recall'] = recall_score(labels, predictions, zero_division=0)
    metrics['f1'] = f1_score(labels, predictions, zero_division=0)
    
    # Confusion Matrix
    cm = confusion_matrix(labels, predictions)
    if cm.shape == (2, 2):
        tn, fp, fn, tp = cm.ravel()
        metrics['true_negative'] = int(tn)
        metrics['false_positive'] = int(fp)
        metrics['false_negative'] = int(fn)
        metrics['true_positive'] = int(tp)

    if metrics.get('true_negative', -1) == 0 or metrics.get('true_positive', -1) == 0:
        print("⚠️ threshold가 한쪽 클래스로 전부 쏠려 예측하는 지점에서 선택됨 — F1/Accuracy가 base rate에 낚인 결과일 수 있음")

    return metrics


def print_metrics(metrics: Dict[str, float]):
    """
    메트릭 출력
    
    Args:
        metrics: 메트릭 딕셔너리
    """
    print("\n" + "="*50)
    print("Evaluation Metrics")
    print("="*50)
    
    print(f"AUC:        {metrics['auc']:.4f}")
    print(f"Threshold:  {metrics['threshold']:.6f}")
    print(f"Accuracy:   {metrics['accuracy']:.4f}")
    print(f"Precision:  {metrics['precision']:.4f}")
    print(f"Recall:     {metrics['recall']:.4f}")
    print(f"F1 Score:   {metrics['f1']:.4f}")
    
    if 'true_positive' in metrics:
        print("\nConfusion Matrix:")
        print(f"  TN: {metrics['true_negative']:4d}  |  FP: {metrics['false_positive']:4d}")
        print(f"  FN: {metrics['false_negative']:4d}  |  TP: {metrics['true_positive']:4d}")
    
    print("="*50 + "\n")


if __name__ == "__main__":
    # 메트릭 테스트
    print("=== Metrics Test ===")
    
    # 더미 데이터 생성
    np.random.seed(42)
    
    # 정상: 낮은 스코어, 이상: 높은 스코어
    normal_scores = np.random.normal(0.02, 0.01, 100)
    anomaly_scores = np.random.normal(0.08, 0.02, 50)
    
    scores = np.concatenate([normal_scores, anomaly_scores])
    labels = np.concatenate([np.zeros(100), np.ones(50)])
    
    # 메트릭 계산
    metrics = compute_metrics(scores, labels)
    print_metrics(metrics)
    
    # 최적 임계값
    optimal_threshold, optimal_f1 = find_optimal_threshold(scores, labels)
    print(f"Optimal Threshold: {optimal_threshold:.6f}")
    print(f"Optimal F1 Score: {optimal_f1:.4f}")
    
    print("\n✓ Metrics test completed!")

