"""
ImageNet(또는 서브셋) 프록시 로더 — EfficientAD pretraining penalty용.

루트 아래를 재귀적으로 순회해 이미지 경로를 모읍니다(ImageFolder 구조 필수 아님).
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PIL import Image
from torch.utils.data import Dataset


DEFAULT_EXTENSIONS: Tuple[str, ...] = (".JPEG", ".jpeg", ".jpg", ".JPG", ".png", ".PNG", ".webp")


def collect_image_paths(root: str | Path, extensions: Tuple[str, ...] = DEFAULT_EXTENSIONS) -> List[Path]:
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"imagenet_path 가 디렉터리가 아닙니다: {root}")
    ext_set = set(extensions)
    paths: List[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in ext_set:
            paths.append(p)
    paths.sort()
    return paths


class ImageNetSubsetDataset(Dataset):
    """pretraining penalty용 소규모 ImageNet 서브셋."""

    def __init__(
        self,
        root: str | Path,
        transform: Optional[Callable] = None,
        extensions: Tuple[str, ...] = DEFAULT_EXTENSIONS,
        gray_prob: float = 0.3,
    ):
        self.root = Path(root).resolve()
        self.transform = transform
        self.gray_prob = gray_prob
        self.paths = collect_image_paths(self.root, extensions)

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, index: int):
        if not self.paths:
            raise RuntimeError(
                f"이미지가 없습니다. imagenet_path 를 확인하세요: {self.root}"
            )
        path = self.paths[index]
        img = Image.open(path).convert("RGB")
        if self.gray_prob > 0 and random.random() < self.gray_prob:
            img = img.convert("L").convert("RGB")
        if self.transform is not None:
            img = self.transform(img)
        return img
