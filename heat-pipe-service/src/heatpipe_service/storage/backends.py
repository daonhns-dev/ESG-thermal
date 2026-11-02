"""File references and result objects for local testbed or MinIO storage."""

from __future__ import annotations

import hashlib
import os
import shutil
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import urlparse

from heatpipe_service.api.models import FileReference


class StorageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_key(key: str) -> str:
    path = PurePosixPath(key)
    if (not key or key.startswith("/") or "\\" in key or ":" in key
            or any(part in {"", ".", ".."} for part in key.split("/"))):
        raise StorageError("INVALID_OBJECT_KEY", "Object key must be a relative POSIX path")
    return path.as_posix()


def verify_file(path: Path, reference: FileReference) -> None:
    if reference.size_bytes is not None and path.stat().st_size != reference.size_bytes:
        raise StorageError("FILE_INTEGRITY_ERROR", "File size does not match the request")
    if reference.sha256 is not None:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != reference.sha256.lower():
            raise StorageError("FILE_INTEGRITY_ERROR", "SHA-256 does not match the request")


def _download_url(url: str, destination: Path, allowed_hosts: frozenset[str]) -> None:
    parsed = urlparse(url)
    if parsed.hostname not in allowed_hosts or parsed.scheme != "https":
        raise StorageError("SOURCE_URL_NOT_ALLOWED", "HTTPS source host is not allowed")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            with destination.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
    except Exception as error:
        raise StorageError("DOWNLOAD_FAILED", "Could not download source URL") from error


class Storage(Protocol):
    def fetch(self, reference: FileReference, destination: Path) -> None: ...
    def put(self, source: Path, object_key: str) -> str: ...
    def read_bytes(self, object_key: str) -> bytes: ...


class LocalStorage:
    def __init__(self, root: Path | str, allowed_url_hosts: frozenset[str] = frozenset()) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed_url_hosts = allowed_url_hosts

    def _path(self, object_key: str) -> Path:
        path = (self.root / validate_key(object_key)).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError("INVALID_OBJECT_KEY", "Object key escapes storage root")
        return path

    def fetch(self, reference: FileReference, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".download")
        try:
            if reference.object_key is not None:
                source = self._path(reference.object_key)
                if not source.is_file():
                    raise StorageError("INPUT_FILE_NOT_FOUND", "Referenced input file was not found")
                shutil.copyfile(source, temporary)
            else:
                _download_url(str(reference.url), temporary, self.allowed_url_hosts)
            verify_file(temporary, reference)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def put(self, source: Path, object_key: str) -> str:
        destination = self._path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".upload")
        try:
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return object_key

    def read_bytes(self, object_key: str) -> bytes:
        path = self._path(object_key)
        if not path.is_file():
            raise StorageError("RESULT_NOT_FOUND", "Result object was not found")
        return path.read_bytes()


class MinioStorage:
    def __init__(
        self, bucket: str, endpoint: str | None = None, access_key: str | None = None,
        secret_key: str | None = None, secure: bool = True,
        allowed_url_hosts: frozenset[str] = frozenset(), client: object | None = None,
    ) -> None:
        if client is None:
            try:
                from minio import Minio
            except ImportError as error:
                raise RuntimeError("Install the 'minio' optional dependency for MinioStorage") from error
            if not endpoint or not access_key or not secret_key:
                raise ValueError("MinIO endpoint and credentials are required")
            client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self.client = client
        self.bucket = bucket
        self.allowed_url_hosts = allowed_url_hosts

    def fetch(self, reference: FileReference, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".download")
        try:
            if reference.object_key is not None:
                try:
                    self.client.fget_object(
                        bucket_name=self.bucket,
                        object_name=validate_key(reference.object_key),
                        file_path=str(temporary),
                    )
                except Exception as error:
                    raise StorageError("DOWNLOAD_FAILED", "Could not download MinIO object") from error
            else:
                _download_url(str(reference.url), temporary, self.allowed_url_hosts)
            verify_file(temporary, reference)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def put(self, source: Path, object_key: str) -> str:
        key = validate_key(object_key)
        try:
            self.client.fput_object(bucket_name=self.bucket, object_name=key, file_path=str(source))
        except Exception as error:
            raise StorageError("UPLOAD_FAILED", "Could not upload MinIO object") from error
        return key

    def read_bytes(self, object_key: str) -> bytes:
        try:
            response = self.client.get_object(bucket_name=self.bucket, object_name=validate_key(object_key))
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception as error:
            raise StorageError("RESULT_NOT_FOUND", "Could not read MinIO result object") from error


def storage_from_env() -> Storage:
    hosts = frozenset(filter(None, os.environ.get("HEATPIPE_ALLOWED_SOURCE_HOSTS", "").split(",")))
    kind = os.environ.get("HEATPIPE_STORAGE", "local").lower()
    if kind == "local":
        return LocalStorage(os.environ.get("HEATPIPE_STORAGE_ROOT", "data/objects"), hosts)
    if kind == "minio":
        return MinioStorage(
            bucket=os.environ["HEATPIPE_MINIO_BUCKET"],
            endpoint=os.environ["HEATPIPE_MINIO_ENDPOINT"],
            access_key=os.environ["HEATPIPE_MINIO_ACCESS_KEY"],
            secret_key=os.environ["HEATPIPE_MINIO_SECRET_KEY"],
            secure=os.environ.get("HEATPIPE_MINIO_SECURE", "true").lower() == "true",
            allowed_url_hosts=hosts,
        )
    raise ValueError(f"Unsupported HEATPIPE_STORAGE: {kind}")
