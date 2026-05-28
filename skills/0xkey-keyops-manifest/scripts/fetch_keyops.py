#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch a published `keyops` binary from a GitHub Release.

This helper downloads the self-contained `keyops` binary (built by the
repository's CI workflow via PyInstaller) from a GitHub Release of
`0xkey-io/enclave-keyops-skills`. It auto-detects the local platform,
downloads the matching binary plus its `.sha256` sidecar, double-verifies
the hash, and writes the binary atomically to a target path.

By design this script:

* Refuses to bypass SHA256 verification, even on transient network
  failures (`SECURITY.md §3` explicitly forbids network-flake workarounds).
* Does NOT require Python on the caller's machine after installation —
  that is the whole point of the binary. This script itself is stdlib-only
  and bootstraps the installation step.
* Stdlib only — same constraint as the other scripts in this package so
  it works without a pip install step.

Failure mode: when auto-fetch cannot complete (404, network, SHA
mismatch, unsupported platform), the script prints a multi-line manual
fallback recipe to stderr (curl + shasum + gh release download) so the
operator can complete the same step out-of-band, and exits 2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_REPO = "0xkey-io/enclave-keyops-skills"

LATEST_TAG = "latest"

# (uname.system_lower, uname.machine_lower) -> release platform slug.
# Hard-coded to only the platforms the CI workflow publishes; an unknown
# pair MUST raise rather than silently degrade.
_PLATFORM_MAP: dict[tuple[str, str], str] = {
    ("linux", "x86_64"): "linux-amd64",
    ("linux", "amd64"): "linux-amd64",
    ("darwin", "arm64"): "darwin-arm64",
    ("darwin", "aarch64"): "darwin-arm64",
}

_VALID_PLATFORMS = ("linux-amd64", "darwin-arm64")


class FetchError(RuntimeError):
    """Raised on any failure that should map to the manual-fallback path."""


def detect_platform() -> str:
    sysname = platform.system().lower()
    machine = platform.machine().lower()
    slug = _PLATFORM_MAP.get((sysname, machine))
    if slug is None:
        raise FetchError(
            f"unsupported platform: system={sysname!r}, machine={machine!r}. "
            f"CI publishes only {list(_VALID_PLATFORMS)}; open an issue at "
            "https://github.com/0xkey-io/enclave-keyops-skills to add your platform."
        )
    return slug


def asset_urls(repo: str, tag: str, plat: str) -> tuple[str, str]:
    base = f"https://github.com/{repo}/releases/download/{tag}/keyops.{plat}"
    return base, f"{base}.sha256"


GITHUB_API_BASE = os.environ.get("FETCH_KEYOPS_API_BASE", "https://api.github.com")


def _request(url: str, *, token: str | None, accept: str | None = None) -> urllib.request.Request:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "0xkey-keyops-fetch-keyops/1")
    req.add_header("Accept", accept or "application/octet-stream")
    return req


def _api_get_json(url: str, *, token: str | None, timeout: float) -> Any:
    """GET a JSON document from the GitHub REST API."""
    try:
        req = _request(url, token=token, accept="application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                raise FetchError(f"unexpected HTTP status {status} for {url}")
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"network error fetching {url}: {e.reason}") from e
    except TimeoutError as e:
        raise FetchError(f"timeout fetching {url}") from e
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FetchError(f"malformed JSON from {url}: {e}") from e


