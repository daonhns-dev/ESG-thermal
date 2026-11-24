"""Package allowlisted source/test snapshots for review, never server secrets."""
from pathlib import Path
import difflib
import hashlib
import json
import subprocess
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    repo = root.parent
    selected = [
        ".env.example", "pyproject.toml", "compose.yaml", "compose.portal.yaml",
        "compose.https.yaml", "compose.external-data.yaml", "compose.shared-input.yaml",
        "Caddyfile.test", "scripts/export_openapi.py", "scripts/build_shared_path_example.py",
        "scripts/build_plan_a_review_package.py", "docs/CLAUDE_REVIEW_PLAN_A_20261118.md",
        "docs/Q15_SHARED_PATH_V1_1_DRAFT.md", "docs/Q15_VTI_INTEGRATION_API_FINAL.md",
        "docs/openapi.json", "docs/JOB_STATE_V0.md", "docs/DOCKER_DEPLOYMENT.md",
        "docs/VTI_R2_12_ERROR_CODES_2026-11-05.md",
        "docs/EXTERNAL_STORAGE_MIGRATION_2026-11-18.md",
        "docs/CLAUDE_REVIEW_ROLLBACK_STORAGE_R3_20261119.md",
        "compose.keep-current-data.yaml", "deploy/portal/compose.keep-current-minio.yaml",
        "docs/examples/int-003-shared-path.example.json",
        "docs/examples/analysis-result-v0.example.json", "docs/README.md",
        "docs/schemas/analysis-result-v0.schema.json",
    ]
    files = [root / name for name in selected]
    files += [p for p in (root / "src").rglob("*")
              if p.is_file() and p.suffix in {".py", ".json", ".png"} and "__pycache__" not in p.parts]
    files += list((root / "tests" / "unit").glob("*.py"))
    files += list((root / "tests" / "fixtures").glob("*.py"))
    files = sorted(set(files))
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.name == ".env" or "__pycache__" in path.parts:
            raise ValueError("Unapproved file")
    git = lambda *args: subprocess.check_output(["git", *args], cwd=repo)
    manifest = {"purpose": "LOCAL REVIEW ONLY; NOT A SERVER DEPLOYMENT PACKAGE",
                "base_commit": git("rev-parse", "HEAD").decode().strip(),
                "note": "Current snapshots include earlier uncommitted changes. See review document for scope.",
                "sha256": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in files}}
    output = root / "output" / "VTI_PLAN_A_Claude_Review_20261119_r3.zip"
    output.parent.mkdir(exist_ok=True)
    diff_paths = [p.relative_to(repo).as_posix() for p in files]
    patch = git("diff", "--no-ext-diff", "HEAD", "--", *diff_paths)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
        archive.writestr("REVIEW_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("tracked-changes-vs-head.patch", patch)
        previous = root / "output" / "VTI_PLAN_A_Claude_Review_20261118_r2.zip"
        if previous.is_file():
            store_path = "src/heatpipe_service/jobs/store.py"
            with zipfile.ZipFile(previous) as old:
                before = old.read(store_path).decode("utf-8").splitlines(keepends=True)
            after = (root / store_path).read_text(encoding="utf-8").splitlines(keepends=True)
            archive.writestr("r3-store-vs-r2.patch", "".join(difflib.unified_diff(
                before, after, fromfile="r2/" + store_path, tofile="r3/" + store_path)))
        archive.writestr("START_HERE.txt", "Read docs/CLAUDE_REVIEW_ROLLBACK_STORAGE_R3_20261119.md first.\n"
                         "No server changes have been applied. No .env/secrets/real input data included.\n"
                         "Patch covers tracked files only; new files are provided as snapshots.\n"
                         "Revision r3: omit nulls from persisted job request JSON; keep r2 fixes.\n"
                         "Unit tests: Linux Python 3.12, 87 passed. Golden tests not included.\n")
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert not any(Path(name).name == ".env" for name in archive.namelist())
    print(output)


if __name__ == "__main__":
    main()
