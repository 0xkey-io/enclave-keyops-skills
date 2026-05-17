#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch a published `qos_client` binary from a GitHub Release.

This helper is the operator-facing counterpart of the Builder's
`0xkey-qos_client-release` GitHub Actions workflow. It auto-detects the
local platform, downloads the matching binary plus its `.sha256` sidecar
from the configured release tag, double-verifies the hash (and an
optional out-of-band expected SHA256), and writes the binary atomically
to a target path.

By design this script:

* Refuses to bypass SHA256 verification, even on transient network
  failures (`SECURITY.md §3` explicitly forbids "网络抖动" workarounds).
* Does NOT modify the role's `config.json`; the caller (e.g.
  `role_init.py`, or a human operator) reads the written `.sha256`
  sidecar and updates `qos_client_sha256_expected` itself. This keeps
  the script side-effect-scoped to "drop the binary on disk".
* Stdlib only — same constraint as `role_init.py` /
  `enclave_keyops.py` so it works from any role workdir on any
  Python 3.11+ machine without a pip install step.

Failure mode: when auto-fetch can't complete (404, network, SHA
mismatch, unsupported platform), the script prints a multi-line
manual fallback recipe to stderr (curl + shasum + gh release download)
so the operator can complete the same step out-of-band, and exits 2.
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
from typing import Any, Optional, Tuple


DEFAULT_REPO = "0xkey-io/qos"

# Sentinel for "let GitHub pick the latest stable release". Treated as the
# default both on the CLI (`--release-tag` is optional) and in role_init.py
# (no flag → latest). See `resolve_release_tag` for the resolution rules.
LATEST_TAG = "latest"

# (uname.system_lower, uname.machine_lower) -> release platform slug.
# We intentionally hard-code only the platforms that Builder publishes
# under the active 0xkey runbook; an unknown pair MUST raise rather
# than silently degrade to a closest-match binary.
_PLATFORM_MAP: dict[Tuple[str, str], str] = {
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
            f"Builder publishes only {list(_VALID_PLATFORMS)}; ask the Builder "
            "to add the missing platform to the next release."
        )
    return slug


def asset_urls(repo: str, tag: str, plat: str) -> Tuple[str, str]:
    base = f"https://github.com/{repo}/releases/download/{tag}/qos_client.{plat}"
    return base, f"{base}.sha256"


# Overridable for tests; production points at GitHub's REST API root.
GITHUB_API_BASE = os.environ.get("FETCH_QOS_CLIENT_API_BASE", "https://api.github.com")


def _request(url: str, *, token: Optional[str], accept: Optional[str] = None) -> urllib.request.Request:
    req = urllib.request.Request(url)
    # GitHub's release asset CDN returns 200 for public repos without auth.
    # When `token` is set, the same Authorization header is forwarded across
    # the redirect chain (objects.githubusercontent.com), which is required
    # for private repos. Authorization on a public asset is harmless.
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "0xkey-keyops-fetch-qos-client/1")
    req.add_header("Accept", accept or "application/octet-stream")
    return req


def _api_get_json(url: str, *, token: Optional[str], timeout: float) -> Any:
    """GET a JSON document from the GitHub REST API. Raises FetchError on
    non-200 / network errors. Used only for release metadata lookup; the
    binary itself still flows through the asset CDN.
    """
    try:
        req = _request(url, token=token, accept="application/vnd.github+json")
        # The X-GitHub-Api-Version header is best-practice but optional; we
        # hard-pin it so a future GitHub default change can't silently shift
        # the response shape that resolve_release_tag depends on.
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
    want: Optional[str],
    token: Optional[str],
    timeout: float,
) -> str:
    """Resolve a (possibly-implicit) release identifier to a concrete tag.

    Resolution rules:

    * ``want`` is None or the literal string ``"latest"`` → query
      ``GET /repos/<repo>/releases/latest``. GitHub returns the most recent
      *non-prerelease, non-draft* release. This is the safe default for
      ceremony pinning.
    * If ``/releases/latest`` returns 404 (no stable release yet — common
      while the project is in RC), fall back to
      ``GET /repos/<repo>/releases?per_page=1``. We then resolve to the
      most recent release of *any* kind (prerelease included) and emit a
      stderr warning so the operator notices they are running off a
      prerelease binary. Production should publish a stable release to
      remove the warning.
    * Any other ``want`` is treated as an explicit tag and returned
      verbatim. We do not validate that the tag exists here; the caller's
      asset download will surface a 404 with a clear URL.
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
        # No stable release yet → fall back to the newest of any kind.
        list_api = f"{GITHUB_API_BASE}/repos/{repo}/releases?per_page=1"
        items = _api_get_json(list_api, token=token, timeout=timeout)
        if not isinstance(items, list) or not items:
            raise FetchError(
                f"{repo} has no releases at all; ask the Builder to publish "
                "an initial qos_client release before re-running."
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
                f"prerelease {tag}. Pin a stable tag with --release-tag <tag> "
                "before running a production ceremony.\n"
            )
        return tag
    if not isinstance(data, dict):
        raise FetchError(f"unexpected response shape from {api}: not an object")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise FetchError(f"unexpected response shape from {api}: missing tag_name")
    return tag


def _download_to(url: str, dest: Path, *, token: Optional[str], timeout: float) -> None:
    """Stream the URL into `dest` (truncate-and-write), no atomic guarantees here.

    The caller is expected to write to a `.partial` path and rename only on
    full success; see `fetch_binary` for the atomic flow.
    """
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
    """Parse a `.sha256` sidecar produced by `sha256sum` / `shasum -a 256`.

    Accepts both the bare-hex form (one line, just the hex) and the
    `<hex>  <filename>` form. Whitespace tolerant.
    """
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise FetchError(f"empty .sha256 sidecar: {path}")
    first = text.splitlines()[0].strip()
    token = first.split()[0]
    if len(token) != 64 or not all(c in "0123456789abcdefABCDEF" for c in token):
        raise FetchError(f"malformed sha256 in {path}: {first!r}")
    return token.lower()


def _atomic_write_executable(src: Path, dest: Path) -> None:
    """Move `src` onto `dest` and chmod 0755. Same-device-only is fine here
    because we always write the temp file next to the destination.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)
    dest.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


