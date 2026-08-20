"""
단일 이미지 / 디렉터리 배치 추론 스크립트
"""

import argparse
import random
from pathlib import Path
import sys
import numpy as np
import torch
from PIL import Image
from sklearn.metrics import precision_recall_curve, roc_auc_score

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.ae import ConvAutoEncoder, SimpleAutoEncoder
from datasets.dataset import get_transforms
from utils.visualization import plot_anomaly_heatmap


class AnomalyDetector:
    """이상탐지 추론 클래스"""
    
    def __init__(self, checkpoint_path: str, device: str = 'cuda'):
        """
        Args:
            checkpoint_path: 체크포인트 파일 경로
            device: 디바이스
        """
        self.device = device if torch.cuda.is_available() else 'cpu'
        
        checkpoint = torch.load(checkpoint_path, map_location=self.device)
        self.config = checkpoint['config']
        
        self.model = self.build_model()
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.to(self.device)
        self.model.eval()
        
        image_size = (
            self.config.get('model', {}).get('image_size')
            or self.config.get('data', {}).get('image_size', 256)
        )
        self.transform = get_transforms(image_size=image_size, is_train=False)
        
        # 임계값 및 anomaly score 옵션 
        inf = self.config.get('inference', {})
        self.threshold = inf.get('threshold', 0.05)
        self.score_mode = inf.get('anomaly_score_mode', 'pixel_mse')
        self.multi_scale = inf.get('multi_scale', True)
        self.anomaly_smooth = inf.get('anomaly_smooth', True)
        self.blur_kernel_size = inf.get('blur_kernel_size', 5)
        self.blur_sigma = inf.get('blur_sigma', 1.5)

        is_vae = self.config.get('model', {}).get('vae', False)
        print(f"Loaded model from: {checkpoint_path}")
        print(f"Device: {self.device}")
        print(f"Model mode: {'VAE' if is_vae else 'AE'}")
        print(f"Threshold: {self.threshold}")
    
    def build_model(self):
        """모델 생성 (체크포인트 config 구조에 맞춤: model에는 image_size 없을 수 있음)"""
        model_config = self.config['model']
        model_name = model_config['name']
        
        if model_name == 'ConvAutoEncoder':
            model = ConvAutoEncoder(
                input_channels=model_config['input_channels'],
                latent_dim=model_config['latent_dim'],
                base_channels=model_config.get('base_channels', 32),
                depth=model_config.get('depth', 5),
                vae=model_config.get('vae', False),
                use_attention=model_config.get('use_attention', True),
            )
        elif model_name == 'SimpleAutoEncoder':
            model = SimpleAutoEncoder(
                input_channels=model_config['input_channels'],
                latent_dim=model_config['latent_dim']
            )
        else:
            raise ValueError(f"Unknown model: {model_name}")
        
        return model
    
    def predict(self, image_path: str, save_path: str = None) -> dict:
        """
        단일 이미지 이상탐지
        
        Args:
            image_path: 이미지 파일 경로
            save_path: 결과 저장 경로
        
        Returns:
            result: {
                'is_anomaly': bool,
                'anomaly_score': float,
                'anomaly_map': np.ndarray
            }
        """
        image = Image.open(image_path)
        gray_image = image.convert('L')
        original_image = np.array(gray_image)

        image_tensor = self.transform(gray_image).unsqueeze(0).to(self.device)
        
        # 추론 (evaluation과 동일한 score_mode 사용 가능)
        with torch.no_grad():
            anomaly_map, anomaly_score = self.model.get_anomaly_score(
                image_tensor,
                multi_scale=self.multi_scale,
                smooth=self.anomaly_smooth,
                score_mode=self.score_mode,
                blur_kernel_size=self.blur_kernel_size,
                blur_sigma=self.blur_sigma,
            )
        
        # Numpy 변환
        anomaly_map = anomaly_map[0, 0].cpu().numpy()
        anomaly_score = anomaly_score[0].item()
        
        # 이상 여부 판단
        is_anomaly = anomaly_score >= self.threshold
        
        # 결과
        result = {
            'is_anomaly': is_anomaly,
            'anomaly_score': anomaly_score,
            'anomaly_map': anomaly_map,
            'threshold': self.threshold
        }
        
        # 시각화 및 저장
        if save_path:
            from skimage.transform import resize
            anomaly_map_resized = resize(
                anomaly_map,
                original_image.shape,
                order=1,
                preserve_range=True
            )
            
            title = f"{'🔥 ANOMALY' if is_anomaly else '✓ NORMAL'} (Score: {anomaly_score:.4f})"
            plot_anomaly_heatmap(
                original_image / 255.0,
                anomaly_map_resized,
                save_path=save_path,
                title=title
            )
            print(f"Saved result to: {save_path}")
        
        return result


