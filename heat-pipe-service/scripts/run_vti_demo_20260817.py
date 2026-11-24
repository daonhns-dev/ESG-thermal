"""Offline full-frame Demo analysis using a verified, mounted release source."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform
import struct
import time

import cv2
import numpy
import pydantic
import scipy
import heatpipe_service
from heatpipe_service.api.models import AnalysisResultResponse
from heatpipe_service.jobs.batch import BatchConfig, BatchRequest, run_batch_safely


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--runtime-image', required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise SystemExit('STOP: output directory must be empty')
    manifest = json.loads(args.manifest.read_text())
    package = Path(heatpipe_service.__file__).resolve().parent
    verified = 0
    for name, expected in manifest['sha256'].items():
        if name.startswith('src/heatpipe_service/'):
            assert digest(package / name.removeprefix('src/heatpipe_service/')) == expected, name
            verified += 1
    print(f'RELEASE_SOURCE_VERIFIED={verified}', flush=True)
    stem = '20260817_002325'
    files = {ext: args.input / (stem + '.' + ext) for ext in ('att', 'atg', 'avi')}
    inventory = {ext: {'name': p.name, 'size_bytes': p.stat().st_size, 'sha256': digest(p)}
                 for ext, p in files.items()}
    with files['att'].open('rb') as stream:
        header = struct.unpack('<4H', stream.read(8))
    frames, remaining = divmod(inventory['att']['size_bytes'] - 8, header[2] * header[3] * 2)
    assert remaining == 0 and frames == 3481
    assert inventory['atg']['size_bytes'] == frames * 39
    cap = cv2.VideoCapture(str(files['avi']))
    assert cap.isOpened()
    avi_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    avi_fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    assert avi_frames == frames
    print(f'INPUTS_VERIFIED: ATT={frames} ATG={frames} AVI_header={avi_frames}', flush=True)
    started = time.monotonic()
    previous = None
    def progress(p):
        nonlocal previous
        key = (p.status, p.progress_percent // 5)
        if key != previous:
            print(f'{p.status} {p.progress_percent}% frames={p.processed_frame_count}/{p.total_frame_count}', flush=True)
            previous = key
    artifacts = run_batch_safely(
        BatchRequest(job_id='JOB-20261123-990001', monitoring_data_id=stem,
                     att_path=files['att'], atg_path=files['atg'], output_dir=output),
        BatchConfig(analysis_device='cpu', frame_stride=1, max_results=50), on_progress=progress,
    )
    if not artifacts.success:
        raise RuntimeError('Demo analysis failed; inspect offline output')
    payload = json.loads(artifacts.result_json.read_text())
    AnalysisResultResponse.model_validate(payload)
    assert payload['processing']['frame_range']['total_att_frames'] == frames
    assert payload['processing']['frame_range']['processed_frame_count'] == frames
    for candidate in payload['candidates']:
        assert 'att_offset_ms' not in candidate
        frame = candidate['representative_frame']
        assert 0 <= frame < frames
        metadata = candidate['result_image']
        png = (output / metadata['path']).resolve()
        assert png.is_relative_to(output)
        assert png.stat().st_size == metadata['size_bytes']
        assert digest(png) == metadata['sha256']
        assert png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    with (output / 'frame_matching.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        fields = ('candidate_id', 'rank', 'representative_frame', 'captured_at', 'thermal_image_path')
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for c in payload['candidates']:
            writer.writerow({**{k: c[k] for k in fields if k != 'thermal_image_path'},
                             'thermal_image_path': c['result_image']['path']})
    for ext in ('att', 'atg'):
        assert digest(files[ext]) == inventory[ext]['sha256'], 'Original changed during analysis'
    report = {'kind': 'offline Demo analysis; not an operational API job',
              'job_id': payload['job_id'], 'inputs': inventory,
              'source_manifest_sha256': digest(args.manifest),
              'verified_source_files': verified, 'runtime_image': args.runtime_image,
              'runtime_matches_production_dependencies': 'not established',
              'environment': {'python': platform.python_version(), 'numpy': numpy.__version__,
                              'scipy': scipy.__version__, 'opencv': cv2.__version__, 'pydantic': pydantic.__version__},
              'avi_header': {'frames': avi_frames, 'fps': avi_fps, 'duration_seconds': avi_frames / avi_fps},
              'processed_frames': frames, 'candidates': len(payload['candidates']),
              'pngs_verified': len(payload['candidates']), 'elapsed_seconds': round(time.monotonic()-started, 3),
              'original_att_atg_sha256_after_analysis': 'unchanged',
              'att_offset_ms': 'not supplied; frame-index matching is the proposal',
              'synchronization': 'equal frame count only; exact frame synchronization not certified by this run'}
    (output / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print('DEMO_ANALYSIS_VERIFIED=' + json.dumps({'frames': frames, 'candidates': len(payload['candidates']),
                                               'pngs': len(payload['candidates']), 'elapsed_seconds': report['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    main()
