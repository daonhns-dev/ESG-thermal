"""Inside a network-none candidate container, using only a restored data copy."""
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.parse import quote
import urllib.request
from uuid import uuid4

BASE = 'http://127.0.0.1:8000'
HEADERS = {'Authorization': 'Bearer isolated-r3-check-only'}


def request(path, body=None, key=None):
    headers = dict(HEADERS)
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if key:
        headers['Idempotency-Key'] = key
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, response.read()


def result_check(job_id):
    _, raw = request(f'/api/v1/analysis-jobs/{job_id}/results')
    result = json.loads(raw)
    for candidate in result['candidates']:
        image = candidate['result_image']
        _, png = request(image['path'])
        assert png.startswith(b'\x89PNG\r\n\x1a\n')
        assert len(png) == image['size_bytes']
        assert hashlib.sha256(png).hexdigest() == image['sha256']
    return len(result['candidates'])


def main():
    for _ in range(80):
        try:
            assert json.loads(request('/health')[1])['status'] == 'ok'
            break
        except OSError:
            time.sleep(0.25)
    else:
        raise RuntimeError('Isolated API startup failed')
    with sqlite3.connect('file:/data/jobs.sqlite3?mode=ro', uri=True) as db:
        assert db.execute('pragma integrity_check').fetchall() == [('ok',)]
        active = db.execute("select job_id,status from jobs where status not in ('SUCCEEDED','FAILED','CANCELLED')").fetchall()
        assert not active, active
        old = db.execute("select job_id from jobs where status='SUCCEEDED' and result_key is not null order by id desc limit 1").fetchone()
        original_count = db.execute('select count(*) from jobs').fetchone()[0]
    assert old, 'No completed backup job available'
    old_pngs = result_check(old[0])

    body = json.loads(Path('/validation/path-request.json').read_text())
    body['monitoring_data_id'] = 'R3CHECK-' + uuid4().hex
    # Path mode must remain disabled in this URL-only deployment rehearsal.
    try:
        request('/api/v1/analysis-jobs', body)
    except HTTPError as error:
        assert error.code == 503
        assert json.loads(error.read())['error']['code'] == 'SHARED_INPUT_NOT_CONFIGURED'
    else:
        raise AssertionError('Path request unexpectedly admitted')
    originals = {}
    for kind in ('att', 'atg'):
        relative = body[kind].pop('path')
        file = Path('/validation/samples') / relative
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        assert digest == body[kind]['sha256']
        assert file.stat().st_size == body[kind]['size_bytes']
        originals[file] = digest
        body[kind]['url'] = 'http://127.0.0.1:18090/' + quote(relative)

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 18090), partial(QuietHandler, directory='/validation/samples'))
    Thread(target=server.serve_forever, daemon=True).start()
    worker = None
    try:
        with Path('/data/r3-validation-worker.log').open('wb') as log:
            worker = subprocess.Popen(['heatpipe-worker', '--poll-seconds', '0.2'], stdout=log, stderr=subprocess.STDOUT)
            key = 'r3-isolated-' + uuid4().hex
            status, raw = request('/api/v1/analysis-jobs', body, key)
            assert status == 202, (status, raw)
            job_id = json.loads(raw)['job_id']
            for _ in range(240):
                assert worker.poll() is None, 'Isolated worker exited'
                state = json.loads(request(f'/api/v1/analysis-jobs/{job_id}')[1])
                if state['status'] in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
                    break
                time.sleep(0.25)
            assert state['status'] == 'SUCCEEDED', state
            assert state['progress_percent'] == 100, state
            pngs = result_check(job_id)
            assert pngs > 0
            assert json.loads(request('/api/v1/analysis-jobs', body, key)[1])['job_id'] == job_id
            with sqlite3.connect('file:/data/jobs.sqlite3?mode=ro', uri=True) as db:
                saved = json.loads(db.execute('select request_json from jobs where job_id=?', (job_id,)).fetchone()[0])
                assert all('path' not in saved[kind] for kind in ('att', 'atg'))
                assert db.execute('select count(*) from jobs').fetchone()[0] == original_count + 1
            assert json.loads(request('/health')[1])['worker_running'] is True
            assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in originals.items())
            print(json.dumps({'existing_job': old[0], 'existing_pngs_verified': old_pngs,
                              'synthetic_url_job': job_id, 'status': 'SUCCEEDED', 'pngs_verified': pngs,
                              'idempotency_replay': 'ok', 'null_path_omitted': True,
                              'path_mode': 'disabled', 'originals': 'unchanged'}, sort_keys=True))
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=10)
        server.shutdown()


if __name__ == '__main__':
    main()
