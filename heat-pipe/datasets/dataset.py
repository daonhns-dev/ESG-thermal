"""
데이터셋 로더 및 전처리
"""

import os
from pathlib import Path
from typing import Optional, Callable, List, Tuple

import torch
from torch.utils.data import Dataset, DataLoader, Subset
from torchvision import transforms
from PIL import Image
import numpy as np


def extract_csv_features(csv_path: Path, temp_array: np.ndarray = None) -> np.ndarray:
    """
    CSV에서 메타데이터 + 온도 통계 추출 (멀티모달용)
    
    Returns:
        features: (n_features,) 배열
    """
    meta = {}
    if temp_array is None:
        temp_array = load_thermal_csv(csv_path)
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if ';' in line:
                k, v = line.split(';', 1)
                k, v = k.strip(), v.strip()
                try:
                    meta[k] = float(v)
                except ValueError:
                    pass
    
    features = [
        meta.get('Emissivity', 0.95),
        meta.get('ReflectedTemperature', 15.0),
        meta.get('Distance', 1.0),
        meta.get('AtmosphericTemperature', 15.0),
        meta.get('RelativeHumidity', 50.0),
        np.nanmean(temp_array) if temp_array.size else 25.0,
        np.nanstd(temp_array) if temp_array.size else 5.0,
        np.nanmin(temp_array) if temp_array.size else 20.0,
        np.nanmax(temp_array) if temp_array.size else 35.0,
        np.median(temp_array) if temp_array.size else 25.0,
    ]
    return np.array(features, dtype=np.float32)