# 데이터셋과 동일한 이미지 확장자
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff'}


def collect_images_from_dir(input_dir: Path, max_per_class: int | None = None, seed: int = 42,) -> list[tuple[Path, str]]:
    """
    input_dir 아래 normal/, anomaly/ 폴더에서 이미지 경로 수집.
    max_per_class가 있으면 클래스당 그 수만큼만 무작위 샘플링 (가성비용).
    Returns: [(image_path, subfolder_name), ...]
    """
    collected_by_class = {'normal': [], 'anomaly': []}
    for subfolder in ('normal', 'anomaly'):
        folder = input_dir / subfolder
        if not folder.is_dir():
            continue
        for ext in IMAGE_EXTENSIONS:
            for img_path in sorted(folder.glob(f'*{ext}')):
                if img_path.is_file():
                    collected_by_class[subfolder].append((img_path, subfolder))

    if max_per_class is not None:
        rng = random.Random(seed)
        for subfolder in ('normal', 'anomaly'):
            lst = collected_by_class[subfolder]
            if len(lst) > max_per_class:
                collected_by_class[subfolder] = rng.sample(lst, max_per_class)

    return collected_by_class['normal'] + collected_by_class['anomaly']


def run_batch(
    detector: AnomalyDetector,
    input_dir: Path,
    output_dir: Path,
    save_heatmap: bool = True,
    max_per_class: int | None = None,
    seed: int = 42,
):
    """디렉터리 내 이미지에 대해 추론 실행 (max_per_class 있으면 샘플만)."""
    image_list = collect_images_from_dir(input_dir, max_per_class=max_per_class, seed=seed)
    if not image_list:
        print(f"No images found under {input_dir}/normal or {input_dir}/anomaly")
        return

    if max_per_class is not None:
        print(f"Sampled up to {max_per_class} per class (seed={seed}) -> {len(image_list)} images")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for sub in ('normal', 'anomaly'):
        (output_dir / sub).mkdir(parents=True, exist_ok=True)

    results_summary = []
    for i, (img_path, subfolder) in enumerate(image_list):
        save_path = (output_dir / subfolder / f"{img_path.stem}_result.png") if save_heatmap else None
        try:
            result = detector.predict(str(img_path), save_path)
            results_summary.append({
                'path': str(img_path),
                'label_dir': subfolder,
                'score': result['anomaly_score'],
                'pred': result['is_anomaly'],
            })
            pred_str = 'ANOMALY' if result['is_anomaly'] else 'Normal'
            print(f"  [{i+1}/{len(image_list)}] {img_path.name} ({subfolder}) -> {result['anomaly_score']:.4f} [{pred_str}]")
        except Exception as e:
            print(f"  [{i+1}/{len(image_list)}] {img_path.name} ERROR: {e}")
            results_summary.append({'path': str(img_path), 'label_dir': subfolder, 'error': str(e)})

    valid = [r for r in results_summary if 'error' not in r]
    if valid:
        tp = sum(1 for r in valid if r['label_dir'] == 'anomaly' and r['pred'])
        tn = sum(1 for r in valid if r['label_dir'] == 'normal' and not r['pred'])
        fp = sum(1 for r in valid if r['label_dir'] == 'normal' and r['pred'])
        fn = sum(1 for r in valid if r['label_dir'] == 'anomaly' and not r['pred'])
        n_normal = sum(1 for r in valid if r['label_dir'] == 'normal')
        n_anomaly = sum(1 for r in valid if r['label_dir'] == 'anomaly')
        acc = (tp + tn) / len(valid) if valid else 0
        print("\n" + "="*50)
        print("Batch Summary")
        print("="*50)
        print(f"Total: {len(valid)} (normal: {n_normal}, anomaly: {n_anomaly})")
        print(f"TN: {tn}  |  FP: {fp}")
        print(f"FN: {fn}  |  TP: {tp}")
        print(f"Accuracy: {acc:.4f}")
        print("="*50)

        scores_arr = np.array([r['score'] for r in valid])
        labels_arr = np.array([1 if r['label_dir'] == 'anomaly' else 0 for r in valid])
        auc = float("nan")
        if len(np.unique(labels_arr)) > 1:
            auc = float(roc_auc_score(labels_arr, scores_arr))
        precisions, recalls, thresholds = precision_recall_curve(labels_arr, scores_arr)
        f1s = 2 * precisions[:-1] * recalls[:-1] / (precisions[:-1] + recalls[:-1] + 1e-8)
        best_idx = np.argmax(f1s)
        best_thr = float(thresholds[best_idx])
        print(f"AUC: {auc:.4f}")
        print(f"\nOptimal Threshold (F1-max, this batch): {best_thr:.6f}")
        print(f"  → Precision: {precisions[best_idx]:.4f}  Recall: {recalls[best_idx]:.4f}  F1: {f1s[best_idx]:.4f}")
        print("  (score_mode/데이터 변경 시 스케일이 달라지므로 재탐색 필요)")

        import json
        save_dir = Path("results/fp_analysis")
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / "batch_results.json"
        with save_path.open("w", encoding="utf-8") as f:
            json.dump(valid, f, ensure_ascii=False, indent=2)
        print(f"\nResults saved to: {save_path}")


