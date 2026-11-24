#!/usr/bin/env bash
# Run from the extracted standalone build kit, never the live Compose directory.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
expected_base='sha256:bc8e9b3395a9e79100c3d3a3422d776eb67848a5bced06bdd4d12156b98c43dc'
base='heatpipe-service:pre-storage-20261119'
candidate='heatpipe-service:r3-20261119-build2'
test "$(docker image inspect "$base" --format '{{.Id}}')" = "$expected_base"
if docker image inspect "$candidate" >/dev/null 2>&1; then
  echo 'STOP: candidate tag already exists; choose a new reviewed tag'; exit 1
fi
sha256sum -c BUILD_SHA256SUMS
# A failure here means the old image lacks local build tools; do not fetch new ones.
docker run --rm --network none --entrypoint python "$base" -c 'import setuptools, wheel'
manifest_hash=$(sha256sum CANDIDATE_MANIFEST.json | cut -d ' ' -f 1)
docker build --pull=false --network=none --build-arg "BASE_IMAGE=$base" \
  --build-arg "SOURCE_MANIFEST_SHA256=$manifest_hash" -f Dockerfile.r3 -t "$candidate" .
for image in "$base" "$candidate"; do
  docker run --rm --network none --entrypoint python "$image" -c 'import importlib.metadata as m,json; print(json.dumps(sorted((d.metadata["Name"].lower().replace("_","-"),d.version) for d in m.distributions() if d.metadata["Name"].lower().replace("_","-") != "heatpipe-service")))' > "${image##*:}.dependencies.json"
done
cmp pre-storage-20261119.dependencies.json r3-20261119-build2.dependencies.json
docker run --rm -i --network none --entrypoint python \
  --mount "type=bind,src=$PWD/CANDIDATE_MANIFEST.json,dst=/candidate-manifest.json,readonly" \
  "$candidate" - /candidate-manifest.json < verify-installed-candidate.py
docker run --rm --network none --entrypoint python "$candidate" -c 'from heatpipe_service.api.models import FileReference; from heatpipe_service.jobs.worker import JobWorker; assert "path" in FileReference.model_fields; print("PASS: path model and worker imports")'
docker image inspect "$candidate" --format '{{.Id}} manifest={{index .Config.Labels "com.daon.heatpipe.source-manifest-sha256"}}'
echo 'BUILD_ONLY_COMPLETE: dependencies unchanged; live tag and services untouched'
