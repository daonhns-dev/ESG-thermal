"""Read published plain input files without downloading or copying their bytes."""

from pathlib import Path

from heatpipe_service.api.models import FileReference
from heatpipe_service.storage.backends import StorageError, validate_key, verify_file

SHARED_ROOT_MARKER = ".heatpipe-shared-root"
SHARED_ROOT_MARKER_CONTENT = b"heatpipe-shared-input-v1\n"


def validate_shared_root(root: Path) -> Path:
    """Require an explicitly provisioned root, not an empty mountpoint directory."""
    if not root.is_absolute():
        raise StorageError("SHARED_INPUT_NOT_CONFIGURED", "Shared input root must be absolute")
    root = root.resolve()
    marker = root / SHARED_ROOT_MARKER
    try:
        if not root.is_dir() or marker.is_symlink() or not marker.is_file():
            raise OSError("Shared root marker missing")
        with marker.open("rb") as stream:
            if stream.read(len(SHARED_ROOT_MARKER_CONTENT) + 1) != SHARED_ROOT_MARKER_CONTENT:
                raise OSError("Shared root marker invalid")
    except OSError:
        raise StorageError("SHARED_INPUT_NOT_CONFIGURED", "Shared input storage is not ready") from None
    return root


def link_shared_input(
    reference: FileReference, destination: Path, root: Path,
) -> None:
    """Link an explicit relative path beneath a plain-file root.

    This root must contain published original files, not MinIO's backing store.
    Missing or invalid inputs fail explicitly; this function never downloads.
    """
    if reference.path is None:
        raise StorageError("INVALID_SHARED_PATH", "Shared input requires an explicit path")
    key = reference.path
    root = validate_shared_root(root)
    source = (root / validate_key(key)).resolve()
    if not source.is_relative_to(root):
        raise StorageError("INVALID_SHARED_PATH", "Shared input escapes its configured root")
    if not source.is_file():
        raise StorageError("INPUT_FILE_NOT_FOUND", "Shared input is not a published regular file")
    verify_file(source, reference)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A killed worker skips finally cleanup. Unlink only this attempt's local
    # entry (including a dangling symlink), never the shared original it targets.
    destination.unlink(missing_ok=True)
    destination.symlink_to(source)
