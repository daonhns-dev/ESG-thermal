#!/usr/bin/env bash
# Apply only with VTI holding new requests. No portal or Docker restart.
set -euo pipefail
umask 077
cd "$HOME/heat-pipe-service"
image='sha256:5e007f46e1856316d7c8c45cc715693955b43505cb3dd34c41d93decb08c7948'
root='/data/data/portal/raw'
data='/data/data/analysis'
mountpoint -q /data
test ! -e compose.shared-input.yaml
for c in heatpipe-service-api-1 heatpipe-service-worker-1; do
    test "$(docker inspect "$c" --format '{{.Image}}')" = "$image"
    test "$(docker inspect "$c" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}')" = "$data"
done
docker run --rm --network none --user 10001:10001 --mount "type=bind,src=$root,dst=/shared-input,readonly" \
    --entrypoint python "$image" -c 'from pathlib import Path; from itertools import islice; from heatpipe_service.storage.shared_inputs import validate_shared_root; root=validate_shared_root(Path("/shared-input")); print("SHARED_ROOT_OK"); samples=[p for ext in ("*.att","*.atg") for p in islice(root.rglob(ext),3)]; [(p.is_file() and p.resolve().is_relative_to(root) or (_ for _ in ()).throw(RuntimeError("Not a regular input beneath shared root"))) for p in samples]; [(lambda f: (f.read(1),f.close()))(p.open("rb")) for p in samples]; print("READABLE_SAMPLE_FILES=",len(samples))'
idle() {
    docker exec "$1" python -c 'import sqlite3,sys; d=sqlite3.connect("file:/data/jobs.sqlite3?mode=ro",uri=True); rows=d.execute("select job_id,status from jobs where status not in (?,?,?) or next_attempt_at is not null",("SUCCEEDED","FAILED","CANCELLED")).fetchall(); print("ACTIVE_JOBS=",rows); sys.exit(3 if rows else 0)'
}
idle heatpipe-service-api-1
sudo -v
state=$(mktemp -d /data/data/backups/shared-input-enable-20261123-XXXXXX)
echo "SHARED_STATE=$state"
cp -p .env "$state/env.before"
docker compose config --format json > "$state/config.before.json"
phase=prepared
api_stopped=0
worker_stopped=0
env_changed=0
overlay_created=0
finish() {
    status=$?
    trap - EXIT
    if test "$status" != 0; then
        if test "$phase" = transition || test "$phase" = live; then
            echo "STOP: container transition attempted; inspect before rollback: $state" >&2
        else
            if test "$env_changed" = 1; then cp -p "$state/env.before" .env; fi
            if test "$overlay_created" = 1; then rm -- compose.shared-input.yaml; fi
            if test "$api_stopped" = 1; then docker start heatpipe-service-api-1 >/dev/null || true; fi
            if test "$worker_stopped" = 1; then docker start heatpipe-service-worker-1 >/dev/null || true; fi
            echo 'Previous configuration/containers restored if changed.' >&2
        fi
    fi
    exit "$status"
}
trap finish EXIT
overlay_created=1
cat > compose.shared-input.yaml <<'YAML'
services:
  api:
    environment:
      HEATPIPE_SHARED_INPUT_ROOT: /shared-input
    volumes:
      - type: bind
        source: ${HEATPIPE_SHARED_INPUT_HOST_ROOT:?Set the published plain-file root}
        target: /shared-input
        read_only: true
        bind:
          create_host_path: false
  worker:
    environment:
      HEATPIPE_SHARED_INPUT_ROOT: /shared-input
    volumes:
      - type: bind
        source: ${HEATPIPE_SHARED_INPUT_HOST_ROOT:?Set the published plain-file root}
        target: /shared-input
        read_only: true
        bind:
          create_host_path: false
YAML
env_changed=1
python3 - <<'PY'
import os,re,tempfile
from pathlib import Path
p=Path('.env')
updates={'COMPOSE_FILE':'compose.yaml:compose.portal.yaml:compose.external-data.yaml:compose.shared-input.yaml',
         'HEATPIPE_SHARED_INPUT_HOST_ROOT':'/data/data/portal/raw'}
pattern=re.compile(r'^\s*(?:export\s+)?('+'|'.join(updates)+r')\s*=')
lines=[s for s in p.read_text().splitlines() if not pattern.match(s)]
lines.extend(k+'='+v for k,v in updates.items())
with tempfile.NamedTemporaryFile('w',dir=p.parent,prefix='.env.shared-',delete=False) as f:
    f.write('\n'.join(lines)+'\n'); f.flush(); os.fsync(f.fileno()); temporary=f.name
