"""Bundle isolated image checks and already issued synthetic samples."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def main():
    files = {name: (ROOT / 'deploy/analysis' / name).read_bytes().replace(b'\r\n', b'\n')
             for name in ('validate-r3-isolated.py', 'validate-r3-isolated.sh')}
    with ZipFile(ROOT / 'output/VTI_API_v1.1_방안A_20261119_송부용.zip') as archive:
        body = archive.read('examples/int-003-path.request.json')
        files['path-request.json'] = body
        for kind in ('att', 'atg'):
            reference = json.loads(body)[kind]
            name = 'samples/' + reference['path']
            data = archive.read(name)
            assert hashlib.sha256(data).hexdigest() == reference['sha256']
            assert len(data) == reference['size_bytes']
            files[name] = data
    compile(files['validate-r3-isolated.py'], 'validate-r3-isolated.py', 'exec')
    files['VALIDATION_SHA256SUMS'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                                         for name, data in sorted(files.items())).encode()
    output = ROOT / 'output/analysis-r3-isolated-validation-20261119.tar.gz'
    prefix = 'analysis-r3-isolated-validation'
    folders = {prefix}
    for name in files:
        folders.update(str(p).replace('\\', '/') for p in (Path(prefix) / name).parents if str(p) != '.')
    with tarfile.open(output, 'w:gz') as archive:
        for name in sorted(folders, key=lambda n: (n.count('/'), n)):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(prefix + '/' + name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    with tarfile.open(output, 'r:gz') as archive:
        for member in archive:
            if member.isfile():
                assert archive.extractfile(member).read() == files[member.name.removeprefix(prefix + '/')]
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(digest + '  ' + output.name + '\n')
    print(json.dumps({'file': str(output), 'sha256': digest, 'files': len(files)}, indent=2))


if __name__ == '__main__':
    main()
