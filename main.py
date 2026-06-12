import argparse
import os
import sys
import threading
import traceback
import re
import shutil
import subprocess
import time
import winreg
from pathlib import Path
from typing import Any, Callable, Optional, cast

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _default_download_dir() -> Path:
    """Return the system default download directory.

    On Windows this uses the official "Downloads" known folder so it works
    across different PCs/locales/users. Falls back to ~/Downloads.
    """

    # Windows known folder: FOLDERID_Downloads
    if os.name == "nt":
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", wintypes.BYTE * 8),
                ]

            def guid_from_uuid(u: uuid.UUID) -> GUID:
                b = u.bytes_le
                return GUID(
                    int.from_bytes(b[0:4], "little"),
                    int.from_bytes(b[4:6], "little"),
                    int.from_bytes(b[6:8], "little"),
                    (wintypes.BYTE * 8).from_buffer_copy(b[8:16]),
                )

            folder_id = guid_from_uuid(uuid.UUID("374DE290-123F-4565-9164-39C4925E467B"))
            p_path = wintypes.LPWSTR()
            # SHGetKnownFolderPath(REFKNOWNFOLDERID, DWORD, HANDLE, PWSTR*)
            hr = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(folder_id), 0, None, ctypes.byref(p_path)
            )
            if hr == 0 and p_path and p_path.value:
                try:
                    return Path(p_path.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(p_path)
        except Exception:
            pass

        # Reasonable Windows fallback if API isn't available
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            return Path(userprofile) / "Downloads"

    # Cross-platform fallback
    return Path.home() / "Downloads"


def _ensure_dir(p: Path) -> Path:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


_APP_REG_KEY = r"Software\ytdlp-onefile"
_REG_DOWNLOAD_DIR = "DownloadDir"
_REG_PROGRESS_X = "ProgressWidgetX"
_REG_PROGRESS_Y = "ProgressWidgetY"
_REG_YTDLP_LAST_UPDATE_CHECK = "YtDlpLastUpdateCheck"

_YTDLP_LOCK = threading.Lock()

_YTDLP_UPDATE_STATE_LOCK = threading.Lock()
_YTDLP_UPDATING = False

_YTDLP_VERSION_CACHE_LOCK = threading.Lock()
_YTDLP_VERSION_CACHE_VALUE: Optional[str] = None
_YTDLP_VERSION_CACHE_TS: float = 0.0
_YTDLP_VERSION_REFRESHING = False


def _get_configured_download_dir() -> Optional[Path]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APP_REG_KEY, 0, winreg.KEY_READ) as key:
            val, typ = winreg.QueryValueEx(key, _REG_DOWNLOAD_DIR)
            if typ != winreg.REG_SZ:
                return None
            if not isinstance(val, str) or not val.strip():
                return None
            return Path(os.path.expandvars(val.strip()))
    except Exception:
        return None


def _set_configured_download_dir(p: Optional[Path]) -> None:
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _APP_REG_KEY) as key:
            if p is None:
                try:
                    winreg.DeleteValue(key, _REG_DOWNLOAD_DIR)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
                return
            winreg.SetValueEx(key, _REG_DOWNLOAD_DIR, 0, winreg.REG_SZ, str(p))
    except Exception:
        return


def _get_configured_progress_pos() -> Optional[tuple[int, int]]:
    if os.name != "nt":
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APP_REG_KEY, 0, winreg.KEY_READ) as key:
            x, x_typ = winreg.QueryValueEx(key, _REG_PROGRESS_X)
            y, y_typ = winreg.QueryValueEx(key, _REG_PROGRESS_Y)
            if x_typ != winreg.REG_DWORD or y_typ != winreg.REG_DWORD:
                return None
            return (int(x), int(y))
    except Exception:
        return None


def _set_configured_progress_pos(x: Optional[int], y: Optional[int]) -> None:
    if os.name != "nt":
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _APP_REG_KEY) as key:
            if x is None or y is None:
                for name in (_REG_PROGRESS_X, _REG_PROGRESS_Y):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        pass
                return
            winreg.SetValueEx(key, _REG_PROGRESS_X, 0, winreg.REG_DWORD, int(x))
            winreg.SetValueEx(key, _REG_PROGRESS_Y, 0, winreg.REG_DWORD, int(y))
    except Exception:
        return


def _effective_download_dir() -> Path:
    p = _get_configured_download_dir()
    if p is not None:
        return _ensure_dir(p)
    return _ensure_dir(_default_download_dir())


def _short_path_for_menu(p: Path, max_len: int = 60) -> str:
    s = str(p)
    if len(s) <= max_len:
        return s
    return s[: max_len // 2 - 2] + "..." + s[-(max_len // 2) :]


def _pick_folder_win32(owner_hwnd: int, title: str, initial_dir: Optional[Path] = None) -> Optional[Path]:
    """Best-effort folder picker for the Win32 tray fallback (no Qt)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        ole32 = ctypes.windll.ole32
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32

        BIF_RETURNONLYFSDIRS = 0x0001
        BIF_NEWDIALOGSTYLE = 0x0040
        BFFM_INITIALIZED = 1
        BFFM_SETSELECTIONW = 0x467
        MAX_PATH = 260

        class BROWSEINFOW(ctypes.Structure):
            _fields_ = [
                ("hwndOwner", wintypes.HWND),
                ("pidlRoot", wintypes.LPVOID),
                ("pszDisplayName", wintypes.LPWSTR),
                ("lpszTitle", wintypes.LPCWSTR),
                ("ulFlags", wintypes.UINT),
                ("lpfn", wintypes.LPVOID),
                ("lParam", wintypes.LPARAM),
                ("iImage", wintypes.INT),
            ]

        CallbackType = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT, wintypes.LPARAM, wintypes.LPARAM)
        init_buf = None

        def _callback(hwnd, msg, lparam, lpdata):  # noqa: ANN001
            try:
                if msg == BFFM_INITIALIZED and lpdata:
                    user32.SendMessageW(hwnd, BFFM_SETSELECTIONW, 1, lpdata)
            except Exception:
                pass
            return 0

        cb = CallbackType(_callback)

        try:
            ole32.CoInitialize(None)
        except Exception:
            pass

        try:
            display = ctypes.create_unicode_buffer(MAX_PATH)
            flags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
            lpdata = 0
            if initial_dir is not None:
                init_buf = ctypes.create_unicode_buffer(str(initial_dir))
                lpdata = ctypes.cast(init_buf, ctypes.c_void_p).value or 0

            bi = BROWSEINFOW(
                wintypes.HWND(int(owner_hwnd) if owner_hwnd else 0),
                None,
                display,
                str(title),
                flags,
                ctypes.cast(cb, wintypes.LPVOID),
                wintypes.LPARAM(lpdata),
                0,
            )

            pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
            if not pidl:
                return None

            try:
                out = ctypes.create_unicode_buffer(MAX_PATH)
                if shell32.SHGetPathFromIDListW(pidl, out):
                    s = out.value.strip()
                    return Path(s) if s else None
                return None
            finally:
                try:
                    ole32.CoTaskMemFree(pidl)
                except Exception:
                    pass
        finally:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass
    except Exception:
        return None


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _write_crash_log(prefix: str, exc: BaseException) -> Optional[Path]:
    try:
        log_path = Path(os.environ.get("TEMP", str(Path.home()))) / "ytdlp-onefile.log"
        text = f"{prefix}\n\n{traceback.format_exc()}\n"
        log_path.write_text(text, encoding="utf-8", errors="replace")
        return log_path
    except Exception:
        return None


def _bundled_dir() -> Optional[Path]:
    """Return the PyInstaller extraction dir when running as a onefile exe."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return None


def _executable_dir() -> Optional[Path]:
    """Return the directory of the running executable when frozen (onedir/onefile)."""
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            return None
    return None


def _resolve_ffmpeg_dir(cli_ffmpeg_dir: Optional[str]) -> Optional[Path]:
    if cli_ffmpeg_dir:
        p = Path(cli_ffmpeg_dir)
        return p if p.exists() else None

    bundled = _bundled_dir()
    if bundled:
        if (bundled / "ffmpeg.exe").exists():
            return bundled

    exe_dir = _executable_dir()
    if exe_dir:
        if (exe_dir / "ffmpeg.exe").exists():
            return exe_dir
        internal = exe_dir / "_internal"
        if (internal / "ffmpeg.exe").exists():
            return internal

    # Dev-mode fallback: expect vendor/ next to this script
    vendor = Path(__file__).resolve().parent / "vendor"
    return vendor if vendor.exists() else None


def _resolve_ytdlp_exe() -> Optional[Path]:
    env = (
        os.environ.get("YTDLP_EXE")
        or os.environ.get("YTDLP_PATH")
        or os.environ.get("YTDLP")
        or os.environ.get("YT_DLP")
    )
    if env:
        try:
            p = Path(env)
            if p.exists():
                return p
        except Exception:
            pass

    exe_dir = _executable_dir()
    if exe_dir:
        for candidate in (
            exe_dir / "yt-dlp.exe",
            exe_dir / "vendor" / "yt-dlp.exe",
            exe_dir / "_internal" / "yt-dlp.exe",
            exe_dir / "_internal" / "vendor" / "yt-dlp.exe",
        ):
            if candidate.exists():
                return candidate

    # In onefile mode, _MEIPASS is an extracted internal temp dir.
    # Keep this after exe_dir so an external yt-dlp.exe next to the app wins.
    bundled = _bundled_dir()
    if bundled:
        p = bundled / "yt-dlp.exe"
        if p.exists():
            return p

    vendor = Path(__file__).resolve().parent / "vendor"
    p = vendor / "yt-dlp.exe"
    if p.exists():
        return p

    which = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if which:
        try:
            return Path(which)
        except Exception:
            return None
    return None


def _get_reg_int(name: str) -> Optional[int]:
    if os.name != "nt":
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APP_REG_KEY, 0, winreg.KEY_READ) as key:
            val, typ = winreg.QueryValueEx(key, name)
            if typ in (getattr(winreg, "REG_QWORD", 11), winreg.REG_DWORD):
                return int(val)
            if typ == winreg.REG_SZ and isinstance(val, str) and val.strip():
                try:
                    return int(val.strip())
                except Exception:
                    return None
    except Exception:
        return None
    return None


def _set_reg_int(name: str, value: int) -> None:
    if os.name != "nt":
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _APP_REG_KEY) as key:
            reg_qword = getattr(winreg, "REG_QWORD", None)
            if reg_qword is not None:
                winreg.SetValueEx(key, name, 0, reg_qword, int(value))
            else:
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value) & 0xFFFFFFFF)
    except Exception:
        return


