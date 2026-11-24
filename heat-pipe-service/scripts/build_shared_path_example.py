"""Build tiny synthetic ATT/ATG plus the matching plan-A JSON (never real data)."""
from pathlib import Path
import hashlib
import json
import struct


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / "tmp" / "shared-path-example" / "2026" / "11"
    target.mkdir(parents=True, exist_ok=True)
    att = target / "example-att.att"
    atg = target / "example-atg.atg"
    frames = []
    for index in range(4):
        values = [3000 + ((x * 13 + y * 7) % 17) +
                  (500 if (x - 48) ** 2 + (y - 47 - index) ** 2 < 25 else 0)
                  for y in range(64) for x in range(96)]
        frames.append(struct.pack("<6144H", *values))
    att.write_bytes(struct.pack("<4H", 1, 0, 96, 64) + b"".join(frames))
    atg.write_bytes(b"".join(
        f"2026-11-18 12:00:{i:02d}".encode("ascii") + struct.pack("<idd", i, 37.5, 126.9)
        for i in range(4)))
    def reference(path):
        return {"path": path.relative_to(target.parents[1]).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    request = {"monitoring_data_id": "shared-path-example", "att": reference(att),
               "atg": reference(atg), "frame_range": {"start": 0, "end_exclusive": 4}}
    output = root / "docs" / "examples" / "int-003-shared-path.example.json"
    output.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
