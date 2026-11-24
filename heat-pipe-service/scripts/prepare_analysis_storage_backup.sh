#!/usr/bin/env bash
# Preserve the currently running analysis image and source/config, without stopping services.
# This is NOT the SQLite/data-volume backup. Run that separately with writers stopped.
set -euo pipefail
umask 077

project=/home/energy/heat-pipe-service
expected=sha256:bc8e9b3395a9e79100c3d3a3422d776eb67848a5bced06bdd4d12156b98c43dc
tag=heatpipe-service:pre-storage-20261119
mountpoint -q /data
test "$(findmnt -n -o SOURCE --mountpoint /data)" = /dev/mapper/pve-vm--200002--disk--1
for container in heatpipe-service-api-1 heatpipe-service-worker-1; do
    test "$(docker inspect --format '{{.Image}}' "$container")" = "$expected"
done
test "$(docker image inspect --format '{{.Id}}' heatpipe-service:local)" = "$expected"
if docker image inspect "$tag" >/dev/null 2>&1; then
    test "$(docker image inspect --format '{{.Id}}' "$tag")" = "$expected"
else
    docker tag "$expected" "$tag"
fi

mkdir -p /data/data/backups
backup=$(mktemp -d /data/data/backups/analysis-pre-move-20261119-XXXXXX)
printf 'BACKUP_DIR=%s\n' "$backup"
printf '%s\n' "$expected" > "$backup/image-id.txt"
docker inspect heatpipe-service-api-1 heatpipe-service-worker-1 \
    --format '{{.Name}} image={{.Image}}' > "$backup/running-images.txt"
docker image save "$tag" | gzip > "$backup/analysis-image.tar.gz.partial"
mv "$backup/analysis-image.tar.gz.partial" "$backup/analysis-image.tar.gz"

cd "$project"
files=(.env Dockerfile pyproject.toml README.md compose.yaml compose.https.yaml
       compose.portal.yaml compose.keep-current-data.yaml Caddyfile.test src)
for item in "${files[@]}"; do test -e "$item"; done
tar --numeric-owner -czf "$backup/source-and-settings.tar.gz.partial" -- "${files[@]}"
mv "$backup/source-and-settings.tar.gz.partial" "$backup/source-and-settings.tar.gz"
gzip -t "$backup/analysis-image.tar.gz"
tar -tzf "$backup/source-and-settings.tar.gz" >/dev/null
(cd "$backup" && sha256sum analysis-image.tar.gz source-and-settings.tar.gz image-id.txt running-images.txt > SHA256SUMS)
printf 'Image and source/settings preserved. Services were not stopped. Data-volume backup is still pending.\n'
printf 'BACKUP_DIR=%s\n' "$backup"
du -h "$backup/analysis-image.tar.gz" "$backup/source-and-settings.tar.gz"