def _maybe_autoupdate_ytdlp(check_every_seconds: int = 24 * 60 * 60) -> None:
    """Best-effort background yt-dlp update check.

    Runs yt-dlp's built-in self-update (`-U`) in the background.
    Throttled via HKCU registry so it doesn't run on every launch.
    """

    ytdlp_exe = _resolve_ytdlp_exe()
    if not ytdlp_exe or not ytdlp_exe.exists():
        try:
            _append_log_line("yt-dlp: update check skipped (yt-dlp.exe not found)")
        except Exception:
            pass
        return

    now = int(time.time())
    last = _get_reg_int(_REG_YTDLP_LAST_UPDATE_CHECK) or 0
    if last and (now - last) < int(check_every_seconds):
        try:
            mins = max(0, int((now - last) // 60))
            _append_log_line(f"yt-dlp: update check skipped (checked {mins} min ago)")
        except Exception:
            pass
        return

    # Set timestamp first to avoid repeated spawns if the app is launched multiple times quickly.
    _set_reg_int(_REG_YTDLP_LAST_UPDATE_CHECK, now)

    try:
        _append_log_line(f"yt-dlp: update check scheduled ({ytdlp_exe})")
    except Exception:
        pass

    _start_ytdlp_update(force=True, reason="auto")


def _is_ytdlp_updating() -> bool:
    with _YTDLP_UPDATE_STATE_LOCK:
        return bool(_YTDLP_UPDATING)


def _start_ytdlp_update(
    *,
    force: bool,
    reason: str,
    on_done=None,
) -> bool:
    """Start yt-dlp self-update (`-U`) in a background thread.

    - `force=True` bypasses throttle (caller should handle throttle if desired).
    - Returns True if a new update thread was started; False if already updating or missing exe.
    - `on_done(success: bool, message: str)` is called from the worker thread.
    """

    ytdlp_exe = _resolve_ytdlp_exe()
    if not ytdlp_exe or not ytdlp_exe.exists():
        return False

    with _YTDLP_UPDATE_STATE_LOCK:
        global _YTDLP_UPDATING
        if _YTDLP_UPDATING:
            return False
        _YTDLP_UPDATING = True

    def worker() -> None:
        global _YTDLP_UPDATING
        success = False
        msg = ""
        try:
            with _YTDLP_LOCK:
                cmd = [str(ytdlp_exe), "-U", "--no-warnings"]
                _append_log_line(f"yt-dlp: update start ({reason})")
                kw = _subprocess_kwargs_no_window()
                cp = subprocess.run(cmd, cwd=str(_effective_download_dir()), timeout=240, **kw)
                out = (cp.stdout or "").strip()

            if cp.returncode == 0:
                success = True
                msg = out.splitlines()[-1].strip() if out else "ok"
                _append_log_line(f"yt-dlp: update ok: {msg}")
            else:
                msg = out.splitlines()[-1].strip() if out else f"exit={cp.returncode}"
                _append_log_line(f"yt-dlp: update failed: {msg}")

            # Refresh cached version after update (best-effort).
            try:
                ver = _query_ytdlp_version(ytdlp_exe)
                if ver:
                    with _YTDLP_VERSION_CACHE_LOCK:
                        global _YTDLP_VERSION_CACHE_VALUE, _YTDLP_VERSION_CACHE_TS
                        _YTDLP_VERSION_CACHE_VALUE = ver
                        _YTDLP_VERSION_CACHE_TS = time.time()
            except Exception:
                pass
        except Exception as exc:
            msg = str(exc)
            _append_log_line(f"yt-dlp: update error: {exc}")
        finally:
            with _YTDLP_UPDATE_STATE_LOCK:
                _YTDLP_UPDATING = False
            if on_done is not None:
                try:
                    on_done(bool(success), msg)
                except Exception:
                    pass

    threading.Thread(target=worker, daemon=True).start()
    return True


def _query_ytdlp_version(ytdlp_exe: Path) -> Optional[str]:
    try:
        with _YTDLP_LOCK:
            cp = subprocess.run(
                [str(ytdlp_exe), "--version"],
                cwd=str(_effective_download_dir()),
                timeout=15,
                **_subprocess_kwargs_no_window(),
            )
        if cp.returncode != 0:
            return None
        out = (cp.stdout or "").strip()
        if not out:
            return None
        # yt-dlp version is usually a single line.
        return out.splitlines()[0].strip() or None
    except Exception:
        return None


def _schedule_ytdlp_version_refresh(force: bool = False, ttl_seconds: int = 10 * 60) -> None:
    global _YTDLP_VERSION_REFRESHING

    ytdlp_exe = _resolve_ytdlp_exe()
    if not ytdlp_exe or not ytdlp_exe.exists():
        return

    now = time.time()
    with _YTDLP_VERSION_CACHE_LOCK:
        if not force and _YTDLP_VERSION_CACHE_VALUE and (now - _YTDLP_VERSION_CACHE_TS) < float(ttl_seconds):
            return
        if _YTDLP_VERSION_REFRESHING:
            return
        _YTDLP_VERSION_REFRESHING = True

    def worker() -> None:
        global _YTDLP_VERSION_REFRESHING
        try:
            ver = _query_ytdlp_version(ytdlp_exe)
            with _YTDLP_VERSION_CACHE_LOCK:
                if ver:
                    global _YTDLP_VERSION_CACHE_VALUE, _YTDLP_VERSION_CACHE_TS
                    _YTDLP_VERSION_CACHE_VALUE = ver
                    _YTDLP_VERSION_CACHE_TS = time.time()
        finally:
            with _YTDLP_VERSION_CACHE_LOCK:
                _YTDLP_VERSION_REFRESHING = False

    threading.Thread(target=worker, daemon=True).start()


def _ytdlp_version_label() -> str:
    ytdlp_exe = _resolve_ytdlp_exe()
    if not ytdlp_exe or not ytdlp_exe.exists():
        return "yt-dlp: (not found)"

    if _is_ytdlp_updating():
        return "yt-dlp: (updating...)"

    with _YTDLP_VERSION_CACHE_LOCK:
        ver = _YTDLP_VERSION_CACHE_VALUE
        ts = _YTDLP_VERSION_CACHE_TS

    # Trigger refresh if stale/missing.
    if not ver or (time.time() - ts) > 10 * 60:
        _schedule_ytdlp_version_refresh(force=False)
        return "yt-dlp: (checking...)" if not ver else f"yt-dlp: {ver}"

    return f"yt-dlp: {ver}"


def _subprocess_kwargs_no_window() -> dict:
    kw = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        try:
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        except Exception:
            pass
    return kw


def _probe_first_playlist_title(url: str, ytdlp_exe: Path, ffmpeg_dir: Optional[Path]) -> Optional[str]:
    try:
        cmd = [
            str(ytdlp_exe),
            "--skip-download",
            "--flat-playlist",
            "--playlist-items",
            "1",
            "--print",
            "%(title)s",
            "--quiet",
            "--no-warnings",
            url,
        ]
        if ffmpeg_dir:
            cmd.insert(1, "--ffmpeg-location")
            cmd.insert(2, str(ffmpeg_dir))

        run_kw = {
            "cwd": str(_effective_download_dir()),
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 45,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            try:
                run_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            except Exception:
                pass

        cp = subprocess.run(cmd, **run_kw)
        if cp.returncode != 0:
            return None
        for line in (cp.stdout or "").splitlines():
            t = line.strip()
            if t:
                return t
        return None
    except Exception:
        return None


def _probe_playlist_count(url: str, ytdlp_exe: Path, ffmpeg_dir: Optional[Path]) -> Optional[int]:
    """Best-effort playlist size probe before starting the full download."""
    try:
        cmd = [
            str(ytdlp_exe),
            "--skip-download",
            "--flat-playlist",
            "--dump-single-json",
            "--quiet",
            "--no-warnings",
            url,
        ]
        if ffmpeg_dir:
            cmd.insert(1, "--ffmpeg-location")
            cmd.insert(2, str(ffmpeg_dir))

        run_kw = {
            "cwd": str(_effective_download_dir()),
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 60,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            try:
                run_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            except Exception:
                pass

        cp = subprocess.run(cmd, **run_kw)
        if cp.returncode != 0:
            return None

        txt = (cp.stdout or "").strip()
        if not txt:
            return None
        data = json.loads(txt)
        if not isinstance(data, dict):
            return None

        for key in ("playlist_count", "n_entries"):
            try:
                raw_val = data.get(key)
                if raw_val is None:
                    continue
                val = int(raw_val)
                if val > 0:
                    return val
            except Exception:
                pass

        entries = data.get("entries")
        if isinstance(entries, list) and len(entries) > 0:
            return int(len(entries))
        return None
    except Exception:
        return None


def _run_ytdlp_with_progress(
    cmd: list[str],
    cwd: Path,
    status_cb,
    extra_progress_hook=None,
    cancel_event: Optional[threading.Event] = None,
    remember_path_cb=None,
) -> tuple[int, str]:
    output_lines: list[str] = []

    pct_re = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
    item_re = re.compile(r"\bitem\s+(\d+)\s+of\s+(\d+)\b", re.IGNORECASE)
    video_re = re.compile(r"\bvideo\s+(\d+)\s+of\s+(\d+)\b", re.IGNORECASE)
    any_dl_re = re.compile(r"\bdownloading\s+(\d+)\s+of\s+(\d+)\b", re.IGNORECASE)
    dest_re = re.compile(r"Destination:\s+(.*)")
    merge_re = re.compile(r"Merging\s+formats\s+into\s+\"(.+?)\"", re.IGNORECASE)

    # Avoid running yt-dlp while it is self-updating.
    with _YTDLP_LOCK:
        proc = subprocess.Popen(cmd, cwd=str(cwd), **_subprocess_kwargs_no_window())

        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if cancel_event is not None and cancel_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break

                line = (raw or "").rstrip("\r\n")
                if line:
                    output_lines.append(line)
                    if len(output_lines) > 400:
                        output_lines = output_lines[-200:]

                try:
                    if remember_path_cb is not None:
                        m = dest_re.search(line)
                        if m:
                            remember_path_cb(m.group(1).strip())
                        m = merge_re.search(line)
                        if m:
                            remember_path_cb(m.group(1).strip())
                except Exception:
                    pass

                try:
                    if extra_progress_hook is not None:
                        m = item_re.search(line) or video_re.search(line) or any_dl_re.search(line)
                        if m:
                            extra_progress_hook({"playlist_index": int(m.group(1)), "playlist_count": int(m.group(2))})
                except Exception:
                    pass

                try:
                    m = pct_re.search(line)
                    if m:
                        pct = float(m.group(1))
                        if pct < 0:
                            pct = 0.0
                        if pct > 100:
                            pct = 100.0
                        status_cb(f"Downloading... {pct:.1f}%")
                        if extra_progress_hook is not None:
                            extra_progress_hook(
                                {
                                    "status": "downloading",
                                    "downloaded_bytes": pct,
                                    "total_bytes": 100.0,
                                }
                            )
                        continue
                except Exception:
                    pass

                # Rough status mapping for non-download phases.
                try:
                    low = (line or "").lower()
                    if "[merger]" in low or "[extractaudio]" in low or "[ffmpeg]" in low:
                        status_cb("Processing...")
                except Exception:
                    pass

            try:
                proc.wait(timeout=20)
            except Exception:
                pass
        finally:
            if cancel_event is not None and cancel_event.is_set():
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass

        return int(proc.returncode or 0), "\n".join(output_lines)


def _prepend_path(dir_path: Path) -> None:
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = str(dir_path) + os.pathsep + current


def _default_ffmpeg_dir(cli_ffmpeg_dir: Optional[str] = None) -> Optional[Path]:
    ffmpeg_dir = _resolve_ffmpeg_dir(cli_ffmpeg_dir)
    if ffmpeg_dir:
        _prepend_path(ffmpeg_dir)
    return ffmpeg_dir


def _ffmpeg_healthcheck(ffmpeg_dir: Optional[Path]) -> Optional[str]:
    """Return an error string if ffmpeg/ffprobe are missing or fail to start."""
    if not ffmpeg_dir:
        return (
            "FFmpeg folder not set/found.\n\n"
            "Fix: Put ffmpeg.exe (and ffprobe.exe) into onefile/vendor/ then rebuild."
        )

    ffmpeg_path = ffmpeg_dir / "ffmpeg.exe"
    ffprobe_path = ffmpeg_dir / "ffprobe.exe"

    if not ffmpeg_path.exists():
        return f"Missing: {ffmpeg_path}"

    # ffprobe is optional for some flows but yt-dlp uses it for metadata.
    if not ffprobe_path.exists():
        return (
            f"Missing: {ffprobe_path}\n\n"
            "Fix: Put ffprobe.exe next to ffmpeg.exe (recommended)."
        )

    # Do NOT execute ffmpeg.exe here; if it's a shared build missing DLLs,
    # Windows will pop multiple system dialogs (one per missing DLL).
    # Instead, inspect the PE import table and check required FFmpeg DLLs exist.
    try:
        import pefile  # type: ignore

        def imported_dlls(exe_path: Path) -> set[str]:
            pe = pefile.PE(str(exe_path))
            dlls: set[str] = set()
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
                try:
                    name = entry.dll.decode("utf-8", errors="ignore").lower()
                except Exception:
                    continue
                if name:
                    dlls.add(name)
            return dlls

        ffmpeg_imports = imported_dlls(ffmpeg_path)
        ffprobe_imports = imported_dlls(ffprobe_path)
        imports = ffmpeg_imports | ffprobe_imports

        # Check for the common FFmpeg shared-library DLLs.
        ffmpeg_lib_prefixes = (
            "avcodec-",
            "avdevice-",
            "avfilter-",
            "avformat-",
            "avutil-",
            "swresample-",
            "swscale-",
            "postproc-",
        )
        required = sorted(
            {
                dll
                for dll in imports
                if dll.endswith(".dll") and dll.startswith(ffmpeg_lib_prefixes)
            }
        )

        if required:
            present = {p.name.lower() for p in ffmpeg_dir.glob("*.dll")}
            missing = [dll for dll in required if dll not in present]
            if missing:
                missing_list = "\n".join(f"- {name}" for name in missing)
                return (
                    "FFmpeg is a shared build, but required DLLs are missing.\n\n"
                    "Copy these DLLs into onefile/vendor/ (same folder as ffmpeg.exe), then rebuild:\n"
                    f"{missing_list}\n\n"
                    "Alternative: replace ffmpeg.exe/ffprobe.exe with a static build (no extra DLLs)."
                )
    except Exception:
        # If PE inspection fails for any reason, fall back to a minimal check.
        # (Still avoid executing ffmpeg.exe to prevent Windows loader popups.)
        pass

    return None


def _get_clipboard_text() -> Optional[str]:
    """Return clipboard text, or None if not available."""
    try:
        import win32clipboard  # type: ignore
        import win32con  # type: ignore

        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return None
            data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return data if isinstance(data, str) else None
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


_URL_RE = re.compile(r"^https?://\S+\Z", re.IGNORECASE)

_INJECTED_URL_LOCK = threading.Lock()
_INJECTED_URL_VALUE: Optional[str] = None
_INJECTED_URL_TS: float = 0.0

_LOCAL_HTTP_STARTED_LOCK = threading.Lock()
_LOCAL_HTTP_STARTED = False
_LOCAL_HTTP_ON_URL_LOCK = threading.Lock()
_LOCAL_HTTP_ON_URL: Optional[Callable[[Any], None]] = None

_HTTP_PING_LOCK = threading.Lock()
_HTTP_PING_COUNT = 0
_HTTP_PING_LAST_LOG_TS: float = 0.0


def _set_local_http_on_url(cb: Optional[Callable[[Any], None]]) -> None:
    with _LOCAL_HTTP_ON_URL_LOCK:
        global _LOCAL_HTTP_ON_URL
        _LOCAL_HTTP_ON_URL = cb


def _normalize_download_request(obj: Any) -> Optional[dict[str, Any]]:
    try:
        if isinstance(obj, str):
            obj = {"url": obj}
        if not isinstance(obj, dict):
            return None

        url = str(obj.get("url") or "").strip()
        if not url:
            url = str(obj.get("srcUrl") or "").strip() or str(obj.get("pageUrl") or "").strip()

        youtube_id = str(obj.get("youtubeId") or "").strip()
        if youtube_id and not url:
            url = f"https://www.youtube.com/watch?v={youtube_id}"

        if url and not url.lower().startswith("http") and len(url) <= 32:
            # Treat as YouTube id.
            url = f"https://www.youtube.com/watch?v={url}"

        if not url or len(url) > 8192 or not _URL_RE.match(url):
            return None

        mode = str(obj.get("mode") or "video").strip().lower()
        if mode not in ("video", "music"):
            mode = "video"

        playlist = bool(obj.get("playlist") or False)

        video_height = obj.get("videoHeight")
        if video_height is None:
            video_height = obj.get("height")
        if video_height is None:
            video_height = obj.get("h")
        try:
            video_height_i = int(video_height) if video_height is not None and str(video_height).strip() else None
        except Exception:
            video_height_i = None

        audio_bitrate = obj.get("audioBitrate")
        if audio_bitrate is None:
            audio_bitrate = obj.get("abr")
        try:
            audio_bitrate_i = int(audio_bitrate) if audio_bitrate is not None and str(audio_bitrate).strip() else None
        except Exception:
            audio_bitrate_i = None

        if mode == "video":
            audio_bitrate_i = None
        else:
            video_height_i = None

        return {
            "url": url,
            "mode": mode,
            "videoHeight": video_height_i,
            "audioBitrate": audio_bitrate_i,
            "playlist": playlist,
        }
    except Exception:
        return None


def _notify_local_http_url(url: str) -> bool:
    try:
        with _LOCAL_HTTP_ON_URL_LOCK:
            cb = _LOCAL_HTTP_ON_URL
        if cb is None:
            return False
        req = _normalize_download_request(str(url))
        if req is None:
            return False
        cb(req)
        return True
    except Exception:
        return False


def _notify_local_http_download(req: Any) -> bool:
    try:
        with _LOCAL_HTTP_ON_URL_LOCK:
            cb = _LOCAL_HTTP_ON_URL
        if cb is None:
            return False
        norm = _normalize_download_request(req)
        if norm is None:
            return False
        cb(norm)
        return True
    except Exception:
        return False


def _start_best_video_download(url: str, ffmpeg_dir: Optional[Path]) -> None:
    """Start a best-quality (no height cap) video download in the background."""
    url = (url or "").strip()
    if not url or not _URL_RE.match(url):
        return

    def status_cb(msg: str) -> None:
        try:
            _append_log_line(f"auto: {msg}")
        except Exception:
            pass

    def done_cb(err: Optional[str]) -> None:
        try:
            if err:
                _append_log_line(f"auto: failed: {err}")
            else:
                _append_log_line("auto: done")
        except Exception:
            pass

    threading.Thread(
        target=_download_with_ytdlp,
        args=(
            url,
            "video",
            None,
            None,
            False,
            ffmpeg_dir,
            status_cb,
            done_cb,
            None,
            None,
        ),
        daemon=True,
    ).start()


def _set_injected_url(url: str) -> bool:
    url = (url or "").strip()
    if not url or len(url) > 8192:
        return False
    if not _URL_RE.match(url):
        return False
    with _INJECTED_URL_LOCK:
        global _INJECTED_URL_VALUE, _INJECTED_URL_TS
        _INJECTED_URL_VALUE = url
        _INJECTED_URL_TS = time.time()
    return True


def _get_injected_url(max_age_seconds: float = 24 * 60 * 60) -> Optional[str]:
    with _INJECTED_URL_LOCK:
        url = _INJECTED_URL_VALUE
        ts = _INJECTED_URL_TS
    if not url:
        return None
    if max_age_seconds is not None and (time.time() - float(ts)) > float(max_age_seconds):
        return None
    return url


def _start_local_http_listener() -> None:
    """Start a tiny localhost-only HTTP server to inject URLs.

    Endpoints:
    - GET  /set?url=...    -> stores URL and triggers auto-download
    - POST /set            -> body can be JSON {"url": "..."} or plain text (triggers auto-download)
    - GET  /download?v=... -> extension compatibility; 'v' can be YouTube id or full URL
    - POST /download       -> extension compatibility; JSON supports {"url": "..."} (or srcUrl/pageUrl)
    - GET  /last           -> returns last stored URL
    - GET  /ping           -> health
    """
    global _LOCAL_HTTP_STARTED
    with _LOCAL_HTTP_STARTED_LOCK:
        if _LOCAL_HTTP_STARTED:
            return
        _LOCAL_HTTP_STARTED = True

    class Handler(BaseHTTPRequestHandler):
        server_version = "ytdlp-onefile/localhost"

        def log_message(self, format: str, *args: object) -> None:  # quiet
            return

        def _send(self, code: int, payload: dict) -> None:
            try:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(int(code))
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                # Allow browser fetch() from localhost scripts.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()
                self.wfile.write(raw)
            except Exception:
                pass

        def do_OPTIONS(self) -> None:  # CORS preflight
            try:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()
            except Exception:
                pass

        def do_GET(self) -> None:
            try:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/ping":
                    try:
                        now = time.time()
                        with _HTTP_PING_LOCK:
                            global _HTTP_PING_COUNT, _HTTP_PING_LAST_LOG_TS
                            _HTTP_PING_COUNT += 1
                            cnt = _HTTP_PING_COUNT
                            last = float(_HTTP_PING_LAST_LOG_TS or 0.0)
                            if (now - last) >= 2.0:
                                _HTTP_PING_LAST_LOG_TS = now
                                ip = "?"
                                try:
                                    ip = str(getattr(self, "client_address", ["?"])[0])
                                except Exception:
                                    ip = "?"
                                _append_log_line(f"http: ping from {ip} count={cnt}")
                    except Exception:
                        pass
                    self._send(200, {"ok": True})
                    return
                if parsed.path == "/download":
                    qs = urllib.parse.parse_qs(parsed.query or "")
                    v = (qs.get("v") or [""])[0]
                    mode = (qs.get("mode") or [""])[0]
                    h = (qs.get("h") or qs.get("height") or [""])[0]
                    abr = (qs.get("abr") or [""])[0]
                    playlist = (qs.get("playlist") or [""])[0]
                    req = {
                        "url": (v or "").strip(),
                        "mode": mode,
                        "h": h,
                        "abr": abr,
                        "playlist": str(playlist).strip().lower() in ("1", "true", "yes", "y"),
                    }
                    norm = _normalize_download_request(req)
                    if norm is None:
                        self._send(400, {"ok": False, "error": "invalid url"})
                        return
                    _set_injected_url(str(norm.get("url") or ""))
                    _append_log_line(f"http: download url: {norm.get('url')}")
                    started = _notify_local_http_download(norm)
                    self._send(200, {"ok": True, "started": started})
                    return
                if parsed.path == "/last":
                    self._send(200, {"ok": True, "url": _get_injected_url()})
                    return
                if parsed.path == "/set":
                    qs = urllib.parse.parse_qs(parsed.query or "")
                    url = (qs.get("url") or [""])[0]
                    ok = _set_injected_url(url)
                    if ok:
                        _append_log_line(f"http: injected url: {url}")
                        started = _notify_local_http_url(url)
                        self._send(200, {"ok": True, "started": started})
                    else:
                        self._send(400, {"ok": False, "error": "invalid url"})
                    return
                self._send(404, {"ok": False, "error": "not found"})
            except Exception as exc:
                self._send(500, {"ok": False, "error": str(exc)})

        def do_POST(self) -> None:
            try:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path not in ("/set", "/download"):
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except Exception:
                    length = 0
                raw = b""
                if length > 0:
                    raw = self.rfile.read(min(length, 16 * 1024))
                text = (raw or b"").decode("utf-8", errors="replace").strip()
                obj: Any = None
                url = ""
                if text.startswith("{"):
                    try:
                        obj = json.loads(text)
                        if isinstance(obj, dict):
                            url = str(obj.get("url") or "")
                            if not url:
                                # Extension may send these.
                                url = str(obj.get("srcUrl") or "") or str(obj.get("pageUrl") or "")
                    except Exception:
                        url = ""
                if not url:
                    url = text

                # If extension sent a YouTube id, it may come in as just "dQw4...".
                if url and not url.lower().startswith("http") and len(url) <= 32:
                    url = f"https://www.youtube.com/watch?v={url}"

                ok = _set_injected_url(url)
                if ok:
                    if parsed.path == "/download":
                        _append_log_line(f"http: download url: {url}")
                    else:
                        _append_log_line(f"http: injected url: {url}")
                    started = _notify_local_http_download({
                        "url": url,
                        "mode": obj.get("mode") if isinstance(obj, dict) else None,
                        "videoHeight": (obj.get("videoHeight") if isinstance(obj, dict) else None),
                        "audioBitrate": (obj.get("audioBitrate") if isinstance(obj, dict) else None),
                        "playlist": (obj.get("playlist") if isinstance(obj, dict) else None),
                    })
                    self._send(200, {"ok": True, "started": started})
                else:
                    self._send(400, {"ok": False, "error": "invalid url"})
            except Exception as exc:
                self._send(500, {"ok": False, "error": str(exc)})

    def worker() -> None:
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", 8791), Handler)
        except OSError as exc:
            _append_log_line(f"http: listener not started: {exc}")
            return
        try:
            _append_log_line("http: listening on http://localhost:8791")
            httpd.serve_forever(poll_interval=0.5)
        except Exception as exc:
            _append_log_line(f"http: listener stopped: {exc}")
        finally:
            try:
                httpd.server_close()
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()


def _clipboard_url() -> Optional[str]:
    injected = _get_injected_url()
    if injected and _URL_RE.match(injected):
        return injected
    text = _get_clipboard_text()
    if not text:
        return None
    text = text.strip()
    if not _URL_RE.match(text):
        return None
    return text


def _append_log_line(line: str) -> None:
    try:
        log_path = Path(os.environ.get("TEMP", str(Path.home()))) / "ytdlp-onefile.log"
        # Use append mode to avoid races between threads.
        with log_path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _is_autostart_enabled(app_name: str) -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        ) as key:
            winreg.QueryValueEx(key, app_name)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _set_autostart(app_name: str, enabled: bool) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        if enabled:
            exe = str(Path(sys.executable).resolve())
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{exe}"')
        else:
            try:
                winreg.DeleteValue(key, app_name)
            except FileNotFoundError:
                pass


def _download_with_ytdlp(
    url: str,
    mode: str,
    video_height: Optional[int],
    audio_bitrate_kbps: Optional[int],
    playlist: bool,
    ffmpeg_dir: Optional[Path],
    status_cb,
    done_cb,
    extra_progress_hook=None,
    cancel_event: Optional[threading.Event] = None,
):
    class _Canceled(Exception):
        pass

    download_dir = _effective_download_dir()
    seen_paths: set[Path] = set()

    def _remember_path(raw: Optional[str]) -> None:
        if not raw:
            return
        try:
            cleaned = raw.strip().strip('"')
            if not cleaned:
                return
            p = Path(cleaned)
            if p.is_absolute():
                seen_paths.add(p)
            else:
                seen_paths.add((download_dir / p).resolve())
        except Exception:
            pass

    def _cleanup_canceled_items() -> None:
        keep_exts = {".mp3", ".mp4"}
        temp_exts = {".part", ".ytdl", ".tmp", ".temp"}

        candidates: set[Path] = set(seen_paths)
        try:
            for p in download_dir.rglob("*"):
                if p.is_file():
                    low_name = p.name.lower()
                    low_suffix = p.suffix.lower()
                    if low_suffix in temp_exts or low_name.endswith(".part-frag"):
                        candidates.add(p)
        except Exception:
            pass

        for p in candidates:
            try:
                if not p.exists() or not p.is_file():
                    continue
                low_suffix = p.suffix.lower()
                low_name = p.name.lower()
                if low_suffix in keep_exts:
                    continue
                if low_suffix in temp_exts or ".part" in low_name or low_suffix not in keep_exts:
                    p.unlink(missing_ok=True)
            except Exception:
                pass

    def _safe_folder_name(name: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name).strip().rstrip(".")
        return cleaned[:120] or "Playlist"

    try:
        ytdlp_exe = _resolve_ytdlp_exe()
        if not ytdlp_exe or not ytdlp_exe.exists():
            done_cb(
                "yt-dlp.exe not found. Put yt-dlp.exe next to the app (or set YTDLP_EXE)."
            )
            return

        if playlist:
            try:
                title = _probe_first_playlist_title(url, ytdlp_exe, ffmpeg_dir)
                if title:
                    download_dir = _ensure_dir(download_dir / _safe_folder_name(title))
            except Exception:
                pass
            try:
                if extra_progress_hook is not None:
                    cnt = _probe_playlist_count(url, ytdlp_exe, ffmpeg_dir)
                    if isinstance(cnt, int) and cnt > 0:
                        extra_progress_hook({"playlist_index": 0, "playlist_count": int(cnt)})
            except Exception:
                pass

        # Add a per-download token so repeated downloads of the same source
        # always produce unique output names.
        download_instance_id = os.urandom(4).hex()
        output_template = (
            f"%(title).140B [%(id)s] [{download_instance_id}].%(ext)s"
        )

        base_cmd = [str(ytdlp_exe), "--newline", "--progress"]
        if ffmpeg_dir:
            base_cmd += ["--ffmpeg-location", str(ffmpeg_dir)]
        base_cmd += ["-P", f"home:{download_dir}"]
        base_cmd += ["-o", output_template]
        if not playlist:
            base_cmd += ["--no-playlist"]

        def run_cmd(cmd: list[str]) -> tuple[int, str]:
            rc, out = _run_ytdlp_with_progress(
                cmd,
                cwd=download_dir,
                status_cb=status_cb,
                extra_progress_hook=extra_progress_hook,
                cancel_event=cancel_event,
                remember_path_cb=_remember_path,
            )
            if cancel_event is not None and cancel_event.is_set():
                raise _Canceled("Canceled")
            return rc, out

        if mode == "music":
            cmd = list(base_cmd)
            if audio_bitrate_kbps:
                # Prefer audio-only streams when possible. Some sites expose only muxed
                # "bestaudio" formats that still include video; vcodec=none avoids that.
                cmd += [
                    "-f",
                    (
                        f"ba[vcodec=none][abr<={audio_bitrate_kbps}]"
                        f"/ba[vcodec=none]"
                        f"/bestaudio[abr<={audio_bitrate_kbps}]"
                        f"/bestaudio"
                        f"/best"
                    ),
                ]
            else:
                cmd += ["-f", "ba[vcodec=none]/bestaudio/best"]
            cmd += ["-x", "--audio-format", "mp3"]
            # Override any user config that keeps the original download; users expect
            # audio-only output from this mode.
            cmd += ["--no-keep-video"]
            if audio_bitrate_kbps:
                # Prefer yt-dlp's native flag; avoids Windows quoting/parsing issues
                # and targets the ExtractAudio step correctly.
                cmd += ["--audio-quality", f"{audio_bitrate_kbps}K"]
            cmd += [url]

            rc, out = run_cmd(cmd)
            if rc != 0:
                low = (out or "").lower()
                if "no such option" in low and "keep-video" in low:
                    # Older yt-dlp builds may not support the explicit negated flag.
                    # Retry without it.
                    try:
                        cmd2 = [c for c in cmd if c != "--no-keep-video"]
                        rc, out = run_cmd(cmd2)
                    except Exception:
                        pass
                if rc != 0:
                    raise RuntimeError(out.strip() or f"yt-dlp failed ({rc})")
        else:
            def run_video(format_sel: str) -> tuple[int, str]:
                cmd = list(base_cmd)
                cmd += ["-f", format_sel, "--merge-output-format", "mp4", url]
                return run_cmd(cmd)

            if video_height:
                fmt = f"bv*[height<={video_height}]+ba/b[height<={video_height}]/b[height<={video_height}]"
                rc, out = run_video(fmt)
                if rc != 0:
                    low = (out or "").lower()
                    is_format_issue = (
                        "requested format" in low
                        or ("format" in low and "not available" in low)
                        or "no video formats" in low
                    )
                    if is_format_issue:
                        _append_log_line(f"{video_height}p not available; falling back to Best")
                        rc, out = run_video("bv*+ba/b")
                    if rc != 0:
                        raise RuntimeError(out.strip() or f"yt-dlp failed ({rc})")
            else:
                rc, out = run_video("bv*+ba/b")
                if rc != 0:
                    raise RuntimeError(out.strip() or f"yt-dlp failed ({rc})")

        done_cb(None)
    except _Canceled as exc:
        if playlist:
            _cleanup_canceled_items()
        done_cb(str(exc))
    except Exception as exc:
        done_cb(str(exc))


def run() -> int:
    parser = argparse.ArgumentParser(
        prog="ytdlp-onefile",
        description="Downloader that calls external yt-dlp.exe and uses bundled ffmpeg (via PyInstaller).",
        add_help=True,
    )
    parser.add_argument(
        "--ffmpeg-dir",
        help="Directory that contains ffmpeg.exe (and optionally ffprobe.exe). Overrides bundled copy.",
        default=None,
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to yt-dlp (prefix with --). Example: ytdlp-onefile -- <url>",
    )

    ns = parser.parse_args()

    # Support calling like: app.exe <url> ... (without the `--` separator)
    forwarded = ns.args
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    if not forwarded:
        # No args: run tray UI (intended for double-click usage)
        return run_tray(ffmpeg_dir=ns.ffmpeg_dir)

    _default_ffmpeg_dir(ns.ffmpeg_dir)

    # If the user didn't set an explicit output in forwarded args, make the
    # default save location the system Downloads folder (consistent with tray).
    try:
        os.chdir(str(_effective_download_dir()))
    except Exception:
        pass

    ytdlp_exe = _resolve_ytdlp_exe()
    if not ytdlp_exe or not ytdlp_exe.exists():
        print("yt-dlp.exe not found. Put yt-dlp.exe next to the app (or set YTDLP_EXE).", file=sys.stderr)
        if _is_frozen():
            try:
                input("\nPress Enter to exit...")
            except EOFError:
                pass
        return 1

    # If the user didn't set an explicit ffmpeg-location, pass our resolved one.
    resolved_ffmpeg = _resolve_ffmpeg_dir(ns.ffmpeg_dir)
    has_ffmpeg_arg = any(
        a == "--ffmpeg-location" or a.startswith("--ffmpeg-location=")
        for a in forwarded
    )
    cmd = [str(ytdlp_exe)]
    if resolved_ffmpeg and not has_ffmpeg_arg:
        cmd += ["--ffmpeg-location", str(resolved_ffmpeg)]
    cmd += forwarded

    # Run with streaming output so the user can see progress when launched from a console.
    try:
        proc = subprocess.Popen(cmd, cwd=str(Path.cwd()), stdin=subprocess.DEVNULL)
        return int(proc.wait() or 0)
    except Exception as exc:  # pragma: no cover
        print(str(exc), file=sys.stderr)
        return 1


def run_tray(ffmpeg_dir: Optional[str] = None) -> int:
    # Windows-only tray mode: no window.
    # Right-click: show a menu.
    # Clipboard URL is used for actions; if clipboard doesn't contain a URL, do nothing (no popup).
    try:
        # Fire-and-forget yt-dlp update check. (No UI, logs only.)
        _maybe_autoupdate_ytdlp()
        _schedule_ytdlp_version_refresh(force=True)

        # Allow other local scripts to inject a URL.
        _start_local_http_listener()

        # Prefer PyQt tray/menu for a modern Windows-like UI.
        try:
            _append_log_line("tray: trying PyQt")
            return run_tray_qt(ffmpeg_dir=ffmpeg_dir)
        except Exception as exc:
            _append_log_line(f"tray: PyQt failed: {exc}")
            # Fall back to Win32 tray if PyQt is missing/broken.
            pass

        import win32api  # type: ignore
        import win32con  # type: ignore
        import win32gui  # type: ignore

        # pywin32 typing stubs are frequently incomplete/incorrect; treat them as Any
        # so we don't get a sea of false-positive editor errors.
        win32api = cast(Any, win32api)
        win32con = cast(Any, win32con)
        win32gui = cast(Any, win32gui)

        resolved_ffmpeg_dir = _default_ffmpeg_dir(ffmpeg_dir)

        # Register the localhost callback later, after Qt objects exist, so we can
        # safely show/update the progress widget on the UI thread.
        _set_local_http_on_url(None)

        def _start_best_video_download_from_req(req: Any) -> None:
            try:
                if isinstance(req, dict):
                    url = str(req.get("url") or "")
                else:
                    url = str(req or "")
                _start_best_video_download(url, resolved_ffmpeg_dir)
            except Exception:
                pass

        callback_msg = win32con.WM_USER + 20
        WM_UPDATE_TIP = win32con.WM_APP + 1
        try:
            WM_TASKBARCREATED = win32gui.RegisterWindowMessage("TaskbarCreated")
        except Exception:
            WM_TASKBARCREATED = 0
        class_name = "ytdlp_onefile_tray"

        # Non-zero icon ID tends to be more reliable across Windows builds.
        tray_icon_id = 1

        app_name = "ytdlp-onefile"

        # Playlist progress
        playlist_index = 0
        playlist_count = 0
        playlist_downloading = False
        playlist_cancel_event: Optional[threading.Event] = None

        # Tray icon handle (initialized after window creation)
        hicon = None

        # Tooltip updates must run on the tray window thread.
        tip_lock = threading.Lock()
        tip_pending: Optional[str] = None

        def request_tray_tooltip_update(hwnd) -> None:
            nonlocal tip_pending
            try:
                with tip_lock:
                    tip_pending = tooltip_text()
                win32gui.PostMessage(hwnd, WM_UPDATE_TIP, 0, 0)
            except Exception:
                pass

        # Menu IDs
        ID_AUTOSTART = 2002
        ID_CANCEL_PLAYLIST = 2003
        ID_DOWNLOADS_SET = 2010
        ID_DOWNLOADS_RESET = 2011
        ID_DOWNLOADS_CURRENT = 2012
        ID_PROGRESS_POS_RESET = 2013
        ID_YTDLP_VERSION = 2014
        ID_YTDLP_FORCE_UPDATE = 2015
        ID_PLAYLIST_VIDEO_720 = 2301
        ID_PLAYLIST_VIDEO_1080 = 2302
        ID_PLAYLIST_VIDEO_1440 = 2303
        ID_PLAYLIST_VIDEO_2160 = 2304
        ID_PLAYLIST_AUDIO_128 = 2401
        ID_PLAYLIST_AUDIO_192 = 2402
        ID_PLAYLIST_AUDIO_256 = 2403
        ID_PLAYLIST_AUDIO_320 = 2404
        ID_EXIT = 2099

        ID_VIDEO_720 = 2101
        ID_VIDEO_1080 = 2102
        ID_VIDEO_1440 = 2103
        ID_VIDEO_2160 = 2104

        ID_AUDIO_128 = 2201
        ID_AUDIO_192 = 2202
        ID_AUDIO_256 = 2203
        ID_AUDIO_320 = 2204

        def tooltip_text() -> str:
            nonlocal playlist_index, playlist_count
            if playlist_count and playlist_index:
                return f"YT Download {playlist_index}/{playlist_count}"
            return "YT Download"

        def load_tray_icon():
            # Use the EXE icon so it matches Explorer.
            try:
                large, small = win32gui.ExtractIconEx(str(Path(sys.executable).resolve()), 0)
                chosen = small[0] if small else (large[0] if large else None)
                for h in (small or []):
                    if chosen is not None and h == chosen:
                        continue
                    try:
                        win32gui.DestroyIcon(h)
                    except Exception:
                        pass
                for h in (large or []):
                    if chosen is not None and h == chosen:
                        continue
                    try:
                        win32gui.DestroyIcon(h)
                    except Exception:
                        pass
                if chosen is not None:
                    return chosen
            except Exception:
                pass
            return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)

        def update_tray_tooltip(hwnd, text: Optional[str] = None) -> None:
            try:
                flags = win32gui.NIF_TIP
                tip = tooltip_text() if text is None else str(text)
                nid = (hwnd, tray_icon_id, flags, callback_msg, 0, tip)
                win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
            except Exception:
                pass

        def playlist_progress_hook(hwnd, progress) -> None:
            nonlocal playlist_index, playlist_count
            try:
                info = progress.get("info_dict") or progress
                idx = info.get("playlist_index")
                cnt = info.get("playlist_count")
                if isinstance(idx, int) and isinstance(cnt, int) and cnt > 0:
                    if idx != playlist_index or cnt != playlist_count:
                        playlist_index = idx
                        playlist_count = cnt
                        request_tray_tooltip_update(hwnd)
            except Exception:
                pass

        def start_download(
            hwnd,
            mode: str,
            video_height: Optional[int],
            audio_bitrate: Optional[int],
            playlist: bool = False,
        ) -> None:
            nonlocal playlist_index, playlist_count, playlist_downloading, playlist_cancel_event
            url = _clipboard_url()
            if not url:
                return

            _append_log_line(f"trigger {mode}: {url}")

            if playlist:
                if playlist_downloading:
                    return
                playlist_downloading = True
                playlist_cancel_event = threading.Event()
                playlist_index = 0
                playlist_count = 0
                request_tray_tooltip_update(hwnd)

            def done_cb(err: Optional[str]) -> None:
                nonlocal playlist_index, playlist_count, playlist_downloading, playlist_cancel_event
                if err:
                    if "canceled" in err.lower():
                        _append_log_line(f"{mode} canceled")
                    else:
                        _append_log_line(f"{mode} failed: {err}")
                else:
                    _append_log_line(f"{mode} done")
                if playlist:
                    playlist_downloading = False
                    playlist_cancel_event = None
                    playlist_index = 0
                    playlist_count = 0
                    request_tray_tooltip_update(hwnd)

            extra_hook = (lambda p: playlist_progress_hook(hwnd, p)) if playlist else None

            threading.Thread(
                target=_download_with_ytdlp,
                args=(
                    url,
                    mode,
                    video_height,
                    audio_bitrate,
                    playlist,
                    resolved_ffmpeg_dir,
                    (lambda _m: None),
                    done_cb,
                    extra_hook,
                    playlist_cancel_event if playlist else None,
                ),
                daemon=True,
            ).start()

        def build_menu() -> int:
            menu = win32gui.CreatePopupMenu()
            settings_menu = win32gui.CreatePopupMenu()

            as_enabled = _is_autostart_enabled(app_name)
            as_flags = win32con.MF_STRING | (win32con.MF_CHECKED if as_enabled else 0)
            win32gui.AppendMenu(settings_menu, as_flags, ID_AUTOSTART, "Autostart")

            downloads_menu = win32gui.CreatePopupMenu()
            cur = _short_path_for_menu(_effective_download_dir())
            win32gui.AppendMenu(downloads_menu, win32con.MF_STRING | win32con.MF_GRAYED, ID_DOWNLOADS_CURRENT, f"Current: {cur}")
            win32gui.AppendMenu(downloads_menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(downloads_menu, win32con.MF_STRING, ID_DOWNLOADS_SET, "Set Folder...")
            win32gui.AppendMenu(downloads_menu, win32con.MF_STRING, ID_DOWNLOADS_RESET, "Reset to Default")
            win32gui.AppendMenu(settings_menu, win32con.MF_POPUP, downloads_menu, "Downloads")

            win32gui.AppendMenu(settings_menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(settings_menu, win32con.MF_STRING, ID_PROGRESS_POS_RESET, "Reset position")

            try:
                win32gui.AppendMenu(settings_menu, win32con.MF_SEPARATOR, 0, "")
                upd_flags = win32con.MF_STRING | (win32con.MF_GRAYED if _is_ytdlp_updating() else 0)
                win32gui.AppendMenu(settings_menu, upd_flags, ID_YTDLP_FORCE_UPDATE, "Update yt-dlp now")
                win32gui.AppendMenu(settings_menu, win32con.MF_STRING | win32con.MF_GRAYED, ID_YTDLP_VERSION, _ytdlp_version_label())
            except Exception:
                pass

            win32gui.AppendMenu(menu, win32con.MF_POPUP, settings_menu, "Settings")

            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")

            if playlist_downloading:
                win32gui.AppendMenu(menu, win32con.MF_STRING, ID_CANCEL_PLAYLIST, "Cancel")

            playlist_menu = win32gui.CreatePopupMenu()
            playlist_video_menu = win32gui.CreatePopupMenu()
            win32gui.AppendMenu(playlist_video_menu, win32con.MF_STRING, ID_PLAYLIST_VIDEO_720, "720p")
            win32gui.AppendMenu(playlist_video_menu, win32con.MF_STRING, ID_PLAYLIST_VIDEO_1080, "1080p")
            win32gui.AppendMenu(playlist_video_menu, win32con.MF_STRING, ID_PLAYLIST_VIDEO_1440, "1440p")
            win32gui.AppendMenu(playlist_video_menu, win32con.MF_STRING, ID_PLAYLIST_VIDEO_2160, "4K")
            win32gui.AppendMenu(playlist_menu, win32con.MF_POPUP, playlist_video_menu, "Video")

            playlist_audio_menu = win32gui.CreatePopupMenu()
            win32gui.AppendMenu(playlist_audio_menu, win32con.MF_STRING, ID_PLAYLIST_AUDIO_128, "128 kbps")
            win32gui.AppendMenu(playlist_audio_menu, win32con.MF_STRING, ID_PLAYLIST_AUDIO_192, "192 kbps")
            win32gui.AppendMenu(playlist_audio_menu, win32con.MF_STRING, ID_PLAYLIST_AUDIO_256, "256 kbps")
            win32gui.AppendMenu(playlist_audio_menu, win32con.MF_STRING, ID_PLAYLIST_AUDIO_320, "320 kbps")
            win32gui.AppendMenu(playlist_menu, win32con.MF_POPUP, playlist_audio_menu, "Audio")
            win32gui.AppendMenu(menu, win32con.MF_POPUP, playlist_menu, "Playlist")

            video_menu = win32gui.CreatePopupMenu()
            win32gui.AppendMenu(video_menu, win32con.MF_STRING, ID_VIDEO_720, "720p")
            win32gui.AppendMenu(video_menu, win32con.MF_STRING, ID_VIDEO_1080, "1080p")
            win32gui.AppendMenu(video_menu, win32con.MF_STRING, ID_VIDEO_1440, "1440p")
            win32gui.AppendMenu(video_menu, win32con.MF_STRING, ID_VIDEO_2160, "4K")
            win32gui.AppendMenu(menu, win32con.MF_POPUP, video_menu, "Video")

            audio_menu = win32gui.CreatePopupMenu()
            win32gui.AppendMenu(audio_menu, win32con.MF_STRING, ID_AUDIO_128, "128 kbps")
            win32gui.AppendMenu(audio_menu, win32con.MF_STRING, ID_AUDIO_192, "192 kbps")
            win32gui.AppendMenu(audio_menu, win32con.MF_STRING, ID_AUDIO_256, "256 kbps")
            win32gui.AppendMenu(audio_menu, win32con.MF_STRING, ID_AUDIO_320, "320 kbps")
            win32gui.AppendMenu(menu, win32con.MF_POPUP, audio_menu, "Audio")

            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_EXIT, "Exit")
            return menu

        def handle_menu_command(hwnd, cmd: int) -> None:
            if cmd == ID_AUTOSTART:
                current = _is_autostart_enabled(app_name)
                _set_autostart(app_name, not current)
                _append_log_line(f"autostart: {not current}")
            elif cmd == ID_DOWNLOADS_SET:
                chosen = _pick_folder_win32(int(hwnd), "Select download folder", _effective_download_dir())
                if chosen is not None:
                    _set_configured_download_dir(_ensure_dir(chosen))
                    _append_log_line(f"download-dir: {chosen}")
            elif cmd == ID_DOWNLOADS_RESET:
                _set_configured_download_dir(None)
                _append_log_line("download-dir: reset")
            elif cmd == ID_PROGRESS_POS_RESET:
                _set_configured_progress_pos(None, None)
                _append_log_line("progress-pos: reset")
            elif cmd == ID_YTDLP_FORCE_UPDATE:
                started = _start_ytdlp_update(force=True, reason="manual")
                if started:
                    _schedule_ytdlp_version_refresh(force=True)
            elif cmd == ID_CANCEL_PLAYLIST:
                nonlocal playlist_cancel_event
                if playlist_cancel_event is not None:
                    playlist_cancel_event.set()
                    _append_log_line("playlist cancel requested")
            elif cmd in (ID_PLAYLIST_VIDEO_720, ID_PLAYLIST_VIDEO_1080, ID_PLAYLIST_VIDEO_1440, ID_PLAYLIST_VIDEO_2160):
                height = {
                    ID_PLAYLIST_VIDEO_720: 720,
                    ID_PLAYLIST_VIDEO_1080: 1080,
                    ID_PLAYLIST_VIDEO_1440: 1440,
                    ID_PLAYLIST_VIDEO_2160: 2160,
                }[cmd]
                start_download(hwnd, "video", height, None, True)
            elif cmd in (ID_PLAYLIST_AUDIO_128, ID_PLAYLIST_AUDIO_192, ID_PLAYLIST_AUDIO_256, ID_PLAYLIST_AUDIO_320):
                abr = {
                    ID_PLAYLIST_AUDIO_128: 128,
                    ID_PLAYLIST_AUDIO_192: 192,
                    ID_PLAYLIST_AUDIO_256: 256,
                    ID_PLAYLIST_AUDIO_320: 320,
                }[cmd]
                start_download(hwnd, "music", None, abr, True)
            elif cmd in (ID_VIDEO_720, ID_VIDEO_1080, ID_VIDEO_1440, ID_VIDEO_2160):
                height = {
                    ID_VIDEO_720: 720,
                    ID_VIDEO_1080: 1080,
                    ID_VIDEO_1440: 1440,
                    ID_VIDEO_2160: 2160,
                }[cmd]
                start_download(hwnd, "video", height, None)
            elif cmd in (ID_AUDIO_128, ID_AUDIO_192, ID_AUDIO_256, ID_AUDIO_320):
                abr = {
                    ID_AUDIO_128: 128,
                    ID_AUDIO_192: 192,
                    ID_AUDIO_256: 256,
                    ID_AUDIO_320: 320,
                }[cmd]
                start_download(hwnd, "music", None, abr)
            elif cmd == ID_EXIT:
                win32gui.DestroyWindow(hwnd)

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_UPDATE_TIP:
                try:
                    with tip_lock:
                        tip = tip_pending
                    if tip:
                        update_tray_tooltip(hwnd, tip)
                except Exception:
                    pass
                return 0

            if WM_TASKBARCREATED and msg == WM_TASKBARCREATED:
                # Explorer restarted; re-add our tray icon.
                try:
                    if hicon is not None:
                        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
                        nid = (hwnd, tray_icon_id, flags, callback_msg, hicon, tooltip_text())
                        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
                        try:
                            notify_ver = getattr(win32gui, "NOTIFYICON_VERSION_4", None)
                            if notify_ver is not None:
                                win32gui.Shell_NotifyIcon(win32gui.NIM_SETVERSION, (hwnd, tray_icon_id, notify_ver))
                        except Exception:
                            pass
                except Exception:
                    pass
                return 0

            def show_menu() -> None:
                menu = build_menu()
                try:
                    pos = win32gui.GetCursorPos()
                    win32gui.SetForegroundWindow(hwnd)
                    cmd = win32gui.TrackPopupMenu(
                        menu,
                        win32con.TPM_RETURNCMD | win32con.TPM_NONOTIFY | win32con.TPM_RIGHTBUTTON,
                        pos[0],
                        pos[1],
                        0,
                        hwnd,
                        None,
                    )
                    if cmd:
                        handle_menu_command(hwnd, int(cmd))
                    win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
                finally:
                    try:
                        win32gui.DestroyMenu(menu)
                    except Exception:
                        pass

            if msg == callback_msg:
                if lparam == win32con.WM_RBUTTONDOWN:
                    _append_log_line(f"tray-callback right-down lparam={int(lparam)}")
                elif lparam in (
                    win32con.WM_RBUTTONUP,
                    win32con.WM_CONTEXTMENU,
                    win32con.WM_LBUTTONUP,
                ):
                    _append_log_line(f"tray-callback right-up lparam={int(lparam)}")
                    show_menu()

                return 0

            # Fallback: some Windows builds send context/right-click directly to the window,
            # not via the tray callback message.
            if msg in (
                win32con.WM_CONTEXTMENU,
                win32con.WM_RBUTTONUP,
                win32con.WM_RBUTTONDOWN,
                win32con.WM_LBUTTONUP,
                win32con.WM_LBUTTONDOWN,
            ):
                if msg in (win32con.WM_RBUTTONDOWN, win32con.WM_LBUTTONDOWN):
                    _append_log_line(f"wnd right-down msg={int(msg)}")
                else:
                    _append_log_line(f"wnd right-up msg={int(msg)}")
                    show_menu()
                return 0

            if msg == win32con.WM_DESTROY:
                try:
                    win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, tray_icon_id))
                except Exception:
                    pass
                win32gui.PostQuitMessage(0)
                return 0

            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = class_name
        wc.lpfnWndProc = wnd_proc

        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass

        hwnd = win32gui.CreateWindow(
            class_name,
            class_name,
            0,
            0,
            0,
            win32con.CW_USEDEFAULT,
            win32con.CW_USEDEFAULT,
            0,
            0,
            wc.hInstance,
            None,
        )

        hicon = load_tray_icon()
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        nid = (hwnd, tray_icon_id, flags, callback_msg, hicon, tooltip_text())
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

        # If PyQt is unavailable and we’re in Win32 fallback mode,
        # still honor localhost auto-download (best-quality video only).
        _set_local_http_on_url(_start_best_video_download_from_req)

        # Ask for modern notification icon behavior (improves mouse event delivery on Win10/11).
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_SETVERSION, (hwnd, tray_icon_id, win32gui.NOTIFYICON_VERSION_4))
        except Exception:
            pass

        win32gui.PumpMessages()
        return 0
    except Exception as exc:  # pragma: no cover
        _write_crash_log("Tray startup failed", exc)
        return 1


