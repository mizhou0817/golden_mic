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
  * allowed-tools that is not a space-separated string, or that pre-approves
    a shell or terminal tool (Bash, shell, PowerShell, runInTerminal, ...):
    project rule, every terminal command needs the user's approval
  * relative Markdown links in project skills (gm-*, golden-mic-*) that do not
    resolve (exact case), or any link that leaves the repository root
  * invisible or control characters in any scanned text file: bidirectional
    controls, Unicode tag characters, soft hyphen, invisible operators, C0/C1
    controls (the carriers of hidden instructions)
  * frontmatter keys hooks / mcp-servers: a skill must not carry them
  * REQUIRED_FLAGS: skills whose manual-only / hidden status is a project
    decision (find-bugs, diagnosing-bugs, playwright-cli, golden-mic-release,
    ...) must keep it, so a reinstall that drops the flag fails loudly
  * a script file that is not on the REVIEWED_SCRIPTS list
  * an executable or compiled file (.exe, .dll, .jar, .wasm, ...), a symlink or
    junction, and a NUL byte in a text file (it would stop the hidden-character scan)
  * a hooks / mcp-servers / allowed-tools key spelled another way (mcpServers,
    Hooks, Allowed-Tools): keys are compared without case, "-" and "_"
  * a project skill (gm-*, golden-mic-*) without a "## " section for when to
    use it, steps, an example and verification (task spec 5.1; REQUIRED_SECTIONS)
Warnings: body over 500 lines, zero-width characters, binary files, unknown
frontmatter keys, context other than inline/fork, folders without SKILL.md,
a code fence that is never closed, broken links (and absolute or out-of-repo
links) in third-party skills (their files are kept byte-for-byte, so known
upstream gaps are recorded in docs/AGENT_SKILLS.md instead).
Also scanned for invisible characters only: .github/copilot-instructions.md,
.agents/references and docs/agents.

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
PROJECT_PREFIXES = ("gm-", "golden-mic-")
# Tool names (lowercased, non-alphanumerics removed) that run arbitrary commands.
SHELL_TOOL_RE = re.compile(r"bash|shell|terminal|powershell|pwsh|cmd|command|exec|^z?sh$|^(?:ksh|fish|run|runtasks?|task|python|node)$")
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
# Characters nobody needs in a skill and that can hide an instruction from a human reader.
INVISIBLE_ERROR_CHARS = {
    *BIDI_CHARS,
    *(chr(c) for c in range(0xE0000, 0xE0080)),   # Unicode tag characters ("ASCII smuggling")
    *(chr(c) for c in range(0xE0100, 0xE01F0)),   # variation selectors supplement
    *(chr(c) for c in range(0x2061, 0x2065)),     # invisible math operators
    "\u00ad", "\u180e", "\u3164", "\uffa0", "\u2028", "\u2029", "\u007f",
    *(chr(c) for c in range(0x80, 0xA0)),         # C1 controls
    *(chr(c) for c in (*range(0, 9), 11, 12, *range(14, 32))),  # C0 controls except TAB, LF, CR
}
# Policy flags that are project decisions (docs/AGENT_SKILLS.md section 3); a reinstall must not drop them.
REQUIRED_FLAGS = {
    "find-bugs": {"disable-model-invocation": True},
    "diagnosing-bugs": {"disable-model-invocation": True},
    "playwright-cli": {"disable-model-invocation": True},
    "golden-mic-release": {"disable-model-invocation": True},
    "gm-ai-labeling": {"disable-model-invocation": True},
    "gm-quality-benchmark": {"disable-model-invocation": True},
    "golden-mic-backend-map": {"user-invocable": False},
    "gm-news-integrity": {"user-invocable": False},
}
# Compared after lower-casing and dropping "-" and "_": mcpServers, mcp_servers and Hooks are the same keys.
FORBIDDEN_KEYS = {"hooks", "mcpservers"}
# Task spec 5.1: every project skill says when (not) to use it and gives steps, an example and how to verify.
REQUIRED_SECTIONS = (
    ("when to use / not to use", ("何时使用", "适用")),
    ("steps", ("步骤",)),
    ("example", ("示例",)),
    ("verification", ("验证",)),
)
SCRIPT_SUFFIXES = {".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".py", ".js", ".mjs", ".cjs", ".ts", ".vbs"}
# Reviewed scripts that skill folders may ship (docs/AGENT_SKILLS.md section 5). Anything else is an error.
REVIEWED_SCRIPTS = {".agents/skills/diagnosing-bugs/scripts/hitl-loop.template.sh"}
# Not skills, but text the agent reads: checked for invisible characters only.
EXTRA_TEXT_PATHS = (".github/copilot-instructions.md", ".agents/references", "docs/agents")
BINARY_SUFFIXES = {".bin", ".zip", ".gz", ".tgz"}
# A skill folder has no business shipping native or compiled code, and unlike a script it can not be read for review.
EXECUTABLE_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".node", ".wasm", ".com", ".scr", ".msi", ".jar", ".pyc", ".pyd", ".lnk", ".app"}
# Files that must be readable text: a NUL byte in one stops the hidden-character scan, so it is an error there.
TEXT_SUFFIXES = {"", ".md", ".markdown", ".txt", ".json", ".yml", ".yaml", ".toml", ".html", ".htm", ".css", ".csv", ".xml", ".ini", ".cfg", ".rst", ".svg"}
REPARSE_POINT = 0x400  # Windows symlinks and junctions

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
    if text[:1] in ("@", "`", "&", "*", "!", "%") or text.startswith(("- ", "? ", ": ")):
        problems.append(f"plain value may not start with {text[:1]!r}; quote it")
    comment = re.search(r"\s#", text)
    if comment:
        text = text[: comment.start()].rstrip()
    if re.search(r":(\s|$)", text):
        problems.append('unquoted ": " in a plain value is invalid YAML; quote the value')
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
        return Collection("flow-mapping" if rest[:1] == "{" else "flow-sequence"), []
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