def resolve_release_tag(
    repo: str,
    *,
    want: str | None,
    token: str | None,
    timeout: float,
) -> str:
    """Resolve a (possibly-implicit) release identifier to a concrete tag.

    * ``want`` is None or ``"latest"`` → query ``/repos/<repo>/releases/latest``.
    * If ``/releases/latest`` returns 404 (no stable release yet), fall back
      to ``/releases?per_page=1`` and emit a stderr WARN.
    * Any other ``want`` is returned verbatim.
    """
    if want is not None and want != LATEST_TAG:
        return want
    api = f"{GITHUB_API_BASE}/repos/{repo}/releases/latest"
    try:
        data = _api_get_json(api, token=token, timeout=timeout)
    except FetchError as e:
        msg = str(e)
        if "HTTP 404" not in msg:
            raise
        list_api = f"{GITHUB_API_BASE}/repos/{repo}/releases?per_page=1"
        items = _api_get_json(list_api, token=token, timeout=timeout)
        if not isinstance(items, list) or not items:
            raise FetchError(
                f"{repo} has no releases at all; the CI workflow must publish "
                "a release before fetch-keyops can auto-resolve."
            ) from e
        first = items[0]
        tag = first.get("tag_name")
        if not isinstance(tag, str) or not tag:
            raise FetchError(
                f"unexpected response shape from {list_api}: missing tag_name"
            ) from e
        if first.get("prerelease"):
            sys.stderr.write(
                f"WARN: {repo} has no stable release yet; resolved 'latest' to "
                f"prerelease {tag}. Pin a stable tag with --release-tag <tag>.\n"
            )
        return tag
    if not isinstance(data, dict):
        raise FetchError(f"unexpected response shape from {api}: not an object")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise FetchError(f"unexpected response shape from {api}: missing tag_name")
    return tag


