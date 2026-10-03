from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DATE_EPOCH = 1785196800  # 2026-07-28T00:00:00Z


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic CycloneDX SBOM from uv.lock.")
    parser.add_argument("--output", type=Path, default=ROOT / "sbom.cdx.json")
    arguments = parser.parse_args()
    output_path = arguments.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="golden-mic-sbom-") as directory:
        raw_path = Path(directory) / "raw.cdx.json"
        subprocess.run(
            [
                "uv",
                "export",
                "--preview-features",
                "sbom-export",
                "--format",
                "cyclonedx1.5",
                "--frozen",
                "--no-dev",
                "--output-file",
                str(raw_path),
            ],
            cwd=ROOT,
            check=True,
        )
        payload = json.loads(raw_path.read_text(encoding="utf-8"))

    lock_hash = hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    source_date_epoch = int(os.environ.get("SOURCE_DATE_EPOCH", DEFAULT_SOURCE_DATE_EPOCH))
    timestamp = datetime.fromtimestamp(source_date_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    payload["serialNumber"] = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'golden-mic:' + lock_hash)}"
    metadata = payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise RuntimeError("CycloneDX metadata must be an object.")
    metadata["timestamp"] = timestamp
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"sbom={output_path} uv_lock_sha256={lock_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
