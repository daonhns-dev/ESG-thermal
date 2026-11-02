FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
ARG INSTALL_GPU=0
RUN python -c "import tomllib; p=tomllib.load(open('pyproject.toml','rb'))['project']; print('\n'.join(p['dependencies'] + (p['optional-dependencies']['gpu'] if '$INSTALL_GPU' == '1' else [])))" > /tmp/requirements.txt \
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps . \
    && groupadd --gid 10001 heatpipe \
    && useradd --uid 10001 --gid heatpipe --no-create-home heatpipe \
    && mkdir -p /data \
    && chown heatpipe:heatpipe /data

USER heatpipe
ENV HOME=/data \
    CUPY_CACHE_DIR=/data/.cupy/kernel_cache \
    HEATPIPE_JOB_DB_PATH=/data/jobs.sqlite3 \
    HEATPIPE_STORAGE=local \
    HEATPIPE_STORAGE_ROOT=/data/objects \
    HEATPIPE_WORK_ROOT=/data/work

EXPOSE 8000
CMD ["uvicorn", "heatpipe_service.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
