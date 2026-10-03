from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from uuid import uuid4

_ENV_ASSIGNMENT = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=.*)$")
LEGACY_ENVIRONMENT_KEYS = {
    "VIDEO_PROCESSING_PROVIDER",
    "VOLCENGINE_MEDIAKIT_API_KEYS",
    "VOLCENGINE_MEDIAKIT_BASE_URL",
    "VOLCENGINE_MEDIAKIT_BITRATE_LEVEL",
    "VOLCENGINE_MEDIAKIT_FPS",
    "VOLCENGINE_MEDIAKIT_MAX_CONCURRENCY",
    "VOLCENGINE_MEDIAKIT_MAX_DOWNLOAD_MB",
    "VOLCENGINE_MEDIAKIT_MAX_RETRIES",
    "VOLCENGINE_MEDIAKIT_OUTPUT_DESTINATION",
    "VOLCENGINE_MEDIAKIT_POLL_INTERVAL_SECONDS",
    "VOLCENGINE_MEDIAKIT_RESOLUTION",
    "VOLCENGINE_MEDIAKIT_TASK_TIMEOUT_SECONDS",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove retired MediaKit keys without reading or printing environment values."
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()

    path = arguments.path.resolve()
    if not path.is_file():
        raise SystemExit(f"Environment file not found: {path}")
    original = path.read_text(encoding="utf-8-sig")
    lines = original.splitlines(keepends=True)
    retained: list[str] = []
    removed: list[str] = []
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if match and match.group(2) in LEGACY_ENVIRONMENT_KEYS:
            removed.append(match.group(2))
            continue
        retained.append(line)

    if arguments.check:
        if removed:
            print("legacy_environment_keys=" + ",".join(sorted(set(removed))))
            return 1
        print("legacy_environment_keys=none")
        return 0

    if removed:
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        mode = path.stat().st_mode
        try:
            temporary.write_text("".join(retained), encoding="utf-8", newline="")
            os.chmod(temporary, mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    print(
        f"removed_legacy_keys={len(removed)} names="
        + (",".join(sorted(set(removed))) if removed else "none")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