def _download_to(url: str, dest: Path, *, token: str | None, timeout: float) -> None:
    try:
        with urllib.request.urlopen(_request(url, token=token), timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                raise FetchError(f"unexpected HTTP status {status} for {url}")
            with dest.open("wb") as f:
                shutil.copyfileobj(resp, f, length=1 << 20)
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"network error fetching {url}: {e.reason}") from e
    except TimeoutError as e:
        raise FetchError(f"timeout fetching {url}") from e


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_remote_sha(path: Path) -> str:
    """Parse a `.sha256` sidecar produced by ``sha256sum`` / ``shasum -a 256``."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise FetchError(f"empty .sha256 sidecar: {path}")
    first = text.splitlines()[0].strip()
    token = first.split()[0]
    if len(token) != 64 or not all(c in "0123456789abcdefABCDEF" for c in token):
        raise FetchError(f"malformed sha256 in {path}: {first!r}")
    return token.lower()


def _atomic_write_executable(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)
    dest.chmod(
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        | stat.S_IRGRP | stat.S_IXGRP
        | stat.S_IROTH | stat.S_IXOTH
    )


def fetch_binary(
    *,
    repo: str,
    tag: str,
    plat: str,
    out: Path,
    expected_sha256: str | None,
    token: str | None,
    timeout: float,
) -> str:
    """Download the binary + sidecar, verify, install. Returns the verified hex."""
    if plat not in _VALID_PLATFORMS:
        raise FetchError(
            f"refusing unknown platform slug {plat!r}; expected one of {_VALID_PLATFORMS}"
        )

    bin_url, sha_url = asset_urls(repo, tag, plat)

    out = out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    bin_partial = out.with_suffix(out.suffix + ".partial")
    sha_partial = out.with_suffix(out.suffix + ".sha256.partial")
    sha_final = Path(str(out) + ".sha256")
    bin_tainted = Path(str(out) + ".tainted")

    for p in (bin_partial, sha_partial):
        if p.exists():
            p.unlink()

    _download_to(bin_url, bin_partial, token=token, timeout=timeout)
    _download_to(sha_url, sha_partial, token=token, timeout=timeout)

    local_hex = _sha256(bin_partial)
    remote_hex = _read_remote_sha(sha_partial)

    if local_hex != remote_hex:
        if bin_tainted.exists():
            bin_tainted.unlink()
        bin_partial.replace(bin_tainted)
        sha_partial.unlink(missing_ok=True)
        raise FetchError(
            f"sha256 mismatch:\n"
            f"  binary  : {local_hex}\n"
            f"  release : {remote_hex}\n"
            f"  binary URL: {bin_url}\n"
            f"  sha   URL: {sha_url}\n"
            f"  quarantined at: {bin_tainted}\n"
            "Do NOT use this binary. This is the SECURITY.md §3 red line: "
            "never bypass sha256 verification."
        )

    if expected_sha256:
        norm = expected_sha256.strip().lower()
        if norm != local_hex:
            if bin_tainted.exists():
                bin_tainted.unlink()
            bin_partial.replace(bin_tainted)
            sha_partial.unlink(missing_ok=True)
            raise FetchError(
                f"--expected-sha256 mismatch:\n"
                f"  downloaded: {local_hex}\n"
                f"  expected  : {norm}\n"
                f"  quarantined at: {bin_tainted}\n"
                "The release sidecar AND the operator's expected hash must "
                "both agree before this binary is trusted."
            )

    _atomic_write_executable(bin_partial, out)
    sha_partial.replace(sha_final)
    sha_final.chmod(0o644)

    return local_hex


def print_manual_fallback(
    *,
    reason: str,
    repo: str,
    tag: str,
    plat: str | None,
    out: Path,
) -> None:
    """Emit a copy/paste recipe for completing the same step by hand."""
    if plat:
        bin_url, sha_url = asset_urls(repo, tag, plat)
        bin_name = f"keyops.{plat}"
    else:
        bin_url = f"https://github.com/{repo}/releases/download/{tag}/keyops.<platform>"
        sha_url = f"{bin_url}.sha256"
        bin_name = "keyops.<platform>"

    sys.stderr.write(
        "\n"
        f"Auto-fetch failed: {reason}\n"
        "\n"
        "Manual fallback (you must complete BOTH the download and the SHA256 check):\n"
        "\n"
        f"  curl -fL -o {bin_name}        '{bin_url}'\n"
        f"  curl -fL -o {bin_name}.sha256 '{sha_url}'\n"
        f"  shasum -a 256 -c <(printf '%s  {bin_name}\\n' \"$(cat {bin_name}.sha256)\")\n"
        f"  install -m 0755 {bin_name} '{out}'\n"
        f"  install -m 0644 {bin_name}.sha256 '{out}.sha256'\n"
        "\n"
        "Or, with the GitHub CLI:\n"
        "\n"
        f"  gh release download '{tag}' -R '{repo}' -p '{bin_name}*' -D /tmp/keyops_dl\n"
        f"  shasum -a 256 -c /tmp/keyops_dl/{bin_name}.sha256\n"
        f"  install -m 0755 /tmp/keyops_dl/{bin_name} '{out}'\n"
        f"  install -m 0644 /tmp/keyops_dl/{bin_name}.sha256 '{out}.sha256'\n"
        "\n"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--release-tag",
        default=LATEST_TAG,
        help=(
            "GitHub release tag (e.g. v0.4.0). "
            f"Default: {LATEST_TAG} — resolves via GitHub's /releases/latest API "
            "(skips drafts and prereleases)."
        ),
    )
    p.add_argument(
        "--platform",
        default="auto",
        choices=("auto",) + _VALID_PLATFORMS,
        help="release platform slug. 'auto' detects from uname; refuses on unknown platforms.",
    )
    p.add_argument(
        "--out",
        required=True,
        help=(
            "destination path for the binary. The matching .sha256 sidecar is "
            "written next to it as `<out>.sha256`."
        ),
    )
    p.add_argument(
        "--expected-sha256",
        default=None,
        help=(
            "optional out-of-band expected hash. When set, the downloaded "
            "binary must match BOTH the release .sha256 sidecar AND this value."
        ),
    )
    p.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"GitHub repo (default: {DEFAULT_REPO}).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="per-request timeout in seconds (default 60).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    out = Path(ns.out).expanduser()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    plat: str | None = None
    resolved_tag = ns.release_tag
    try:
        plat = detect_platform() if ns.platform == "auto" else ns.platform
        resolved_tag = resolve_release_tag(
            ns.repo, want=ns.release_tag, token=token, timeout=ns.timeout
        )
        digest = fetch_binary(
            repo=ns.repo,
            tag=resolved_tag,
            plat=plat,
            out=out,
            expected_sha256=ns.expected_sha256,
            token=token,
            timeout=ns.timeout,
        )
    except FetchError as e:
        print_manual_fallback(
            reason=str(e).splitlines()[0],
            repo=ns.repo,
            tag=resolved_tag,
            plat=plat,
            out=out,
        )
        sys.stderr.write(f"detail:\n{e}\n")
        return 2
    except Exception as e:  # pragma: no cover - defensive
        print_manual_fallback(
            reason=f"unexpected error: {type(e).__name__}: {e}",
            repo=ns.repo,
            tag=resolved_tag,
            plat=plat,
            out=out,
        )
        return 2

    print(f"installed: {out}")
    print(f"sidecar:   {out}.sha256")
    print(f"release:   {resolved_tag}")
    print(f"sha256:    {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
