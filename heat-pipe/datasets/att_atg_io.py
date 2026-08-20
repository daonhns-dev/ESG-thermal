"""
.att(열화상 raw) / .atg(프레임별 GPS·타임스탬프) 리더

제조사 포맷 문서 없이 실제 파일 바이너리를 분석해 역추정한 포맷이며,
2026-08-13 열수송관 모니터링 데이터 13개 세션(.avi 있는 9개 세션 전부)에서
프레임 수가 .avi와 정확히 일치함을 확인해 검증했다.

.att 구조
    [8바이트 헤더: uint16 x4 = (version, reserved, width, height)]
    + [프레임 0: height*width uint16 LE] + [프레임 1] + ... (프레임간 여백 없음)
    온도(°C) = raw uint16 값 / 100  (실측 30~49°C 범위로 확인, 클리핑 없음)

.atg 구조 (프레임당 고정 39바이트 레코드, .att/.avi와 프레임 인덱스 1:1 대응)
    [19바이트 ASCII "YYYY-MM-DD HH:MM:SS"]
    + [int32 tag]           # 정확한 의미 미확인 (GPS 위성 수/fix quality로 추정, 5~29 사이 변동)
    + [float64 lat]
    + [float64 lon]

주의: 녹화가 비정상 종료된 세션은 .atg 크기가 39의 배수가 아니거나(레코드 중간에 잘림),
.avi 자체가 없을 수 있다. read_atg()는 온전한 레코드만 반환하고 나머지는 버린다.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

import numpy as np

ATT_HEADER_SIZE = 8
ATG_RECORD_SIZE = 39
TEMP_SCALE = 100.0


@dataclass
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


@dataclass
class AtgRecord:
    timestamp: str
    tag: int
    lat: float
    lon: float


def read_att_header(path: Path) -> AttHeader:
    with open(path, "rb") as f:
        head = f.read(ATT_HEADER_SIZE)
    if len(head) < ATT_HEADER_SIZE:
        raise ValueError(f"{path}: 헤더가 {ATT_HEADER_SIZE}바이트보다 작음 (손상된 파일)")
    version, reserved, width, height = struct.unpack("<4H", head)
    return AttHeader(version=version, reserved=reserved, width=width, height=height)


def att_frame_count(path: Path, header: Optional[AttHeader] = None) -> int:
    header = header or read_att_header(path)
    size = Path(path).stat().st_size
    remain = size - ATT_HEADER_SIZE
    if remain % header.frame_bytes != 0:
        raise ValueError(
            f"{path}: 헤더 이후 크기({remain})가 프레임 크기({header.frame_bytes})의 배수가 아님 "
            "(파일이 잘렸거나 세션이 비정상 종료됐을 가능성)"
        )
    return remain // header.frame_bytes


def read_att_frame_raw(path: Path, index: int, header: Optional[AttHeader] = None) -> np.ndarray:
    """지정한 인덱스의 열화상 프레임을 (H, W) uint16 raw 값으로 반환 (°C = 반환값 / 100)"""
    header = header or read_att_header(path)
    offset = ATT_HEADER_SIZE + index * header.frame_bytes
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read(header.frame_bytes)
    if len(raw) != header.frame_bytes:
        raise IndexError(f"{path}: 프레임 {index} 읽기 실패 (범위 초과)")
    return np.frombuffer(raw, dtype="<u2").reshape(header.height, header.width).copy()


def read_att_frame_celsius(path: Path, index: int, header: Optional[AttHeader] = None) -> np.ndarray:
    """지정한 인덱스의 열화상 프레임을 (H, W) float32 섭씨 온도 배열로 반환"""
    return read_att_frame_raw(path, index, header).astype(np.float32) / TEMP_SCALE


def iter_att_frames_raw(path: Path, header: Optional[AttHeader] = None) -> Iterator[np.ndarray]:
    """열화상 프레임을 순서대로 스트리밍 (uint16 raw, 전체를 메모리에 올리지 않음)"""
    header = header or read_att_header(path)
    n = att_frame_count(path, header)
    with open(path, "rb") as f:
        f.seek(ATT_HEADER_SIZE)
        for _ in range(n):
            raw = f.read(header.frame_bytes)
            if len(raw) != header.frame_bytes:
                break
            yield np.frombuffer(raw, dtype="<u2").reshape(header.height, header.width).copy()


def read_atg(path: Path) -> List[AtgRecord]:
    """프레임별 타임스탬프/GPS 레코드 전체를 반환. 파일이 잘렸으면 온전한 레코드까지만 반환"""
    size = Path(path).stat().st_size
    n = size // ATG_RECORD_SIZE
    records: List[AtgRecord] = []
    with open(path, "rb") as f:
        for _ in range(n):
            rec = f.read(ATG_RECORD_SIZE)
            if len(rec) != ATG_RECORD_SIZE:
                break
            ts = rec[0:19].decode("ascii", errors="replace")
            tag = struct.unpack("<i", rec[19:23])[0]
            lat, lon = struct.unpack("<dd", rec[23:39])
            records.append(AtgRecord(timestamp=ts, tag=tag, lat=lat, lon=lon))
    return records
