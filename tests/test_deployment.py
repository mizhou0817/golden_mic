import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from deploy.verify_release_archive import REQUIRED_MEMBERS, validate_release_archive
from deploy.run_with_environment import parse_environment_file
from deploy.validate_environment_file import environment_schema_differences
from backend.instance_lock import InstanceLock, InstanceLockError
from tests.test_workspace_access import run_isolated_main_case


class ReleaseArchiveValidationTest(unittest.TestCase):
    def test_accepts_minimal_regular_file_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "release.tar.gz"
            _write_archive(archive_path, [(name, b"valid", "file") for name in REQUIRED_MEMBERS])

            validate_release_archive(archive_path)

    def test_rejects_path_traversal_symlink_and_secret_paths(self) -> None:
        cases = [
            ("../escape", "file", "Unsafe release archive path"),
            ("golden-mic/link", "symlink", "links/devices are forbidden"),
            ("golden-mic/.env", "file", "Forbidden release archive path"),
        ]
        for name, kind, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                archive_path = Path(directory) / "release.tar.gz"
                entries = [(member, b"valid", "file") for member in REQUIRED_MEMBERS]
                entries.append((name, b"invalid", kind))
                _write_archive(archive_path, entries)

                with self.assertRaisesRegex(RuntimeError, expected):
                    validate_release_archive(archive_path)

    def test_rejects_duplicate_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "release.tar.gz"
            entries = [(member, b"valid", "file") for member in REQUIRED_MEMBERS]
            duplicate = next(iter(REQUIRED_MEMBERS))
            entries.append((duplicate, b"duplicate", "file"))
            _write_archive(archive_path, entries)

            with self.assertRaisesRegex(RuntimeError, "Duplicate"):
                validate_release_archive(archive_path)


class StrictProductionSmokeTest(unittest.TestCase):
    def test_strict_production_http_contract(self) -> None:
        # Actual main lifespan/preflight in TEMP, with dynamic local media tools
        # and external transports blocked. X-Authenticated-User grants nothing;
        # production always uses anonymous CSRF plus per-task capabilities.
        run_isolated_main_case(self, "production_http")

    def test_anonymous_production_http_contract(self) -> None:
        run_isolated_main_case(self, "production_csrf_and_rate")


class EnvironmentFileParserTest(unittest.TestCase):
    def test_values_are_parsed_without_shell_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service.env"
            path.write_text(
                "PLAIN=alpha$HOME$(touch should-not-run)\n"
                "QUOTED='value with spaces and $HOME'\n"
                "EMPTY=\n",
                encoding="utf-8",
            )

            parsed = parse_environment_file(path)

            self.assertEqual(parsed["PLAIN"], "alpha$HOME$(touch should-not-run)")
            self.assertEqual(parsed["QUOTED"], "value with spaces and $HOME")
            self.assertEqual(parsed["EMPTY"], "")
            self.assertFalse((Path.cwd() / "should-not-run").exists())

    def test_production_template_exactly_covers_settings_schema(self) -> None:
        values = parse_environment_file(
            Path(__file__).resolve().parent.parent
            / "deploy"
            / "golden-mic.env.production.example"
        )

        missing, unknown = environment_schema_differences(values)

        self.assertEqual(missing, [])
        self.assertEqual(unknown, [])

    def test_duplicate_environment_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.env"
            path.write_text("APP_ENV=production\nAPP_ENV=development\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Duplicate"):
                parse_environment_file(path)


class InstanceLockTest(unittest.TestCase):
    def test_second_production_instance_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.lock"
            first = InstanceLock(path)
            second = InstanceLock(path)
            first.acquire()
            try:
                with self.assertRaises(InstanceLockError):
                    second.acquire()
            finally:
                first.release()

            second.acquire()
            second.release()


def _write_archive(path: Path, entries: list[tuple[str, bytes, str]]) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        for name, payload, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "golden-mic/backend/main.py"
                archive.addfile(info)
                continue
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


if __name__ == "__main__":
    unittest.main()
