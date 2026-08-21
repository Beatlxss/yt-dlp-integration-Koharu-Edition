"""Shared, standard-library-only primitives for the Koharu updater.

The app uses this module to discover releases. The standalone updater uses it
again to validate the already selected manifest and downloaded files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from functools import total_ordering
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping, Optional, Sequence


MANIFEST_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024
DEFAULT_NETWORK_TIMEOUT_SECONDS = 15.0
DEFAULT_USER_AGENT = "Naughty-Koharu-Updater/1"

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_GITHUB_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class UpdateError(RuntimeError):
    """A safe-to-display updater error with optional diagnostic detail."""

    def __init__(self, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail or message


@total_ordering
@dataclass(frozen=True)
class SemanticVersion:
    """A strict Semantic Version 2.0 value."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = field(default=(), compare=False)

    @classmethod
    def parse(cls, value: object) -> "SemanticVersion":
        if not isinstance(value, str):
            raise UpdateError("The release version is invalid.")
        text = value.strip()
        if text[:1].lower() == "v":
            text = text[1:]
        match = _SEMVER_RE.fullmatch(text)
        if match is None:
            raise UpdateError("The release version is not valid semantic versioning.", text)

        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        for identifier in prerelease:
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise UpdateError("The release version is not valid semantic versioning.", text)
        build = tuple(match.group(5).split(".")) if match.group(5) else ()
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease, build)

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += "+" + ".".join(self.build)
        return text

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        own_numbers = (self.major, self.minor, self.patch)
        other_numbers = (other.major, other.minor, other.patch)
        if own_numbers != other_numbers:
            return own_numbers < other_numbers
        if not self.prerelease:
            return False if not other.prerelease else False
        if not other.prerelease:
            return True

        for own_identifier, other_identifier in zip(self.prerelease, other.prerelease):
            if own_identifier == other_identifier:
                continue
            own_numeric = own_identifier.isdigit()
            other_numeric = other_identifier.isdigit()
            if own_numeric and other_numeric:
                return int(own_identifier) < int(other_identifier)
            if own_numeric != other_numeric:
                return own_numeric
            return own_identifier < other_identifier
        return len(self.prerelease) < len(other.prerelease)


def version_is_newer(candidate: object, installed: object) -> bool:
    """Return whether ``candidate`` is semantically newer than ``installed``."""

    return SemanticVersion.parse(candidate) > SemanticVersion.parse(installed)


def format_byte_count(value: int) -> str:
    """Format a non-negative byte count for a compact UI label."""

    amount = max(0, int(value))
    units = ("B", "KB", "MB", "GB")
    number = float(amount)
    for unit in units:
        if number < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(number)} {unit}"
            return f"{number:.1f} {unit}"
        number /= 1024.0
    return f"{amount} B"


def normalize_github_repository(value: object) -> str:
    if not isinstance(value, str):
        raise UpdateError("The update repository is not configured.")
    repository = value.strip()
    if not _GITHUB_REPOSITORY_RE.fullmatch(repository):
        raise UpdateError("The update repository is not configured.", repository)
    return repository


def validate_relative_install_path(value: object) -> str:
    """Return a canonical relative Windows path or reject unsafe input."""

    if not isinstance(value, str):
        raise UpdateError("An update file path is invalid.")
    raw_path = value.strip()
    if not raw_path or len(raw_path) > 1024:
        raise UpdateError("An update file path is invalid.", raw_path)
    if any(ord(character) < 32 for character in raw_path):
        raise UpdateError("An update file path is invalid.", raw_path)
    if raw_path.startswith(("/", "\\")):
        raise UpdateError("An update file path must be relative.", raw_path)

    windows_path = PureWindowsPath(raw_path)
    if windows_path.is_absolute() or windows_path.drive or windows_path.root:
        raise UpdateError("An update file path must be relative.", raw_path)
    if not windows_path.parts:
        raise UpdateError("An update file path is invalid.", raw_path)

    clean_parts: list[str] = []
    for part in windows_path.parts:
        if part in ("", ".", "..") or ":" in part:
            raise UpdateError("An update file path is unsafe.", raw_path)
        clean_parts.append(part)
    if clean_parts[0].casefold().startswith(".koharu-update"):
        raise UpdateError("An update file path is reserved for the updater.", raw_path)
    return "/".join(clean_parts)


def resolve_install_path(install_dir: Path | str, relative_path: object) -> Path:
    """Resolve a manifest path and prove it remains below ``install_dir``."""

    canonical_path = validate_relative_install_path(relative_path)
    root = Path(install_dir).resolve(strict=False)
    target = root.joinpath(*PureWindowsPath(canonical_path).parts).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise UpdateError("An update file path is outside the application folder.", canonical_path) from exc
    return target


