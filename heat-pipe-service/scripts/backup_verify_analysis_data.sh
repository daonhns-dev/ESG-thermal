#!/usr/bin/env bash
# Briefly stop only the existing analysis containers, back up their volume, and
# verify an isolated restoration. Always restart the original containers on exit.
# No Compose up, source deployment, mount change, or portal operation occurs.
set -euo pipefail
trap 'code=$?; printf "ERROR: backup check failed at line %s (exit %s)\n" "$LINENO" "$code" >&2' ERR
umask 077

backup=${1:?Usage: bash backup-verify-analysis-data.sh /data/data/backups/analysis-pre-move-20261119-XXXXXX}
backup=$(readlink -f -- "$backup")
case "$backup" in /data/data/backups/analysis-pre-move-20261119-*) ;; *) echo 'Invalid backup directory' >&2; exit 1;; esac
expected=sha256:bc8e9b3395a9e79100c3d3a3422d776eb67848a5bced06bdd4d12156b98c43dc
api=heatpipe-service-api-1
worker=heatpipe-service-worker-1
volume=heatpipe-service_heatpipe_data
mountpoint -q /data
test "$(findmnt -n -o SOURCE --mountpoint /data)" = /dev/mapper/pve-vm--200002--disk--1
test "$(cat "$backup/image-id.txt")" = "$expected"
(cd "$backup" && sha256sum -c SHA256SUMS)
test ! -e "$backup/analysis-data.tar.gz"
test ! -e "$backup/analysis-data.tar.gz.partial"
test ! -e "$backup/restore-check"
for container in "$api" "$worker"; do
    test "$(docker inspect --format '{{.Image}}' "$container")" = "$expected"
    test "$(docker inspect --format '{{.State.Running}}' "$container")" = true
    test "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$container")" = "$volume"
done
source=$(docker volume inspect --format '{{.Mountpoint}}' "$volume")
sudo -v
sudo test -d "$source"

check_idle() {
    docker exec "$1" python -c 'import sqlite3,sys; db=sqlite3.connect("file:/data/jobs.sqlite3?mode=ro",uri=True); rows=db.execute("select status,count(*) from jobs where status in (?,?,?,?,?,?,?) group by status",("QUEUED","VALIDATING","DOWNLOADING","ANALYZING","RENDERING","UPLOADING","CANCELLING")).fetchall(); db.close(); print("ACTIVE_JOBS=",rows); sys.exit(3 if rows else 0)'
}
check_idle "$api"

api_stopped=0
worker_stopped=0
restore_created=0
restore_name=heatpipe-restore-check-$(basename "$backup")
if docker inspect "$restore_name" >/dev/null 2>&1; then
    echo 'Restore-check container name already exists; inspect it before retrying.' >&2
    exit 1
fi
restore_services() {
    result=$?
    trap - EXIT
    set +e
    if test "$restore_created" = 1; then
        docker rm -f "$restore_name" >/dev/null || result=1
    fi
    if test "$api_stopped" = 1; then
        docker start "$api" >/dev/null || result=1
    fi
    if test "$worker_stopped" = 1; then
        docker start "$worker" >/dev/null || result=1
    fi
    printf 'Original analysis containers restarted (if stopped). Exit=%s\n' "$result"
    exit "$result"
}
trap restore_services EXIT

api_stopped=1
docker stop --timeout 30 "$api"
# Close the admission race: an incoming request may have arrived before API stop.
# If any work exists, leave the worker running and restore API; do not copy data.
check_idle "$worker"
worker_stopped=1
docker stop --timeout 30 "$worker"

docker run --rm --network none --user 10001:10001 \
    --mount "type=volume,source=$volume,target=/data" "$expected" \
    python -c 'import sqlite3,json; db=sqlite3.connect("file:/data/jobs.sqlite3?mode=rw",uri=True); checkpoint=db.execute("pragma wal_checkpoint(TRUNCATE)").fetchone(); assert checkpoint[0]==0,checkpoint; integrity=db.execute("pragma integrity_check").fetchall(); assert integrity==[("ok",)],integrity; print(json.dumps({"checkpoint":checkpoint,"integrity":"ok","jobs":db.execute("select count(*) from jobs").fetchone()[0]},sort_keys=True)); db.close()' \
    > "$backup/source-db-check.json"

