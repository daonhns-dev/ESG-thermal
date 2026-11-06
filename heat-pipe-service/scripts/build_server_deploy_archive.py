"""Bundle the current working tree needed for the CPU test-server deployment."""

from __future__ import annotations

from pathlib import Path
from tarfile import open as tar_open


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "output" / "heatpipe-service-cpu-deploy-20261106.tar.gz"
FILES = (
    ".dockerignore",
    ".env.example",
    "Dockerfile",
    "README.md",
    "compose.yaml",
    "pyproject.toml",
)


def main() -> None:
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    with tar_open(DESTINATION, "w:gz") as archive:
        for name in FILES:
            archive.add(ROOT / name, arcname=f"heat-pipe-service/{name}")
        for source in sorted((ROOT / "src").rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                archive.add(source, arcname=f"heat-pipe-service/{source.relative_to(ROOT).as_posix()}")
    print(DESTINATION)


if __name__ == "__main__":
    main()
