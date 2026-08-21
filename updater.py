"""Standalone transactional updater for the installed Naughty Koharu build."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Optional, TypeAlias

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception:  # pragma: no cover - only used when Tcl/Tk is unavailable
    tk = None
    messagebox = None
    ttk = None

from app_version import APP_EXECUTABLE_NAME
from update_core import (
    DEFAULT_NETWORK_TIMEOUT_SECONDS,
    ManifestFile,
    UpdateError,
    UpdateManifest,
    compare_dotted_versions,
    download_to_file,
    file_matches_manifest,
    format_byte_count,
    load_manifest_file,
    resolve_install_path,
)


_STAGING_DIRECTORY_NAME = ".koharu-update"
_JOURNAL_FILE_NAME = "transaction.json"
_LOG_FILE_NAME = "ytdlp-onefile.log"
_UNINSTALL_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{9A7A2F8E-9A3C-4F76-9E7E-2F2C7A7C37E1}_is1"

_ProgressEvent: TypeAlias = (
    tuple[Literal["status"], str, None]
    | tuple[Literal["progress"], int, Optional[int]]
    | tuple[Literal["done"], None, None]
)


def _log(level: str, message: str, **fields: object) -> None:
    """Write updater events to the same per-user log as the application."""

    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    details = " ".join(f"{key}={_log_value(value, key)}" for key, value in fields.items() if value is not None)
    line = f"[{timestamp}] [{level.upper():<5}] [UPDATER ] [-      ] {message}"
    if details:
        line += " " + details
    try:
        log_path = Path(os.environ.get("TEMP", str(Path.home()))) / _LOG_FILE_NAME
        with log_path.open("a", encoding="utf-8", errors="replace") as stream:
            stream.write(line + "\n")
    except OSError:
        pass


def _log_value(value: object, key: str) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    if key.lower() == "url":
        try:
            from urllib.parse import urlsplit, urlunsplit

            parsed = urlsplit(text)
            text = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        except Exception:
            pass
    return json.dumps(text, ensure_ascii=True) if any(character.isspace() for character in text) else text


class ProgressReporter:
    def status(self, text: str) -> None:
        return

    def progress(self, received: int, total: Optional[int]) -> None:
        return


@dataclass
class _WorkerOutcome:
    result: object | None = None
    error: BaseException | None = None


class TkProgressReporter(ProgressReporter):
    """A small standard-library progress window with a non-GUI fallback."""

    def __init__(self, enabled: bool) -> None:
        self._enabled = bool(enabled and tk is not None and ttk is not None)
        self._events: queue.Queue[_ProgressEvent] = queue.Queue()

    def status(self, text: str) -> None:
        self._events.put(("status", str(text), None))

    def progress(self, received: int, total: Optional[int]) -> None:
        self._events.put(("progress", int(received), total))

    def run(self, worker: Callable[[], object]) -> object:
        if not self._enabled or tk is None or ttk is None:
            return worker()

        tk_module = tk
        ttk_module = ttk
        messagebox_module = messagebox
        try:
            root = tk_module.Tk()
            root.title("Naughty Koharu Update")
            root.resizable(False, False)
            root.geometry("420x125")
            root.protocol("WM_DELETE_WINDOW", lambda: None)

            frame = ttk_module.Frame(root, padding=18)
            frame.pack(fill="both", expand=True)
            label_var = tk_module.StringVar(value="Preparing update...")
            ttk_module.Label(frame, textvariable=label_var, wraplength=380).pack(fill="x", pady=(0, 12))
            bar = ttk_module.Progressbar(frame, orient="horizontal", length=380, mode="determinate", maximum=100)
            bar.pack(fill="x")
            detail_var = tk_module.StringVar(value="")
            ttk_module.Label(frame, textvariable=detail_var).pack(anchor="w", pady=(8, 0))

            outcome = _WorkerOutcome()

            def run_worker() -> None:
                try:
                    outcome.result = worker()
                except BaseException as exc:  # pragma: no cover - protective UI boundary
                    outcome.error = exc
                finally:
                    self._events.put(("done", None, None))

            threading.Thread(target=run_worker, daemon=True).start()

            def drain_events() -> None:
                done = False
                while True:
                    try:
                        event = self._events.get_nowait()
                    except queue.Empty:
                        break
                    if event[0] == "status":
                        label_var.set(event[1])
                    elif event[0] == "progress":
                        received = event[1]
                        total = event[2] if event[2] is not None and event[2] > 0 else None
                        if total is None:
                            bar.configure(mode="indeterminate")
                            bar.start(12)
                            detail_var.set(f"Downloaded {format_byte_count(received)}")
                        else:
                            bar.stop()
                            bar.configure(mode="determinate", value=max(0, min(100, int((received / total) * 100))))
                            detail_var.set(f"{format_byte_count(received)} of {format_byte_count(total)}")
                    elif event[0] == "done":
                        done = True

                if done:
                    error = outcome.error
                    result = outcome.result
                    if error is not None:
                        label_var.set("The update failed.")
                        if messagebox_module is not None:
                            messagebox_module.showerror("Naughty Koharu Update", str(error), parent=root)
                    elif isinstance(result, UpdateResult) and not result.success:
                        label_var.set("The update failed.")
                        if messagebox_module is not None:
                            messagebox_module.showerror("Naughty Koharu Update", result.message, parent=root)
                    else:
                        label_var.set("Update installed. Restarting Naughty Koharu...")
                        bar.stop()
                        bar.configure(mode="determinate", value=100)
                    root.after(700, root.destroy)
                    return
                root.after(80, drain_events)

            root.after(80, drain_events)
            root.mainloop()
            if outcome.error is not None:
                raise outcome.error
            return outcome.result
        except Exception as exc:  # pragma: no cover - Tk availability differs by Windows install
            _log("WARN", "Progress window unavailable", error=exc)
            return worker()


@dataclass(frozen=True)
class UpdateResult:
    success: bool
    message: str
    detail: str = ""
    restart_application: bool = False


@dataclass(frozen=True)
class StagedFile:
    manifest_file: ManifestFile
    downloaded_path: Path


def _staging_dir(install_dir: Path) -> Path:
    root = install_dir.resolve(strict=False)
    staging = (root / _STAGING_DIRECTORY_NAME).resolve(strict=False)
    try:
        staging.relative_to(root)
    except ValueError as exc:
        raise UpdateError("The updater staging folder is unsafe.") from exc
    if staging.is_symlink():
        raise UpdateError("The updater staging folder cannot be a symbolic link.")
    return staging


def _journal_path(staging_dir: Path) -> Path:
    return staging_dir / _JOURNAL_FILE_NAME


def _write_journal(path: Path, journal: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(journal, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_journal(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError("A previous update transaction could not be read.", str(exc)) from exc
    if not isinstance(value, dict) or not isinstance(value.get("records", []), list):
        raise UpdateError("A previous update transaction is invalid.")
    return value


def _backup_path(staging_dir: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.startswith("backups/"):
        raise UpdateError("A previous update transaction is invalid.")
    candidate = (staging_dir / Path(value)).resolve(strict=False)
    backups_root = (staging_dir / "backups").resolve(strict=False)
    try:
        candidate.relative_to(backups_root)
    except ValueError as exc:
        raise UpdateError("A previous update transaction is unsafe.") from exc
    return candidate


def _rollback_journal(install_dir: Path, staging_dir: Path, journal: dict[str, object], reporter: ProgressReporter) -> None:
    records = journal.get("records", [])
    if not isinstance(records, list):
        raise UpdateError("A previous update transaction is invalid.")
    reporter.status("Restoring the previous version...")
    for record in reversed(records):
        if not isinstance(record, dict):
            raise UpdateError("A previous update transaction is invalid.")
        target = resolve_install_path(install_dir, record.get("target"))
        existed = record.get("existed")
        if not isinstance(existed, bool):
            raise UpdateError("A previous update transaction is invalid.")
        backup = _backup_path(staging_dir, record.get("backup"))
        if target.exists():
            if not target.is_file():
                raise UpdateError("Rollback found an unexpected folder in the application directory.", str(target))
            target.unlink()
        if existed:
            if not backup.is_file():
                raise UpdateError("Rollback backup data is missing.", str(backup))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, target)
        _log("INFO", "Rollback restored file", path=record.get("target"))


def _remove_staging_directory(staging_dir: Path) -> None:
    if not staging_dir.exists():
        return
    if staging_dir.is_symlink():
        raise UpdateError("The updater staging folder cannot be a symbolic link.")
    shutil.rmtree(staging_dir)


def recover_previous_transaction(install_dir: Path, reporter: ProgressReporter) -> None:
    """Recover an interrupted transaction before any new update is attempted."""

    staging_dir = _staging_dir(install_dir)
    if not staging_dir.exists():
        return
    journal_file = _journal_path(staging_dir)
    if not journal_file.exists():
        _log("WARN", "Removing incomplete staging directory without journal")
        _remove_staging_directory(staging_dir)
        return
    journal = _read_journal(journal_file)
    state = journal.get("state")
    if state in ("committed", "rolled_back", "staging"):
        _remove_staging_directory(staging_dir)
        return
    if state != "applying":
        raise UpdateError("A previous update transaction has an invalid state.")
    _log("WARN", "Recovering interrupted update transaction")
    _rollback_journal(install_dir, staging_dir, journal, reporter)
    _remove_staging_directory(staging_dir)


def _subprocess_kwargs_no_window() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def _installed_ytdlp_version(path: Path) -> Optional[str]:
    try:
        completed = subprocess.run([str(path), "--version"], timeout=15, **_subprocess_kwargs_no_window())
        if completed.returncode != 0:
            return None
        output = (completed.stdout or "").strip()
        return output.splitlines()[0].strip() if output else None
    except (OSError, subprocess.SubprocessError):
        return None


def _should_skip_dependency_update(manifest_file: ManifestFile, destination: Path) -> bool:
    if manifest_file.component != "yt-dlp" or not manifest_file.minimum_version or not destination.is_file():
        return False
    installed_version = _installed_ytdlp_version(destination)
    if installed_version is None:
        return False
    if compare_dotted_versions(installed_version, manifest_file.minimum_version) >= 0:
        _log(
            "INFO",
            "Preserving compatible user-managed yt-dlp",
            path=manifest_file.path,
            installed=installed_version,
            minimum=manifest_file.minimum_version,
        )
        return True
    return False


def select_update_files(install_dir: Path, manifest: UpdateManifest) -> tuple[ManifestFile, ...]:
    selected: list[ManifestFile] = []
    for manifest_file in manifest.files:
        destination = resolve_install_path(install_dir, manifest_file.path)
        if _should_skip_dependency_update(manifest_file, destination):
            continue
        if not file_matches_manifest(destination, manifest_file):
            selected.append(manifest_file)
    return tuple(selected)


def _download_selected_files(
    staging_dir: Path,
    selected_files: tuple[ManifestFile, ...],
    reporter: ProgressReporter,
    *,
    allow_file_urls: bool,
) -> tuple[StagedFile, ...]:
    downloads_dir = staging_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    total_size = sum(item.size for item in selected_files if item.size is not None)
    total_known = all(item.size is not None for item in selected_files)
    completed_size = 0
    staged: list[StagedFile] = []

    for index, manifest_file in enumerate(selected_files, start=1):
        reporter.status(f"Downloading {index} of {len(selected_files)}: {manifest_file.path}")
        _log("INFO", "Downloading update file", path=manifest_file.path, url=manifest_file.url)
        download_path = downloads_dir / f"{index:04d}.part"

        def on_progress(received: int, _file_total: Optional[int], completed: int = completed_size) -> None:
            reporter.progress(completed + received, total_size if total_known else None)

        received = download_to_file(
            manifest_file.url,
            download_path,
            expected_sha256=manifest_file.sha256,
            expected_size=manifest_file.size,
            timeout=DEFAULT_NETWORK_TIMEOUT_SECONDS,
            progress=on_progress,
            allow_file_urls=allow_file_urls,
        )
        completed_size += received
        reporter.progress(completed_size, total_size if total_known else None)
        _log("INFO", "Verified update file", path=manifest_file.path, bytes=received)
        staged.append(StagedFile(manifest_file, download_path))
    return tuple(staged)


def _apply_staged_files(
    install_dir: Path,
    staging_dir: Path,
    staged_files: tuple[StagedFile, ...],
    deleted_files: tuple[str, ...],
    reporter: ProgressReporter,
) -> None:
    backups_dir = staging_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    journal: dict[str, object] = {"state": "applying", "records": []}
    journal_file = _journal_path(staging_dir)
    _write_journal(journal_file, journal)
    records = journal["records"]
    assert isinstance(records, list)

    try:
        all_operations: list[tuple[str, object]] = [("replace", item) for item in staged_files]
        all_operations.extend(("delete", path) for path in deleted_files)
        for index, (operation, value) in enumerate(all_operations, start=1):
            if operation == "replace":
                assert isinstance(value, StagedFile)
                target_path = value.manifest_file.path
                source_path = value.downloaded_path
            else:
                assert isinstance(value, str)
                target_path = value
                source_path = None

            destination = resolve_install_path(install_dir, target_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination = resolve_install_path(install_dir, target_path)
            existed = destination.exists()
            if existed and not destination.is_file():
                raise UpdateError("An update target is an unexpected folder.", str(destination))
            if operation == "delete" and not existed:
                continue

            backup_relative = f"backups/{index:04d}.bak"
            record: dict[str, object] = {
                "operation": operation,
                "target": target_path,
                "backup": backup_relative,
                "existed": existed,
                "applied": False,
            }
            records.append(record)
            _write_journal(journal_file, journal)
            backup = _backup_path(staging_dir, backup_relative)
            if existed:
                os.replace(destination, backup)
            if source_path is not None:
                os.replace(source_path, destination)
                _log("INFO", "Replaced application file", path=target_path)
            else:
                _log("INFO", "Removed obsolete application file", path=target_path)
            record["applied"] = True
            _write_journal(journal_file, journal)
        journal["state"] = "committed"
        _write_journal(journal_file, journal)
    except BaseException as install_error:
        try:
            _rollback_journal(install_dir, staging_dir, journal, reporter)
            journal["state"] = "rolled_back"
            _write_journal(journal_file, journal)
        except BaseException as rollback_error:
            _log("ERROR", "Rollback failed", error=rollback_error)
            raise UpdateError("The update failed and rollback also failed.", str(rollback_error)) from rollback_error
        if isinstance(install_error, UpdateError):
            raise install_error
        if isinstance(install_error, PermissionError):
            raise UpdateError(
                "Windows could not replace an application file. Close Naughty Koharu and any programs using its folder, then try again.",
                str(install_error),
            ) from install_error
        if isinstance(install_error, OSError) and getattr(install_error, "errno", None) in (errno.ENOSPC, 112):
            raise UpdateError("There is not enough free disk space to install the update.", str(install_error)) from install_error
        if isinstance(install_error, OSError):
            raise UpdateError("Windows could not replace an application file.", str(install_error)) from install_error
        raise


def _current_updater_is_targeted(install_dir: Path, selected_files: tuple[ManifestFile, ...]) -> bool:
    try:
        current_executable = Path(sys.executable).resolve(strict=False)
    except OSError:
        return False
    for manifest_file in selected_files:
        if resolve_install_path(install_dir, manifest_file.path) == current_executable:
            return True
    return False


def apply_manifest_update(
    install_dir: Path | str,
    manifest: UpdateManifest,
    reporter: Optional[ProgressReporter] = None,
    *,
    allow_file_urls: bool = False,
) -> tuple[tuple[ManifestFile, ...], tuple[str, ...]]:
    """Apply one verified manifest and return changed and deleted paths."""

    report = reporter or ProgressReporter()
    root = Path(install_dir).resolve(strict=False)
    if not root.is_dir():
        raise UpdateError("The application installation folder does not exist.", str(root))
    recover_previous_transaction(root, report)
    selected_files = select_update_files(root, manifest)
    existing_deletions = tuple(path for path in manifest.deleted_files if resolve_install_path(root, path).exists())
    if _current_updater_is_targeted(root, selected_files):
        raise UpdateError("The updater must be started from a temporary copy before it can update itself.")
    if not selected_files and not existing_deletions:
        _log("INFO", "Application is already current", version=manifest.version)
        return (), ()

    staging_dir = _staging_dir(root)
    try:
        _remove_staging_directory(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=False)
        _write_journal(_journal_path(staging_dir), {"state": "staging", "records": []})
    except PermissionError as exc:
        raise UpdateError("Windows denied access to the application update folder.", str(exc)) from exc
    except OSError as exc:
        if getattr(exc, "errno", None) in (errno.ENOSPC, 112):
            raise UpdateError("There is not enough free disk space to prepare the update.", str(exc)) from exc
        raise UpdateError("Could not prepare the application update folder.", str(exc)) from exc
    try:
        _log("INFO", "Update transaction started", version=manifest.version, files=len(selected_files), deletes=len(existing_deletions))
        staged_files = _download_selected_files(staging_dir, selected_files, report, allow_file_urls=allow_file_urls)
        report.status("Installing verified update files...")
        _apply_staged_files(root, staging_dir, staged_files, existing_deletions, report)
        _remove_staging_directory(staging_dir)
        _log("INFO", "Update transaction completed", version=manifest.version)
        return selected_files, existing_deletions
    except BaseException:
        try:
            if staging_dir.exists():
                journal = _read_journal(_journal_path(staging_dir))
                if journal.get("state") in ("staging", "rolled_back"):
                    _remove_staging_directory(staging_dir)
        except BaseException as cleanup_error:
            _log("WARN", "Temporary update cleanup failed", error=cleanup_error)
        raise


def wait_for_process_exit(process_id: int, timeout_seconds: float) -> bool:
    """Wait for the app PID using a Windows process handle, with a safe fallback."""

    if process_id <= 0:
        return True
    timeout_seconds = max(1.0, min(float(timeout_seconds), 900.0))
    if os.name == "nt":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            process_synchronize = 0x00100000
            process_query_limited_information = 0x1000
            handle = kernel32.OpenProcess(process_synchronize | process_query_limited_information, False, int(process_id))
            if handle:
                try:
                    result = kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000))
                    return result == 0
                finally:
                    kernel32.CloseHandle(handle)
            if ctypes.get_last_error() in (87, 1168):
                return True
        except Exception as exc:
            _log("WARN", "Windows process wait unavailable", error=exc)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        except OSError:
            return True
        time.sleep(0.15)
    return False


def _known_production_install_locations() -> tuple[Path, ...]:
    locations: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        locations.append((Path(local_app_data) / "Naughty Koharu").resolve(strict=False))
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_REGISTRY_KEY, 0, winreg.KEY_READ) as key:
                install_location, value_type = winreg.QueryValueEx(key, "InstallLocation")
                if value_type == winreg.REG_SZ and isinstance(install_location, str) and install_location.strip():
                    locations.append(Path(install_location.strip()).resolve(strict=False))
        except OSError:
            pass
    return tuple(locations)


def _is_production_install(install_dir: Path) -> bool:
    candidate = str(install_dir.resolve(strict=False)).casefold()
    return any(candidate == str(location.resolve(strict=False)).casefold() for location in _known_production_install_locations())


def _launch_application(install_dir: Path, launch_relative_path: str) -> None:
    application = resolve_install_path(install_dir, launch_relative_path)
    if not application.is_file():
        raise UpdateError("The updated application executable is missing.", str(application))
    subprocess.Popen(
        [str(application)],
        cwd=str(install_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    _log("INFO", "Restarted application", path=launch_relative_path)


def _show_error(message: str) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, str(message), "Naughty Koharu Update", 0x10)
    except Exception:
        pass


def _schedule_temporary_session_cleanup() -> None:
    """Delete only the known copied-updater session after this process releases it."""

    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        executable = Path(sys.executable).resolve(strict=False)
        session_dir = executable.parent
        temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
        session_dir.relative_to(temp_root)
        if not session_dir.name.startswith("NaughtyKoharuUpdate-"):
            return
        cleanup_script = temp_root / f"NaughtyKoharuCleanup-{os.getpid()}.cmd"
        quoted_session = str(session_dir)
        cleanup_script.write_text(
            "@echo off\r\n"
            ":retry\r\n"
            f"rmdir /s /q \"{quoted_session}\"\r\n"
            f"if exist \"{quoted_session}\" (timeout /t 1 /nobreak >nul & goto retry)\r\n"
            "del /q \"%~f0\"\r\n",
            encoding="ascii",
        )
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        }
        subprocess.Popen(["cmd.exe", "/d", "/c", str(cleanup_script)], **kwargs)
        _log("DEBUG", "Scheduled temporary updater cleanup", path=session_dir)
    except Exception as exc:
        _log("WARN", "Could not schedule temporary updater cleanup", error=exc)


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install a verified Naughty Koharu update.")
    parser.add_argument("--install-dir", required=True, help="Existing Naughty Koharu installation directory.")
    parser.add_argument("--manifest", required=True, help="Path to a downloaded update-manifest.json file.")
    parser.add_argument("--parent-pid", type=int, default=0, help="Running app process that must exit before replacement.")
    parser.add_argument("--wait-timeout", type=float, default=90.0, help="Maximum seconds to wait for the app to close.")
    parser.add_argument("--launch", default=APP_EXECUTABLE_NAME, help="Relative application executable to launch after success.")
    parser.add_argument("--test-mode", action="store_true", help="Allow an explicit local test manifest and file URLs.")
    parser.add_argument("--allow-production-install", action="store_true", help="Explicitly permit test mode in the default production folder.")
    parser.add_argument("--no-launch", action="store_true", help="Do not restart the application after testing.")
    parser.add_argument("--no-ui", action="store_true", help="Suppress the updater progress window.")
    return parser.parse_args(argv)


def _perform_update(args: argparse.Namespace, reporter: ProgressReporter) -> UpdateResult:
    install_dir = Path(args.install_dir).resolve(strict=False)
    parent_exited = False
    try:
        if not wait_for_process_exit(int(args.parent_pid), float(args.wait_timeout)):
            return UpdateResult(False, "Naughty Koharu did not close in time. Close it completely, then try the update again.")
        parent_exited = True
        manifest = load_manifest_file(args.manifest, allow_file_urls=bool(args.test_mode))
        if manifest.test_only != bool(args.test_mode):
            raise UpdateError("This update manifest can only be used with its matching test-mode setting.")
        if args.test_mode and _is_production_install(install_dir) and not args.allow_production_install:
            raise UpdateError("Test mode refuses to modify the production installation without explicit approval.")
        reporter.status("Checking installed application files...")
        changed, deleted = apply_manifest_update(
            install_dir,
            manifest,
            reporter,
            allow_file_urls=bool(args.test_mode),
        )
        message = f"Installed Naughty Koharu {manifest.version}."
        _log("INFO", "Update installed", version=manifest.version, changed=len(changed), deleted=len(deleted))
        return UpdateResult(True, message, restart_application=not bool(args.no_launch or args.test_mode))
    except UpdateError as exc:
        _log("ERROR", "Update failed", error=exc, detail=exc.detail)
        return UpdateResult(False, str(exc), exc.detail, restart_application=parent_exited and not bool(args.no_launch or args.test_mode))
    except BaseException as exc:  # pragma: no cover - defensive process boundary
        _log("ERROR", "Unexpected update failure", error=exc, traceback=traceback.format_exc())
        return UpdateResult(
            False,
            "The update failed unexpectedly. Your previous application files were kept where possible.",
            str(exc),
            restart_application=parent_exited and not bool(args.no_launch or args.test_mode),
        )


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    _log("INFO", "Updater started", install_dir=args.install_dir, parent_pid=args.parent_pid, test_mode=args.test_mode)
    try:
        reporter = TkProgressReporter(enabled=not bool(args.no_ui))
        result = reporter.run(lambda: _perform_update(args, reporter))
        if not isinstance(result, UpdateResult):
            _show_error("The updater ended without a result.")
            return 1

        if result.restart_application:
            try:
                _launch_application(Path(args.install_dir).resolve(strict=False), str(args.launch))
            except UpdateError as exc:
                _log("ERROR", "Application restart failed", error=exc)
                if result.success:
                    result = UpdateResult(False, str(exc), exc.detail)

        if not result.success and args.no_ui:
            _show_error(result.message)
        return 0 if result.success else 1
    finally:
        _schedule_temporary_session_cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
