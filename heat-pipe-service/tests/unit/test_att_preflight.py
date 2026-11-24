from pathlib import Path
import runpy
import struct

import pytest

inspect_att = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/check_att_input.py'))['inspect_att']


@pytest.mark.parametrize('content, reason', [
    (b'123', 'ATT_HEADER_TOO_SHORT'),
    (struct.pack('<4H', 1, 0, 0, 2), 'ATT_INVALID_DIMENSIONS'),
    (struct.pack('<4H', 1, 0, 2, 2), 'ATT_NO_COMPLETE_FRAMES'),
    (struct.pack('<4H', 1, 0, 2, 2) + bytes(11), 'ATT_FRAME_ALIGNMENT_ERROR'),
    (struct.pack('<4H', 1, 0, 2, 2) + bytes(16), 'FRAME_STRUCTURE_OK'),
])
def test_att_preflight_rejects_incomplete_input_without_modification(tmp_path, content, reason):
    source = tmp_path / 'input.att'
    source.write_bytes(content)
    report = inspect_att(source)
    assert report['reason'] == reason
    assert report['status'] == ('PASS' if reason == 'FRAME_STRUCTURE_OK' else 'INVALID')
    assert source.read_bytes() == content
    if reason == 'ATT_FRAME_ALIGNMENT_ERROR':
        assert report['complete_frames'] == 1
        assert report['remaining_bytes'] == 3


def test_att_preflight_reports_missing_source(tmp_path):
    report = inspect_att(tmp_path / 'missing.att')
    assert report['reason'] == 'ATT_READ_ERROR'
    assert report['error_type'] == 'FileNotFoundError'