def run_tray_qt(ffmpeg_dir: Optional[str] = None) -> int:
    """PyQt tray menu implementation (dark + slightly translucent)."""
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets  # type: ignore

        # Allow other local scripts to inject a URL.
        _start_local_http_listener()

        def _try_enable_windows_backdrop(widget: "QtWidgets.QWidget", kind: str = "transient") -> None:
            """Best-effort Windows 11 backdrop (Mica / Acrylic-like).

            Notes:
            - Works only on Windows 11 (and only for certain window types).
            - Popup menus are not guaranteed to support Mica; we attempt and ignore failures.
            """
            if sys.platform != "win32":
                return
            try:
                # Win11 is build >= 22000
                if getattr(sys, "getwindowsversion")().build < 22000:
                    return
            except Exception:
                return

            try:
                import ctypes
                from ctypes import wintypes

                hwnd = int(widget.winId())
                if not hwnd:
                    return

                dwm = ctypes.WinDLL("dwmapi")
                DwmSetWindowAttribute = dwm.DwmSetWindowAttribute
                DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPCVOID, wintypes.DWORD]
                HRESULT = getattr(wintypes, "HRESULT", ctypes.c_long)
                DwmSetWindowAttribute.restype = HRESULT

                # Dark mode hint (helps match the dark menu styling)
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                dark = ctypes.c_int(1)
                DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark), ctypes.sizeof(dark))

                # Preferred approach (Win11 22H2+): system backdrop type.
                # 2=MainWindow(Mica), 3=Transient(Acrylic-ish), 4=Tabbed(Mica Alt)
                DWMWA_SYSTEMBACKDROP_TYPE = 38
                backdrop_map = {"mica": 2, "transient": 3, "tabbed": 4}
                backdrop_val = ctypes.c_int(int(backdrop_map.get(kind, 3)))
                DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_SYSTEMBACKDROP_TYPE,
                    ctypes.byref(backdrop_val),
                    ctypes.sizeof(backdrop_val),
                )

                # Legacy/undocumented (older Win11 builds): DWMWA_MICA_EFFECT (bool)
                DWMWA_MICA_EFFECT = 1029
                mica_on = ctypes.c_int(1)
                DwmSetWindowAttribute(hwnd, DWMWA_MICA_EFFECT, ctypes.byref(mica_on), ctypes.sizeof(mica_on))

                # More reliable for popup menus: Acrylic blur behind (Win10/11).
                # Uses undocumented SetWindowCompositionAttribute; ignore if unavailable.
                try:
                    user32 = ctypes.WinDLL("user32")
                    SetWindowCompositionAttribute = getattr(user32, "SetWindowCompositionAttribute", None)
                    if SetWindowCompositionAttribute is not None:
                        class ACCENT_POLICY(ctypes.Structure):
                            _fields_ = [
                                ("AccentState", ctypes.c_int),
                                ("AccentFlags", ctypes.c_int),
                                ("GradientColor", ctypes.c_int),
                                ("AnimationId", ctypes.c_int),
                            ]

                        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
                            _fields_ = [
                                ("Attribute", ctypes.c_int),
                                ("Data", ctypes.c_void_p),
                                ("SizeOfData", ctypes.c_size_t),
                            ]

                        WCA_ACCENT_POLICY = 19
                        ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

                        # GradientColor is AABBGGRR. Use semi-transparent black.
                        # Alpha 0xCC ~= 80% opacity.
                        gradient = 0xCC000000
                        accent = ACCENT_POLICY(
                            ACCENT_ENABLE_ACRYLICBLURBEHIND,
                            2,  # draw all borders
                            gradient,
                            0,
                        )
                        data = WINDOWCOMPOSITIONATTRIBDATA(
                            WCA_ACCENT_POLICY,
                            ctypes.cast(ctypes.byref(accent), ctypes.c_void_p),
                            ctypes.sizeof(accent),
                        )
                        SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
                except Exception:
                    pass
            except Exception:
                return

        resolved_ffmpeg_dir = _default_ffmpeg_dir(ffmpeg_dir)

        # Register localhost callback later, after Qt objects exist, so we can
        # safely show/update the progress widget on the UI thread.
        _set_local_http_on_url(None)

        app = cast(QtWidgets.QApplication, QtWidgets.QApplication.instance() or QtWidgets.QApplication([]))
        app.setQuitOnLastWindowClosed(False)
        try:
            app.setStyle("Fusion")
        except Exception:
            pass

        # Make submenus appear faster.
        try:
            class _FastMenuStyle(QtWidgets.QProxyStyle):
                def styleHint(self, hint, option=None, widget=None, returnData=None):
                    if hint == QtWidgets.QStyle.StyleHint.SH_Menu_SubMenuPopupDelay:
                        return 0
                    return super().styleHint(hint, option, widget, returnData)

            app.setStyle(_FastMenuStyle(app.style()))
        except Exception:
            pass

        try:
            available = bool(QtWidgets.QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:
            available = False
        _append_log_line(f"qt: systemTrayAvailable={available}")
        if not available:
            raise RuntimeError("System tray not available")

        class TrayBridge(QtCore.QObject):
            tooltipChanged = QtCore.pyqtSignal(str)
            progressChanged = QtCore.pyqtSignal(int)
            statusChanged = QtCore.pyqtSignal(str)
            hideProgress = QtCore.pyqtSignal()
            playlistChanged = QtCore.pyqtSignal(int, int)
            autoDownloadRequested = QtCore.pyqtSignal(object)

        bridge = TrayBridge()

        def _resolve_progress_gif() -> Optional[Path]:
            # Prefer a file shipped next to the executable / internal folder,
            # but fall back to dev-mode vendor/.
            candidates: list[Path] = []
            exe_dir = _executable_dir()
            if exe_dir:
                candidates.append(exe_dir / "progress.gif")
                candidates.append(exe_dir / "_internal" / "progress.gif")
                candidates.append(exe_dir / "blue-archive-koharu.gif")
                candidates.append(exe_dir / "_internal" / "blue-archive-koharu.gif")
                candidates.append(exe_dir / "vendor" / "blue-archive-koharu.gif")
                candidates.append(exe_dir / "_internal" / "vendor" / "blue-archive-koharu.gif")
            bundled = _bundled_dir()
            if bundled:
                candidates.append(bundled / "progress.gif")
                candidates.append(bundled / "blue-archive-koharu.gif")
                candidates.append(bundled / "vendor" / "blue-archive-koharu.gif")
            candidates.append(Path(__file__).resolve().parent / "vendor" / "blue-archive-koharu.gif")

            for p in candidates:
                try:
                    if p.exists():
                        return p
                except Exception:
                    continue
            return None

        progress_gif_path = _resolve_progress_gif()
        try:
            formats = []
            try:
                for f in QtGui.QImageReader.supportedImageFormats():
                    raw = getattr(f, "data", lambda: b"")()
                    if isinstance(raw, (bytes, bytearray)):
                        formats.append(bytes(raw).decode("ascii", errors="ignore").lower())
                    else:
                        formats.append(str(raw).lower())
            except Exception:
                formats = []
            _append_log_line(
                f"qt: progress-gif path={progress_gif_path} gifSupported={'gif' in set(formats)}"
            )
        except Exception:
            pass

        class ProgressWidget(QtWidgets.QWidget):
            def __init__(self):
                super().__init__(None)
                self.setWindowFlags(
                    QtCore.Qt.WindowType.Tool
                    | QtCore.Qt.WindowType.FramelessWindowHint
                    | QtCore.Qt.WindowType.WindowStaysOnTopHint
                    | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
                )
                try:
                    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
                except Exception:
                    pass

                self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
                self.resize(300, 76)

                self._drag_offset: Optional[QtCore.QPoint] = None
                self._cancel_event: Optional[threading.Event] = None
                self._pct: int = 0
                self._playlist_index: int = 0
                self._playlist_count: int = 0
                self._status: str = "Downloading..."

                outer = QtWidgets.QHBoxLayout(self)
                outer.setContentsMargins(14, 12, 14, 12)
                outer.setSpacing(10)

                # Left: animated GIF (optional)
                self.gif_label = QtWidgets.QLabel("")
                self.gif_label.setObjectName("gifLabel")
                self.gif_label.setFixedSize(56, 56)
                self.gif_label.setVisible(False)
                self._movie: Optional[QtGui.QMovie] = None
                if progress_gif_path is not None:
                    try:
                        mv = QtGui.QMovie(str(progress_gif_path))
                        mv.setCacheMode(QtGui.QMovie.CacheMode.CacheAll)
                        mv.setScaledSize(QtCore.QSize(56, 56))
                        ok = bool(mv.isValid())
                        _append_log_line(f"qt: progress-gif movieValid={ok}")
                        if ok:
                            self.gif_label.setMovie(mv)
                            self._movie = mv
                            self.gif_label.setVisible(True)
                    except Exception:
                        self._movie = None
                        self.gif_label.setVisible(False)

                outer.addWidget(self.gif_label, 0, QtCore.Qt.AlignmentFlag.AlignTop)

                # Right: badges on top, progress row below
                right = QtWidgets.QVBoxLayout()
                right.setContentsMargins(0, 0, 0, 0)
                right.setSpacing(8)

                header = QtWidgets.QHBoxLayout()
                header.setContentsMargins(0, 0, 0, 0)
                header.setSpacing(8)

                self.status_badge = QtWidgets.QLabel("Downloading... 0%")
                self.status_badge.setObjectName("statusBadge")
                try:
                    self.status_badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft)
                except Exception:
                    pass
                header.addWidget(self.status_badge, 1)

                self.playlist_badge = QtWidgets.QLabel("")
                self.playlist_badge.setObjectName("playlistBadge")
                try:
                    self.playlist_badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                except Exception:
                    pass
                self.playlist_badge.setVisible(False)
                header.addWidget(self.playlist_badge, 0)

                self.progress = QtWidgets.QProgressBar()
                self.progress.setRange(0, 100)
                try:
                    self.progress.setTextVisible(False)
                except Exception:
                    pass

                row = QtWidgets.QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(10)
                row.addWidget(self.progress, 1)

                self.cancel_btn = QtWidgets.QPushButton("Cancel")
                self.cancel_btn.setFixedWidth(70)
                try:
                    self.cancel_btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
                except Exception:
                    pass
                self.cancel_btn.clicked.connect(self._on_cancel_clicked)
                row.addWidget(self.cancel_btn, 0)

                right.addLayout(header)
                right.addLayout(row)
                outer.addLayout(right, 1)

                self.setStyleSheet(
                    "QLabel { color: #ffffff; }"
                    "QLabel#gifLabel { background: transparent; border: none; padding: 0; }"
                    "QLabel#statusBadge, QLabel#playlistBadge {"
                    " background: rgba(255,255,255,18);"
                    " border: 1px solid rgba(255,255,255,22);"
                    " border-radius: 8px;"
                    " padding: 4px 10px;"
                    "}"
                    "QProgressBar {"
                    " background: rgba(255,255,255,30);"
                    " border-radius: 5px;"
                    " height: 10px;"
                    "}"
                    "QProgressBar::chunk {"
                    " background: #C3B1E1;"
                    " border-radius: 5px;"
                    "}"
                    "QPushButton {"
                    " color: #ffffff;"
                    " background: rgba(255,255,255,20);"
                    " border: 1px solid rgba(255,255,255,28);"
                    " border-radius: 6px;"
                    " padding: 4px 10px;"
                    "}"
                    "QPushButton:hover { background: rgba(255,255,255,28); }"
                    "QPushButton:pressed { background: rgba(255,255,255,34); }"
                    "QPushButton:disabled { color: rgba(255,255,255,120); }"
                )

            def begin(self, cancel_event: Optional[threading.Event]) -> None:
                self._cancel_event = cancel_event
                try:
                    self._pct = 0
                    self._playlist_index = 0
                    self._playlist_count = 0
                    self._status = "Downloading..."
                    self._refresh_badges()
                    self.progress.setValue(0)
                    self.cancel_btn.setEnabled(True)
                except Exception:
                    pass

            def showEvent(self, event) -> None:  # type: ignore[override]
                try:
                    if self._movie is not None:
                        self._movie.start()
                except Exception:
                    pass
                super().showEvent(event)

            def hideEvent(self, event) -> None:  # type: ignore[override]
                try:
                    if self._movie is not None:
                        self._movie.stop()
                except Exception:
                    pass
                super().hideEvent(event)

            def _refresh_badges(self) -> None:
                try:
                    if self._status.strip().lower().startswith("downloading"):
                        self.status_badge.setText(f"Downloading... {self._pct}%")
                    else:
                        self.status_badge.setText(self._status)

                    if self._playlist_count > 0:
                        self.playlist_badge.setText(f"{self._playlist_index}/{self._playlist_count}")
                        self.playlist_badge.setVisible(True)
                    else:
                        self.playlist_badge.setVisible(False)
                except Exception:
                    pass

            def set_percent(self, pct: int) -> None:
                try:
                    self._pct = int(pct)
                    if self._pct < 0:
                        self._pct = 0
                    if self._pct > 100:
                        self._pct = 100
                    self.progress.setValue(self._pct)
                    self._refresh_badges()
                except Exception:
                    pass

            def set_status(self, text: str) -> None:
                try:
                    self._status = str(text or "").strip() or "Downloading..."
                    self._refresh_badges()
                except Exception:
                    pass

            def schedule_hide(self, delay_ms: int = 1500) -> None:
                try:
                    QtCore.QTimer.singleShot(int(delay_ms), self.hide)
                except Exception:
                    try:
                        self.hide()
                    except Exception:
                        pass

            def set_playlist_progress(self, index: int, count: int) -> None:
                try:
                    self._playlist_index = int(index)
                    self._playlist_count = int(count)
                    if self._playlist_index < 0:
                        self._playlist_index = 0
                    if self._playlist_count < 0:
                        self._playlist_count = 0
                    self._refresh_badges()
                except Exception:
                    pass

            def _on_cancel_clicked(self) -> None:
                try:
                    if self._cancel_event is not None:
                        self._cancel_event.set()
                    self.cancel_btn.setEnabled(False)
                    self._status = "Canceling..."
                    self._refresh_badges()
                except Exception:
                    pass

            def paintEvent(self, event) -> None:  # type: ignore[override]
                try:
                    painter = QtGui.QPainter(self)
                    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
                    rect = self.rect().adjusted(0, 0, -1, -1)

                    bg = QtGui.QColor(20, 20, 20, 230)
                    border = QtGui.QColor(255, 255, 255, 28)
                    painter.setBrush(bg)
                    pen = QtGui.QPen(border)
                    pen.setWidth(1)
                    painter.setPen(pen)
                    painter.drawRoundedRect(rect, 10, 10)
                except Exception:
                    pass

            def mousePressEvent(self, event) -> None:  # type: ignore[override]
                try:
                    if event.button() == QtCore.Qt.MouseButton.LeftButton:
                        self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                        event.accept()
                        return
                except Exception:
                    pass
                super().mousePressEvent(event)

            def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
                try:
                    if self._drag_offset is not None and (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
                        self.move(event.globalPosition().toPoint() - self._drag_offset)
                        event.accept()
                        return
                except Exception:
                    pass
                super().mouseMoveEvent(event)

            def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
                try:
                    if event.button() == QtCore.Qt.MouseButton.LeftButton:
                        self._drag_offset = None
                        try:
                            _set_configured_progress_pos(self.x(), self.y())
                        except Exception:
                            pass
                except Exception:
                    pass
                super().mouseReleaseEvent(event)

        progress_widget = ProgressWidget()
        try:
            restored = False
            saved_pos = _get_configured_progress_pos()
            if saved_pos is not None:
                x0, y0 = saved_pos
                rect = QtCore.QRect(x0, y0, progress_widget.width(), progress_widget.height())
                for s in QtGui.QGuiApplication.screens() or []:
                    try:
                        if s.availableGeometry().intersects(rect):
                            progress_widget.move(int(x0), int(y0))
                            restored = True
                            break
                    except Exception:
                        continue

            if not restored:
                screen = QtGui.QGuiApplication.primaryScreen()
                if screen is not None:
                    geo = screen.availableGeometry()
                    x = int(geo.x() + geo.width() - progress_widget.width() - 20)
                    y = int(geo.y() + geo.height() - progress_widget.height() - 20)
                    progress_widget.move(x, y)
        except Exception:
            pass

        try:
            app.aboutToQuit.connect(lambda: _set_configured_progress_pos(progress_widget.x(), progress_widget.y()))
        except Exception:
            pass

        bridge.progressChanged.connect(progress_widget.set_percent)
        bridge.statusChanged.connect(progress_widget.set_status)
        bridge.hideProgress.connect(progress_widget.schedule_hide)
        bridge.playlistChanged.connect(progress_widget.set_playlist_progress)

        def _auto_progress_hook(progress: dict) -> None:
            try:
                info = progress.get("info_dict") or progress
                idx = info.get("playlist_index")
                cnt = info.get("playlist_count")
                if isinstance(idx, int) and isinstance(cnt, int) and cnt > 0:
                    bridge.playlistChanged.emit(int(idx), int(cnt))

                if (progress.get("status") or "") != "downloading":
                    return
                downloaded = progress.get("downloaded_bytes") or 0
                total = progress.get("total_bytes") or progress.get("total_bytes_estimate") or 0
                if total:
                    pct = int((float(downloaded) / float(total)) * 100.0)
                    if pct < 0:
                        pct = 0
                    if pct > 100:
                        pct = 100
                    bridge.progressChanged.emit(int(pct))
            except Exception:
                pass

        def _start_download_qt(req: Any) -> None:
            norm = _normalize_download_request(req)
            if norm is None:
                return

            url = str(norm.get("url") or "").strip()
            mode = str(norm.get("mode") or "video")
            video_height = norm.get("videoHeight")
            audio_bitrate = norm.get("audioBitrate")
            playlist = bool(norm.get("playlist") or False)

            if not url or not _URL_RE.match(url):
                return

            try:
                if progress_widget.isVisible():
                    # Avoid stacking multiple auto-downloads on top of each other.
                    _append_log_line("auto: ignored (download already visible)")
                    return
            except Exception:
                pass

            if mode == "music":
                q = f"{int(audio_bitrate)}kbps" if isinstance(audio_bitrate, int) and audio_bitrate else "best"
            else:
                q = f"{int(video_height)}p" if isinstance(video_height, int) and video_height else "best"
            _append_log_line(f"auto: trigger {mode} ({q}){' playlist' if playlist else ''}: {url}")

            cancel_event = threading.Event()
            try:
                progress_widget.begin(cancel_event)
                bridge.statusChanged.emit(f"Downloading {mode} ({q})...")
                bridge.progressChanged.emit(0)
                progress_widget.show()
            except Exception:
                pass

            def done_cb(err: Optional[str]) -> None:
                try:
                    if err:
                        _append_log_line(f"auto: failed: {err}")
                    else:
                        _append_log_line("auto: done")
                    bridge.progressChanged.emit(100)
                    bridge.statusChanged.emit("Done")
                    bridge.hideProgress.emit()
                except Exception:
                    pass

            threading.Thread(
                target=_download_with_ytdlp,
                args=(
                    url,
                    mode,
                    (int(video_height) if isinstance(video_height, int) else None),
                    (int(audio_bitrate) if isinstance(audio_bitrate, int) else None),
                    bool(playlist),
                    resolved_ffmpeg_dir,
                    (lambda m: bridge.statusChanged.emit(str(m))),
                    done_cb,
                    _auto_progress_hook,
                    cancel_event,
                ),
                daemon=True,
            ).start()

        bridge.autoDownloadRequested.connect(_start_download_qt)

        # When localhost injects a URL/request, auto-start the requested download (with progress UI).
        _set_local_http_on_url(lambda req: bridge.autoDownloadRequested.emit(req))

        def _icon_from_path(path: Path) -> Optional[QtGui.QIcon]:
            try:
                if not path.exists():
                    return None

                base = QtGui.QIcon(str(path))
                if base.isNull():
                    return None

                # Build a multi-size icon so the tray can pick the correct size.
                # For .ico files, base.pixmap(sz,sz) will select the closest embedded frame,
                # which is usually much sharper than scaling a single large pixmap.
                ico = QtGui.QIcon()
                for sz in (16, 20, 24, 32, 48, 64):
                    pm = base.pixmap(sz, sz)
                    if pm.isNull():
                        # Fallback: try scaling whatever Qt can load.
                        pm2 = QtGui.QPixmap(str(path))
                        if not pm2.isNull():
                            pm = pm2.scaled(
                                sz,
                                sz,
                                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                QtCore.Qt.TransformationMode.SmoothTransformation,
                            )
                    if not pm.isNull():
                        ico.addPixmap(pm)
                return None if ico.isNull() else ico
            except Exception:
                return None

        def resolve_icon() -> QtGui.QIcon:
            # Prefer an actual shipped .ico file; embedded exe icons can be inconsistent for tray.
            candidates: list[Path] = []
            exe_dir = _executable_dir()
            if exe_dir:
                candidates.append(exe_dir / "app.ico")
                candidates.append(exe_dir / "_internal" / "app.ico")
            bundled = _bundled_dir()
            if bundled:
                candidates.append(bundled / "app.ico")
            candidates.append(Path(__file__).resolve().parent / "vendor" / "app.ico")

            for p in candidates:
                ico = _icon_from_path(p)
                if ico is not None:
                    _append_log_line(f"qt: tray icon from {p}")
                    return ico

            try:
                ico = QtGui.QIcon(str(Path(sys.executable).resolve()))
                if not ico.isNull():
                    _append_log_line("qt: tray icon from exe")
                    return ico
            except Exception:
                pass

            _append_log_line("qt: tray icon fallback")
            try:
                style = app.style()
                if style is not None:
                    return style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
            except Exception:
                pass
            return QtGui.QIcon()

        icon = resolve_icon()
        if icon.isNull():
            try:
                style = app.style()
                if style is not None:
                    icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
            except Exception:
                pass
        tray = QtWidgets.QSystemTrayIcon(icon)

        def set_tooltip(text: str) -> None:
            tray.setToolTip(text)

        bridge.tooltipChanged.connect(set_tooltip)

        playlist_index = 0
        playlist_count = 0
        playlist_downloading = False
        playlist_cancel_event: Optional[threading.Event] = None

        def tooltip_text() -> str:
            nonlocal playlist_index, playlist_count
            if playlist_count and playlist_index:
                return f"YT Download {playlist_index}/{playlist_count}"
            return "YT Download"

        tray.setToolTip(tooltip_text())

        # Preload version string so it is ready when menus are opened.
        _schedule_ytdlp_version_refresh(force=True)

        menu = QtWidgets.QMenu()
        menu.setObjectName("trayMenu")
        # Dark + glassy (translucent). Note: true background blur (Acrylic/Mica)
        # requires native Windows composition APIs; this is a Qt-only approximation.
        try:
            menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        except Exception:
            pass
        try:
            menu.setWindowOpacity(0.97)
        except Exception:
            pass

        # Try to avoid square menu window corners.
        try:
            menu.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
        except Exception:
            pass
        menu.setStyleSheet(
            """
            QMenu#trayMenu {
                background-color: rgba(18, 18, 18, 220);
                border: 1px solid rgba(255, 255, 255, 28);
                border-radius: 10px;
                padding: 2px;
            }
            QMenu#trayMenu::item {
                padding: 5px 25px 5px 15px;
                color: #ffffff;
            }
            QMenu#trayMenu::item:selected {
                background-color: rgba(255, 255, 255, 34);
                border-radius: 8px;
            }
            QMenu#trayMenu::separator {
                height: 1px;
                background: rgba(255, 255, 255, 18);
                margin: 2px 6px;
            }
            """
        )
        try:
            # Reserve horizontal space so submenu arrows never overlap labels.
            menu.setMinimumWidth(30)
        except Exception:
            pass

        def _apply_rounded_menu_mask(w: "QtWidgets.QWidget", radius: int = 10) -> None:
            # Qt can paint rounded corners via stylesheet, but the native popup window
            # may still be a square. Masking makes the window edges actually rounded.
            try:
                r = w.rect()
                if r.isNull():
                    return
                path = QtGui.QPainterPath()
                path.addRoundedRect(QtCore.QRectF(r), float(radius), float(radius))
                poly = path.toFillPolygon().toPolygon()
                w.setMask(QtGui.QRegion(poly))
            except Exception:
                pass

        def _round_menu_later(w: "QtWidgets.QWidget") -> None:
            QtCore.QTimer.singleShot(0, lambda: _apply_rounded_menu_mask(w, 10))

        menu.aboutToShow.connect(lambda: _round_menu_later(menu))

        action_autostart = QtGui.QAction("Autostart", menu)
        action_autostart.setCheckable(True)

        action_cancel_playlist = QtGui.QAction("Cancel", menu)
        action_cancel_playlist.setVisible(False)

        submenu_video = cast(QtWidgets.QMenu, menu.addMenu("Video"))
        submenu_audio = cast(QtWidgets.QMenu, menu.addMenu("Audio"))

        submenu_playlist = cast(QtWidgets.QMenu, menu.addMenu("Playlist"))
        submenu_playlist_video = cast(QtWidgets.QMenu, submenu_playlist.addMenu("Video"))
        submenu_playlist_audio = cast(QtWidgets.QMenu, submenu_playlist.addMenu("Audio"))

        # Ensure submenus get the same styling (rounded + glass).
        for sm in (submenu_video, submenu_audio, submenu_playlist, submenu_playlist_video, submenu_playlist_audio):
            try:
                sm.setObjectName("trayMenu")
            except Exception:
                pass
            try:
                sm.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
            except Exception:
                pass
            try:
                sm.setWindowOpacity(0.98)
            except Exception:
                pass
            try:
                sm.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
            except Exception:
                pass
            try:
                sm.aboutToShow.connect(lambda _sm=sm: _round_menu_later(_sm))
            except Exception:
                pass

        # Applying native Windows backdrops (Acrylic/Mica) to QMenu popups can produce
        # an extra rectangular background behind the rounded/translucent Qt styling.
        # Keep the menu single-layer by default.
        def _apply_backdrop_later(w: "QtWidgets.QWidget") -> None:
            return

        _enable_native_menu_backdrop = False
        if _enable_native_menu_backdrop:
            def _apply_backdrop_later(w: "QtWidgets.QWidget") -> None:
                QtCore.QTimer.singleShot(0, lambda: _try_enable_windows_backdrop(w, "transient"))

            menu.aboutToShow.connect(lambda: _apply_backdrop_later(menu))
            submenu_video.aboutToShow.connect(lambda: _apply_backdrop_later(submenu_video))
            submenu_audio.aboutToShow.connect(lambda: _apply_backdrop_later(submenu_audio))
            submenu_playlist.aboutToShow.connect(lambda: _apply_backdrop_later(submenu_playlist))
            submenu_playlist_video.aboutToShow.connect(lambda: _apply_backdrop_later(submenu_playlist_video))
            submenu_playlist_audio.aboutToShow.connect(lambda: _apply_backdrop_later(submenu_playlist_audio))
        action_exit = QtGui.QAction("Exit", menu)

        submenu_settings = cast(QtWidgets.QMenu, menu.addMenu("Settings"))
        submenu_settings.addAction(action_autostart)

        submenu_downloads = cast(QtWidgets.QMenu, submenu_settings.addMenu("Downloads"))

        # Match the styling used for the other submenus.
        for sm in (submenu_settings, submenu_downloads):
            try:
                sm.setObjectName("trayMenu")
            except Exception:
                pass
            try:
                sm.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
            except Exception:
                pass
            try:
                sm.setWindowOpacity(0.98)
            except Exception:
                pass
            try:
                sm.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
            except Exception:
                pass
            try:
                sm.aboutToShow.connect(lambda _sm=sm: _apply_backdrop_later(_sm))
            except Exception:
                pass
            try:
                sm.aboutToShow.connect(lambda _sm=sm: _round_menu_later(_sm))
            except Exception:
                pass

        # Slightly increase padding only inside Settings submenus.
        try:
            _settings_css = (
                menu.styleSheet()
                + "\n"
                + "QMenu#trayMenu { padding: 2px; }\n"
                + "QMenu#trayMenu::item { padding: 5px 42px 5px 12px; }\n"
                + "QMenu#trayMenu::separator { margin: 2px 2px; }\n"
            )
            submenu_settings.setStyleSheet(_settings_css)
            submenu_downloads.setStyleSheet(_settings_css)
        except Exception:
            pass

        action_dl_current = QtGui.QAction("", submenu_downloads)
        action_dl_current.setEnabled(False)
        action_dl_set = QtGui.QAction("Set Folder...", submenu_downloads)
        action_dl_reset = QtGui.QAction("Reset to Default", submenu_downloads)
        submenu_downloads.addAction(action_dl_current)
        submenu_downloads.addSeparator()
        submenu_downloads.addAction(action_dl_set)
        submenu_downloads.addAction(action_dl_reset)

        action_reset_popup_pos = QtGui.QAction("Reset position", submenu_settings)
        submenu_settings.addSeparator()
        submenu_settings.addAction(action_reset_popup_pos)

        # yt-dlp controls (keep menu open + update label live)
        submenu_settings.addSeparator()

        ytdlp_live_timer = QtCore.QTimer(submenu_settings)
        ytdlp_live_timer.setInterval(250)

        ytdlp_transient = {"text": None, "until": 0.0}

        def _should_show_ytdlp_checkmark() -> bool:
            try:
                if _is_ytdlp_updating():
                    return True
                now = time.time()
                transient_text = (ytdlp_transient.get("text") or "")
                transient_until = float(ytdlp_transient.get("until") or 0.0)
                if transient_text and now < transient_until:
                    # Show checkmark while user-initiated checking/updating is displayed.
                    return True
            except Exception:
                return False
            return False

        class _YtDlpMenuRow(QtWidgets.QWidget):
            def __init__(self, parent_menu: "QtWidgets.QMenu"):
                super().__init__(parent_menu)
                self._menu = parent_menu
                self._text = "yt-dlp: (checking...)"
                self._hover = False
                self._checked = False

                try:
                    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
                except Exception:
                    pass
                self.setMouseTracking(True)

                try:
                    self.setSizePolicy(
                        QtWidgets.QSizePolicy.Policy.Expanding,
                        QtWidgets.QSizePolicy.Policy.Fixed,
                    )
                except Exception:
                    pass
                try:
                    # Guard against 0-height size hints causing the row to disappear.
                    self.setMinimumHeight(24)
                except Exception:
                    pass

            def setText(self, text: str) -> None:
                self._text = str(text)
                self.update()

            def setChecked(self, checked: bool) -> None:
                self._checked = bool(checked)
                self.update()

            def enterEvent(self, event):  # noqa: ANN001
                self._hover = True
                self.update()
                try:
                    super().enterEvent(event)
                except Exception:
                    pass

            def leaveEvent(self, a0):  # noqa: ANN001
                event = a0
                self._hover = False
                self.update()
                try:
                    super().leaveEvent(event)
                except Exception:
                    pass

            def mouseReleaseEvent(self, a0):  # noqa: ANN001
                event = a0
                try:
                    btn = getattr(event, "button", lambda: None)()
                    if btn == QtCore.Qt.MouseButton.LeftButton and self.isEnabled():
                        on_force_update_clicked()
                        try:
                            accept_fn = getattr(event, "accept", None)
                            if callable(accept_fn):
                                accept_fn()
                        except Exception:
                            pass
                        return
                except Exception:
                    pass
                try:
                    super().mouseReleaseEvent(event)
                except Exception:
                    pass

            def sizeHint(self):  # noqa: ANN001
                try:
                    opt = QtWidgets.QStyleOptionMenuItem()
                    try:
                        opt.initFrom(self._menu)
                    except Exception:
                        pass
                    opt.text = self._text
                    opt.menuItemType = QtWidgets.QStyleOptionMenuItem.MenuItemType.Normal
                    opt.checkType = QtWidgets.QStyleOptionMenuItem.CheckType.NonExclusive
                    opt.checked = bool(self._checked)
                    opt.state = QtWidgets.QStyle.StateFlag.State_Enabled
                    style = cast(Any, self.style())
                    sz = style.sizeFromContents(
                        QtWidgets.QStyle.ContentsType.CT_MenuItem,
                        opt,
                        QtCore.QSize(),
                        self._menu,
                    )
                    if sz.height() < 24:
                        sz.setHeight(24)
                    return sz
                except Exception:
                    try:
                        fm = self.fontMetrics()
                        w = int(fm.horizontalAdvance(self._text) + 60)
                        if w < 1:
                            w = 1
                        return QtCore.QSize(w, 24)
                    except Exception:
                        return QtCore.QSize(1, 24)

            def paintEvent(self, a0):  # noqa: ANN001
                event = a0
                try:
                    p = QtGui.QPainter(self)
                    opt = QtWidgets.QStyleOptionMenuItem()
                    try:
                        opt.initFrom(self._menu)
                    except Exception:
                        opt.initFrom(self)
                    opt.rect = self.rect()
                    opt.text = self._text
                    opt.menuItemType = QtWidgets.QStyleOptionMenuItem.MenuItemType.Normal
                    # Match native padding/indent: most items are NOT checkable.
                    # The menu reserves a checkmark column because it contains checkable items (e.g. Autostart).
                    try:
                        opt.menuHasCheckableItems = True
                    except Exception:
                        pass
                    try:
                        menu_style = cast(Any, self._menu.style())
                        opt.maxIconWidth = int(
                            menu_style.pixelMetric(
                                QtWidgets.QStyle.PixelMetric.PM_SmallIconSize,
                                opt,
                                self._menu,
                            )
                        )
                    except Exception:
                        pass

                    if bool(self._checked):
                        opt.checkType = QtWidgets.QStyleOptionMenuItem.CheckType.NonExclusive
                        opt.checked = True
                    else:
                        opt.checkType = QtWidgets.QStyleOptionMenuItem.CheckType.NotCheckable
                        opt.checked = False

                    st = QtWidgets.QStyle.StateFlag.State_None
                    if self.isEnabled():
                        st |= QtWidgets.QStyle.StateFlag.State_Enabled
                    if self._hover and self.isEnabled():
                        st |= QtWidgets.QStyle.StateFlag.State_Selected
                        st |= QtWidgets.QStyle.StateFlag.State_Active
                    opt.state = st

                    style = cast(Any, self.style())
                    style.drawControl(
                        QtWidgets.QStyle.ControlElement.CE_MenuItem,
                        opt,
                        p,
                        self._menu,
                    )
                except Exception:
                    pass

        ytdlp_row_widget = _YtDlpMenuRow(submenu_settings)

        ytdlp_action = QtWidgets.QWidgetAction(submenu_settings)
        ytdlp_action.setDefaultWidget(ytdlp_row_widget)
        submenu_settings.addAction(ytdlp_action)
        submenu_settings.addSeparator()

        action_open_log = QtGui.QAction("Logs", submenu_settings)
        submenu_settings.addAction(action_open_log)

        # Keep strong Python refs; QWidgetAction/defaultWidget can be fragile if wrappers are GC'd.
        try:
            submenu_settings._ytdlp_row_widget = ytdlp_row_widget  # type: ignore[attr-defined]
            submenu_settings._ytdlp_row_action = ytdlp_action  # type: ignore[attr-defined]
        except Exception:
            pass

        def refresh_ytdlp_row() -> None:
            try:
                now = time.time()
                transient_text = ytdlp_transient.get("text")
                transient_until = float(ytdlp_transient.get("until") or 0.0)
                if transient_text and now < transient_until:
                    ytdlp_row_widget.setText(str(transient_text))
                else:
                    ytdlp_row_widget.setText(_ytdlp_version_label())
            except Exception:
                pass

            try:
                ytdlp_row_widget.setChecked(_should_show_ytdlp_checkmark())
            except Exception:
                pass
            try:
                has_exe = bool(_resolve_ytdlp_exe())
                enabled = bool(has_exe and (not _is_ytdlp_updating()))
                ytdlp_row_widget.setEnabled(enabled)
                cur = (
                    QtCore.Qt.CursorShape.PointingHandCursor
                    if enabled
                    else QtCore.Qt.CursorShape.ArrowCursor
                )
                ytdlp_row_widget.setCursor(cur)
            except Exception:
                pass

        def on_force_update_clicked() -> None:
            # Start update without closing menu.
            try:
                ytdlp_transient["text"] = "yt-dlp: (checking...)"
                ytdlp_transient["until"] = time.time() + 0.8
            except Exception:
                pass
            refresh_ytdlp_row()
            if _is_ytdlp_updating():
                return
            if not ytdlp_row_widget.isEnabled():
                return

            def on_done(_ok: bool, _msg: str) -> None:
                # Jump back to Qt thread.
                try:
                    def _finish() -> None:
                        try:
                            ytdlp_transient["text"] = None
                            ytdlp_transient["until"] = 0.0
                        except Exception:
                            pass
                        _schedule_ytdlp_version_refresh(force=True)
                        refresh_ytdlp_row()

                    QtCore.QTimer.singleShot(0, _finish)
                except Exception:
                    pass

            started = _start_ytdlp_update(force=True, reason="manual", on_done=on_done)
            if started:
                refresh_ytdlp_row()

        def open_log_file() -> None:
            try:
                log_path = Path(os.environ.get("TEMP", str(Path.home()))) / "ytdlp-onefile.log"
                if not log_path.exists():
                    log_path.touch()

                startfile_fn = getattr(os, "startfile", None)
                if callable(startfile_fn):
                    startfile_fn(str(log_path))
                    return

                if sys.platform == "darwin":
                    subprocess.Popen(
                        ["open", str(log_path)],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(
                        ["xdg-open", str(log_path)],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            except Exception as exc:
                _append_log_line(f"open-log failed: {exc}")

        action_open_log.triggered.connect(lambda _=False: open_log_file())
        ytdlp_live_timer.timeout.connect(refresh_ytdlp_row)

        try:
            submenu_settings.aboutToShow.connect(lambda: (refresh_ytdlp_row(), ytdlp_live_timer.start()))
        except Exception:
            pass
        try:
            submenu_settings.aboutToHide.connect(lambda: ytdlp_live_timer.stop())
        except Exception:
            pass

        def reset_popup_position() -> None:
            try:
                screen = QtGui.QGuiApplication.primaryScreen()
                if screen is not None:
                    geo = screen.availableGeometry()
                    x = int(geo.x() + geo.width() - progress_widget.width() - 20)
                    y = int(geo.y() + geo.height() - progress_widget.height() - 20)
                    progress_widget.move(x, y)
                    _set_configured_progress_pos(x, y)
                else:
                    _set_configured_progress_pos(None, None)
                _append_log_line("progress-pos: reset")
            except Exception:
                pass

        action_reset_popup_pos.triggered.connect(reset_popup_position)

        menu.addSeparator()
        menu.addAction(action_cancel_playlist)
        menu.addMenu(submenu_playlist)
        menu.addSeparator()

        def add_video_action(label: str, height: int) -> None:
            act = QtGui.QAction(label, submenu_video)
            act.triggered.connect(lambda _=False, h=height: start_download("video", h, None))
            submenu_video.addAction(act)

        add_video_action("720p", 720)
        add_video_action("1080p", 1080)
        add_video_action("1440p", 1440)
        add_video_action("4K", 2160)

        def add_audio_action(label: str, abr: int) -> None:
            act = QtGui.QAction(label, submenu_audio)
            act.triggered.connect(lambda _=False, a=abr: start_download("music", None, a))
            submenu_audio.addAction(act)

        def add_playlist_video_action(label: str, height: int) -> None:
            act = QtGui.QAction(label, submenu_playlist_video)
            act.triggered.connect(lambda _=False, h=height: start_download("video", h, None, True))
            submenu_playlist_video.addAction(act)

        add_playlist_video_action("720p", 720)
        add_playlist_video_action("1080p", 1080)
        add_playlist_video_action("1440p", 1440)
        add_playlist_video_action("4K", 2160)

        def add_playlist_audio_action(label: str, abr: int) -> None:
            act = QtGui.QAction(label, submenu_playlist_audio)
            act.triggered.connect(lambda _=False, a=abr: start_download("music", None, a, True))
            submenu_playlist_audio.addAction(act)

        add_playlist_audio_action("128 kbps", 128)
        add_playlist_audio_action("192 kbps", 192)
        add_playlist_audio_action("256 kbps", 256)
        add_playlist_audio_action("320 kbps", 320)

        add_audio_action("128 kbps", 128)
        add_audio_action("192 kbps", 192)
        add_audio_action("256 kbps", 256)
        add_audio_action("320 kbps", 320)

        menu.addMenu(submenu_video)
        menu.addMenu(submenu_audio)
        menu.addSeparator()
        menu.addAction(action_exit)

        def refresh_autostart_check() -> None:
            try:
                checked = _is_autostart_enabled("ytdlp-onefile")
            except Exception:
                checked = False
            # Do not trigger toggled/triggered callbacks while refreshing.
            try:
                action_autostart.blockSignals(True)
                action_autostart.setChecked(bool(checked))
            finally:
                action_autostart.blockSignals(False)

        menu.aboutToShow.connect(refresh_autostart_check)

        def refresh_downloads_label() -> None:
            try:
                p = _effective_download_dir()
                action_dl_current.setText(f"Current: {_short_path_for_menu(p)}")
            except Exception:
                action_dl_current.setText("Current: (unknown)")

        menu.aboutToShow.connect(refresh_downloads_label)

        def set_download_folder() -> None:
            try:
                start = str(_effective_download_dir())
                chosen = QtWidgets.QFileDialog.getExistingDirectory(None, "Select download folder", start)
                if chosen:
                    p = _ensure_dir(Path(chosen))
                    _set_configured_download_dir(p)
                    _append_log_line(f"download-dir: {p}")
            except Exception as exc:
                _append_log_line(f"download-dir set failed: {exc}")

        def reset_download_folder() -> None:
            _set_configured_download_dir(None)
            _append_log_line("download-dir: reset")

        action_dl_set.triggered.connect(lambda _=False: set_download_folder())
        action_dl_reset.triggered.connect(lambda _=False: reset_download_folder())

        def refresh_cancel_visibility() -> None:
            action_cancel_playlist.setVisible(bool(playlist_downloading))

        menu.aboutToShow.connect(refresh_cancel_visibility)

        def playlist_progress_hook(progress) -> None:
            nonlocal playlist_index, playlist_count
            info = progress.get("info_dict") or progress
            idx = info.get("playlist_index")
            cnt = info.get("playlist_count")
            if isinstance(idx, int) and isinstance(cnt, int) and cnt > 0:
                if idx != playlist_index or cnt != playlist_count:
                    playlist_index = idx
                    playlist_count = cnt
                    bridge.tooltipChanged.emit(tooltip_text())
                    bridge.playlistChanged.emit(idx, cnt)

        def progress_hook(p) -> None:
            try:
                if (p.get("status") or "") != "downloading":
                    return
                downloaded = p.get("downloaded_bytes") or 0
                total = p.get("total_bytes") or p.get("total_bytes_estimate") or 0
                if total:
                    pct = int((float(downloaded) / float(total)) * 100.0)
                    if pct < 0:
                        pct = 0
                    if pct > 100:
                        pct = 100
                    bridge.progressChanged.emit(pct)
            except Exception:
                pass

        def start_download(
            mode: str,
            video_height: Optional[int],
            audio_bitrate: Optional[int],
            playlist: bool = False,
        ) -> None:
            nonlocal playlist_index, playlist_count, playlist_downloading, playlist_cancel_event
            url = _clipboard_url()
            if not url:
                return

            _append_log_line(f"trigger {mode}: {url}")

            if playlist:
                if playlist_downloading:
                    return
                playlist_downloading = True
                playlist_cancel_event = threading.Event()
                playlist_index = 0
                playlist_count = 0
                bridge.tooltipChanged.emit(tooltip_text())

            cancel_event = playlist_cancel_event if playlist else threading.Event()

            try:
                progress_widget.begin(cancel_event)
                progress_widget.show()
            except Exception:
                pass

            def done_cb(err: Optional[str]) -> None:
                nonlocal playlist_index, playlist_count, playlist_downloading, playlist_cancel_event
                if err:
                    if "canceled" in err.lower():
                        _append_log_line(f"{mode} canceled")
                    else:
                        _append_log_line(f"{mode} failed: {err}")
                else:
                    _append_log_line(f"{mode} done")

                try:
                    bridge.progressChanged.emit(100)
                    bridge.statusChanged.emit("Done")
                    bridge.hideProgress.emit()
                except Exception:
                    pass

                if playlist:
                    playlist_downloading = False
                    playlist_cancel_event = None
                    playlist_index = 0
                    playlist_count = 0
                    bridge.tooltipChanged.emit(tooltip_text())

            if playlist:
                def extra_hook(p) -> None:
                    playlist_progress_hook(p)
                    progress_hook(p)
            else:
                extra_hook = progress_hook

            threading.Thread(
                target=_download_with_ytdlp,
                args=(
                    url,
                    mode,
                    video_height,
                    audio_bitrate,
                    playlist,
                    resolved_ffmpeg_dir,
                    (lambda m: bridge.statusChanged.emit(str(m))),
                    done_cb,
                    extra_hook,
                    cancel_event,
                ),
                daemon=True,
            ).start()

        def cancel_playlist() -> None:
            nonlocal playlist_cancel_event
            if playlist_cancel_event is not None:
                playlist_cancel_event.set()
                _append_log_line("playlist cancel requested")

        action_cancel_playlist.triggered.connect(cancel_playlist)

        def toggle_autostart() -> None:
            checked = bool(action_autostart.isChecked())
            _set_autostart("ytdlp-onefile", checked)
            _append_log_line(f"autostart: {checked}")

        action_autostart.triggered.connect(lambda _=False: toggle_autostart())
        action_exit.triggered.connect(lambda: (tray.hide(), app.quit()))

        def show_menu_at_cursor() -> None:
            refresh_autostart_check()
            refresh_cancel_visibility()
            menu.popup(QtGui.QCursor.pos())

        def on_tray_activated(reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
            if reason in (
                QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
                QtWidgets.QSystemTrayIcon.ActivationReason.Context,
            ):
                show_menu_at_cursor()

        tray.activated.connect(on_tray_activated)

        tray.show()
        try:
            visible = bool(tray.isVisible())
        except Exception:
            visible = False
        _append_log_line(f"qt: trayVisible={visible}")
        if not visible:
            # Force fallback to Win32 tray if Qt couldn't show.
            tray.hide()
            raise RuntimeError("Qt tray icon not visible")
        return int(app.exec())
    except Exception as exc:  # pragma: no cover
        _write_crash_log("PyQt tray startup failed", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