def unclosed_fence(lines: list[str]) -> int | None:
    """Line number of a code fence that is never closed (links after it are not checked)."""
    fence, opened_at = "", 0
    for lineno, line in enumerate(lines, 1):
        if fence:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                fence = ""
            continue
        opener = FENCE_RE.match(line)
        if opener:
            fence, opened_at = opener.group(1), lineno
    return opened_at if fence else None


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
    open_fence = unclosed_fence(lines)
    if open_fence:
        report.warn(f"{where_file}:{open_fence + offset}", "code fence is never closed; later links are not checked")
    complain = report.error if strict else report.warn
    for lineno, raw in iter_links(lines):
        target = raw.strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        where = f"{where_file}:{lineno + offset}"
        if DRIVE_RE.match(target) or target.startswith(("/", "\\")):
            complain(where, f"absolute path in link {raw!r}; use a relative path")
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
            complain(where, f"link leaves the repository root: {raw!r}")
            continue
        if exists_exact_case(resolved, repo):
            continue
        message = f"link target not found (exact case): {raw!r}"
        if strict:
            report.error(where, message)
        else:
            report.warn(where, message)


def normalized_key(key: str) -> str:
    return re.sub(r"[-_]", "", key.lower())


def is_link(path: Path) -> bool:
    """A symlink or a Windows junction (os.path.islink does not see junctions); unreadable counts as a link."""
    try:
        return path.is_symlink() or bool(getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0) & REPARSE_POINT)
    except OSError:
        return True


def check_hidden(where: str, text: str, report: Report) -> None:
    body = text[1:] if text.startswith("\ufeff") else text
    bad = sorted({f"U+{ord(ch):04X}" for ch in body if ch in INVISIBLE_ERROR_CHARS})
    if bad:
        report.error(where, f"invisible or control characters {', '.join(bad)}")
    hidden = sorted({f"U+{ord(ch):04X}" for ch in body if ch in ZERO_WIDTH_CHARS})
    if hidden:
        report.warn(where, f"zero-width characters {', '.join(hidden)}")


def scan_extra_text(repo: Path, report: Report) -> None:
    """Text the agent reads that is not a skill folder: invisible characters only."""
    for entry in EXTRA_TEXT_PATHS:
        target = repo / entry
        files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file()) if target.is_dir() else []
        for path in files:
            data = path.read_bytes()
            if b"\x00" in data[:8192]:
                report.error(path.relative_to(repo).as_posix(), "NUL byte in a text file: the hidden-character scan can not read it")
                continue
            check_hidden(path.relative_to(repo).as_posix(), data.decode("utf-8", errors="replace"), report)


def scan_files(folder: Path, repo: Path, report: Report, strict: bool) -> None:
    for current, dirs, names in os.walk(folder, followlinks=False):
        here = Path(current)
        dirs.sort()
        for entry in list(dirs):
            if entry == ".git":
                dirs.remove(entry)
            elif is_link(here / entry):
                report.error((here / entry).relative_to(repo).as_posix(),
                             "symlink or junction to a folder inside a skill; it can lead outside the repository")
                dirs.remove(entry)
        for name in sorted(names):
            path = here / name
            where = path.relative_to(repo).as_posix()
            suffix = path.suffix.lower()
            if is_link(path):
                report.error(where, "symlink inside a skill folder; it can point at a file outside the repository")
                continue
            if suffix in SCRIPT_SUFFIXES and where not in REVIEWED_SCRIPTS:
                report.error(where, "script file that is not on the reviewed list (REVIEWED_SCRIPTS); "
                                    "review it, then record it in docs/AGENT_SKILLS.md")
            try:
                data = path.read_bytes()
            except OSError as exc:
                report.error(where, f"can not read the file ({exc.strerror or exc})")
                continue
            if suffix in EXECUTABLE_SUFFIXES:
                report.error(where, f"executable or compiled file ({len(data)} bytes); a skill ships text, not binaries")
                continue
            has_nul = b"\x00" in data[:8192]
            if has_nul and (suffix in TEXT_SUFFIXES or suffix in SCRIPT_SUFFIXES):
                report.error(where, "NUL byte in a text file: the hidden-character scan can not read it")
                continue
            if has_nul or suffix in BINARY_SUFFIXES:
                report.warn(where, f"binary file ({len(data)} bytes)")
                continue
            text = data.decode("utf-8", errors="replace")
            check_hidden(where, text, report)
            if suffix == ".md" and path.name != "SKILL.md":
                check_links(path, text.splitlines(), 0, repo, report, strict)


