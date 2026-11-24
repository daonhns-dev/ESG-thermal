"""Read-only ATT preflight before uploading: python check_att_input.py FILE..."""
import argparse
import json
from pathlib import Path
import struct


def inspect_att(path: Path) -> dict:
    result = {'file': str(path), 'status': 'INVALID'}
    try:
        with path.open('rb') as stream:
            header = stream.read(8)
            size = path.stat().st_size
        result['size_bytes'] = size
        if len(header) != 8:
            return {**result, 'reason': 'ATT_HEADER_TOO_SHORT'}
        version, reserved, width, height = struct.unpack('<4H', header)
        result.update(version=version, reserved=reserved, width=width, height=height)
        if width == 0 or height == 0:
            return {**result, 'reason': 'ATT_INVALID_DIMENSIONS'}
        frame_bytes = width * height * 2
        frames, remaining = divmod(size - 8, frame_bytes)
        result.update(frame_bytes=frame_bytes, complete_frames=frames, remaining_bytes=remaining)
        if remaining:
            return {**result, 'reason': 'ATT_FRAME_ALIGNMENT_ERROR'}
        if frames == 0:
            return {**result, 'reason': 'ATT_NO_COMPLETE_FRAMES'}
        return {**result, 'status': 'PASS', 'reason': 'FRAME_STRUCTURE_OK'}
    except OSError as error:
        return {**result, 'reason': 'ATT_READ_ERROR', 'error_type': type(error).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', type=Path, nargs='+')
    args = parser.parse_args()
    results = [inspect_att(path) for path in args.files]
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if all(r['status'] == 'PASS' for r in results) else 2)


if __name__ == '__main__':
    main()
