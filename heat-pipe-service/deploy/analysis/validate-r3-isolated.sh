#!/usr/bin/env bash
# Uses an offline backup copy; never mounts production data or publishes a port.
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
sha256sum -c VALIDATION_SHA256SUMS
candidate='heatpipe-service:r3-20261119-build2'
expected='sha256:5e007f46e1856316d7c8c45cc715693955b43505cb3dd34c41d93decb08c7948'
test "$(docker image inspect "$candidate" --format '{{.Id}}')" = "$expected"
backup='/data/data/backups/analysis-pre-move-20261119-B5B0pB'
mountpoint -q /data
(cd "$backup" && sha256sum -c DATA_SHA256SUMS)
sudo -v
check_dir=$(mktemp -d /data/data/backups/r3-image-validation-XXXXXX)
echo "VALIDATION_DIR=$check_dir"
mkdir "$check_dir/data"
sudo tar --numeric-owner --acls --xattrs -xzf "$backup/analysis-data.tar.gz" -C "$check_dir/data"
container_id=''
cleanup() {
    status=$?
    trap - EXIT
    if test -n "$container_id"; then
        docker logs "$container_id" > "$check_dir/api.log" 2>&1 || true
        docker rm -f "$container_id" >/dev/null || status=1
    fi
    echo "ISOLATED_CHECK_EXIT=$status; production untouched; validation files retained"
    exit "$status"
}
trap cleanup EXIT
container_id=$(docker create --network none --memory 512m --cpus 1 --user 10001:10001 \
    --mount "type=bind,src=$check_dir/data,dst=/data" \
    --mount "type=bind,src=$PWD,dst=/validation,readonly" \
    -e HEATPIPE_ENV=production -e HEATPIPE_API_TOKEN=isolated-r3-check-only \
    -e HEATPIPE_ALLOWED_INTERNAL_HTTP_HOSTS=127.0.0.1:18090 \
    -e HEATPIPE_SHARED_INPUT_ROOT= "$candidate")
docker start "$container_id" >/dev/null
docker exec "$container_id" python /validation/validate-r3-isolated.py | tee "$check_dir/check-result.json"
echo 'ISOLATED_VALIDATION_PASS: restored results + synthetic URL API/worker flow'
