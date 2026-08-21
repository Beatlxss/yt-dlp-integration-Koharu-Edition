import hashlib
import tempfile
import unittest
from pathlib import Path

from update_core import (
    ManifestFile,
    SemanticVersion,
    UpdateError,
    UpdateManifest,
    compare_dotted_versions,
    download_to_file,
    resolve_install_path,
    validate_relative_install_path,
    version_is_newer,
)


class SemanticVersionTests(unittest.TestCase):
    def test_semantic_version_precedence(self) -> None:
        self.assertTrue(version_is_newer("1.3.1", "1.3.0"))
        self.assertTrue(version_is_newer("1.3.1", "1.3.1-beta.2"))
        self.assertTrue(version_is_newer("1.3.1-beta.11", "1.3.1-beta.2"))
        self.assertFalse(version_is_newer("1.3.1-beta.2", "1.3.1"))
        self.assertEqual(SemanticVersion.parse("v1.3.1+build.7"), SemanticVersion.parse("1.3.1"))

    def test_invalid_semantic_version_is_rejected(self) -> None:
        with self.assertRaises(UpdateError):
            SemanticVersion.parse("1.03.1")


class DottedVersionTests(unittest.TestCase):
    def test_numeric_and_text_tokens_compare_in_order(self) -> None:
        self.assertEqual(compare_dotted_versions("2025.01.01", "2025.01.01"), 0)
        self.assertLess(compare_dotted_versions("2025.01.01", "2025.01.02"), 0)
        self.assertGreater(compare_dotted_versions("2025.01.02", "2025.01.01"), 0)
        self.assertLess(compare_dotted_versions("2025.01.01.dev", "2025.01.01.rc"), 0)


class ManifestSafetyTests(unittest.TestCase):
    def test_relative_paths_cannot_escape_install_folder(self) -> None:
        self.assertEqual(validate_relative_install_path("_internal/python3.dll"), "_internal/python3.dll")
        for unsafe_path in ("../outside.exe", "C:\\outside.exe", "\\\\server\\share\\file.exe", "folder/../file.exe", ".koharu-update/staged.exe"):
            with self.subTest(path=unsafe_path):
                with self.assertRaises(UpdateError):
                    validate_relative_install_path(unsafe_path)

        with tempfile.TemporaryDirectory() as temp_dir:
            target = resolve_install_path(temp_dir, "_internal/python3.dll")
            self.assertTrue(target.is_relative_to(Path(temp_dir).resolve()))

    def test_manifest_rejects_insecure_or_conflicting_entries(self) -> None:
        digest = "a" * 64
        base = {"path": "Naughty Koharu.exe", "url": "https://example.test/app.exe", "sha256": digest}
        with self.assertRaises(UpdateError):
            UpdateManifest.from_dict({"version": "1.3.1", "files": [{**base, "url": "http://example.test/app.exe"}]})
        with self.assertRaises(UpdateError):
            UpdateManifest.from_dict({"version": "1.3.1", "files": [base, {**base, "path": "naughty koharu.exe"}]})
        with self.assertRaises(UpdateError):
            UpdateManifest.from_dict({"version": "1.3.1", "files": [base], "deleted_files": ["Naughty Koharu.exe"]})


class VerifiedDownloadTests(unittest.TestCase):
    def test_local_test_download_verifies_hash_and_size(self) -> None:
        payload = b"verified updater test payload"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.bin"
            output = root / "output.bin"
            source.write_bytes(payload)
            downloaded = download_to_file(
                source.as_uri(),
                output,
                expected_sha256=digest,
                expected_size=len(payload),
                allow_file_urls=True,
            )
            self.assertEqual(downloaded, len(payload))
            self.assertEqual(output.read_bytes(), payload)

    def test_hash_mismatch_removes_partial_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.bin"
            output = root / "output.bin"
            source.write_bytes(b"not the expected content")
            with self.assertRaises(UpdateError):
                download_to_file(
                    source.as_uri(),
                    output,
                    expected_sha256="0" * 64,
                    expected_size=24,
                    allow_file_urls=True,
                )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()