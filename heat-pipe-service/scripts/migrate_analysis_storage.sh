#!/usr/bin/env bash
# Storage-only migration: keep the already running image and URL-mode contract.
# No portal operations, source rebuild, shared-input overlay or volume deletion.
set -euo pipefail
trap 'code=$?; printf "ERROR: migration stopped at line %s (exit %s)\n" "$LINENO" "$code" >&2' ERR
umask 077

backup=${1:?Usage: bash migrate-analysis-storage.sh VERIFIED_BACKUP_DIR [--preflight-only]}
backup=$(readlink -f -- "$backup")
case "$backup" in /data/data/backups/analysis-pre-move-20261119-*) ;; *) exit 1;; esac
mode=${2:---migrate}
case "$mode" in --migrate|--preflight-only) ;; *) exit 1;; esac
project=/home/energy/heat-pipe-service
target=/data/data/analysis
expected=sha256:bc8e9b3395a9e79100c3d3a3422d776eb67848a5bced06bdd4d12156b98c43dc
api=heatpipe-service-api-1
worker=heatpipe-service-worker-1
volume=heatpipe-service_heatpipe_data
cd "$project"
test -z "${COMPOSE_FILE:-}"
test -z "${COMPOSE_PATH_SEPARATOR:-}"
test -z "${HEATPIPE_EXTERNAL_ANALYSIS_ROOT:-}"
test -f .env && test ! -L .env && test -O .env
test -f compose.external-data.yaml
mountpoint -q /data
test "$(findmnt -n -o SOURCE --mountpoint /data)" = /dev/mapper/pve-vm--200002--disk--1
test "$(systemctl show docker.service -p RequiresMountsFor --value)" = /data
test "$(cat "$backup/image-id.txt")" = "$expected"
(cd "$backup" && sha256sum -c SHA256SUMS && sha256sum -c DATA_SHA256SUMS)
test "$(docker image inspect --format '{{.Id}}' heatpipe-service:local)" = "$expected"
test "$(docker image inspect --format '{{.Id}}' heatpipe-service:pre-storage-20261119)" = "$expected"
sudo -v
sudo python3 - "$target" <<'PY'
import os, sys
from pathlib import Path
p = Path(sys.argv[1])
assert not p.is_symlink() and p.is_dir() and not any(p.iterdir()), "Target must be an empty regular directory"
assert (p.stat().st_uid, p.stat().st_gid) == (10001, 10001), "Target owner mismatch"
PY
for container in "$api" "$worker"; do
    test "$(docker inspect --format '{{.Image}}' "$container")" = "$expected"
    test "$(docker inspect --format '{{.State.Running}}' "$container")" = true
    test "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$container")" = "$volume"
done
source=$(docker volume inspect --format '{{.Mountpoint}}' "$volume")
sudo test -d "$source"

validate_config() {
    python3 -c 'import json,sys; c=json.load(sys.stdin); assert set(c["services"])=={"api","worker"};
for name in ("api","worker"):
 s=c["services"][name]; m=[v for v in s["volumes"] if v["target"]=="/data"]; assert len(m)==1 and m[0]["type"]=="bind" and m[0]["source"]=="/data/data/analysis" and not m[0].get("read_only",False); assert s["image"]=="heatpipe-service:local"; assert not s.get("environment",{}).get("HEATPIPE_SHARED_INPUT_ROOT"); assert s["restart"]=="unless-stopped"
assert "daon-api" in c["services"]["api"]["networks"]["heatpipe-shared"]["aliases"]
print("PASS: storage-only effective config, existing image and URL mode")'
}
HEATPIPE_EXTERNAL_ANALYSIS_ROOT="$target" docker compose -f compose.yaml -f compose.portal.yaml -f compose.external-data.yaml config --format json | validate_config
check_idle() {
    docker exec "$1" python -c 'import sqlite3,sys; d=sqlite3.connect("file:/data/jobs.sqlite3?mode=ro",uri=True); r=d.execute("select status,count(*) from jobs where status in (?,?,?,?,?,?) group by status",("QUEUED","VALIDATING","DOWNLOADING","ANALYZING","RENDERING","UPLOADING")).fetchall(); d.close(); print("ACTIVE_JOBS=",r); sys.exit(3 if r else 0)'
}
check_idle "$api"
if test "$mode" = --preflight-only; then
    echo 'PREFLIGHT_PASS: no service or configuration changes made'
    exit 0
fi

state=$(mktemp -d /data/data/backups/analysis-move-20261119-XXXXXX)
printf 'MOVE_STATE=%s\n' "$state"
cp -p .env "$state/env.before"
cp -p compose.yaml compose.portal.yaml compose.external-data.yaml "$state/"
phase=preparing
api_stopped=0
worker_stopped=0
env_changed=0
finish() {
    result=$?
    trap - EXIT
    set +e
    if test "$result" != 0; then
        if test "$phase" = recreating || test "$phase" = live; then
            printf 'STOP: container transition attempted. Do not auto-roll back: new writes may exist. State: %s\n' "$state" >&2
        else
            if test "$env_changed" = 1; then cp -p "$state/env.before" "$project/.env" || result=1; fi
            if test "$api_stopped" = 1; then docker start "$api" >/dev/null || result=1; fi
            if test "$worker_stopped" = 1; then docker start "$worker" >/dev/null || result=1; fi
            echo 'Original containers restored if stopped; copied target is preserved for inspection.' >&2
        fi
    fi
    exit "$result"
}
trap finish EXIT
api_stopped=1
docker stop --timeout 30 "$api"
check_idle "$worker"
worker_stopped=1
docker stop --timeout 30 "$worker"
phase=stopped