def validate_download_url(value: object, *, allow_file_urls: bool = False) -> str:
    """Permit HTTPS URLs, and local file URLs only during explicit test mode."""

    if not isinstance(value, str):
        raise UpdateError("An update download URL is invalid.")
    url = value.strip()
    if not url or len(url) > 4096 or any(ord(character) < 32 for character in url):
        raise UpdateError("An update download URL is invalid.")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() == "https":
        if not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise UpdateError("An update download URL is invalid.", url)
        return url
    if allow_file_urls and parsed.scheme.lower() == "file":
        if parsed.netloc not in ("", "localhost") or not parsed.path or parsed.fragment:
            raise UpdateError("A local test download URL is invalid.", url)
        return url
    raise UpdateError("Updates must use a secure HTTPS download URL.", url)


def sha256_file(path: Path | str, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            block = stream.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class ManifestFile:
    path: str
    url: str
    sha256: str
    size: Optional[int] = None
    component: str = "application"
    minimum_version: Optional[str] = None

    @classmethod
    def from_mapping(cls, value: object, *, allow_file_urls: bool = False) -> "ManifestFile":
        if not isinstance(value, Mapping):
            raise UpdateError("The update manifest contains an invalid file entry.")
        path = validate_relative_install_path(value.get("path"))
        url = validate_download_url(value.get("url"), allow_file_urls=allow_file_urls)
        raw_hash = value.get("sha256")
        if not isinstance(raw_hash, str) or not _SHA256_RE.fullmatch(raw_hash.strip()):
            raise UpdateError("The update manifest contains an invalid SHA-256 hash.", path)

        raw_size = value.get("size")
        if raw_size is not None and (not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size < 0):
            raise UpdateError("The update manifest contains an invalid file size.", path)

        component = value.get("component", "application")
        if not isinstance(component, str) or not component.strip() or len(component) > 64:
            raise UpdateError("The update manifest contains an invalid component.", path)
        normalized_component = component.strip().lower()

        minimum_version = value.get("minimum_version")
        if minimum_version is not None:
            if not isinstance(minimum_version, str) or not minimum_version.strip() or len(minimum_version) > 128:
                raise UpdateError("The update manifest contains an invalid dependency version.", path)
            minimum_version = minimum_version.strip()

        return cls(
            path=path,
            url=url,
            sha256=raw_hash.strip().lower(),
            size=raw_size,
            component=normalized_component,
            minimum_version=minimum_version,
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "path": self.path,
            "url": self.url,
            "sha256": self.sha256,
            "component": self.component,
        }
        if self.size is not None:
            value["size"] = self.size
        if self.minimum_version is not None:
            value["minimum_version"] = self.minimum_version
        return value


@dataclass(frozen=True)
class UpdateManifest:
    version: SemanticVersion
    files: tuple[ManifestFile, ...]
    deleted_files: tuple[str, ...] = ()
    release_tag: Optional[str] = None
    test_only: bool = False
    schema_version: int = MANIFEST_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: object, *, allow_file_urls: bool = False) -> "UpdateManifest":
        if not isinstance(value, Mapping):
            raise UpdateError("The update manifest is malformed.")
        schema_version = value.get("schema_version", MANIFEST_SCHEMA_VERSION)
        if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version != MANIFEST_SCHEMA_VERSION:
            raise UpdateError("This update manifest version is not supported.")

        version = SemanticVersion.parse(value.get("version"))
        raw_files = value.get("files")
        if not isinstance(raw_files, list) or len(raw_files) > 4096:
            raise UpdateError("The update manifest has an invalid file list.")
        files = tuple(ManifestFile.from_mapping(item, allow_file_urls=allow_file_urls) for item in raw_files)
        file_paths: set[str] = set()
        for manifest_file in files:
            key = manifest_file.path.casefold()
            if key in file_paths:
                raise UpdateError("The update manifest contains duplicate file paths.", manifest_file.path)
            file_paths.add(key)

        raw_deleted_files = value.get("deleted_files", [])
        if not isinstance(raw_deleted_files, list) or len(raw_deleted_files) > 4096:
            raise UpdateError("The update manifest has an invalid deletion list.")
        deleted_files = tuple(validate_relative_install_path(item) for item in raw_deleted_files)
        deleted_paths: set[str] = set()
        for deleted_path in deleted_files:
            key = deleted_path.casefold()
            if key in deleted_paths or key in file_paths:
                raise UpdateError("The update manifest contains conflicting file paths.", deleted_path)
            deleted_paths.add(key)

        release_tag = value.get("release_tag")
        if release_tag is not None:
            if not isinstance(release_tag, str) or not release_tag.strip() or len(release_tag) > 128:
                raise UpdateError("The update manifest contains an invalid release tag.")
            release_tag = release_tag.strip()

        test_only = value.get("test_only", False)
        if not isinstance(test_only, bool):
            raise UpdateError("The update manifest contains an invalid test-mode flag.")

        return cls(
            version=version,
            files=files,
            deleted_files=deleted_files,
            release_tag=release_tag,
            test_only=test_only,
            schema_version=schema_version,
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "version": str(self.version),
            "files": [item.to_dict() for item in self.files],
            "deleted_files": list(self.deleted_files),
        }
        if self.release_tag:
            value["release_tag"] = self.release_tag
        if self.test_only:
            value["test_only"] = True
        return value


