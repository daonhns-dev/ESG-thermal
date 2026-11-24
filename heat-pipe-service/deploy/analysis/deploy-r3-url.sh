#!/usr/bin/env bash
# Apply only in the agreed maintenance window. Does NOT restart Docker/host.
set -euo pipefail
umask 077
cd "$HOME/heat-pipe-service"
old='sha256:bc8e9b3395a9e79100c3d3a3422d776eb67848a5bced06bdd4d12156b98c43dc'
new='sha256:5e007f46e1856316d7c8c45cc715693955b43505cb3dd34c41d93decb08c7948'
target='/data/data/analysis'
mountpoint -q /data
test "$(docker image inspect heatpipe-service:r3-20261119-build2 --format '{{.Id}}')" = "$new"
test "$(docker image inspect heatpipe-service:pre-storage-20261119 --format '{{.Id}}')" = "$old"
test "$(docker image inspect heatpipe-service:local --format '{{.Id}}')" = "$old"
for service in api worker; do
    c="heatpipe-service-$service-1"
    test "$(docker inspect "$c" --format '{{.Image}}')" = "$old"
    test "$(docker inspect "$c" --format '{{.State.Running}}')" = true
    test "$(docker inspect "$c" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}')" = "$target"
done
docker compose config --format json | python3 -c 'import json,sys; c=json.load(sys.stdin); assert set(c["services"])=={"api","worker"}; [(lambda s: (s["image"]=="heatpipe-service:local" or sys.exit("STOP: unexpected image"), not s.get("environment",{}).get("HEATPIPE_SHARED_INPUT_ROOT") or sys.exit("STOP: path mode enabled"), len([m for m in s.get("volumes",[]) if m["target"]=="/data" and m["type"]=="bind" and m["source"]=="/data/data/analysis"])==1 or sys.exit("STOP: unexpected data mount")))(c["services"][n]) for n in ("api","worker")]; print("PASS: URL-only effective configuration")'
idle() {
    docker exec "$1" python -c 'import sqlite3,sys; d=sqlite3.connect("file:/data/jobs.sqlite3?mode=ro",uri=True); rows=d.execute("select job_id,status from jobs where status not in (?,?,?) or next_attempt_at is not null",("SUCCEEDED","FAILED","CANCELLED")).fetchall(); print("ACTIVE_JOBS=",rows); sys.exit(3 if rows else 0)'
}
idle heatpipe-service-api-1
sudo -v
state=$(mktemp -d /data/data/backups/r3-deploy-20261123-XXXXXX)
echo "DEPLOY_STATE=$state"
cp -p .env "$state/env.before"
for file in compose.yaml compose.portal.yaml compose.external-data.yaml; do cp -p "$file" "$state/"; done
docker compose config --format json > "$state/effective-config.before.json"
docker ps --format '{{.Names}} {{.Status}}' > "$state/containers.before.txt"
printf '%s\n' "$old" > "$state/image.before.txt"
docker tag "$old" "heatpipe-service:pre-r3-$(basename "$state")"
phase=preflight
api_stopped=0
worker_stopped=0
finish() {
    status=$?
    trap - EXIT
    if test "$status" != 0; then
        if test "$phase" = transition || test "$phase" = live; then
            echo "STOP: image transition attempted; inspect state before rollback: $state" >&2
        else
            if test "$api_stopped" = 1; then docker start heatpipe-service-api-1 >/dev/null || true; fi
            if test "$worker_stopped" = 1; then docker start heatpipe-service-worker-1 >/dev/null || true; fi
            echo 'Old containers restored if stopped; no image replacement attempted.' >&2
        fi
    fi
    exit "$status"
}
trap finish EXIT
api_stopped=1
docker stop --timeout 30 heatpipe-service-api-1
idle heatpipe-service-worker-1
worker_stopped=1
docker stop --timeout 30 heatpipe-service-worker-1
phase=stopped
docker run --rm --network none --user 10001:10001 --mount "type=bind,src=$target,dst=/data" "$old" \
    python -c 'import sqlite3,json; d=sqlite3.connect("file:/data/jobs.sqlite3?mode=rw",uri=True); assert d.execute("pragma wal_checkpoint(TRUNCATE)").fetchone()[0]==0; assert d.execute("pragma integrity_check").fetchall()==[("ok",)]; row=d.execute("select job_id from jobs where status=\"SUCCEEDED\" and result_key is not null order by id desc limit 1").fetchone(); print(json.dumps({"baseline":d.execute("select count(*),max(id),max(job_id) from jobs").fetchone(),"existing_result":row[0] if row else None})); d.close()' > "$state/db.before.json"
sudo tar --numeric-owner --acls --xattrs -C "$target" -cpf - . | gzip -3 > "$state/analysis-data.tar.gz"
gzip -t "$state/analysis-data.tar.gz"
(cd "$state" && sha256sum analysis-data.tar.gz db.before.json image.before.txt > SHA256SUMS)
phase=transition
docker tag "$new" heatpipe-service:local
docker compose up -d --no-deps --no-build --pull never --force-recreate api worker
phase=live
for service in api worker; do
    c="heatpipe-service-$service-1"
    test "$(docker inspect "$c" --format '{{.Image}}')" = "$new"
    test "$(docker inspect "$c" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}')" = "$target"
done
docker exec -i heatpipe-service-api-1 python - <<'PY' > "$state/service.after.json"
import hashlib,json,os,sqlite3,time,urllib.request
def get(path):
    req=urllib.request.Request('http://127.0.0.1:8000'+path,headers={'Authorization':'Bearer '+os.environ['HEATPIPE_API_TOKEN']})
    with urllib.request.urlopen(req,timeout=5) as r: return r.read()
for _ in range(80):
    try:
        health=json.loads(get('/health'))
        if health['status']=='ok' and health['worker_running']: break
    except OSError: pass
    time.sleep(0.5)
else: raise RuntimeError('API/worker readiness failed')
with sqlite3.connect('file:/data/jobs.sqlite3?mode=ro',uri=True) as db:
    assert db.execute('pragma integrity_check').fetchall()==[('ok',)]
    baseline=db.execute('select count(*),max(id),max(job_id) from jobs').fetchone()
    row=db.execute("select job_id from jobs where status='SUCCEEDED' and result_key is not null order by id desc limit 1").fetchone()
report={'health':health,'baseline':baseline,'path_mode':'disabled'}
assert not os.environ.get('HEATPIPE_SHARED_INPUT_ROOT')
if row:
    result=json.loads(get('/api/v1/analysis-jobs/'+row[0]+'/results'))
    for c in result['candidates']:
        image=c['result_image']; png=get(image['path'])
        assert len(png)==image['size_bytes'] and hashlib.sha256(png).hexdigest()==image['sha256']
    report.update(existing_result=row[0],pngs_verified=len(result['candidates']))
print(json.dumps(report,sort_keys=True))
PY
python3 - "$state" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); before=json.loads((p/'db.before.json').read_text()); after=json.loads((p/'service.after.json').read_text())
assert after['baseline'][0]>=before['baseline'][0]
assert (after['baseline'][1] or 0)>=(before['baseline'][1] or 0)
print('DB_BEFORE=',before)
print('SERVICE_AFTER=',after)
PY
echo "R3_URL_DEPLOY_SUCCESS; DEPLOY_STATE=$state"
echo 'Docker and host restart NOT performed. Coordinate the next step with VTI.'
