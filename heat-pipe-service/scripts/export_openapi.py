"""Export the development-mode FastAPI contract to a static OpenAPI JSON file."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heatpipe_service.api.app import create_app


def main() -> None:
    os.environ["HEATPIPE_ENV"] = "development"
    destination = ROOT / "docs" / "openapi.json"
    scratch = ROOT / "tmp"
    scratch.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=scratch) as directory:
        app = create_app(Path(directory) / "jobs.sqlite3")
        document = app.openapi()
    destination.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
