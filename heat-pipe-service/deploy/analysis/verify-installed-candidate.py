"""Verify installed package bytes, not just COPY input or a single feature."""
import hashlib
import json
from pathlib import Path
import sys
import sysconfig

import heatpipe_service

package = Path(heatpipe_service.__file__).resolve().parent
site = Path(sysconfig.get_path('purelib')).resolve()
if not package.is_relative_to(site):
    raise SystemExit(f'STOP: package is not installed under site-packages: {package}')
manifest = json.loads(Path(sys.argv[1]).read_text())
prefix = 'src/heatpipe_service/'
expected = {name.removeprefix(prefix): digest for name, digest in manifest['sha256'].items()
            if name.startswith(prefix)}
actual = {p.relative_to(package).as_posix() for p in package.rglob('*')
          if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
if actual != set(expected):
    raise SystemExit(f'STOP: installed file set differs; missing={sorted(set(expected)-actual)}, extra={sorted(actual-set(expected))}')
for name, digest in expected.items():
    if hashlib.sha256((package / name).read_bytes()).hexdigest() != digest:
        raise SystemExit(f'STOP: installed package SHA-256 mismatch: {name}')
print(f'PASS: all {len(expected)} installed package files match candidate SHA-256')