def second_level_headings(body: list[str]) -> list[str]:
    """Text of the "## " headings outside code fences."""
    headings: list[str] = []
    fence: str | None = None
    for line in body:
        opener = FENCE_RE.match(line)
        if opener:
            marker = opener.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None and line.startswith("## "):
            headings.append(line[3:].strip())
    return headings


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
    if is_link(skill_file):
        report.error(where, "SKILL.md is a symlink or junction; it can point outside the repository")
        return Skill(rel, None, 0, "")
    try:
        raw = skill_file.read_bytes()
    except OSError as exc:
        report.error(where, f"can not read SKILL.md ({exc.strerror or exc})")
        return Skill(rel, None, 0, "")
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
        elif len(description.strip().encode("utf-16-le")) // 2 > MAX_DESCRIPTION:
            report.error(where, f"description is over {MAX_DESCRIPTION} UTF-16 units (VS Code counts those)")

    for key in BOOLEAN_KEYS:
        if key in fields and not isinstance(fields[key], bool):
            report.error(where, f"{key} must be true or false")
    if "compatibility" in fields:
        compatibility = fields["compatibility"]
        if not isinstance(compatibility, str) or not 1 <= len(compatibility.strip()) <= MAX_COMPATIBILITY:
            report.error(where, f"compatibility must be a string of 1-{MAX_COMPATIBILITY} characters")
    if "metadata" in fields and not (isinstance(fields["metadata"], Collection) and fields["metadata"].kind in ("mapping", "flow-mapping")):
        report.error(where, "metadata must be a mapping")
    tools_key = next((key for key in fields if normalized_key(key) == "allowedtools"), None)
    if tools_key is not None:
        if tools_key != "allowed-tools":
            report.error(where, f"frontmatter key {tools_key!r}: spell it allowed-tools; another spelling may still be honored by some agents")
        tools = fields[tools_key]
        if not isinstance(tools, str):
            report.error(where, "allowed-tools must be a space-separated string")
        else:
            # Split on spaces outside parentheses: "Bash(npx playwright:*)" is one tool.
            for token in re.findall(r"[^\s(]+(?:\([^)]*\))?", tools):
                base = re.sub(r"[^a-z0-9]", "", token.split("(", 1)[0].lower())
                if "*" in token.split("(", 1)[0]:
                    report.error(where, f"allowed-tools wildcard {token!r} pre-approves every tool it matches")
                elif SHELL_TOOL_RE.search(base):
                    report.error(where, f"allowed-tools pre-approves a shell or terminal tool "
                                        f"{token!r}; terminal commands need per-command approval")
    if "context" in fields and fields["context"] not in ("inline", "fork"):
        report.warn(where, "context should be 'inline' or 'fork'")
    forbidden = sorted(key for key in fields if normalized_key(key) in FORBIDDEN_KEYS)
    for key in forbidden:
        report.error(where, f"frontmatter key {key!r}: a skill must not carry hooks or MCP servers")
    for key in sorted(set(fields) - KNOWN_KEYS - set(forbidden) - {tools_key}):
        report.warn(where, f"unknown frontmatter key {key!r}")
    for key, expected in REQUIRED_FLAGS.get(folder.name, {}).items():
        if fields.get(key) is not expected:
            report.error(where, f"project policy (docs/AGENT_SKILLS.md section 3): {folder.name} must set {key}: "
                                f"{str(expected).lower()}; a reinstall may have dropped it")

    body = lines[close + 1 :]
    while body and not body[-1].strip():
        body.pop()
    if len(body) > MAX_BODY_LINES:
        report.warn(where, f"body has {len(body)} lines (recommended <= {MAX_BODY_LINES})")
    strict = folder.name.startswith(PROJECT_PREFIXES)
    check_links(skill_file, body, close + 1, repo, report, strict)
    scan_files(folder, repo, report, strict)
    if strict:
        headings = second_level_headings(body)
        for label, words in REQUIRED_SECTIONS:
            if not any(word in heading for heading in headings for word in words):
                report.error(where, f"project skill has no '## ' section for {label} (task spec 5.1): "
                                    f"expected a heading containing {' or '.join(words)}")

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

    scan_extra_text(repo, report)
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