docker run --rm --network none --user 10001:10001 --mount "type=volume,source=$volume,target=/data" "$expected" \
    python -c 'import sqlite3,json; d=sqlite3.connect("file:/data/jobs.sqlite3?mode=rw",uri=True); w=d.execute("pragma wal_checkpoint(TRUNCATE)").fetchone(); assert w[0]==0,w; assert d.execute("pragma integrity_check").fetchall()==[("ok",)]; print(json.dumps({"jobs":d.execute("select count(*) from jobs").fetchone()[0],"integrity":"ok"})); d.close()' > "$state/db.before.json"
sudo tar --numeric-owner --acls --xattrs -C "$source" -cpf - . | gzip > "$state/data-before-copy.tar.gz"
gzip -t "$state/data-before-copy.tar.gz"
sudo rsync -aHAX --numeric-ids "$source/" "$target/"
sudo rsync -aHAXnci --numeric-ids "$source/" "$target/" > "$state/copy-differences.txt"
test ! -s "$state/copy-differences.txt"
sudo chmod 0750 "$target"
docker run --rm --network none --user 10001:10001 --mount "type=bind,source=$target,target=/data,readonly" "$expected" \
    python -c 'import sqlite3,json; d=sqlite3.connect("file:/data/jobs.sqlite3?mode=ro&immutable=1",uri=True); assert d.execute("pragma integrity_check").fetchall()==[("ok",)]; print(json.dumps({"jobs":d.execute("select count(*) from jobs").fetchone()[0],"integrity":"ok"})); d.close()' > "$state/db.copied.json"
cmp "$state/db.before.json" "$state/db.copied.json"
(cd "$state" && sha256sum data-before-copy.tar.gz db.before.json db.copied.json copy-differences.txt > COPY_SHA256SUMS)

# Persist the storage-only defaults, excluding the stopped Caddy https service.
# All secret values and unrelated env lines are preserved without printing them.
env_changed=1
python3 - "$project/.env" <<'PY'
import os, re, sys, tempfile
from pathlib import Path
p = Path(sys.argv[1])
updates = {"COMPOSE_FILE": "compose.yaml:compose.portal.yaml:compose.external-data.yaml",
           "HEATPIPE_EXTERNAL_ANALYSIS_ROOT": "/data/data/analysis"}
lines = p.read_text(encoding="utf-8").splitlines()
pattern = re.compile(r"^\s*(?:export\s+)?(" + "|".join(updates) + r")\s*=")
lines = [line for line in lines if not pattern.match(line)]
lines.extend(f"{key}={value}" for key,value in updates.items())
with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=p.parent, prefix=".env.storage-", delete=False) as f:
    f.write("\n".join(lines) + "\n")
    f.flush()
    os.fsync(f.fileno())
    temporary = f.name
os.chmod(temporary, 0o600)
os.replace(temporary, p)
PY
docker compose config --format json | validate_config
test "$(docker image inspect --format '{{.Id}}' heatpipe-service:local)" = "$expected"
phase=recreating
docker compose up -d --no-build --pull never --no-deps --force-recreate api worker
phase=live
for container in "$api" "$worker"; do
    test "$(docker inspect --format '{{.Image}}' "$container")" = "$expected"
    test "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$container")" = "$target"
done
docker exec -i "$api" python - <<'PY' > "$state/service-check.json"
import hashlib,json,os,sqlite3,time,urllib.request
def get(path):
    r=urllib.request.Request("http://127.0.0.1:8000"+path,headers={"Authorization":"Bearer "+os.environ["HEATPIPE_API_TOKEN"]})
    with urllib.request.urlopen(r,timeout=5) as response: return response.read()
for attempt in range(60):
    try:
        health=json.loads(get("/health"))
        if health["status"]=="ok" and health["worker_running"]: break
    except (OSError,ValueError): pass
    time.sleep(0.5)
else: raise RuntimeError("API/worker did not become ready")
d=sqlite3.connect("file:/data/jobs.sqlite3?mode=ro",uri=True)
row=d.execute("select job_id from jobs where status='SUCCEEDED' and result_key is not null order by id desc limit 1").fetchone()
count=d.execute("select count(*) from jobs").fetchone()[0]
d.close()
report={"health":health,"jobs":count}
if row:
    job=row[0]
    payload=json.loads(get(f"/api/v1/analysis-jobs/{job}/results"))
    report["existing_result"]=job
    if payload["candidates"]:
        image=payload["candidates"][0]["result_image"]
        content=get(image["path"])
        assert len(content)==image["size_bytes"] and hashlib.sha256(content).hexdigest()==image["sha256"]
        report["existing_png"]="size and SHA-256 ok"
print(json.dumps(report,sort_keys=True))
PY
cat "$state/service-check.json"
docker exec "$api" df -h /data
printf 'MIGRATION_SUCCESS: %s; original volume preserved; current image unchanged\n' "$target"
printf 'MOVE_STATE=%s\n' "$state"
