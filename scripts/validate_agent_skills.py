"""Validate the project-level Agent Skills (SKILL.md) used by VS Code / Copilot.

Stdlib only. Reads the skill folders under .github/skills, .agents/skills and
.claude/skills of the repository; writes nothing and never uses the network.

Errors (exit status 1):
  * SKILL.md missing its frontmatter on line 1 ("---", no BOM) or not closed
  * frontmatter that is not a simple YAML mapping: unquoted ": " or a leading
    "@"/"`" in a plain value, duplicate keys, tab indentation (VS Code skips
    such a skill silently)
  * name not matching ^[a-z0-9]+(-[a-z0-9]+)*$, longer than 64 characters or
    different from the folder name; duplicate names across the skill roots
  * description shorter than 1 or longer than 1024 characters
  * user-invocable / disable-model-invocation that are not true/false,
    compatibility outside 1-500 characters, metadata that is not a mapping
  * relative Markdown links in project skills (gm-*) that do not resolve
    (exact case), or any link that leaves the repository root
  * bidirectional control characters in any text file
Warnings: body over 500 lines, zero-width characters, binary files, unknown
frontmatter keys, context other than inline/fork, folders without SKILL.md,
broken links in third-party skills (their files are kept byte-for-byte, so
known upstream gaps are recorded in docs/AGENT_SKILLS.md instead).

Rules follow https://agentskills.io/specification and
https://code.visualstudio.com/docs/agent-customization/agent-skills.

Exit status: 0 = no errors, 1 = errors found, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

SKILL_ROOTS = (".github/skills", ".agents/skills", ".claude/skills")
PROJECT_PREFIX = "gm-"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
KEY_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)[ \t]*:(?:[ \t]+(.*))?$")
BLOCK_HEADER_RE = re.compile(r"^([|>])([+-]?)([1-9]?)([+-]?)[ \t]*(#.*)?$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500
MAX_BODY_LINES = 500
KNOWN_KEYS = {
    # Agent Skills specification
    "name", "description", "license", "compatibility", "metadata", "allowed-tools",
    # VS Code additions
    "argument-hint", "user-invocable", "disable-model-invocation", "context",
}
BOOLEAN_KEYS = ("user-invocable", "disable-model-invocation")
BIDI_CHARS = {chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))}
ZERO_WIDTH_CHARS = {"\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\u2060", "\ufeff"}
BINARY_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".bin", ".node", ".wasm", ".zip", ".gz", ".tgz"}

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
LINK_RE = re.compile(
    r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*(<[^>]*>|[^)\s]+)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
REF_DEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


class Collection:
    """A nested mapping, sequence or flow collection: never a valid string."""

    def __init__(self, kind: str) -> None:
        self.kind = kind


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")


@dataclass
class Skill:
    rel: str
    name: str | None
    body_lines: int
    flags: str


def fold(lines: list[str]) -> str:
    """Approximate YAML folding: lines join with spaces, blank lines break."""
    out: list[str] = []
    paragraph: list[str] = []
    for line in lines:
        if line:
            paragraph.append(line)
            continue
        if paragraph:
            out.append(" ".join(paragraph))
            paragraph = []
        out.append("")
    if paragraph:
        out.append(" ".join(paragraph))
    return "\n".join(out)


def parse_plain(text: str) -> tuple[object, list[str]]:
    problems: list[str] = []
    if text[:1] in ("@", "`"):
        problems.append(f"plain value may not start with {text[0]!r}; quote it")
    if re.search(r":(\s|$)", text):
        problems.append('unquoted ": " in a plain value is invalid YAML; quote the value')
    comment = re.search(r"\s#", text)
    if comment:
        text = text[: comment.start()].rstrip()
    if text in ("true", "True", "TRUE"):
        return True, problems
    if text in ("false", "False", "FALSE"):
        return False, problems
    if text in ("", "~", "null", "Null", "NULL"):
        return None, problems
    return text, problems


def parse_value(rest: str, cont: list[str]) -> tuple[object, list[str]]:
    """Parse one top-level value from its inline part and indented lines."""
    if rest[:1] in ("|", ">"):
        header = BLOCK_HEADER_RE.match(rest)
        if not header:
            return None, [f"invalid block scalar header {rest!r}"]
        indents = [len(line) - len(line.lstrip(" ")) for line in cont if line.strip()]
        if not indents:
            return "", []
        width = min(indents)
        lines = [line[width:].rstrip() if line.strip() else "" for line in cont]
        text = "\n".join(lines) if header.group(1) == "|" else fold(lines)
        return text.strip("\n"), []
    if rest[:1] == '"':
        text = " ".join([rest, *(line.strip() for line in cont if line.strip())])
        match = re.match(r'^"((?:[^"\\]|\\.)*)"[ \t]*(#.*)?$', text)
        if not match:
            return None, ["unterminated or malformed double-quoted string"]
        try:
            return json.loads(f'"{match.group(1)}"'), []
        except ValueError:
            return match.group(1), []
    if rest[:1] == "'":
        text = " ".join([rest, *(line.strip() for line in cont if line.strip())])
        match = re.match(r"^'((?:[^']|'')*)'[ \t]*(#.*)?$", text)
        if not match:
            return None, ["unterminated or malformed single-quoted string"]
        return match.group(1).replace("''", "'"), []
    if rest[:1] in ("[", "{"):
        return Collection("flow"), []
    if rest:
        return parse_plain(" ".join([rest, *(line.strip() for line in cont if line.strip())]))
    first = next((line.strip() for line in cont if line.strip()), "")
    if not first:
        return None, []
    if first == "-" or first.startswith("- "):
        return Collection("sequence"), []
    if KEY_RE.match(first):
        return Collection("mapping"), []
    return parse_plain(" ".join(line.strip() for line in cont if line.strip()))


def parse_frontmatter(lines: list[str]) -> tuple[dict[str, object], list[str]]:
    """Parse the simple YAML mapping between the two '---' lines."""
    fields: dict[str, object] = {}
    problems: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        lineno = index + 2  # line 1 of the file is the opening '---'
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[0] in " \t":
            problems.append(f"frontmatter line {lineno}: unexpected indentation")
            index += 1
            continue
        match = KEY_RE.match(line)
        if not match:
            problems.append(f"frontmatter line {lineno}: expected 'key: value'")
            index += 1
            continue
        key, rest = match.group(1), (match.group(2) or "").strip()
        end = index + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
            if lines[end].startswith("\t"):
                problems.append(f"frontmatter line {end + 2}: tab indentation is invalid YAML")
            end += 1
        cont = lines[index + 1 : end]
        while cont and not cont[-1].strip():
            cont.pop()
        value, value_problems = parse_value(rest, cont)
        problems.extend(f"{key}: {problem}" for problem in value_problems)
        if key in fields:
            problems.append(f"duplicate key {key!r}")
        fields[key] = value
        index = end
    return fields, problems


def iter_links(lines: list[str]):
    """Yield (line number, raw target) for Markdown links outside code."""
    fence = ""
    for lineno, line in enumerate(lines, 1):
        opener = FENCE_RE.match(line)
        if fence:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                fence = ""
            continue
        if opener:
            fence = opener.group(1)
            continue
        scrubbed = INLINE_CODE_RE.sub("", line)
        for match in LINK_RE.finditer(scrubbed):
            yield lineno, match.group(1)
        definition = REF_DEF_RE.match(scrubbed)
        if definition:
            yield lineno, definition.group(1)


def exists_exact_case(path: Path, repo: Path) -> bool:
    """True when every component exists with this exact case inside repo."""
    try:
        parts = path.relative_to(repo).parts
    except ValueError:
        return False
    current = repo
    for part in parts:
        try:
            if part not in os.listdir(current):
                return False
        except OSError:
            return False
        current = current / part
    return True


def check_links(md_file: Path, lines: list[str], offset: int, repo: Path,
                report: Report, strict: bool) -> None:
    where_file = md_file.relative_to(repo).as_posix()
    for lineno, raw in iter_links(lines):
        target = raw.strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        where = f"{where_file}:{lineno + offset}"
        if DRIVE_RE.match(target) or target.startswith(("/", "\\")):
            report.error(where, f"absolute path in link {raw!r}; use a relative path")
            continue
        if not target or target.startswith("#") or SCHEME_RE.match(target):
            continue
        target = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not target:
            continue
        if "\\" in target:
            report.warn(where, f"use '/' instead of '\\' in link {raw!r}")
            target = target.replace("\\", "/")
        resolved = Path(os.path.normpath(md_file.parent / target))
        if not resolved.is_relative_to(repo):
            report.error(where, f"link leaves the repository root: {raw!r}")
            continue
        if exists_exact_case(resolved, repo):
            continue
        message = f"link target not found (exact case): {raw!r}"
        if strict:
            report.error(where, message)
        else:
            report.warn(where, message)


def scan_files(folder: Path, repo: Path, report: Report, strict: bool) -> None:
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(folder).parts:
            continue
        where = path.relative_to(repo).as_posix()
        data = path.read_bytes()
        if path.suffix.lower() in BINARY_SUFFIXES or b"\x00" in data[:8192]:
            report.warn(where, f"binary file ({len(data)} bytes)")
            continue
        text = data.decode("utf-8", errors="replace")
        body = text[1:] if text.startswith("\ufeff") else text
        bidi = sorted({f"U+{ord(ch):04X}" for ch in body if ch in BIDI_CHARS})
        if bidi:
            report.error(where, f"bidirectional control characters {', '.join(bidi)}")
        hidden = sorted({f"U+{ord(ch):04X}" for ch in body if ch in ZERO_WIDTH_CHARS})
        if hidden:
            report.warn(where, f"zero-width characters {', '.join(hidden)}")
        if path.suffix.lower() == ".md" and path.name != "SKILL.md":
            check_links(path, text.splitlines(), 0, repo, report, strict)


def check_skill(folder: Path, repo: Path, report: Report) -> Skill | None:
    rel = folder.relative_to(repo).as_posix()
    entries = os.listdir(folder)
    if "SKILL.md" not in entries:
        wrong = [entry for entry in entries if entry.lower() == "skill.md"]
        if wrong:
            report.error(rel, f"file must be named SKILL.md exactly (found {wrong[0]})")
        else:
            report.warn(rel, "no SKILL.md; VS Code ignores this folder")
        return None
    skill_file = folder / "SKILL.md"
    where = skill_file.relative_to(repo).as_posix()
    raw = skill_file.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        report.error(where, "UTF-8 BOM before the frontmatter; save without BOM")
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        report.error(where, f"not valid UTF-8 ({exc.reason} at byte {exc.start})")
        return Skill(rel, None, 0, "")
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        report.error(where, "frontmatter must start on line 1 with '---'")
        return Skill(rel, None, len(lines), "")
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if close is None:
        report.error(where, "frontmatter is not closed by a '---' line")
        return Skill(rel, None, 0, "")
    fields, problems = parse_frontmatter(lines[1:close])
    for problem in problems:
        report.error(where, problem)

    name = fields.get("name")
    if not isinstance(name, str) or not name:
        report.error(where, "name is missing or not a string")
        name = None
    else:
        if not NAME_RE.match(name):
            report.error(where, f"name {name!r} must match {NAME_RE.pattern}")
        if len(name) > MAX_NAME:
            report.error(where, f"name has {len(name)} characters (max {MAX_NAME})")
        if name != folder.name:
            report.error(where, f"name {name!r} differs from folder {folder.name!r}; VS Code skips it")

    description = fields.get("description")
    if not isinstance(description, str):
        report.error(where, "description is missing or not a string")
    else:
        length = len(description.strip())
        if not 1 <= length <= MAX_DESCRIPTION:
            report.error(where, f"description has {length} characters (allowed 1-{MAX_DESCRIPTION})")

    for key in BOOLEAN_KEYS:
        if key in fields and not isinstance(fields[key], bool):
            report.error(where, f"{key} must be true or false")
    if "compatibility" in fields:
        compatibility = fields["compatibility"]
        if not isinstance(compatibility, str) or not 1 <= len(compatibility.strip()) <= MAX_COMPATIBILITY:
            report.error(where, f"compatibility must be a string of 1-{MAX_COMPATIBILITY} characters")
    if "metadata" in fields and not isinstance(fields["metadata"], Collection):
        report.error(where, "metadata must be a mapping")
    if "context" in fields and fields["context"] not in ("inline", "fork"):
        report.warn(where, "context should be 'inline' or 'fork'")
    for key in sorted(set(fields) - KNOWN_KEYS):
        report.warn(where, f"unknown frontmatter key {key!r}")

    body = lines[close + 1 :]
    while body and not body[-1].strip():
        body.pop()
    if len(body) > MAX_BODY_LINES:
        report.warn(where, f"body has {len(body)} lines (recommended <= {MAX_BODY_LINES})")
    strict = folder.name.startswith(PROJECT_PREFIX)
    check_links(skill_file, body, close + 1, repo, report, strict)
    scan_files(folder, repo, report, strict)

    flags = []
    if fields.get("disable-model-invocation") is True:
        flags.append("manual-only")
    if fields.get("user-invocable") is False:
        flags.append("hidden-from-menu")
    if fields.get("context") == "fork":
        flags.append("fork")
    return Skill(rel, name, len(body), ",".join(flags) or "auto")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="repository root (default: parent of scripts/)")
    parser.add_argument("--list", action="store_true", help="print one line per skill")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    repo = args.root.resolve()
    if not repo.is_dir():
        parser.error(f"not a directory: {repo}")
    report = Report()
    skills: list[Skill] = []
    present: list[str] = []
    for root_name in SKILL_ROOTS:
        root = repo / root_name
        if not root.is_dir():
            continue
        present.append(root_name)
        for child in sorted(root.iterdir()):
            if child.is_dir():
                skill = check_skill(child, repo, report)
                if skill:
                    skills.append(skill)
            else:
                report.warn(child.relative_to(repo).as_posix(), "stray file in skill root")

    seen: dict[str, str] = {}
    for skill in skills:
        if skill.name is None:
            continue
        if skill.name in seen:
            report.error(skill.rel, f"duplicate skill name {skill.name!r} (also {seen[skill.name]})")
        else:
            seen[skill.name] = skill.rel

    if args.list:
        for skill in skills:
            print(f"SKILL   {skill.rel}  name={skill.name}  body_lines={skill.body_lines}  {skill.flags}")
    for line in report.errors:
        print(f"ERROR   {line}")
    for line in report.warnings:
        print(f"WARNING {line}")
    roots = ", ".join(present) if present else "none"
    print(f"Checked {len(skills)} skill(s) in {roots}: "
          f"{len(report.errors)} error(s), {len(report.warnings)} warning(s).")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
