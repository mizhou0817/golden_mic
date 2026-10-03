import asyncio
import json
import sys

from pydantic import ValidationError

from .config import get_settings
from .readiness import run_full_preflight
from .storage import sanitize_sensitive_text


def main() -> int:
    try:
        settings = get_settings()
        report = asyncio.run(run_full_preflight(settings))
    except (OSError, RuntimeError, ValidationError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ready": False,
                    "checks": {"settings": False},
                    "errors": [sanitize_sensitive_text(exc)],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ready else 1


if __name__ == "__main__":
    sys.exit(main())