@dataclass(frozen=True)
class GitHubRelease:
    tag_name: str
    manifest: UpdateManifest
    html_url: Optional[str] = None


def parse_manifest_bytes(raw: bytes, *, allow_file_urls: bool = False) -> UpdateManifest:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("The update manifest is too large.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("The update manifest is not valid JSON.", str(exc)) from exc
    return UpdateManifest.from_dict(value, allow_file_urls=allow_file_urls)


def load_manifest_file(path: Path | str, *, allow_file_urls: bool = False) -> UpdateManifest:
    manifest_path = Path(path)
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise UpdateError("The update manifest is too large.")
        return parse_manifest_bytes(manifest_path.read_bytes(), allow_file_urls=allow_file_urls)
    except OSError as exc:
        raise UpdateError("The local update manifest could not be read.", str(exc)) from exc


def file_matches_manifest(path: Path | str, manifest_file: ManifestFile) -> bool:
    candidate = Path(path)
    try:
        return candidate.is_file() and sha256_file(candidate).casefold() == manifest_file.sha256.casefold()
    except OSError:
        return False


def _read_response_limited(response: Any, maximum_size: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > maximum_size:
                raise UpdateError("The update manifest is too large.")
        except ValueError:
            pass

    parts: list[bytes] = []
    received = 0
    while True:
        block = response.read(min(64 * 1024, maximum_size + 1 - received))
        if not block:
            break
        received += len(block)
        if received > maximum_size:
            raise UpdateError("The update manifest is too large.")
        parts.append(block)
    return b"".join(parts)


def _open_url(url: str, *, timeout: float, accept: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": DEFAULT_USER_AGENT,
        },
    )
    return urllib.request.urlopen(request, timeout=float(timeout))


def _network_error(context: str, exc: BaseException) -> UpdateError:
    if isinstance(exc, PermissionError):
        return UpdateError("Windows denied access while saving the update. Check permissions and close applications using the install folder.", f"{context}: {exc}")
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) in (28, 112):
            return UpdateError("There is not enough free disk space to download the update.", f"{context}: {exc}")
        return UpdateError("Could not save the downloaded update file.", f"{context}: {exc}")
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (403, 429):
            return UpdateError("GitHub is rate-limiting update checks. Please try again later.", f"{context}: HTTP {exc.code}")
        if exc.code == 404:
            return UpdateError("No published update release was found.", f"{context}: HTTP 404")
        return UpdateError("The update server returned an error.", f"{context}: HTTP {exc.code}")
    if isinstance(exc, urllib.error.URLError):
        return UpdateError("Could not reach GitHub to check for updates.", f"{context}: {exc.reason}")
    return UpdateError("The update check failed.", f"{context}: {exc}")


def _fetch_json(url: str, *, timeout: float) -> object:
    try:
        with _open_url(url, timeout=timeout, accept="application/vnd.github+json") as response:
            final_url = validate_download_url(response.geturl())
            if final_url != url and urllib.parse.urlsplit(final_url).scheme.lower() != "https":
                raise UpdateError("The update server redirected to an insecure URL.")
            raw = _read_response_limited(response, MAX_MANIFEST_BYTES)
    except UpdateError:
        raise
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise _network_error("GitHub API request", exc) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned invalid release data.", str(exc)) from exc


def _fetch_manifest(url: str, *, timeout: float) -> UpdateManifest:
    try:
        with _open_url(url, timeout=timeout, accept="application/json") as response:
            validate_download_url(response.geturl())
            raw = _read_response_limited(response, MAX_MANIFEST_BYTES)
    except UpdateError:
        raise
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise _network_error("release manifest download", exc) from exc
    return parse_manifest_bytes(raw)


