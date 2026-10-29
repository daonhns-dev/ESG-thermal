"""Readers for the reverse-engineered ATT thermal and ATG metadata formats."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ATT_HEADER_SIZE = 8
ATG_RECORD_SIZE = 39
TEMP_SCALE = 100.0


@dataclass(frozen=True)
class AttHeader:
    version: int
    reserved: int
    width: int
    height: int

    @property
    def frame_pixels(self) -> int:
        return self.width * self.height

    @property
    def frame_bytes(self) -> int:
        return self.frame_pixels * 2


@dataclass(frozen=True)
class AtgRecord:
    timestamp: str
    tag: int
    lat: float
    lon: float


def read_att_header(path: Path) -> AttHeader:
    path = Path(path)
    with path.open("rb") as stream:
        head = stream.read(ATT_HEADER_SIZE)
    if len(head) < ATT_HEADER_SIZE:
        raise ValueError(f"{path}: ATT header is shorter than {ATT_HEADER_SIZE} bytes")
    version, reserved, width, height = struct.unpack("<4H", head)
    if width <= 0 or height <= 0:
        raise ValueError(f"{path}: invalid ATT dimensions {width}x{height}")
    return AttHeader(version=version, reserved=reserved, width=width, height=height)


def att_frame_count(path: Path, header: AttHeader | None = None) -> int:
    path = Path(path)
    header = header or read_att_header(path)
    remaining = path.stat().st_size - ATT_HEADER_SIZE
    if remaining < 0 or remaining % header.frame_bytes != 0:
        raise ValueError(
            f"{path}: ATT payload size {remaining} is not divisible by frame size {header.frame_bytes}"
        )
    return remaining // header.frame_bytes


def read_att_frame_raw(path: Path, index: int, header: AttHeader | None = None) -> np.ndarray:
    path = Path(path)
    if index < 0:
        raise IndexError(f"{path}: ATT frame index must be non-negative: {index}")
    header = header or read_att_header(path)
    offset = ATT_HEADER_SIZE + index * header.frame_bytes
    with path.open("rb") as stream:
        stream.seek(offset)
        raw = stream.read(header.frame_bytes)
    if len(raw) != header.frame_bytes:
        raise IndexError(f"{path}: ATT frame {index} is outside the file")
    return np.frombuffer(raw, dtype="<u2").reshape(header.height, header.width).copy()


def read_att_frame_celsius(path: Path, index: int, header: AttHeader | None = None) -> np.ndarray:
    return read_att_frame_raw(path, index, header).astype(np.float32) / TEMP_SCALE


def iter_att_frames_raw(path: Path, header: AttHeader | None = None) -> Iterator[np.ndarray]:
    path = Path(path)
    header = header or read_att_header(path)
    frame_count = att_frame_count(path, header)
    with path.open("rb") as stream:
        stream.seek(ATT_HEADER_SIZE)
        for _ in range(frame_count):
            raw = stream.read(header.frame_bytes)
            if len(raw) != header.frame_bytes:
                break
            yield np.frombuffer(raw, dtype="<u2").reshape(header.height, header.width).copy()


def read_atg(path: Path) -> list[AtgRecord]:
    """Read all complete ATG records; an incomplete trailing record is ignored."""
    path = Path(path)
    record_count = path.stat().st_size // ATG_RECORD_SIZE
    records: list[AtgRecord] = []
    with path.open("rb") as stream:
        for _ in range(record_count):
            record = stream.read(ATG_RECORD_SIZE)
            if len(record) != ATG_RECORD_SIZE:
                break
            timestamp = record[0:19].decode("ascii", errors="replace")
            tag = struct.unpack("<i", record[19:23])[0]
            lat, lon = struct.unpack("<dd", record[23:39])
            records.append(AtgRecord(timestamp=timestamp, tag=tag, lat=lat, lon=lon))
    return records

