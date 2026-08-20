"""
열화상 CSV 파일 직접 추론 스크립트

학습된 모델로 CSV 형식 열화상 데이터 이상탐지
"""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import torch
import numpy as np
from PIL import Image

from models.ae import ConvAutoEncoder, SimpleAutoEncoder
from datasets.dataset import get_transforms, load_thermal_csv
from utils.visualization import plot_anomaly_heatmap


def csv_to_tensor(csv_path: Path, transform) -> torch.Tensor:
    """CSV 열화상 → 모델 입력 텐서"""
    temp_array = load_thermal_csv(csv_path)
    t_min, t_max = np.nanmin(temp_array), np.nanmax(temp_array)
    if t_max - t_min < 1e-6:
        t_max = t_min + 1.0
    img_array = ((temp_array - t_min) / (t_max - t_min) * 255).astype(np.uint8)
    image = Image.fromarray(img_array, mode='L')
    return transform(image).unsqueeze(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', required=True, help='체크포인트 경로')
    parser.add_argument('--csv', required=True, help='CSV 파일 또는 디렉토리')
    parser.add_argument('--output', default='results/csv_predictions', help='결과 저장 경로')
    parser.add_argument('--threshold', type=float, default=None)
    parser.add_argument('--device', default='cuda')
    
    args = parser.parse_args()
    
    # ckpt loaded
    checkpoint = torch.load(args.checkpoint, map_location=args.device)
    config = checkpoint['config']
    
    # model build
    mc = config['model']
    if mc['name'] == 'ConvAutoEncoder':
        model = ConvAutoEncoder(input_channels=mc['input_channels'], latent_dim=mc['latent_dim'], image_size=mc['image_size'])
    else:
        model = SimpleAutoEncoder(input_channels=mc['input_channels'], latent_dim=mc['latent_dim'])
    model.load_state_dict(checkpoint['model_state_dict'])
    model.to(args.device)
    model.eval()
    
    transform = get_transforms(image_size=mc['image_size'], is_train=False)
    threshold = args.threshold or config['inference'].get('threshold', 0.05)
    
    csv_path = Path(args.csv)
    if csv_path.is_file():
        files = [csv_path]
    else:
        files = list(csv_path.rglob('*.csv'))
    
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Files to process: {len(files)}")
    for fp in files:
        try:
            x = csv_to_tensor(fp, transform).to(args.device)
            with torch.no_grad():
                anomaly_map, score = model.get_anomaly_score(x)
            score = score[0].item()
            am = anomaly_map[0, 0].cpu().numpy()
            is_anomaly = score >= threshold
            
            orig = load_thermal_csv(fp)
            t_min, t_max = np.nanmin(orig), np.nanmax(orig)
            img_np = ((orig - t_min) / (t_max - t_min + 1e-6)).astype(np.float32)
            
            from skimage.transform import resize
            am_resized = resize(am, orig.shape, order=1, preserve_range=True)
            title = f"{'ANOMALY' if is_anomaly else 'NORMAL'} (Score: {score:.4f})"
            save_path = out_dir / f"{fp.stem}_result.png"
            plot_anomaly_heatmap(img_np, am_resized, save_path=save_path, title=title)
            
            print(f"  {fp.name}: Score={score:.4f} -> {'ANOMALY' if is_anomaly else 'Normal'} -> {save_path}")
        except Exception as e:
            print(f"  [ERROR] {fp.name}: {e}")


if __name__ == "__main__":
    main()
