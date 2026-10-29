from __future__ import annotations

import struct

import numpy as np
import pytest

from heatpipe_service.analysis.io import (
    ATG_RECORD_SIZE,
    att_frame_count,
    iter_att_frames_raw,
    read_atg,
    read_att_frame_celsius,
    read_att_frame_raw,
    read_att_header,
)


def test_att_and_atg_readers(tmp_path) -> None:
    att_path = tmp_path / "sample.att"
    frames = [
        np.array([[3000, 3100], [3200, 3300]], dtype="<u2"),
        np.array([[3400, 3500], [3600, 3700]], dtype="<u2"),
    ]
    att_path.write_bytes(struct.pack("<4H", 1, 0, 2, 2) + b"".join(frame.tobytes() for frame in frames))

    header = read_att_header(att_path)
    assert (header.width, header.height) == (2, 2)
    assert att_frame_count(att_path, header) == 2
    np.testing.assert_array_equal(read_att_frame_raw(att_path, 1, header), frames[1])
    np.testing.assert_allclose(read_att_frame_celsius(att_path, 0, header), frames[0] / 100)
    assert len(list(iter_att_frames_raw(att_path, header))) == 2
    with pytest.raises(IndexError):
        read_att_frame_raw(att_path, -1, header)
    with pytest.raises(IndexError):
        read_att_frame_raw(att_path, 2, header)

    atg_path = tmp_path / "sample.atg"
    records = []
    for timestamp, tag, lat, lon in (
        ("2026-08-13 14:02:09", 7, 37.5, 126.9),
        ("2026-08-13 14:02:10", 8, 37.6, 127.0),
    ):
        records.append(timestamp.encode("ascii") + struct.pack("<i", tag) + struct.pack("<dd", lat, lon))
    atg_path.write_bytes(b"".join(records) + b"partial")

    parsed = read_atg(atg_path)
    assert atg_path.stat().st_size == ATG_RECORD_SIZE * 2 + 7
    assert [(row.timestamp, row.tag) for row in parsed] == [
        ("2026-08-13 14:02:09", 7),
        ("2026-08-13 14:02:10", 8),
    ]
    assert parsed[0].lat == pytest.approx(37.5)
    assert parsed[1].lon == pytest.approx(127.0)