def main():
    parser = argparse.ArgumentParser(description="Thermal Image Anomaly Detection Inference")
    parser.add_argument('--checkpoint', type=str, required=True,
                       help='Path to checkpoint file')
    parser.add_argument('--image', type=str, default=None,
                       help='Path to single input image')
    parser.add_argument('--input_dir', type=str, default=None,
                       help='Path to directory with normal/ and anomaly/ subfolders (batch mode)')
    parser.add_argument('--output', type=str, default=None,
                       help='Path to save output (single image mode only)')
    parser.add_argument('--output_dir', type=str, default='results/predictions',
                       help='Directory to save batch results (batch mode, default: results/predictions)')
    parser.add_argument('--no_heatmap', action='store_true',
                       help='Do not save heatmap images in batch mode')
    parser.add_argument('--max_per_class', type=int, default=None,
                       help='Max images per class (normal/anomaly) for batch; omit to run all (가성비용 샘플링)')
    parser.add_argument('--threshold', type=float, default=None,
                       help='Anomaly threshold (override config)')
    parser.add_argument('--device', type=str, default='cuda',
                       help='Device (cuda or cpu)')

    args = parser.parse_args()

    if args.image and args.input_dir:
        parser.error("Use either --image or --input_dir, not both.")
    if not args.image and not args.input_dir:
        parser.error("Provide either --image or --input_dir.")

    detector = AnomalyDetector(args.checkpoint, args.device)

    if args.threshold is not None:
        detector.threshold = args.threshold
        print(f"Threshold overridden to: {args.threshold}")

    if args.input_dir:
        # batch mode: input_dir/normal, input_dir/anomaly 내 이미지 전체 추론
        input_dir = Path(args.input_dir)
        if not input_dir.is_dir():
            raise SystemExit(f"Not a directory: {input_dir}")
        print(f"\nBatch inference: {input_dir}")
        run_batch(
            detector,
            input_dir,
            Path(args.output_dir),
            save_heatmap=not args.no_heatmap,
            max_per_class=args.max_per_class,
        )
        return

    # single image mode
    if args.output is None:
        input_path = Path(args.image)
        output_dir = Path("results/predictions")
        output_dir.mkdir(parents=True, exist_ok=True)
        args.output = str(output_dir / f"{input_path.stem}_result.png")

    print(f"\nProcessing: {args.image}")
    result = detector.predict(args.image, args.output)

    print("\n" + "="*50)
    print("Anomaly Detection Result")
    print("="*50)
    print(f"Image:          {args.image}")
    print(f"Anomaly Score:  {result['anomaly_score']:.6f}")
    print(f"Threshold:      {result['threshold']:.6f}")
    print(f"Prediction:     {'🔥 ANOMALY DETECTED' if result['is_anomaly'] else '✓ Normal'}")
    print("="*50)


if __name__ == "__main__":
    main()

