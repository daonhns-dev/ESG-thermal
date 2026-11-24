#!/usr/bin/env bash
# Check the real old image against a COPY of r3-created isolated data.
set -euo pipefail
umask 077
image='heatpipe-service:pre-storage-20261119'
expected='sha256:bc8e9b3395a9e79100c3d3a3422d776eb67848a5bced06bdd4d12156b98c43dc'
source='/data/data/backups/r3-image-validation-yguRmU/data'
test "$(docker image inspect "$image" --format '{{.Id}}')" = "$expected"
mountpoint -q /data
sudo -v
sudo test -f "$source/jobs.sqlite3"
check_dir=$(mktemp -d /data/data/backups/r3-rollback-validation-XXXXXX)
echo "ROLLBACK_CHECK_DIR=$check_dir"
mkdir "$check_dir/data"
sudo rsync -aHAX --numeric-ids "$source/" "$check_dir/data/"
container_id=''
cleanup() {
    status=$?
    trap - EXIT
    if test -n "$container_id"; then
        docker logs "$container_id" > "$check_dir/api.log" 2>&1 || true
        docker rm -f "$container_id" >/dev/null || status=1
    fi
    echo "ROLLBACK_CHECK_EXIT=$status; production untouched; copied test data retained"
    exit "$status"
}
trap cleanup EXIT
container_id=$(docker create --network none --memory 512m --cpus 1 --user 10001:10001 \
    --mount "type=bind,src=$check_dir/data,dst=/data" \
    -e HEATPIPE_ENV=production -e HEATPIPE_API_TOKEN=isolated-rollback-only \
    -e HEATPIPE_ALLOWED_INTERNAL_HTTP_HOSTS=127.0.0.1,127.0.0.1:18090 "$image")
docker start "$container_id" >/dev/null
docker exec -i "$container_id" python - <<'PY' | tee "$check_dir/rollback-result.json"
import hashlib, json, sqlite3, time, urllib.request
import heatpipe_service.api.models as models
assert 'path' not in models.FileReference.model_fields, 'Expected old model'
base = 'http://127.0.0.1:8000'
def call(path, body=None, key=None):
    headers = {'Authorization': 'Bearer isolated-rollback-only'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if key:
        headers['Idempotency-Key'] = key
    request = urllib.request.Request(base + path, headers=headers,
        data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read()
for attempt in range(80):
    try:
        assert json.loads(call('/health')[1])['status'] == 'ok'
        break
    except OSError:
        time.sleep(0.25)
else:
    raise RuntimeError('Old image API did not start')
def db_record():
    with sqlite3.connect('file:/data/jobs.sqlite3?mode=ro', uri=True) as db:
        assert db.execute('pragma integrity_check').fetchall() == [('ok',)]
        return db.execute('select count(*),max(id),max(job_id) from jobs').fetchone()
before = db_record()
job_id = 'JOB-20261119-000028'
with sqlite3.connect('file:/data/jobs.sqlite3?mode=ro', uri=True) as db:
    row = db.execute('select request_json,idempotency_key,status from jobs where job_id=?', (job_id,)).fetchone()
assert row and row[2] == 'SUCCEEDED', 'Expected completed r3 synthetic job'
body, key = json.loads(row[0]), row[1]
assert body['monitoring_data_id'].startswith('R3CHECK-') and key.startswith('r3-isolated-')
assert all('path' not in body[kind] for kind in ('att', 'atg'))
models.CreateJobRequest.model_validate(body)
state = json.loads(call(f'/api/v1/analysis-jobs/{job_id}')[1])
assert state['status'] == 'SUCCEEDED' and state['progress_percent'] == 100
result = json.loads(call(f'/api/v1/analysis-jobs/{job_id}/results')[1])
assert result['candidates']
for candidate in result['candidates']:
    image = candidate['result_image']
    png = call(image['path'])[1]
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    assert len(png) == image['size_bytes']
    assert hashlib.sha256(png).hexdigest() == image['sha256']
status, raw = call('/api/v1/analysis-jobs', body, key)
assert status == 202 and json.loads(raw)['job_id'] == job_id
assert db_record() == before
print(json.dumps({'old_image_model': 'verified', 'r3_created_job': job_id,
    'status': 'SUCCEEDED', 'pngs_verified': len(result['candidates']),
    'idempotency_replay': 'same job', 'job_count': before[0],
    'max_job_id': before[2], 'database_records': 'preserved'}, sort_keys=True))
PY
echo 'REAL_OLD_IMAGE_ROLLBACK_PASS: r3 URL record readable and replayable'
