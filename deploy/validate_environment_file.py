from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import Settings
from deploy.run_with_environment import parse_environment_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate that a production EnvironmentFile exactly covers the Settings schema."
    )
    parser.add_argument("environment_file", type=Path)
    arguments = parser.parse_args()

    values = parse_environment_file(arguments.environment_file)
    missing, unknown = environment_schema_differences(values)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise SystemExit("Environment schema mismatch: " + " ".join(details))

    try:
        settings = Settings(
            _env_file=None,
            **{key.lower(): value for key, value in values.items()},
        )
    except ValidationError as exc:
        messages = [
            f"{'.'.join(str(item) for item in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors(include_input=False)
        ]
        raise SystemExit("Environment validation failed: " + "; ".join(messages)) from None
    if not settings.is_production:
        raise SystemExit("Environment validation failed: APP_ENV must be production.")
    print(f"production_environment=valid fields={len(Settings.model_fields)}")
    return 0


def environment_schema_differences(values: dict[str, str]) -> tuple[list[str], list[str]]:
    expected_keys = {name.upper() for name in Settings.model_fields}
    actual_keys = set(values)
    return sorted(expected_keys - actual_keys), sorted(actual_keys - expected_keys)


if __name__ == "__main__":
    raise SystemExit(main())