def fetch_latest_github_release(
    repository: object,
    manifest_asset_name: str,
    *,
    timeout: float = DEFAULT_NETWORK_TIMEOUT_SECONDS,
) -> GitHubRelease:
    """Fetch the latest public release and its manifest through GitHub's API."""

    repo = normalize_github_repository(repository)
    if not isinstance(manifest_asset_name, str) or not manifest_asset_name.strip():
        raise UpdateError("The release manifest asset is not configured.")
    asset_name = manifest_asset_name.strip()
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    payload = _fetch_json(api_url, timeout=timeout)
    if not isinstance(payload, Mapping):
        raise UpdateError("GitHub returned invalid release data.")
    if payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("No stable application update is available.")

    tag_name = payload.get("tag_name")
    if not isinstance(tag_name, str):
        raise UpdateError("GitHub returned a release without a valid version tag.")
    release_version = SemanticVersion.parse(tag_name)

    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("GitHub returned a release without downloadable assets.")
    manifest_url: Optional[str] = None
    for asset in assets:
        if not isinstance(asset, Mapping) or asset.get("name") != asset_name:
            continue
        candidate = asset.get("browser_download_url")
        manifest_url = validate_download_url(candidate)
        break
    if manifest_url is None:
        raise UpdateError("The latest release does not contain its update manifest.")

    manifest = _fetch_manifest(manifest_url, timeout=timeout)
    if manifest.version != release_version:
        raise UpdateError(
            "The latest release manifest does not match its release version.",
            f"release={release_version} manifest={manifest.version}",
        )
    if manifest.release_tag and SemanticVersion.parse(manifest.release_tag) != release_version:
        raise UpdateError("The update manifest release tag does not match GitHub.")

    html_url = payload.get("html_url")
    return GitHubRelease(
        tag_name=tag_name,
        manifest=manifest,
        html_url=html_url if isinstance(html_url, str) else None,
    )


DownloadProgress = Callable[[int, Optional[int]], None]


def download_to_file(
    url: str,
    destination: Path | str,
    *,
    expected_sha256: str,
    expected_size: Optional[int],
    timeout: float = DEFAULT_NETWORK_TIMEOUT_SECONDS,
    progress: Optional[DownloadProgress] = None,
    allow_file_urls: bool = False,
) -> int:
    """Download a file, verify it, and delete partial output on every failure."""

    safe_url = validate_download_url(url, allow_file_urls=allow_file_urls)
    if not _SHA256_RE.fullmatch(expected_sha256):
        raise UpdateError("The expected update SHA-256 hash is invalid.")
    if expected_size is not None and (expected_size < 0 or isinstance(expected_size, bool)):
        raise UpdateError("The expected update file size is invalid.")

    output = Path(destination)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with _open_url(safe_url, timeout=timeout, accept="application/octet-stream") as response:
            validate_download_url(response.geturl(), allow_file_urls=allow_file_urls)
            advertised_total: Optional[int] = None
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    advertised_total = max(0, int(content_length))
                except ValueError:
                    advertised_total = None

            digest = hashlib.sha256()
            received = 0
            with output.open("wb") as stream:
                while True:
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    stream.write(block)
                    digest.update(block)
                    received += len(block)
                    if progress is not None:
                        try:
                            progress(received, expected_size if expected_size is not None else advertised_total)
                        except Exception:
                            pass

        if expected_size is not None and received != expected_size:
            raise UpdateError(
                "The downloaded update file has an unexpected size.",
                f"expected={expected_size} received={received}",
            )
        actual_hash = digest.hexdigest()
        if actual_hash.casefold() != expected_sha256.casefold():
            raise UpdateError("The downloaded update file failed its security check.", f"expected={expected_sha256} actual={actual_hash}")
        return received
    except UpdateError:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise _network_error("update file download", exc) from exc


def compare_dotted_versions(current: str, required: str) -> int:
    """Compare yt-dlp-style numeric dotted versions without a third-party parser."""

    def tokens(value: str) -> Sequence[tuple[int, int | str]]:
        raw_tokens = re.findall(r"\d+|[A-Za-z]+", str(value).strip())
        if not raw_tokens:
            return ((1, ""),)
        return tuple((0, int(item)) if item.isdigit() else (1, item.casefold()) for item in raw_tokens)

    left = tokens(current)
    right = tokens(required)
    for current_item, required_item in zip(left, right):
        if current_item == required_item:
            continue
        if current_item[0] != required_item[0]:
            return -1 if current_item[0] > required_item[0] else 1

        current_value = current_item[1]
        required_value = required_item[1]
        if isinstance(current_value, int) and isinstance(required_value, int):
            return -1 if current_value < required_value else 1
        if isinstance(current_value, str) and isinstance(required_value, str):
            return -1 if current_value < required_value else 1
        raise AssertionError("Version tokens with matching kinds must have matching value types.")
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1