def fetch_binary(
    *,
    repo: str,
    tag: str,
    plat: str,
    out: Path,
    expected_sha256: Optional[str],
    token: Optional[str],
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

    # Always start from a clean slate so a previous failed run can't
    # masquerade as a successful download.
    for p in (bin_partial, sha_partial):
        if p.exists():
            p.unlink()

    _download_to(bin_url, bin_partial, token=token, timeout=timeout)
    _download_to(sha_url, sha_partial, token=token, timeout=timeout)

    local_hex = _sha256(bin_partial)
    remote_hex = _read_remote_sha(sha_partial)

    if local_hex != remote_hex:
        # Quarantine the bad download next to the target so the operator
        # can inspect / report-incident; never overwrite the existing
        # binary at `out`.
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
    # Mirror the sha sidecar next to the installed binary (overwrite OK).
    sha_partial.replace(sha_final)
    sha_final.chmod(0o644)

    return local_hex


def print_manual_fallback(
    *,
    reason: str,
    repo: str,
    tag: str,
    plat: Optional[str],
    out: Path,
) -> None:
    """Emit a copy/paste recipe for completing the same step by hand."""
    if plat:
        bin_url, sha_url = asset_urls(repo, tag, plat)
        bin_name = f"qos_client.{plat}"
    else:
        bin_url = f"https://github.com/{repo}/releases/download/{tag}/qos_client.<platform>"
        sha_url = f"{bin_url}.sha256"
        bin_name = "qos_client.<platform>"

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
        f"  gh release download '{tag}' -R '{repo}' -p '{bin_name}*' -D /tmp/qos_client_dl\n"
        f"  shasum -a 256 -c /tmp/qos_client_dl/{bin_name}.sha256\n"
        f"  install -m 0755 /tmp/qos_client_dl/{bin_name} '{out}'\n"
        f"  install -m 0644 /tmp/qos_client_dl/{bin_name}.sha256 '{out}.sha256'\n"
        "\n"
        "Cross-check that the resulting hash matches the one Builder published in\n"
        "the release MANIFEST.json before running any KeyOps command.\n"
        "\n"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--release-tag",
        default=LATEST_TAG,
        help=(
            "GitHub release tag (e.g. 0xkey-qos_client-v0.1.0). "
            f"Default: {LATEST_TAG} — resolves via GitHub's "
            "/releases/latest API (skips drafts and prereleases). "
            "When no stable release exists yet the script falls back to the "
            "most recent prerelease and prints a stderr WARN."
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


def main(argv: Optional[list[str]] = None) -> int:
    ns = build_parser().parse_args(argv)
    out = Path(ns.out).expanduser()

    # Token discovery (tolerated absent for public repos; required for
    # private mirrors). Both env names are common GitHub conventions.
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    plat: Optional[str] = None
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
        # Also write the full (multi-line) failure detail for audit.
        sys.stderr.write(f"detail:\n{e}\n")
        return 2
    except Exception as e:  # pragma: no cover - defensive
        # Anything else is unexpected; surface it but still emit the
        # fallback so the operator has a clear next step.
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
