import hashlib
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from update_core import UpdateError, UpdateManifest
from updater import ProgressReporter, _is_production_install, _perform_update, apply_manifest_update


class TransactionTests(unittest.TestCase):
    def test_stages_verified_files_and_only_deletes_explicit_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_dir = root / "install"
            source_dir = root / "release"
            install_dir.mkdir()
            source_dir.mkdir()

            executable = install_dir / "Naughty Koharu.exe"
            obsolete = install_dir / "_internal" / "obsolete.dll"
            unrelated = install_dir / "notes.txt"
            obsolete.parent.mkdir()
            executable.write_bytes(b"old executable")
            obsolete.write_bytes(b"obsolete")
            unrelated.write_bytes(b"keep me")

            replacement = source_dir / "Naughty Koharu.exe"
            replacement.write_bytes(b"new executable")
            manifest = UpdateManifest.from_dict(
                {
                    "version": "1.3.1",
                    "test_only": True,
                    "files": [
                        {
                            "path": "Naughty Koharu.exe",
                            "url": replacement.as_uri(),
                            "sha256": hashlib.sha256(replacement.read_bytes()).hexdigest(),
                            "size": replacement.stat().st_size,
                        }
                    ],
                    "deleted_files": ["_internal/obsolete.dll"],
                },
                allow_file_urls=True,
            )

            changed, deleted = apply_manifest_update(install_dir, manifest, allow_file_urls=True)
            self.assertEqual([item.path for item in changed], ["Naughty Koharu.exe"])
            self.assertEqual(deleted, ("_internal/obsolete.dll",))
            self.assertEqual(executable.read_bytes(), b"new executable")
            self.assertFalse(obsolete.exists())
            self.assertEqual(unrelated.read_bytes(), b"keep me")
            self.assertFalse((install_dir / ".koharu-update").exists())

    def test_bad_download_leaves_existing_file_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_dir = root / "install"
            install_dir.mkdir()
            executable = install_dir / "Naughty Koharu.exe"
            executable.write_bytes(b"known good executable")
            source = root / "bad-release.exe"
            source.write_bytes(b"corrupt payload")
            manifest = UpdateManifest.from_dict(
                {
                    "version": "1.3.1",
                    "test_only": True,
                    "files": [
                        {
                            "path": "Naughty Koharu.exe",
                            "url": source.as_uri(),
                            "sha256": "0" * 64,
                            "size": source.stat().st_size,
                        }
                    ],
                },
                allow_file_urls=True,
            )

            with self.assertRaises(UpdateError):
                apply_manifest_update(install_dir, manifest, allow_file_urls=True)
            self.assertEqual(executable.read_bytes(), b"known good executable")
            self.assertFalse((install_dir / ".koharu-update").exists())

    def test_parent_timeout_does_not_restart_a_second_app(self) -> None:
        args = Namespace(
            install_dir=tempfile.gettempdir(),
            manifest="does-not-matter.json",
            parent_pid=999999,
            wait_timeout=1.0,
            test_mode=False,
            allow_production_install=False,
            no_launch=False,
        )
        with patch("updater.wait_for_process_exit", return_value=False):
            result = _perform_update(args, ProgressReporter())
        self.assertFalse(result.success)
        self.assertFalse(result.restart_application)

    def test_registered_custom_install_is_protected_in_test_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir) / "custom-install"
            install_dir.mkdir()
            with patch("updater._known_production_install_locations", return_value=(install_dir,)):
                self.assertTrue(_is_production_install(install_dir))


if __name__ == "__main__":
    unittest.main()