"""Source-only template contracts: no config import/Settings/dotenv evaluation.

Read ONLY the two nonsecret templates inventoried by run_v2_validation and the
Settings AST. Never inspect an operator environment, credentials or model files.
"""
from __future__ import annotations

import ast
import math
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = (".env.example", "deploy/golden-mic.env.production.example")
# The 26 previously implicit fields. Defaults are derived from real source, not
# copied from private configuration or production-only sizing/path examples.
COMPLETED_FIELDS = frozenset("""
ASR_CACHE_ENABLED ASR_CACHE_DIR ASR_CACHE_TTL_HOURS ASR_CACHE_MAX_ENTRIES
ASR_SPARSE_RETRY_ENABLED ENTITY_VERIFICATION_ENABLED ENTITY_VERIFICATION_MAX_SHOTS
ENTITY_VERIFICATION_MIN_CONFIDENCE MUSIC_LIBRARY_DIR MUSIC_BED_TARGET_LUFS
MUSIC_DUCK_RATIO MOTION_ZOOM_RATIO COLOR_CONSISTENCY_TARGET_LUFS MIN_FREE_DISK_GB
TASK_DISK_RESERVATION_MULTIPLIER MEDIA_COMMAND_TIMEOUT_SECONDS MAX_TOTAL_UPLOAD_MB
MAX_CONCURRENT_UPLOADS MAX_SOURCE_DURATION_SECONDS_PER_FILE
MAX_TOTAL_SOURCE_DURATION_SECONDS MAX_SOURCE_WIDTH MAX_SOURCE_HEIGHT
MAX_SOURCE_FRAME_RATE SHUTDOWN_GRACE_SECONDS TASK_RATE_LIMIT_PER_HOUR DATA_DIR
""".split())
DEV_BOOLEAN_POLICY = {
    "ENABLE_API_DOCS": "true", "ENFORCE_ORIGIN_CHECK": "false",
    "ASR_CACHE_ENABLED": "true", "ASR_SPARSE_RETRY_ENABLED": "false",
    "LOCAL_SPEECH_LICENSE_REVIEWED": "false", "LOCAL_SPEECH_REQUIRED": "false",
    "SYNC_SOUND_ENABLED": "true", "SYNC_SOUND_VAD_ENABLED": "true",
    "ENTITY_VERIFICATION_ENABLED": "false", "VIDEO_EMBEDDING_ENABLED": "true",
    "VIDEO_EMBEDDING_ADAPTIVE_CONCURRENCY": "false", "GENERATIVE_FILL_ENABLED": "false",
}


def source_fields() -> dict[str, ast.AnnAssign]:
    tree = ast.parse((ROOT / "backend/config.py").read_text(encoding="utf-8"))
    settings = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings")
    return {n.target.id.upper(): n for n in settings.body
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}


def template_text(name: str) -> str:
    if name not in TEMPLATES:
        raise ValueError("not an inventoried template")
    path = ROOT / name
    if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
        raise ValueError("template indirection or missing template")
    return path.read_text(encoding="utf-8")