sudo tar --numeric-owner --acls --xattrs -C "$source" -cpf - . | gzip > "$backup/analysis-data.tar.gz.partial"
mv "$backup/analysis-data.tar.gz.partial" "$backup/analysis-data.tar.gz"
gzip -t "$backup/analysis-data.tar.gz"
mkdir "$backup/restore-check"
gzip -dc "$backup/analysis-data.tar.gz" | sudo tar --numeric-owner --acls --xattrs -C "$backup/restore-check" -xpf -
sudo rsync -aHAXnci --numeric-ids "$source/" "$backup/restore-check/" > "$backup/restore-differences.txt"
test ! -s "$backup/restore-differences.txt"

docker run --rm --network none --user 10001:10001 \
    --mount "type=bind,source=$backup/restore-check,target=/data,readonly" "$expected" \
    python -c 'import sqlite3,json; db=sqlite3.connect("file:/data/jobs.sqlite3?mode=ro&immutable=1",uri=True); integrity=db.execute("pragma integrity_check").fetchall(); assert integrity==[("ok",)],integrity; print(json.dumps({"integrity":"ok","jobs":db.execute("select count(*) from jobs").fetchone()[0]},sort_keys=True)); db.close()' \
    > "$backup/restored-db-check.json"
python3 - "$backup" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
source = json.loads((root / "source-db-check.json").read_text())
restored = json.loads((root / "restored-db-check.json").read_text())
assert source["jobs"] == restored["jobs"]
print("PASS: SQLite integrity, restored job count, full-file content and metadata comparison")
PY
(cd "$backup" && sha256sum analysis-data.tar.gz source-db-check.json restored-db-check.json restore-differences.txt > DATA_SHA256SUMS)

# End the production pause before booting a service against the isolated copy.
docker start "$api" >/dev/null
api_stopped=0
docker start "$worker" >/dev/null
worker_stopped=0
docker run -d --name "$restore_name" --network none --user 10001:10001 \
    --mount "type=bind,source=$backup/restore-check,target=/data" \
    -e HEATPIPE_ENV=production -e HEATPIPE_API_TOKEN=restore-check-only \
    "$expected" >/dev/null
restore_created=1
docker exec -i "$restore_name" python - <<'PY' > "$backup/restored-service-check.json"
import hashlib, json, sqlite3, time, urllib.request
base = "http://127.0.0.1:8000"
def get(path):
    request = urllib.request.Request(base + path, headers={"Authorization": "Bearer restore-check-only"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read()
for attempt in range(40):
    try:
        health = json.loads(get("/health"))
        assert health["status"] == "ok"
        break
    except (OSError, ValueError):
        time.sleep(0.25)
else:
    raise RuntimeError("Restored API did not start")
db = sqlite3.connect("file:/data/jobs.sqlite3?mode=ro", uri=True)
row = db.execute("select job_id from jobs where status='SUCCEEDED' and result_key is not null order by id desc limit 1").fetchone()
db.close()
result = {"health": "ok", "existing_result": "no completed real job available"}
if row:
    job_id = row[0]
    payload = json.loads(get(f"/api/v1/analysis-jobs/{job_id}/results"))
    result["existing_result"] = "ok"
    result["job_id"] = job_id
    if payload["candidates"]:
        candidate = payload["candidates"][0]
        png = get(candidate["result_image"]["path"])
        assert len(png) == candidate["result_image"]["size_bytes"]
        assert hashlib.sha256(png).hexdigest() == candidate["result_image"]["sha256"]
        result["existing_png"] = "size and SHA-256 ok"
print(json.dumps(result, sort_keys=True))
PY
(cd "$backup" && sha256sum restored-service-check.json >> DATA_SHA256SUMS)
cat "$backup/restored-service-check.json"
printf 'DATA_BACKUP_VERIFIED=%s\n' "$backup/analysis-data.tar.gz"
# EXIT trap removes only the temporary restore-check container created above.
