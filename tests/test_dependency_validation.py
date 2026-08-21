import tempfile
import unittest
from pathlib import Path

from main import _is_git_lfs_pointer, _is_valid_windows_executable


class DependencyValidationTests(unittest.TestCase):
    def test_git_lfs_pointer_is_not_a_windows_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "yt-dlp.exe"
            candidate.write_text(
                "version https://git-lfs.github.com/spec/v1\n"
                "oid sha256:0123456789abcdef\n"
                "size 1\n",
                encoding="ascii",
            )
            self.assertTrue(_is_git_lfs_pointer(candidate))
            self.assertFalse(_is_valid_windows_executable(candidate))

    def test_minimal_pe_headers_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "valid.exe"
            candidate.write_bytes(b"MZ" + (b"\0" * 58) + (64).to_bytes(4, "little") + b"PE\0\0")
            self.assertFalse(_is_git_lfs_pointer(candidate))
            self.assertTrue(_is_valid_windows_executable(candidate))


if __name__ == "__main__":
    unittest.main()