def parse_template(text: str, fields: dict[str, ast.AnnAssign]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if match is None:
            raise ValueError("malformed assignment")
        key, value = match.groups()
        if key in values:
            raise ValueError("duplicate assignment")
        if key not in fields:
            raise ValueError("unknown field")
        values[key] = value
    if values.keys() != fields.keys():
        raise ValueError("missing fields")
    return values


def source_default(field: ast.AnnAssign) -> object:
    node = field.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "Field":
            node = next(k.value for k in node.keywords if k.arg == "default")
        elif node.func.id in {"Path", "SecretStr"}:
            node = node.args[0]
        else:
            raise ValueError("unreviewed default expression")
    if node is None:
        raise ValueError("missing source default")
    return ast.literal_eval(node)


def validate_values(values: dict[str, str], fields: dict[str, ast.AnnAssign]) -> None:
    for key, field in fields.items():
        value = values[key]
        annotation = ast.unparse(field.annotation)
        if annotation == "bool":
            if value not in {"true", "false"}:
                raise ValueError("boolean must be explicit true or false")
        elif annotation in {"int", "float"}:
            number = int(value) if annotation == "int" else float(value)
            if not math.isfinite(number):
                raise ValueError("nonfinite number")
            if isinstance(field.value, ast.Call):
                for keyword in field.value.keywords:
                    if keyword.arg in {"ge", "gt", "le", "lt"}:
                        bound = ast.literal_eval(keyword.value)
                        ok = {"ge": number >= bound, "gt": number > bound,
                              "le": number <= bound, "lt": number < bound}[keyword.arg]
                        if not ok:
                            raise ValueError("source numeric bound")
        elif isinstance(field.annotation, ast.Subscript) and isinstance(field.annotation.value, ast.Name) \
                and field.annotation.value.id == "Literal":
            if value not in ast.literal_eval(field.annotation.slice):
                raise ValueError("source literal choices")
        elif "Path" in annotation:
            # Empty optional Path values can become cwd, not None. Never stat
            # these example destinations (especially operator model paths).
            # This one new setting has an explicit blank -> None validator;
            # retain the historical rejection for all model/storage paths.
            if key == "LOCAL_SPEECH_BUNDLE_MANIFEST_PATH" and value == "":
                continue
            if not value.strip() or value.strip().lower() in {"none", "null"}:
                raise ValueError("path must be explicit")
        elif annotation not in {"str", "SecretStr"}:
            raise ValueError("unreviewed field type")


class ConfigTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fields = source_fields()
        self.dev_text = template_text(TEMPLATES[0])
        self.dev = parse_template(self.dev_text, self.fields)

    def test_complete_inventory_matches_both_templates(self):
        for name in TEMPLATES:
            with self.subTest(template=name):
                values = parse_template(template_text(name), self.fields)
                self.assertEqual(set(values), set(self.fields))
                validate_values(values, self.fields)

    def test_inventory_paths_match_static_runner(self):
        tree = ast.parse((ROOT / "tests/run_v2_validation.py").read_text(encoding="utf-8"))
        inventory = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "static_inventory")
        loops = [n for n in ast.walk(inventory) if isinstance(n, ast.For)
                 and isinstance(n.target, ast.Name) and n.target.id == "name"
                 and isinstance(n.iter, ast.Tuple)]
        self.assertEqual([ast.literal_eval(n.iter) for n in loops], [TEMPLATES])

    def test_missing_unknown_duplicate_and_malformed_assignments_rejected(self):
        variants = (
            (self.dev_text.replace("APP_ENV=development\n", ""), "missing"),
            (self.dev_text + "\nUNRECOGNIZED_OPTION=false\n", "unknown"),
            (self.dev_text + "\nAPP_ENV=development\n", "duplicate"),
            (self.dev_text + "\nexport APP_ENV=development\n", "malformed"),
        )
        for text, reason in variants:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                parse_template(text, self.fields)

    def test_explicit_boolean_safety_policy(self):
        boolean_fields = {k for k, v in self.fields.items() if ast.unparse(v.annotation) == "bool"}
        self.assertEqual(boolean_fields, DEV_BOOLEAN_POLICY.keys())
        self.assertEqual({k: self.dev[k] for k in boolean_fields}, DEV_BOOLEAN_POLICY)
        for invalid in ("", "1", "0", "True", "yes", "false # implicit"):
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "boolean"):
                validate_values({**self.dev, "LOCAL_SPEECH_REQUIRED": invalid}, self.fields)

    def test_completed_fields_follow_source_defaults_except_documented_opt_outs(self):
        self.assertEqual(len(COMPLETED_FIELDS), 26)
        for key in sorted(COMPLETED_FIELDS):
            with self.subTest(field=key):
                default = source_default(self.fields[key])
                if key in {"ENTITY_VERIFICATION_ENABLED", "ASR_SPARSE_RETRY_ENABLED"}:
                    self.assertIs(default, True)
                    self.assertEqual(self.dev[key], "false")
                elif isinstance(default, bool):
                    self.assertEqual(self.dev[key], str(default).lower())
                elif isinstance(default, (int, float)):
                    self.assertEqual(float(self.dev[key]), default)
                else:
                    self.assertEqual(self.dev[key], default)
        self.assertIs(source_default(self.fields["LOCAL_SPEECH_REQUIRED"]), False)

    def test_development_not_production_storage_or_policy(self):
        self.assertEqual(self.dev["APP_ENV"], "development")
        self.assertEqual(self.dev["QUALITY_GATE_MODE"], "warn")
        self.assertEqual(self.dev["TASK_TTL_HOURS"], "0")
        for key, expected in (("DATA_DIR", "data/tasks"), ("ASR_CACHE_DIR", "data/cache/asr")):
            self.assertEqual(self.dev[key], expected)
        # Source model paths default to None; intentional nonblank inert examples
        # document opt-in destinations, not existing models or reviewed licenses.
        for key in ("LOCAL_SPEAKER_MODEL_PATH", "LOCAL_ALIGNMENT_MODEL_PATH"):
            self.assertIsNone(source_default(self.fields[key]))
            self.assertTrue(self.dev[key].startswith("private-models/"))

    def test_templates_contain_only_blank_or_placeholder_credentials(self):
        keys = {key for key in self.fields if re.search(
            r"(?:API_KEYS?|APP_KEY|ACCESS_TOKEN|SESSION_SECRET|APP_ID|ASR_CLUSTER_ID)$", key)}
        self.assertTrue(keys)
        for name in TEMPLATES:
            values = parse_template(template_text(name), self.fields)
            for key in keys:
                value = values[key]
                with self.subTest(template=name, field=key):
                    # Boolean assertions deliberately never print credential values.
                    self.assertTrue(value == "" if name == TEMPLATES[0]
                                    else value == "" or value.startswith("CHANGE_ME"))

    def test_source_numeric_and_literal_bounds_reject_invalid_values(self):
        for key, value in (("MAX_CONCURRENT_UPLOADS", "0"), ("MAX_TOTAL_UPLOAD_MB", "-1"),
                           ("MOTION_ZOOM_RATIO", "0.26"), ("MIN_FREE_DISK_GB", "nan"),
                           ("APP_ENV", "enabled"), ("LOCAL_ALIGNMENT_MODEL_PATH", "")):
            with self.subTest(field=key), self.assertRaises(ValueError):
                validate_values({**self.dev, key: value}, self.fields)

    def test_development_cross_field_capacity_and_shutdown_constraints(self):
        for lower, upper in (("MAX_UPLOAD_MB", "MAX_TOTAL_UPLOAD_MB"),
                             ("MAX_SOURCE_DURATION_SECONDS_PER_FILE", "MAX_TOTAL_SOURCE_DURATION_SECONDS"),
                             ("MEDIA_COMMAND_TIMEOUT_SECONDS", "SHUTDOWN_GRACE_SECONDS"),
                             ("VIDEO_EMBEDDING_MIN_CONCURRENCY", "VIDEO_EMBEDDING_GLOBAL_CONCURRENCY"),
                             ("VIDEO_EMBEDDING_GLOBAL_CONCURRENCY", "VIDEO_EMBEDDING_GLOBAL_MAX_CONCURRENCY"),
                             ("ANONYMOUS_SESSION_TASK_RATE_LIMIT_PER_HOUR", "ANONYMOUS_IP_TASK_RATE_LIMIT_PER_HOUR"),
                             ("ANONYMOUS_IP_TASK_RATE_LIMIT_PER_HOUR", "ANONYMOUS_GLOBAL_TASK_RATE_LIMIT_PER_HOUR")):
            self.assertLessEqual(float(self.dev[lower]), float(self.dev[upper]))

    def test_only_manifest_field_allows_exact_blank(self):
        self.assertIsNone(source_default(self.fields["LOCAL_SPEECH_BUNDLE_MANIFEST_PATH"]))
        validate_values({**self.dev, "LOCAL_SPEECH_BUNDLE_MANIFEST_PATH": ""}, self.fields)
        for key, value in (("LOCAL_SPEECH_BUNDLE_MANIFEST_PATH", " "),
                           ("LOCAL_SPEECH_BUNDLE_MANIFEST_PATH", "None"),
                           ("LOCAL_SPEECH_BUNDLE_MANIFEST_PATH", "null"),
                           ("LOCAL_SPEAKER_MODEL_PATH", ""),
                           ("LOCAL_ALIGNMENT_MODEL_PATH", ""), ("DATA_DIR", "")):
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "path must be explicit"):
                validate_values({**self.dev, key: value}, self.fields)


if __name__ == "__main__":
    unittest.main()