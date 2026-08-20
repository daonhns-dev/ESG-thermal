"""
산업시설 열화상 CCTV CSV 데이터 → NCC 프로토타입용 PNG 이미지 변환 스크립트

사용법:
    python scripts/convert_thermal_csv_to_images.py \
        --input "k:/산업시설 열화상 CCTV 데이터/1.서부발전/1.고압전동기" \
        --output "data/thermal_high_voltage_motor" \
        --split "0.8"  # train 80%, test 20%
"""

import argparse
from pathlib import Path
import numpy as np
from PIL import Image
import random


def load_thermal_csv(csv_path: Path) -> np.ndarray:
    """
    열화상 CSV 파일 로드 (FLIR/산업용 형식)
    
    형식:
        - 1~5행: 메타데이터 (Emissivity;0.95 등)
        - 6행~: 온도값 (세미콜론 구분, 2D 그리드)
    
    Returns:
        temperature_map: (H, W) 형태의 온도 배열
    """
    with open(csv_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    data_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(';')
        if not parts:
            continue
        first_val = parts[0].strip()
        try:
            float(first_val)
            data_lines.append(line)
        except ValueError:
            continue  
    
    if not data_lines:
        raise ValueError(f"No temperature data found in {csv_path}")
    
    rows = []
    for line in data_lines:
        values = []
        for v in line.split(';'):
            v = v.strip()
            if v:
                try:
                    values.append(float(v))
                except ValueError:
                    pass
        if values:
            rows.append(values)
    
    if not rows:
        raise ValueError(f"Failed to parse temperature data from {csv_path}")
    
    return np.array(rows, dtype=np.float32)


def temperature_to_grayscale(temp_array: np.ndarray, temp_min: float = None, temp_max: float = None) -> np.ndarray:
    """
    온도 배열을 0-255 그레이스케일 이미지로 변환
    
    Args:
        temp_array: 온도 배열 (H, W)
        temp_min: 최소 온도 (None이면 자동)
        temp_max: 최대 온도 (None이면 자동)
    
    Returns:
        uint8 이미지 배열
    """
    if temp_min is None:
        temp_min = np.nanmin(temp_array)
    if temp_max is None:
        temp_max = np.nanmax(temp_array)
    
    # 범위 보정
    temp_range = temp_max - temp_min
    if temp_range < 1e-6:
        temp_range = 1.0
    
    normalized = (temp_array - temp_min) / temp_range
    normalized = np.clip(normalized, 0, 1)
    grayscale = (normalized * 255).astype(np.uint8)
    
    return grayscale


def convert_csv_to_png(csv_path: Path, output_path: Path, temp_min: float = None, temp_max: float = None) -> bool:
    """
    단일 CSV 파일을 PNG로 변환
    
    Returns:
        성공 여부
    """
    try:
        temp_array = load_thermal_csv(csv_path)
        img_array = temperature_to_grayscale(temp_array, temp_min, temp_max)
        img = Image.fromarray(img_array, mode='L')
        img.save(output_path)
        return True
    except Exception as e:
        print(f"  [ERROR] {csv_path.name}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="열화상 CSV → NCC 프로토타입용 PNG 변환"
    )
    parser.add_argument('--input', '-i', type=str, required=True, help='입력 CSV 파일 또는 디렉토리 경로')
    parser.add_argument('--output', '-o', type=str, default='data/thermal_converted', help='출력 디렉토리 경로')
    parser.add_argument('--split', type=float, default=0.8, help='학습/테스트 비율 (0.8 = train 80%%, test 20%%)')
    parser.add_argument('--seed', type=int, default=42, help='랜덤 시드')
    
    args = parser.parse_args()
    
    input_path = Path(args.input)
    output_path = Path(args.output)
    
    csv_files = []
    if input_path.is_file() and input_path.suffix.lower() == '.csv':
        csv_files = [input_path]
    elif input_path.is_dir():
        csv_files = list(input_path.rglob('*.csv'))
    else:
        print(f"입력 경로를 찾을 수 없습니다: {input_path}")
        return 1
    
    if not csv_files:
        print(f"CSV 파일이 없습니다: {input_path}")
        return 1
    
    print(f"발견된 CSV 파일: {len(csv_files)}개")
    
    train_dir = output_path / "train" / "normal"
    test_dir = output_path / "test" / "normal"
    train_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)
    
    random.seed(args.seed)
    random.shuffle(csv_files)
    split_idx = int(len(csv_files) * args.split)
    train_files = csv_files[:split_idx]
    test_files = csv_files[split_idx:]
    
    print(f"  Train: {len(train_files)}개")
    print(f"  Test:  {len(test_files)}개")
    
    success = 0
    for i, csv_path in enumerate(train_files):
        out_name = f"{csv_path.stem}.png"
        out_path = train_dir / out_name
        if convert_csv_to_png(csv_path, out_path):
            success += 1
        if (i + 1) % 10 == 0:
            print(f"  Train 변환: {i+1}/{len(train_files)}")
    
    for i, csv_path in enumerate(test_files):
        out_name = f"{csv_path.stem}.png"
        out_path = test_dir / out_name
        if convert_csv_to_png(csv_path, out_path):
            success += 1
        if (i + 1) % 10 == 0:
            print(f"  Test 변환: {i+1}/{len(test_files)}")
    
    print(f"\n완료: {success}/{len(csv_files)} 파일 변환")
    print(f"출력: {output_path.absolute()}")
    print(f"\n다음 단계: config_ae.yaml의 data.train_dir을 '{output_path / 'train'}'로 설정 후 학습")
    
    return 0


if __name__ == "__main__":
    exit(main())
