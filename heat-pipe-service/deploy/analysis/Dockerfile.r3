# Build only from the preserved, locally verified production image.
ARG BASE_IMAGE=heatpipe-service:pre-storage-20261119
FROM ${BASE_IMAGE}
USER root
# The base contains /app/build. Reusing it can install stale files when the
# archived sources have older mtimes. Always build in a fresh directory.
RUN test ! -e /opt/heatpipe-r3-build2
WORKDIR /opt/heatpipe-r3-build2
COPY pyproject.toml README.md ./
COPY src ./src
# No dependency resolution, build dependency download, or network access.
RUN python -m pip install --force-reinstall --no-index --no-deps --no-build-isolation .
ARG SOURCE_MANIFEST_SHA256
LABEL com.daon.heatpipe.source-manifest-sha256=${SOURCE_MANIFEST_SHA256}
USER heatpipe
WORKDIR /app
