"""Package verified candidate sources without any live Compose settings."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    candidate = ROOT / 'output/analysis-api-worker-r3-candidate-20261119.tar.gz'
    files = {}
    with tarfile.open(candidate, 'r:gz') as archive:
        for member in archive:
            if not member.isfile():
                continue
            name = member.name.removeprefix('heat-pipe-service/')
            if name.startswith('src/') or name in {
                'pyproject.toml', 'README.md', 'CANDIDATE_MANIFEST.json', 'VALIDATION.json'
            }:
                files[name] = archive.extractfile(member).read()
    manifest = json.loads(files['CANDIDATE_MANIFEST.json'])
    for name, data in files.items():
        if name not in {'CANDIDATE_MANIFEST.json', 'VALIDATION.json'}:
            assert hashlib.sha256(data).hexdigest() == manifest['sha256'][name], name
    assert json.loads(files['VALIDATION.json'])['unit_tests'] == 87
    for name in ['Dockerfile.r3', 'build-r3-image.sh', 'verify-installed-candidate.py']:
        files[name] = (ROOT / 'deploy/analysis' / name).read_bytes().replace(b'\r\n', b'\n')
    files['BUILD_SHA256SUMS'] = ''.join(
        hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
        for name, data in sorted(files.items())
    ).encode()
    output = ROOT / 'output/analysis-r3-build-kit-20261119-build2.tar.gz'
    with tarfile.open(output, 'w:gz') as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo('analysis-r3-build-kit/' + name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith('.sh') else 0o644
            archive.addfile(info, io.BytesIO(data))
    with tarfile.open(output, 'r:gz') as archive:
        for member in archive:
            name = member.name.removeprefix('analysis-r3-build-kit/')
            assert archive.extractfile(member).read() == files[name]
            assert not name.startswith('compose.') and Path(name).name != '.env'
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(digest + '  ' + output.name + '\n')
    print(json.dumps({'file': str(output), 'sha256': digest, 'files': len(files),
                      'status': 'prepared only; not built or deployed'}, indent=2))


if __name__ == '__main__':
    main()