def load_thermal_csv(csv_path: Path) -> np.ndarray:
    """
    열화상 CSV 파일 로드 (FLIR/산업시설 형식)
    형식: 1~5행 메타데이터, 6행~ 온도값 (세미콜론 구분)
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
        try:
            float(parts[0].strip())
            data_lines.append(line)
        except ValueError:
            continue
    
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
    
    return np.array(rows, dtype=np.float32) if rows else np.array([[]])


class ThermalCSVDataset(Dataset):
    """
    열화상 CSV 데이터셋 
    """
    
    def __init__(self, root_dir: str, transform: Optional[Callable] = None, is_train: bool = True, csv_paths: Optional[List[Path]] = None):
        self.transform = transform
        self.is_train = is_train
        self.csv_paths = []
        self.labels = []
        
        if csv_paths:
            self.csv_paths = list(csv_paths)
            self.labels = [0] * len(csv_paths)  
        else:
            root = Path(root_dir)
            for ext in ['.csv']:
                for p in root.rglob(f'*{ext}'):
                    if p.is_file():
                        self.csv_paths.append(p)
                        self.labels.append(0)
        
        print(f"Loaded {len(self.csv_paths)} CSV files")
    
    def __len__(self) -> int:
        return len(self.csv_paths)
    
    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, int, str]:
        csv_path = self.csv_paths[idx]
        label = self.labels[idx]
        
        temp_array = load_thermal_csv(csv_path)
        t_min, t_max = np.nanmin(temp_array), np.nanmax(temp_array)
        if t_max - t_min < 1e-6:
            t_max = t_min + 1.0
        img_array = ((temp_array - t_min) / (t_max - t_min) * 255).astype(np.uint8)
        image = Image.fromarray(img_array, mode='L')
        
        if self.transform:
            image = self.transform(image)
        
        return image, label, str(csv_path)


class ThermalImageDataset(Dataset):
    """
    열화상 이미지 데이터셋
    
    Args:
        root_dir: 데이터 디렉토리 경로
        transform: 이미지 변환
        is_train: 학습 모드 여부
    """
    
    def __init__(self, root_dir: str, transform: Optional[Callable] = None, is_train: bool = True):
        self.root_dir = Path(root_dir)
        self.transform = transform
        self.is_train = is_train
        
        self.image_paths = []
        self.labels = []
        
        if is_train:
            normal_dir = self.root_dir / "normal"
            if normal_dir.exists():
                self._load_images_from_dir(normal_dir, label=0)
        else:
            normal_dir = self.root_dir / "normal"
            anomaly_dir = self.root_dir / "anomaly"
            
            if normal_dir.exists():
                self._load_images_from_dir(normal_dir, label=0)
            if anomaly_dir.exists():
                self._load_images_from_dir(anomaly_dir, label=1)
        
        print(f"Loaded {len(self.image_paths)} images from {root_dir}")
        if not is_train:
            num_normal = sum(1 for l in self.labels if l == 0)
            num_anomaly = sum(1 for l in self.labels if l == 1)
            print(f"  - Normal: {num_normal}, Anomaly: {num_anomaly}")
    
    def _load_images_from_dir(self, dir_path: Path, label: int):
        """디렉토리에서 이미지 파일 로드"""
        extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff'}
        for img_path in sorted(dir_path.rglob('*')):
            if img_path.is_file() and img_path.suffix.lower() in extensions:
                self.image_paths.append(img_path)
                self.labels.append(label)
    
    def __len__(self) -> int:
        return len(self.image_paths)
    
    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, int, str]:
        img_path = self.image_paths[idx]
        label = self.labels[idx]
        
        image = Image.open(img_path)
        
        if image.mode != 'L':
            image = image.convert('L')
        
        if self.transform:
            image = self.transform(image)
        else:
            from torchvision import transforms as T
            image = T.ToTensor()(image)

        return image, label, str(img_path)


class ThermalRGBPairDataset(Dataset):
    """
    Thermal + RGB 쌍 데이터셋 (Phase 2)
    
    thermal_dir/normal/xxx.png ↔ rgb_dir/normal/xxx.png
    동일 stem(파일명)으로 매칭. thermal과 RGB는 same resolution, same crop 전제.
    """
    
    def __init__(
        self,
        thermal_dir: str,
        rgb_dir: str,
        transform_thermal: Optional[Callable] = None,
        transform_rgb: Optional[Callable] = None,
        is_train: bool = True
    ):
        self.thermal_dir = Path(thermal_dir)
        self.rgb_dir = Path(rgb_dir)
        self.transform_thermal = transform_thermal
        self.transform_rgb = transform_rgb or get_transforms_rgb(256, is_train)
        
        self.pairs = []
        for sub in ['normal', 'anomaly'] if not is_train else ['normal']:
            t_dir = self.thermal_dir / sub
            r_dir = self.rgb_dir / sub
            if not t_dir.exists() or not r_dir.exists():
                continue
            for tp in t_dir.glob('*.*'):
                if tp.suffix.lower() in ['.png', '.jpg', '.jpeg', '.bmp']:
                    rp = r_dir / tp.name
                    if rp.exists():
                        label = 1 if sub == 'anomaly' else 0
                        self.pairs.append((tp, rp, label))
        
    def __len__(self):
        return len(self.pairs)
    
    def __getitem__(self, idx):
        tp, rp, label = self.pairs[idx]
        thermal = Image.open(tp).convert('L')
        rgb = Image.open(rp).convert('RGB')
        if self.transform_thermal:
            thermal = self.transform_thermal(thermal)
        if self.transform_rgb:
            rgb = self.transform_rgb(rgb)
        # [T, R, G, B] 4채널
        x = torch.cat([thermal, rgb], dim=0)
        return x, label, str(tp)


class ThermalSequenceDataset(Dataset):
    """
    시계열 열화상 이미지 데이터셋 (ConvLSTM용)
    
    Args:
        root_dir: 시퀀스 디렉토리 경로
        sequence_length: 시퀀스 길이
        transform: 이미지 변환
    """
    
    def __init__(self, root_dir: str, sequence_length: int = 10, transform: Optional[Callable] = None):
        self.root_dir = Path(root_dir)
        self.sequence_length = sequence_length
        self.transform = transform
        
        # 시퀀스 디렉토리 수집
        self.sequences = []
        for seq_dir in sorted(self.root_dir.iterdir()):
            if seq_dir.is_dir():
                # 각 시퀀스 폴더 내 이미지 파일 수집
                images = sorted(list(seq_dir.glob('*.png')) + list(seq_dir.glob('*.jpg')))
                if len(images) >= sequence_length:
                    # 슬라이딩 윈도우로 여러 시퀀스 생성
                    for i in range(len(images) - sequence_length + 1):
                        self.sequences.append(images[i:i+sequence_length])
        
        print(f"Loaded {len(self.sequences)} sequences from {root_dir}")
    
    def __len__(self) -> int:
        return len(self.sequences)
    
    def __getitem__(self, idx: int) -> torch.Tensor:
        sequence_paths = self.sequences[idx]
        
        frames = []
        for img_path in sequence_paths:
            image = Image.open(img_path).convert('L')
            if self.transform:
                image = self.transform(image)
            frames.append(image)
        
        # (T, C, H, W) 형태로 스택
        sequence = torch.stack(frames, dim=0)
        
        return sequence


def get_transforms(image_size: int = 256, is_train: bool = True, normalize_thermal: bool = False) -> transforms.Compose:
    """
    열화상 이미지 변환 파이프라인
 
    Train augmentation (FP 분석 기반):
      - RandomHorizontalFlip  : 기존 유지
      - RandomRotation        : 기존 유지 (±10°)
      - ColorJitter           : 기존 유지 (brightness/contrast)
      - RandomPerspective     : T4(카메라 각도 편차) 대응 — distortion_scale=0.1, p=0.3
      - GaussianBlur          : T2(엣지/케이블 과민) 대응  — RandomApply p=0.3, kernel=3, sigma=(0.1,1.0)
 
    distortion_scale/p 튜닝 가이드:
      - FP가 줄지 않으면 distortion_scale을 0.15까지 올려보기
      - validation AUC가 떨어지면 p를 0.2로 낮추기
    """
    tail = [transforms.Normalize(mean=[0.5], std=[0.5])] if normalize_thermal else []

    if is_train:
        return transforms.Compose([
            transforms.Resize((image_size, image_size)),
            transforms.RandomHorizontalFlip(p=0.5),
            transforms.RandomRotation(degrees=10),
            transforms.ColorJitter(brightness=0.1, contrast=0.1),
            # ── FP 대응 augmentation (추가) ──────────────────────────────────
            transforms.RandomPerspective(distortion_scale=0.1, p=0.3),
            transforms.RandomApply([transforms.GaussianBlur(kernel_size=3, sigma=(0.1, 1.0))], p=0.3),
            transforms.ToTensor(),
        ] + tail)

    return transforms.Compose([transforms.Resize((image_size, image_size)), transforms.ToTensor(),] + tail)


def get_efficientad_transforms(image_size: int = 256, three_channel_from_gray: bool = True,) -> transforms.Compose:
    """
    EfficientAD 입력: Resize(256) + (그레이→3채널) + ToTensor → [0,1], (3,256,256).
    ImageNet 정규화는 모델 내부(imagenet_normalize)에서만 적용.
    S–T 학습 시에는 이 변환만 사용(기존 AE용 강한 augmentation 없음).
    """
    ops = [transforms.Resize((image_size, image_size)),]
    if three_channel_from_gray:
        ops.append(transforms.Grayscale(num_output_channels=3))
    ops.append(transforms.ToTensor())
    return transforms.Compose(ops)


def create_efficientad_train_loader(
    train_dir: str,
    batch_size: int = 1,
    image_size: int = 256,
    num_workers: int = 4,
    shuffle: bool = True,
    pin_memory: bool = True,
    train_fraction: float = 1.0,
    subset_seed: Optional[int] = None,
) -> DataLoader:
    """
    정상 학습용 로더. train_dir 은 ``normal`` 폴더의 상위 경로여야 함
    (예: ``data/train`` → ``data/train/normal/*.png``).
    """
    tfm = get_efficientad_transforms(image_size=image_size, three_channel_from_gray=True)
    ds = ThermalImageDataset(root_dir=train_dir, transform=tfm, is_train=True)
    if train_fraction < 1.0:
        rng = np.random.default_rng(subset_seed)
        n = len(ds)
        k = max(1, int(n * train_fraction))
        idx = rng.choice(n, size=k, replace=False)
        ds = Subset(ds, idx.tolist())
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle, num_workers=num_workers, pin_memory=pin_memory)


def get_transforms_rgb(image_size: int = 256, is_train: bool = True) -> transforms.Compose:
    """RGB용 변환 (3채널)"""
    if is_train:
        return transforms.Compose([
            transforms.Resize((image_size, image_size)),
            transforms.RandomHorizontalFlip(p=0.5),
            transforms.RandomRotation(degrees=10),
            transforms.ColorJitter(brightness=0.1, contrast=0.1),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])
    return transforms.Compose([transforms.Resize((image_size, image_size)), transforms.ToTensor(), transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])


def create_dataloaders(
    train_dir: str,
    test_dir: str,
    val_dir: Optional[str] = None,
    batch_size: int = 32,
    image_size: int = 256,
    num_workers: int = 4,
    shuffle: bool = True,
    pin_memory: bool = True,
    train_fraction: float = 1.0,
    test_fraction: float = 1.0,
    subset_seed: Optional[int] = None,
    ) -> Tuple[DataLoader, Optional[DataLoader], DataLoader]:
    """
    학습 및 테스트 데이터로더 생성
 
    Args:
        train_dir: 학습 데이터 디렉토리
        test_dir: 테스트 데이터 디렉토리
        val_dir: 검증 데이터 디렉토리 (None이면 val_loader=None 반환, 호출부가 test_dir로 폴백)
        batch_size: 배치 크기
        image_size: 이미지 크기
        num_workers: 워커 프로세스 수
        shuffle: 셔플 여부
        pin_memory: True면 CUDA 사용 시 GPU 전송 가속 (CPU만 쓸 때는 False 권장)
        train_fraction: 학습 데이터 사용 비율 (0~1). 1.0=전체, 0.1=10% 등
        test_fraction: 테스트 데이터 사용 비율 (0~1)
        subset_seed: 비율 적용 시 샘플링 재현을 위한 시드 (None이면 매번 다른 서브셋)
 
    Returns:
        train_loader, val_loader, test_loader
    """
    train_transform = get_transforms(image_size=image_size, is_train=True)
    test_transform = get_transforms(image_size=image_size, is_train=False)
    
    train_dataset = ThermalImageDataset(root_dir=train_dir, transform=train_transform, is_train=True)
    test_dataset = ThermalImageDataset(root_dir=test_dir, transform=test_transform, is_train=False)

    val_dataset = None
    if val_dir:
        val_dataset = ThermalImageDataset(root_dir=val_dir, transform=test_transform, is_train=False)
        if len(val_dataset) == 0:
            print(f"⚠️ val_dir({val_dir})에 이미지가 없습니다 — val 없이 test로 폴백합니다")
            val_dataset = None
    # 서브셋 비율 적용 (코드 검증용 소량 학습)
    if train_fraction < 1.0 or test_fraction < 1.0:
        rng = np.random.default_rng(subset_seed)
        if train_fraction < 1.0:
            n_train = len(train_dataset)
            k_train = max(1, int(n_train * train_fraction))
            idx_train = rng.choice(n_train, size=k_train, replace=False)
            train_dataset = Subset(train_dataset, idx_train.tolist())
        if test_fraction < 1.0:
            n_test = len(test_dataset)
            k_test = max(1, int(n_test * test_fraction))
            idx_test = rng.choice(n_test, size=k_test, replace=False)
            test_dataset = Subset(test_dataset, idx_test.tolist())
    
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=shuffle, num_workers=num_workers, pin_memory=pin_memory,)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False, num_workers=num_workers, pin_memory=pin_memory,)
    val_loader = None
    if val_dataset is not None:
        val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=num_workers, pin_memory=pin_memory)

    return train_loader, val_loader, test_loader


if __name__ == "__main__":
    print("=== Dataset Test ===")
    
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        train_normal = Path(tmpdir) / "train" / "normal"
        test_normal = Path(tmpdir) / "test" / "normal"
        test_anomaly = Path(tmpdir) / "test" / "anomaly"
        
        train_normal.mkdir(parents=True)
        test_normal.mkdir(parents=True)
        test_anomaly.mkdir(parents=True)
        
        for i in range(10):
            dummy_img = Image.fromarray(np.random.randint(0, 255, (256, 256), dtype=np.uint8))
            dummy_img.save(train_normal / f"train_{i}.png")
        
        for i in range(5):
            dummy_img = Image.fromarray(np.random.randint(0, 255, (256, 256), dtype=np.uint8))
            dummy_img.save(test_normal / f"test_normal_{i}.png")
        
        for i in range(5):
            dummy_img = Image.fromarray(np.random.randint(0, 255, (256, 256), dtype=np.uint8))
            dummy_img.save(test_anomaly / f"test_anomaly_{i}.png")
        
        train_loader, val_loader, test_loader = create_dataloaders(
            train_dir=str(Path(tmpdir) / "train"),
            test_dir=str(Path(tmpdir) / "test"),
            batch_size=4,
            image_size=128,
            num_workers=0
        )
        
        print(f"\nTrain batches: {len(train_loader)}")
        print(f"Test batches: {len(test_loader)}")
        
        for images, labels, paths in train_loader:
            print(f"\nTrain batch:")
            print(f"  Images shape: {images.shape}")
            print(f"  Labels: {labels}")
            break
        
        for images, labels, paths in test_loader:
            print(f"\nTest batch:")
            print(f"  Images shape: {images.shape}")
            print(f"  Labels: {labels}")
            break
    
    print("\n✓ Dataset test completed!")

