from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path


KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute a command with a systemd-style EnvironmentFile without shell evaluation.",
        usage="%(prog)s [--cwd DIRECTORY] [--set KEY=VALUE] environment_file -- command [args ...]",
    )
    parser.add_argument("environment_file", type=Path)
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("--set", action="append", default=[], dest="overrides")
    values = list(sys.argv[1:] if argv is None else argv)
    if "--" not in values:
        # Preserve ordinary --help/argument errors, but never guess where a
        # wrapper option ends and a child command begins.
        parser.parse_args(values)
        parser.error("A command is required after an explicit -- separator.")
    separator = values.index("--")
    arguments = parser.parse_args(values[:separator])
    arguments.command = values[separator + 1:]
    if not arguments.command:
        parser.error("A command is required after --.")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    command = arguments.command

    environment = os.environ.copy()
    environment.update(parse_environment_file(arguments.environment_file))
    for assignment in arguments.overrides:
        key, value = parse_assignment(assignment)
        environment[key] = value
    if arguments.cwd is not None:
        os.chdir(arguments.cwd)
    os.execvpe(command[0], command, environment)
    return 127


def parse_environment_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"Environment file not found: {path}")
    if path.stat().st_size > 1024 * 1024:
        raise RuntimeError("Environment file exceeds 1 MiB.")
    contents = path.read_text(encoding="utf-8-sig")
    if "\x00" in contents:
        raise RuntimeError("Environment file contains a NUL character.")
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        contents.splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        try:
            key, value = parse_assignment(line)
        except ValueError as exc:
            raise RuntimeError(f"Invalid environment assignment at line {line_number}.") from exc
        if key in result:
            raise RuntimeError(f"Duplicate environment key at line {line_number}: {key}")
        result[key] = value
    return result


def parse_assignment(assignment: str) -> tuple[str, str]:
    if "=" not in assignment:
        raise ValueError("Environment assignment is missing '='.")
    key, raw_value = assignment.split("=", maxsplit=1)
    key = key.strip()
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("Environment key is invalid.")
    raw_value = raw_value.strip()
    if not raw_value:
        return key, ""
    if raw_value[0] in {'"', "'"}:
        parsed = shlex.split(raw_value, comments=False, posix=True)
        if len(parsed) != 1:
            raise ValueError("Quoted environment value is invalid.")
        return key, parsed[0]
    return key, raw_value


if __name__ == "__main__":
    raise SystemExit(main())