os.chmod(temporary,0o600); os.replace(temporary,p)
PY
docker compose config --format json > "$state/config.after.json"
python3 - "$state" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); before=json.loads((p/'config.before.json').read_text()); after=json.loads((p/'config.after.json').read_text())
assert set(before['services'])==set(after['services'])=={'api','worker'}
for name in ('api','worker'):
    old=before['services'][name]; new=after['services'][name]
    expected_env=dict(old.get('environment',{})); expected_env['HEATPIPE_SHARED_INPUT_ROOT']='/shared-input'
    assert new.get('environment')==expected_env, (name,'unexpected environment change')
    added=[v for v in new.get('volumes',[]) if v['target']=='/shared-input']
    assert len(added)==1
    v=added[0]
    assert v['type']=='bind' and v['source']=='/data/data/portal/raw' and v.get('read_only') is True
    assert v.get('bind',{}).get('create_host_path') is False
    assert [v for v in new.get('volumes',[]) if v['target']!='/shared-input']==old.get('volumes',[])
    assert {k:v for k,v in new.items() if k not in ('environment','volumes')}=={k:v for k,v in old.items() if k not in ('environment','volumes')}
print('PASS: only shared root environment and read-only input mount added')
PY
api_stopped=1
docker stop --timeout 30 heatpipe-service-api-1
idle heatpipe-service-worker-1
worker_stopped=1
docker stop --timeout 30 heatpipe-service-worker-1
docker run --rm --network none --user 10001:10001 --mount "type=bind,src=$data,dst=/data" "$image" \
    python -c 'import sqlite3,json; d=sqlite3.connect("file:/data/jobs.sqlite3?mode=rw",uri=True); assert d.execute("pragma wal_checkpoint(TRUNCATE)").fetchone()[0]==0; assert d.execute("pragma integrity_check").fetchall()==[("ok",)]; print(json.dumps(d.execute("select count(*),max(id),max(job_id) from jobs").fetchone())); d.close()' > "$state/db.before.json"
sudo tar --numeric-owner --acls --xattrs -C "$data" -cpf - . | gzip -3 > "$state/analysis-data.tar.gz"
gzip -t "$state/analysis-data.tar.gz"
(cd "$state" && sha256sum analysis-data.tar.gz db.before.json > SHA256SUMS)
phase=transition
docker compose up -d --no-deps --no-build --pull never --force-recreate api worker
phase=live
for c in heatpipe-service-api-1 heatpipe-service-worker-1; do
    test "$(docker inspect "$c" --format '{{.Image}}')" = "$image"
    test "$(docker inspect "$c" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}')" = "$data"
    test "$(docker inspect "$c" --format '{{range .Mounts}}{{if eq .Destination "/shared-input"}}{{.Source}}:{{.RW}}{{end}}{{end}}')" = "$root:false"
    docker exec "$c" python -c 'import os; from pathlib import Path; from heatpipe_service.storage.shared_inputs import validate_shared_root; assert os.environ["HEATPIPE_SHARED_INPUT_ROOT"]=="/shared-input"; validate_shared_root(Path("/shared-input")); print("PASS: shared root configured and readable")'
done
docker exec -i heatpipe-service-api-1 python - <<'PY' > "$state/service.after.json"
import json,sqlite3,time,urllib.request
for _ in range(80):
    try:
        health=json.loads(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).read())
        if health['status']=='ok' and health['worker_running']: break
    except OSError: pass
    time.sleep(0.5)
else: raise RuntimeError('API/worker readiness failed')
with sqlite3.connect('file:/data/jobs.sqlite3?mode=ro',uri=True) as db:
    baseline=db.execute('select count(*),max(id),max(job_id) from jobs').fetchone()
print(json.dumps({'health':health,'baseline':baseline}))
PY
python3 - "$state" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); before=json.loads((p/'db.before.json').read_text()); after=json.loads((p/'service.after.json').read_text())
assert after['baseline'][0]>=before[0]
assert (after['baseline'][1] or 0)>=(before[1] or 0)
print('DB_BEFORE=',before); print('SERVICE_AFTER=',after)
PY
echo "SHARED_INPUT_ENABLE_SUCCESS; SHARED_STATE=$state"
echo 'Portal and Docker engine were not restarted. VTI path retry is the next check.'
