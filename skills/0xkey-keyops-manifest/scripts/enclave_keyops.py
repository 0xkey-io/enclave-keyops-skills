#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""0xkey Enclave KeyOps — stdlib CLI wrapping qos_client + kubectl."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

# Substring markers used to recognize a CLI flag whose companion VALUE
# should be redacted from audit logs (e.g. `--secret-path`, `--share-path`,
# `--master-seed-path`). Matching is intentionally restricted to tokens
# that look like flags (start with `-`) — see `sanitize_argv`.
#
# Earlier revisions matched these substrings against the *entire* token,
# which caused three regressions:
#   1. A public binary path like `shared/qos_client` contained the
#      "share" substring → polluted `redact_next` and erased the
#      following subcommand name (`provision-yubikey` got replaced with
#      `[REDACTED]`, hiding the operation type in audit logs).
#   2. A public `.pub` path whose alias contained "share" / "secret"
#      (e.g. `outbox/share-member2.pub`) was preserved verbatim AND set
#      `redact_next`, eating an unrelated downstream token.
#   3. When the sensitive-substring token was the LAST token, the
#      function appended a phantom trailing `[REDACTED]` placeholder,
#      implying the operator omitted a sensitive value when in fact
#      nothing was omitted.
# Constraining the match to flag NAMES (after stripping `=value`) fixes
# all three while preserving the original `--secret-path /...` and
# `--secret-path=/...` redaction shapes.
SENSITIVE_FLAG_MARKERS = ("secret", "share", ".pem", "password", "token", "seed")


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(workdir: Path, p: str | Path) -> Path:
    pp = Path(p)
    if pp.is_absolute():
        return pp
    return (workdir / pp).resolve()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_argv(argv: Sequence[str]) -> List[str]:
    """Redact sensitive flag values from an argv before logging.

    Only tokens that look like CLI flags (start with `-`) are considered
    redaction triggers — see `SENSITIVE_FLAG_MARKERS` for the rationale.
    For each flag whose name contains a sensitive substring:
      - if the flag uses ``--name=value``, the value is replaced inline
      - if the flag uses ``--name value`` form, the FOLLOWING token is
        replaced. If the flag is also the last token (no value present),
        a trailing ``[REDACTED]`` is appended so an accidentally
        truncated argv can never silently leak.
    """
    out: List[str] = []
    redact_next = False
    for a in argv:
        if redact_next:
            out.append("[REDACTED]")
            redact_next = False
            continue
        if a.startswith("-"):
            name, sep, _ = a.partition("=")
            if any(m in name.lower() for m in SENSITIVE_FLAG_MARKERS):
                if sep:
                    out.append(f"{name}=[REDACTED]")
                    continue
                redact_next = True
                out.append(a)
                continue
        out.append(a)
    if redact_next:
        out.append("[REDACTED]")
    return out


def append_audit(log_path: Optional[Path], line: str) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def audit_file_hash(log_path: Optional[Path], path: Path) -> None:
    if log_path is None or not path.is_file():
        return
    append_audit(log_path, f"[sha256] {path.name} {sha256_file(path)}")


def bundle_files(root: Path) -> List[Path]:
    if not root.is_dir():
        sys.stderr.write(f"bundle directory does not exist: {root}\n")
        raise SystemExit(2)
    files: List[Path] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS":
            files.append(p)
    return files


def write_sha256sums(root: Path) -> Path:
    sums = root / "SHA256SUMS"
    with sums.open("w", encoding="utf-8") as f:
        for p in bundle_files(root):
            rel = p.relative_to(root).as_posix()
            f.write(f"{sha256_file(p)}  {rel}\n")
    return sums


def verify_sha256sums(root: Path) -> None:
    sums = root / "SHA256SUMS"
    require_file(sums, "SHA256SUMS")
    ok = True
    with sums.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            expected, _, rel = line.partition("  ")
            if not expected or not rel:
                sys.stderr.write(f"invalid SHA256SUMS line: {line}\n")
                ok = False
                continue
            target = (root / rel).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError:
                sys.stderr.write(f"SHA256SUMS path escapes bundle root: {rel}\n")
                ok = False
                continue
            if not target.is_file():
                sys.stderr.write(f"missing bundled file: {rel}\n")
                ok = False
                continue
            actual = sha256_file(target)
            if actual.lower() != expected.lower():
                sys.stderr.write(
                    f"sha256 mismatch for {rel}: got {actual}, expected {expected}\n"
                )
                ok = False
    if not ok:
        raise SystemExit(1)


def run_process(
    argv: Sequence[str],
    *,
    dry_run: bool,
    cwd: Optional[Path],
    audit_log: Optional[Path],
    allow_failure: bool = False,
) -> int:
    line = ("[dry-run] " if dry_run else "[exec] ") + shlex.join(
        sanitize_argv(list(argv))
    )
    print(line)
    append_audit(audit_log, line)
    if dry_run:
        append_audit(audit_log, "[exit] 0 (dry-run)")
        return 0
    proc = subprocess.run(list(argv), cwd=str(cwd) if cwd else None)
    append_audit(audit_log, f"[exit] {proc.returncode}")
    if proc.returncode != 0 and not allow_failure:
        sys.stderr.write(f"command failed with exit code {proc.returncode}\n")
        raise SystemExit(proc.returncode)
    return proc.returncode


def reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http_request(method: str, url: str, *, data: Optional[bytes] = None) -> Tuple[int, str]:
    req = urllib.request.Request(url, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"request failed for {url}: {e}") from e


@contextlib.contextmanager
def kubectl_port_forward(
    cfg: "Config",
    *,
    namespace: str,
    target: str,
    remote_port: int,
    audit_log: Optional[Path],
) -> Iterator[int]:
    local_port = reserve_local_port()
    argv = [
        cfg.kubectl(),
        "-n",
        namespace,
        "port-forward",
        target,
        f"{local_port}:{remote_port}",
    ]
    line = "[exec] " + shlex.join(sanitize_argv(argv))
    print(line)
    append_audit(audit_log, line)
    proc = subprocess.Popen(
        argv,
        cwd=str(cfg.workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 15
        last_output = ""
        while time.time() < deadline:
            if proc.poll() is not None:
                if proc.stdout:
                    last_output += proc.stdout.read()
                raise RuntimeError(
                    f"kubectl port-forward exited early for {target}:{remote_port}: {last_output.strip()}"
                )
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", local_port)) == 0:
                    yield local_port
                    return
            if proc.stdout:
                # Non-blocking reads are not portable here; keep startup simple and rely on
                # the TCP readiness probe above.
                pass
            time.sleep(0.2)
        raise RuntimeError(f"timed out waiting for port-forward {target}:{remote_port}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def require_file(path: Path, what: str) -> None:
    if not path.is_file():
        sys.stderr.write(f"missing {what}: {path}\n")
        raise SystemExit(2)


class Config:
    def __init__(
        self,
        raw: Dict[str, Any],
        *,
        workdir: Path,
    ) -> None:
        self.raw = raw
        self.workdir = workdir
        self.qos_client = Path(raw["qos_client_path"]).expanduser()
        if "super_repo_root" in raw:
            sys.stderr.write(
                "config.super_repo_root is no longer supported; remove it from the "
                "config file. Set kustomize_overlay_path to an absolute path instead.\n"
            )
            raise SystemExit(2)
        raw.setdefault("kubectl_path", "kubectl")

    def kubectl(self) -> str:
        return str(self.raw["kubectl_path"])

    def paths(self) -> Dict[str, str]:
        return self.raw["paths"]

    def svc(self, name: str) -> Dict[str, Any]:
        for s in self.raw["services"]:
            if s["name"] == name:
                return s
        sys.stderr.write(f"unknown service: {name}\n")
        raise SystemExit(2)

    def all_services(self) -> List[Dict[str, Any]]:
        svcs = list(self.raw["services"])
        if len(svcs) != 5:
            sys.stderr.write("config.services must define exactly five pivots\n")
            raise SystemExit(2)
        return svcs


def validate_config(cfg: Config) -> None:
    cfg.all_services()
    if "paths" not in cfg.raw:
        sys.stderr.write("config.paths required\n")
        raise SystemExit(2)


def check_sensitive_external_path(path: Path, *, workdir: Path, label: str) -> None:
    expanded = path.expanduser()
    resolved = expanded.resolve()
    if not expanded.is_absolute():
        sys.stderr.write(f"{label} must be an absolute path outside the role workdir: {path}\n")
        raise SystemExit(2)
    workdir_resolved = workdir.resolve()
    try:
        resolved.relative_to(workdir_resolved)
    except ValueError:
        return
    sys.stderr.write(f"refusing {label} inside role workdir: {resolved}\n")
    raise SystemExit(2)


def resolve_holder_credential(
    ns: argparse.Namespace,
    *,
    workdir: Path,
    secret_label: str = "member secret path",
) -> List[str]:
    """Translate the YubiKey-vs-file-secret choice into a qos_client argv slice.

    Manifest `approve-manifest`, `proxy-re-encrypt-share`, and
    `after-genesis` all share the same shape: either the holder provides
    ``--yubikey`` (PIV-backed private key) or an external ``--secret-path``.
    This helper centralizes three things that used to be duplicated:

    1. **Mutual exclusion**: passing both `--yubikey` and `--secret-path`
       is an error (`SystemExit(2)`). It hides a footgun where the operator
       thinks the YubiKey is in use but the file path silently takes over
       (or vice versa). One credential, one path.
    2. **Workdir-leak refusal**: when a file path is used, it must live
       outside the role workdir (`SECURITY.md §5`).
    3. **Consistent argv shape**: returns the qos_client flag list so the
       call site can do `argv += resolve_holder_credential(...)` without
       repeating the if/else branch.

    The helper does NOT verify the YubiKey is plugged in or that the PIV
    slot is provisioned — that's qos_client's job at exec time.
    """
    has_yubikey = bool(getattr(ns, "yubikey", False))
    secret_path = getattr(ns, "secret_path", None)
    if has_yubikey and secret_path:
        sys.stderr.write(
            f"--yubikey and --secret-path are mutually exclusive; pick one "
            f"holder credential for the {secret_label} role.\n"
        )
        raise SystemExit(2)
    if not has_yubikey and not secret_path:
        sys.stderr.write(
            f"holder credential required: pass either --yubikey "
            f"(PIV-backed key on the operator's YubiKey) or --secret-path "
            f"(absolute path to {secret_label} stored outside the role workdir).\n"
        )
        raise SystemExit(2)
    if has_yubikey:
        return ["--yubikey"]
    check_sensitive_external_path(
        Path(secret_path),
        workdir=workdir,
        label=secret_label,
    )
    return ["--secret-path", secret_path]


def confirm(msg: str, yes: bool) -> None:
    if yes:
        return
    ans = input(f"{msg} [y/N] ").strip().lower()
    if ans not in {"y", "yes"}:
        sys.stderr.write("aborted\n")
        raise SystemExit(1)


def confirm_dangerous(ns: argparse.Namespace, msg: str, phrase: str) -> None:
    """Require an exact phrase; global --yes intentionally cannot bypass this."""
    if ns.dry_run:
        print(f"[dry-run] dangerous confirmation skipped: {msg}")
        return
    ans = input(f"{msg}\nType {phrase!r} to continue: ").strip()
    if ans != phrase:
        sys.stderr.write("aborted: confirmation phrase mismatch\n")
        raise SystemExit(1)


def _qos_client_fetch_hint(cfg: Config) -> str:
    """Build a copy-paste fetch command for re-installing the qos_client.

    The hint is doctor-mode read-only: it tells the operator EXACTLY which
    command to run next, but doctor itself never invokes the fetch (that is
    a setup-phase side effect; doctor is a health probe). When config.json
    carries no `qos_client_release` block (legacy workspace), the hint
    still falls back to the documented "latest from 0xkey-io/qos" default
    so the operator never has to hunt for syntax.
    """
    rel = cfg.raw.get("qos_client_release") or {}
    fetch_script = script_dir() / "fetch_qos_client.py"
    repo = rel.get("repo") or "0xkey-io/qos"
    # Prefer the concrete tag we resolved at init time so re-fetches are
    # reproducible; fall back to whatever the operator typed (often the
    # literal "latest"), and finally to the "latest" sentinel — which is
    # exactly what role_init.py uses by default.
    tag = rel.get("resolved_tag") or rel.get("tag") or "latest"
    plat = rel.get("platform")
    plat_arg = f"--platform {plat} " if plat else ""
    expected = cfg.raw.get("qos_client_sha256_expected")
    expected_arg = f"--expected-sha256 {expected} " if expected else ""
    repo_arg = f"--repo {repo} " if repo != "0xkey-io/qos" else ""
    return (
        "\n  release-channel hint:\n"
        f"    python3 {fetch_script} \\\n"
        f"      --release-tag {tag} \\\n"
        f"      {repo_arg}{plat_arg}--out {cfg.qos_client} \\\n"
        f"      {expected_arg}\n"
        "  (doctor stays read-only and does not auto-run this command)\n"
    )


def check_qos_client(cfg: Config) -> None:
    qc = cfg.qos_client
    if not qc.is_file():
        sys.stderr.write(
            f"missing qos_client: {qc}\n"
            + _qos_client_fetch_hint(cfg)
        )
        raise SystemExit(2)
    if not os.access(qc, os.X_OK):
        sys.stderr.write(f"qos_client not executable: {qc}\n")
        raise SystemExit(1)
    exp = cfg.raw.get("qos_client_sha256_expected")
    if exp:
        h = sha256_file(qc)
        if h.lower() != str(exp).lower():
            sys.stderr.write(
                f"qos_client sha256 mismatch: got {h} expected {exp}\n"
                + _qos_client_fetch_hint(cfg)
            )
            raise SystemExit(1)
        print(f"qos_client sha256 OK: {h}")


def check_tools(tools: Sequence[str]) -> None:
    for t in tools:
        p = shutil.which(t)
        if not p:
            sys.stderr.write(f"missing tool in PATH: {t}\n")
            raise SystemExit(1)
        print(f"{t}: {p}")


def cmd_doctor_coordinator(cfg: Config, *, audit_log: Optional[Path]) -> None:
    check_qos_client(cfg)
    check_tools(("kubectl", "jq", "aws"))
    allow = cfg.raw.get("kubectl_context_allowlist") or []
    if cfg.raw.get("deploy", {}).get("require_context_match") and allow:
        ctx = subprocess.check_output(
            [cfg.kubectl(), "config", "current-context"],
            text=True,
        ).strip()
        if ctx not in allow:
            sys.stderr.write(
                f"kubectl context {ctx!r} not in allowlist {allow!r}\n",
            )
            raise SystemExit(1)
        print(f"kubectl context OK: {ctx}")
    roster = check_member_roster(cfg)
    counts = {k: len(v) for k, v in roster.items()}
    print(f"member roster OK: {counts}")
    print("doctor coordinator done")


def cmd_doctor_holder(cfg: Config, *, audit_log: Optional[Path]) -> None:
    check_qos_client(cfg)
    print("doctor holder done")


def cmd_key_file_generate(ns: argparse.Namespace, audit_log: Optional[Path]) -> None:
    check_sensitive_external_path(
        Path(ns.master_seed_path),
        workdir=Path(ns.workdir),
        label="master seed / member secret path",
    )
    argv = [
        str(ns.qos_client),
        "generate-file-key",
        "--master-seed-path",
        ns.master_seed_path,
        "--pub-path",
        ns.pub_path,
    ]
    run_process(argv, dry_run=ns.dry_run, cwd=Path(ns.workdir), audit_log=audit_log)
    audit_file_hash(audit_log, Path(ns.pub_path).resolve())


def cmd_key_yubikey_provision(ns: argparse.Namespace, audit_log: Optional[Path]) -> None:
    argv = [str(ns.qos_client), "provision-yubikey", "--pub-path", ns.pub_path]
    run_process(argv, dry_run=ns.dry_run, cwd=Path(ns.workdir), audit_log=audit_log)
    audit_file_hash(audit_log, Path(ns.pub_path).resolve())


def _manifest_dirs(cfg: Config) -> Tuple[Path, Path, Path]:
    ms = resolve_path(cfg.workdir, cfg.paths()["manifest_set_dir"])
    ss = resolve_path(cfg.workdir, cfg.paths()["share_set_dir"])
    ps = resolve_path(cfg.workdir, cfg.paths()["patch_set_dir"])
    return ms, ss, ps


def _pivot_args_for_service(cfg: Config, svc: Mapping[str, Any]) -> str:
    name = svc["name"]
    d = cfg.raw["defaults"]
    if name == "tls-fetcher" and d.get("tls_fetcher_pivot_args"):
        return str(d["tls_fetcher_pivot_args"])
    if name == "notarizer" and d.get("notarizer_pivot_args"):
        return str(d["notarizer_pivot_args"])
    return str(d["pivot_args"])


def parse_quorum_threshold(path: Path, *, set_label: str) -> int:
    """Parse a `quorum_threshold` file written as a single-line decimal int.

    Format (intentionally minimal so qos_client can also consume it):
    one line, optional trailing newline, contents are a decimal integer >= 1.
    Comments, leading whitespace, surrounding whitespace, and any non-digit
    character are rejected so callers cannot accidentally feed in a Bash
    `=2` or YAML-style file.
    """
    if not path.is_file():
        sys.stderr.write(
            f"{set_label} quorum_threshold not found: {path}\n"
            "Format: a single line containing a decimal integer (e.g. `2`). "
            "See SECURITY.md `Threshold 推荐` for recommended values.\n"
        )
        raise SystemExit(2)
    raw = path.read_text(encoding="utf-8")
    stripped = raw.strip()
    if not stripped or not stripped.isdigit() or int(stripped) < 1:
        sys.stderr.write(
            f"{set_label} quorum_threshold at {path} must be a single decimal "
            f"integer >= 1, got {raw!r}\n"
        )
        raise SystemExit(2)
    return int(stripped)


_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _roster_set_label_to_path_key() -> Dict[str, str]:
    return {
        "manifest-set": "manifest_set_dir",
        "share-set": "share_set_dir",
        "patch-set": "patch_set_dir",
    }


def parse_member_roster(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Load and shape-check the Coordinator-issued member roster.

    The roster is the single source of truth for (alias, member_index)
    assignments across the ceremony. It MUST be produced and signed off by
    the Coordinator before any member submits a `.pub`, and is shipped
    inside review / share-request / genesis-output bundles so members can
    verify they were assigned the alias / index they expect.

    Schema (top-level dict):
        manifest_set, share_set, patch_set: optional list of entries.
        Each entry: {alias: str, owner?: str, member_index?: int}
        Share-set entries MUST have member_index (int >= 1).

    On any structural problem, exit 2 with a precise reason. Returns the
    validated roster split by set label (manifest-set / share-set /
    patch-set, hyphen form, matching skill directory names).
    """
    if not path.is_file():
        sys.stderr.write(
            f"member roster not found: {path}\n"
            "Ask the Coordinator for `shared/member-roster.json` (the file the "
            "Coordinator publishes after assigning aliases / member-indexes; "
            "see references/roles/coordinator.md `Alias / member-index "
            "assignment`).\n"
        )
        raise SystemExit(2)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"member roster is not valid JSON ({path}): {exc}\n")
        raise SystemExit(2) from exc
    if not isinstance(raw, dict):
        sys.stderr.write(f"member roster top-level must be an object, got {type(raw).__name__}\n")
        raise SystemExit(2)

    result: Dict[str, List[Dict[str, Any]]] = {}
    for json_key, set_label, requires_index in (
        ("manifest_set", "manifest-set", False),
        ("share_set", "share-set", True),
        ("patch_set", "patch-set", False),
    ):
        entries = raw.get(json_key)
        if entries is None:
            result[set_label] = []
            continue
        if not isinstance(entries, list):
            sys.stderr.write(f"roster.{json_key} must be a list\n")
            raise SystemExit(2)
        seen_alias: set[str] = set()
        seen_index: set[int] = set()
        normalized: List[Dict[str, Any]] = []
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                sys.stderr.write(f"roster.{json_key}[{i}] must be an object\n")
                raise SystemExit(2)
            alias = e.get("alias")
            if not isinstance(alias, str) or not _ALIAS_RE.match(alias):
                sys.stderr.write(
                    f"roster.{json_key}[{i}].alias must match {_ALIAS_RE.pattern} "
                    f"(filename-safe), got {alias!r}\n"
                )
                raise SystemExit(2)
            if alias in seen_alias:
                sys.stderr.write(
                    f"roster.{json_key}: duplicate alias {alias!r}; aliases must "
                    "be unique within a set\n"
                )
                raise SystemExit(2)
            seen_alias.add(alias)
            mi: Optional[int] = None
            if "member_index" in e and e["member_index"] is not None:
                if not isinstance(e["member_index"], int) or isinstance(e["member_index"], bool) or e["member_index"] < 1:
                    sys.stderr.write(
                        f"roster.{json_key}[{i}].member_index must be an int >= 1, "
                        f"got {e['member_index']!r}\n"
                    )
                    raise SystemExit(2)
                mi = int(e["member_index"])
                if mi in seen_index:
                    sys.stderr.write(
                        f"roster.{json_key}: duplicate member_index {mi}; share-set "
                        "member-indexes must be unique\n"
                    )
                    raise SystemExit(2)
                seen_index.add(mi)
            elif requires_index:
                sys.stderr.write(
                    f"roster.{json_key}[{i}] (alias={alias!r}) requires member_index "
                    "(share-set entries must declare a unique 1-based slot)\n"
                )
                raise SystemExit(2)
            entry: Dict[str, Any] = {"alias": alias}
            if mi is not None:
                entry["member_index"] = mi
            owner = e.get("owner")
            if owner is not None:
                if not isinstance(owner, str):
                    sys.stderr.write(
                        f"roster.{json_key}[{i}].owner must be a string when present\n"
                    )
                    raise SystemExit(2)
                entry["owner"] = owner
            normalized.append(entry)
        if requires_index and normalized:
            indexes = sorted(e["member_index"] for e in normalized)
            if indexes != list(range(1, len(indexes) + 1)):
                sys.stderr.write(
                    f"roster.{json_key} member_index values must be a consecutive "
                    f"1..N sequence with no gaps; got {indexes}\n"
                )
                raise SystemExit(2)
        result[set_label] = normalized
    return result


def _check_roster_against_pub_dir(
    entries: List[Dict[str, Any]],
    pub_dir: Path,
    set_label: str,
) -> None:
    """Ensure roster aliases for `set_label` match `*.pub` filenames in pub_dir.

    Skipped if pub_dir doesn't exist (e.g. a non-coordinator workdir or the
    set is intentionally disabled). When entries is empty, also requires
    that pub_dir contains no `*.pub` files — preventing "secret extras"
    that were never registered with the Coordinator.
    """
    if not pub_dir.is_dir():
        return
    on_disk = {p.stem for p in pub_dir.glob("*.pub")}
    on_roster = {e["alias"] for e in entries}
    extras = on_disk - on_roster
    missing = on_roster - on_disk
    if extras:
        sys.stderr.write(
            f"{set_label}: public-key files present in {pub_dir} but missing "
            f"from member-roster.json: {sorted(extras)}\n"
            "Either add them to the roster (Coordinator-side) or remove the "
            "stray .pub files; unregistered keys must not enter a manifest.\n"
        )
        raise SystemExit(2)
    if missing:
        sys.stderr.write(
            f"{set_label}: roster aliases without a matching <alias>.pub in "
            f"{pub_dir}: {sorted(missing)}\n"
            "Collect the missing public keys before continuing.\n"
        )
        raise SystemExit(2)


def check_member_roster(cfg: Config) -> Dict[str, List[Dict[str, Any]]]:
    """Coordinator-side roster gate.

    Loads `paths.member_roster_path` (default `shared/member-roster.json`),
    cross-checks it against each set's `*.pub` directory, and returns the
    parsed roster for callers that want to embed it into bundle metadata.
    Members who never run Coordinator commands never invoke this gate.
    """
    paths = cfg.paths()
    roster_path = resolve_path(
        cfg.workdir,
        paths.get("member_roster_path", "shared/member-roster.json"),
    )
    roster = parse_member_roster(roster_path)
    for set_label, key in _roster_set_label_to_path_key().items():
        if key not in paths:
            continue
        pub_dir = resolve_path(cfg.workdir, paths[key])
        _check_roster_against_pub_dir(roster[set_label], pub_dir, set_label)
    return roster


def _check_threshold_files(cfg: Config) -> None:
    """Validate that each set directory has a parseable quorum_threshold.

    Skipped silently for non-coordinator workdirs that don't ship the set
    layout; we look up paths via cfg.paths() and only check ones that exist
    as directories. The check exists so the Coordinator notices a forgotten
    `cp quorum_threshold.example quorum_threshold` step before manifest
    generation produces a misaligned envelope.
    """
    paths = cfg.paths()
    for key, label in (
        ("manifest_set_dir", "manifest-set"),
        ("share_set_dir", "share-set"),
        ("patch_set_dir", "patch-set"),
    ):
        if key not in paths:
            continue
        d = resolve_path(cfg.workdir, paths[key])
        if not d.is_dir():
            continue
        if not list(d.glob("*.pub")):
            # Patch-set may be intentionally disabled; skip when there are
            # no member public keys in the directory.
            continue
        parse_quorum_threshold(d / "quorum_threshold", set_label=label)


def cmd_manifest_generate(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    _check_threshold_files(cfg)
    check_member_roster(cfg)
    ms, ss, ps = _manifest_dirs(cfg)
    qos_release = resolve_path(cfg.workdir, cfg.paths()["qos_release_dir"])
    pcr = resolve_path(cfg.workdir, cfg.paths()["pcr3_preimage_path"])
    qkp = resolve_path(cfg.workdir, cfg.paths()["quorum_key_pub_path"])
    hashes = resolve_path(cfg.workdir, cfg.paths()["pivot_hashes_dir"])
    mroot = resolve_path(cfg.workdir, cfg.paths()["workdir_manifest_subdir"])
    require_file(pcr, "pcr3 preimage")

    defs = cfg.raw["defaults"]
    bridge = str(defs["bridge_config_json"])

    for svc in cfg.all_services():
        pname = svc["pivot_binary_name"]
        hash_path = hashes / f"{pname}-pivot-hash.txt"
        nonce = svc.get("manifest_nonce")
        if nonce is None:
            sys.stderr.write(
                f"{svc['name']}: set manifest_nonce in config (read old +1 via display)\n",
            )
            raise SystemExit(2)
        mpath = mroot / f"{svc['name']}-manifest.json"
        argv: List[str] = [
            str(cfg.qos_client),
            "generate-manifest",
            "--nonce",
            str(int(nonce)),
            "--namespace",
            str(svc["manifest_namespace"]),
            "--restart-policy",
            str(defs["restart_policy"]),
            "--pivot-hash-path",
            str(hash_path),
            "--pivot-args",
            _pivot_args_for_service(cfg, svc),
            "--bridge-config",
            bridge,
            "--qos-release-dir",
            str(qos_release),
            "--pcr3-preimage-path",
            str(pcr),
            "--manifest-set-dir",
            str(ms),
            "--share-set-dir",
            str(ss),
            "--patch-set-dir",
            str(ps),
            "--quorum-key-path",
            str(qkp),
            "--manifest-path",
            str(mpath),
        ]
        if defs.get("debug_mode_manifest"):
            argv += ["--debug-mode", "true"]
        print(f"== generate-manifest {svc['name']}")
        run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)
        audit_file_hash(audit_log, mpath)


def cmd_manifest_approve(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    # Argument-level validation must happen BEFORE the human confirmation
    # gate. Otherwise the operator has to type the exact phrase
    # "approve-manifest" just to learn they passed `--yubikey` and
    # `--secret-path` together, or forgot a credential entirely — a UX
    # regression caught in real-world integration testing on 2026-05-16.
    cred_argv = resolve_holder_credential(
        ns,
        workdir=cfg.workdir,
        secret_label="member secret path",
    )
    confirm_dangerous(ns, "approve-manifest on reviewed canonical manifest files", "approve-manifest")
    if ns.unsafe_auto_confirm or cfg.raw["defaults"].get("approve_unsafe_auto_confirm"):
        confirm_dangerous(ns, "unsafe-auto-confirm skips qos_client's human manifest prompts", "unsafe-auto-confirm")
    mroot = resolve_path(cfg.workdir, cfg.paths()["workdir_manifest_subdir"])
    ms, ss, ps = _manifest_dirs(cfg)
    qos_release = resolve_path(cfg.workdir, cfg.paths()["qos_release_dir"])
    pcr = resolve_path(cfg.workdir, cfg.paths()["pcr3_preimage_path"])
    hashes = resolve_path(cfg.workdir, cfg.paths()["pivot_hashes_dir"])
    qkp = resolve_path(cfg.workdir, cfg.paths()["quorum_key_pub_path"])
    to_run = cfg.all_services() if not ns.service else [cfg.svc(ns.service)]
    defs = cfg.raw["defaults"]

    for svc in to_run:
        pname = svc["pivot_binary_name"]
        hash_path = hashes / f"{pname}-pivot-hash.txt"
        mpath = mroot / f"{svc['name']}-manifest.json"
        approvals = mroot / "approvals" / svc["name"]
        approvals.mkdir(parents=True, exist_ok=True)
        if not ns.skip_display:
            run_process(
                [
                    str(cfg.qos_client),
                    "display",
                    "--display-type",
                    "manifest",
                    "--file-path",
                    str(mpath),
                ],
                dry_run=ns.dry_run,
                cwd=cfg.workdir,
                audit_log=audit_log,
            )
        argv = [
            str(cfg.qos_client),
            "approve-manifest",
            *cred_argv,
            "--alias",
            ns.alias,
            "--manifest-path",
            str(mpath),
            "--manifest-approvals-dir",
            str(approvals),
            "--qos-release-dir",
            str(qos_release),
            "--pcr3-preimage-path",
            str(pcr),
            "--pivot-hash-path",
            str(hash_path),
            "--quorum-key-path",
            str(qkp),
            "--manifest-set-dir",
            str(ms),
            "--share-set-dir",
            str(ss),
            "--patch-set-dir",
            str(ps),
        ]
        if ns.unsafe_auto_confirm or defs.get("approve_unsafe_auto_confirm"):
            argv.append("--unsafe-auto-confirm")
        print(f"== approve-manifest {svc['name']}")
        run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)
        if not ns.dry_run:
            for approval in sorted(approvals.glob("*.approval")):
                audit_file_hash(audit_log, approval)


def cmd_manifest_envelope(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    mroot = resolve_path(cfg.workdir, cfg.paths()["workdir_manifest_subdir"])
    for svc in cfg.all_services():
        mpath = mroot / f"{svc['name']}-manifest.json"
        approvals = mroot / "approvals" / svc["name"]
        if not ns.dry_run:
            validate_current_round_approvals(mroot, svc)
        envpath = mroot / f"{svc['name']}-manifest-envelope.json"
        argv = [
            str(cfg.qos_client),
            "generate-manifest-envelope",
            "--manifest-approvals-dir",
            str(approvals),
            "--manifest-path",
            str(mpath),
            "--manifest-envelope-path",
            str(envpath),
        ]
        print(f"== generate-manifest-envelope {svc['name']}")
        run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)
        audit_file_hash(audit_log, envpath)


def overlay_path(cfg: Config) -> Path:
    rel = cfg.raw["kustomize_overlay_path"]
    p = Path(rel).expanduser()
    if not p.is_absolute():
        sys.stderr.write(
            f"kustomize_overlay_path must be an absolute path, got {rel!r}.\n"
            "Set it to the absolute filesystem location of the K8s overlay "
            "(for example /Users/you/codes/0xkey/repos/enclave/deploy/k8s/overlays/prod).\n"
        )
        raise SystemExit(2)
    return p.resolve()


def cmd_deploy_render(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    ov = overlay_path(cfg)
    run_process(
        [cfg.kubectl(), "kustomize", str(ov)],
        dry_run=ns.dry_run,
        cwd=cfg.workdir,
        audit_log=audit_log,
    )


def cmd_deploy_apply(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    confirm_dangerous(ns, "kubectl apply -k to the configured enclave overlay", "kubectl-apply")
    ov = overlay_path(cfg)
    run_process(
        [cfg.kubectl(), "apply", "-k", str(ov)],
        dry_run=ns.dry_run,
        cwd=cfg.workdir,
        audit_log=audit_log,
    )


def _kubectl_pod_ip(cfg: Config, svc: Mapping[str, Any]) -> str:
    ns = cfg.raw["kubernetes_namespace"]
    lbl = svc.get("deployment_label_app") or svc["name"]
    out = subprocess.check_output(
        [
            cfg.kubectl(),
            "get",
            "pods",
            "-n",
            ns,
            "-l",
            f"app={lbl}",
            "-o",
            "jsonpath={.items[0].status.podIP}",
        ],
        text=True,
    ).strip()
    if not out or out == "<none>":
        sys.stderr.write(f"empty pod ip for {svc['name']} (app={lbl})\n")
        raise SystemExit(1)
    return out


def _boot_uri_args(cfg: Config, svc: Mapping[str, Any], host_ip: str) -> List[str]:
    args = [
        "--host-ip",
        host_ip,
        "--host-port",
        str(svc["host_port_qos"]),
    ]
    ebp = svc.get("endpoint_base_path")
    if ebp:
        args += ["--endpoint-base-path", str(ebp)]
    return args


def _svc_host(cfg: Config, svc: Mapping[str, Any], resolve_pod_ip: bool) -> str:
    if resolve_pod_ip:
        return _kubectl_pod_ip(cfg, svc)
    hip = svc.get("host_ip")
    if hip:
        return str(hip)
    sys.stderr.write(f"{svc['name']}: set host_ip or use --resolve-pod-ip\n")
    raise SystemExit(2)


def cmd_ceremony_boot(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    mroot = resolve_path(cfg.workdir, cfg.paths()["workdir_manifest_subdir"])
    pcr = resolve_path(cfg.workdir, cfg.paths()["pcr3_preimage_path"])
    pivots = resolve_path(cfg.workdir, cfg.paths()["pivots_dir"])

    if ns.unsafe_skip_attestation:
        confirm_dangerous(ns, "unsafe-skip-attestation disables attestation verification", "unsafe-skip-attestation")

    for svc in cfg.all_services():
        hip = _svc_host(cfg, svc, ns.resolve_pod_ip)
        argv = [
            str(cfg.qos_client),
            "boot-standard",
            *_boot_uri_args(cfg, svc, hip),
            "--pivot-path",
            str(pivots / svc["pivot_binary_name"]),
            "--manifest-envelope-path",
            str(mroot / f"{svc['name']}-manifest-envelope.json"),
            "--pcr3-preimage-path",
            str(pcr),
        ]
        if ns.unsafe_skip_attestation:
            argv.append("--unsafe-skip-attestation")
        print(f"== boot-standard {svc['name']} @ {hip}")
        run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)


def cmd_ceremony_attestation(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    mroot = resolve_path(cfg.workdir, cfg.paths()["workdir_manifest_subdir"])
    att_root = resolve_path(cfg.workdir, ns.attest_dir)
    att_root.mkdir(parents=True, exist_ok=True)
    for svc in cfg.all_services():
        hip = _svc_host(cfg, svc, ns.resolve_pod_ip)
        argv = [
            str(cfg.qos_client),
            "get-attestation-doc",
            *_boot_uri_args(cfg, svc, hip),
            "--manifest-envelope-path",
            str(mroot / f"{svc['name']}-manifest-envelope.json"),
            "--attestation-doc-path",
            str(att_root / f"{svc['name']}.cose"),
        ]
        print(f"== get-attestation-doc {svc['name']}")
        run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)
        audit_file_hash(audit_log, att_root / f"{svc['name']}.cose")


def _resolve_endpoint(
    cfg: Config,
    *,
    explicit: Optional[str],
    resolve_pod_ip: bool,
    label: Optional[str],
    namespace: Optional[str],
) -> Tuple[str, int]:
    if explicit:
        host, _, port = explicit.partition(":")
        if not host or not port or not port.isdigit():
            sys.stderr.write(f"--genesis-endpoint must be host:port, got {explicit!r}\n")
            raise SystemExit(2)
        return host, int(port)
    if not resolve_pod_ip:
        sys.stderr.write(
            "Genesis endpoint required: pass --genesis-endpoint host:port "
            "or --resolve-pod-ip with --genesis-label and --genesis-namespace\n"
        )
        raise SystemExit(2)
    if not label:
        sys.stderr.write("--resolve-pod-ip requires --genesis-label\n")
        raise SystemExit(2)
    ns_arg = namespace or cfg.raw.get("kubernetes_namespace") or "default"
    out = subprocess.check_output(
        [
            cfg.kubectl(),
            "-n",
            ns_arg,
            "get",
            "pod",
            "-l",
            label,
            "-o",
            "jsonpath={.items[0].status.podIP}",
        ],
        text=True,
    ).strip()
    if not out:
        sys.stderr.write(f"could not resolve pod IP for label {label} in namespace {ns_arg}\n")
        raise SystemExit(2)
    port = cfg.raw.get("verification", {}).get("data_plane_port", 3000)
    return out, int(port)


def cmd_ceremony_genesis_boot(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    """Wrap `qos_client boot-genesis`.

    Runs once per ceremony as the first cryptographic step. The Genesis enclave
    is a short-lived target started outside this skill (operator stands it up
    via the Coordinator's existing infra automation); this command just feeds
    the share-set, the DR public key, and the qOS release into it and asks
    qos_client to produce the per-member encrypted shares + quorum_key.pub.
    """
    confirm_dangerous(
        ns,
        "boot-genesis splits the quorum key into encrypted member shares",
        "boot-genesis",
    )
    paths = cfg.paths()
    share_set_dir = resolve_path(cfg.workdir, ns.share_set_dir or paths["share_set_dir"])
    namespace_dir = resolve_path(cfg.workdir, ns.namespace_dir or "genesis-output")
    qos_release = resolve_path(cfg.workdir, ns.qos_release_dir or paths["qos_release_dir"])
    pcr = resolve_path(cfg.workdir, ns.pcr3_preimage_path or paths["pcr3_preimage_path"])
    dr_pub_default = paths.get("dr_key_pub_path", "shared/dr-key.pub")
    dr_pub = resolve_path(cfg.workdir, ns.dr_pub_path or dr_pub_default)

    if not dr_pub.is_file():
        sys.stderr.write(
            f"DR public key not found: {dr_pub}\n"
            "boot-genesis requires --dr-pub-path (or paths.dr_key_pub_path) to point at a "
            "260-hex DR public key file. Generate the DR key in an external vault first; "
            "the DR private key MUST NOT live in this workspace.\n"
        )
        raise SystemExit(2)
    if not share_set_dir.is_dir() or not list(share_set_dir.glob("*.pub")):
        sys.stderr.write(
            f"share-set directory missing or empty (no *.pub files): {share_set_dir}\n"
        )
        raise SystemExit(2)
    parse_quorum_threshold(share_set_dir / "quorum_threshold", set_label="share-set")
    # Hard gate: roster must exist and match the share-set *.pub files
    # before Genesis. After this command, (alias, member-index, .pub, .share)
    # is bound for the lifetime of the resulting quorum_key, so any
    # collision or ad-hoc rename must be caught here.
    check_member_roster(cfg)

    namespace_dir.mkdir(parents=True, exist_ok=True)
    host, port = _resolve_endpoint(
        cfg,
        explicit=ns.genesis_endpoint,
        resolve_pod_ip=ns.resolve_pod_ip,
        label=ns.genesis_label,
        namespace=ns.genesis_namespace,
    )
    argv = [
        str(cfg.qos_client),
        "boot-genesis",
        "--host-ip",
        host,
        "--host-port",
        str(port),
        "--share-set-dir",
        str(share_set_dir),
        "--namespace-dir",
        str(namespace_dir),
        "--qos-release-dir",
        str(qos_release),
        "--pcr3-preimage-path",
        str(pcr),
        "--dr-key-path",
        str(dr_pub),
    ]
    print(f"== boot-genesis @ {host}:{port}")
    run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)
    if not ns.dry_run:
        # Hash the headline output artifacts the Coordinator will hand off.
        for fname in ("genesis_output", "quorum_key.pub"):
            target = namespace_dir / fname
            if target.is_file():
                audit_file_hash(audit_log, target)


def cmd_ceremony_share_extract(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    """Wrap `qos_client after-genesis` for one Share Set member.

    Reads the encrypted share for the member from the Coordinator's
    genesis-output bundle and re-encrypts it to the member's long-term key.
    The output `.share` MUST be written to the member's external key vault,
    not the role workdir; we enforce that with check_sensitive_external_path.
    """
    # Argument-level validation (credentials, paths, bundle presence)
    # runs BEFORE the human confirmation gate. Asking the operator to
    # type "after-genesis" only to be told they forgot --yubikey or that
    # incoming/genesis-output/ is empty is gratuitously hostile.
    paths = cfg.paths()
    namespace_dir = resolve_path(cfg.workdir, ns.namespace_dir or "incoming/genesis-output")
    if not namespace_dir.is_dir():
        sys.stderr.write(
            f"genesis-output namespace directory not found: {namespace_dir}\n"
            "Either point --namespace-dir at the extracted bundle root or run "
            "`bundle extract` on the Coordinator's genesis-output bundle first.\n"
        )
        raise SystemExit(2)
    pcr = resolve_path(cfg.workdir, ns.pcr3_preimage_path or paths["pcr3_preimage_path"])
    check_sensitive_external_path(
        Path(ns.share_path),
        workdir=cfg.workdir,
        label="member share output path",
    )
    cred_argv = resolve_holder_credential(
        ns,
        workdir=cfg.workdir,
        secret_label="member secret path",
    )
    confirm_dangerous(
        ns,
        "after-genesis decrypts this member's quorum share material",
        "after-genesis",
    )
    Path(ns.share_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    argv = [str(cfg.qos_client), "after-genesis", *cred_argv]
    argv += [
        "--share-path",
        ns.share_path,
        "--alias",
        ns.alias,
        "--namespace-dir",
        str(namespace_dir),
        "--pcr3-preimage-path",
        str(pcr),
    ]
    print(f"== after-genesis alias={ns.alias} member-index={ns.member_index}")
    run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)
    if not ns.dry_run:
        audit_file_hash(audit_log, Path(ns.share_path).resolve())


def approval_alias(ns: argparse.Namespace, cfg: Config) -> str:
    alias = getattr(ns, "approval_alias", None) or cfg.raw.get("approval_alias")
    if not alias:
        sys.stderr.write(
            "approval alias required: pass --approval-alias or set config.approval_alias\n"
        )
        raise SystemExit(2)
    return str(alias)


def approval_context(svc: Mapping[str, Any]) -> Tuple[str, str]:
    namespace = str(svc["manifest_namespace"]).replace("/", "-")
    nonce = svc.get("manifest_nonce")
    if nonce is None:
        sys.stderr.write(f"{svc['name']}: manifest_nonce required for approval matching\n")
        raise SystemExit(2)
    return namespace, str(int(nonce))


def validate_current_round_approvals(mroot: Path, svc: Mapping[str, Any]) -> None:
    d = mroot / "approvals" / svc["name"]
    namespace, nonce = approval_context(svc)
    approvals = sorted(d.glob("*.approval"))
    if not approvals:
        sys.stderr.write(f"no .approval in {d}\n")
        raise SystemExit(2)
    bad = [
        p.name
        for p in approvals
        if namespace not in p.stem or not p.stem.endswith(f"-{nonce}")
    ]
    if bad:
        sys.stderr.write(
            f"{svc['name']}: approvals from another service/round detected: {bad}\n"
        )
        raise SystemExit(2)


def approval_for(mroot: Path, svc: Mapping[str, Any], alias: str) -> Path:
    d = mroot / "approvals" / svc["name"]
    namespace, nonce = approval_context(svc)
    matches = [
        p
        for p in sorted(d.glob("*.approval"))
        if p.stem.startswith(f"{alias}-")
        and namespace in p.stem
        and p.stem.endswith(f"-{nonce}")
    ]
    if len(matches) != 1:
        sys.stderr.write(
            f"{svc['name']}: expected exactly one approval for alias={alias}, "
            f"namespace={namespace}, nonce={nonce}; found {len(matches)} in {d}\n"
        )
        raise SystemExit(2)
    return matches[0]


def cmd_ceremony_reencrypt(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    # Argument-level validation must run before the human confirmation
    # gate; see cmd_manifest_approve note above.
    cred_argv = resolve_holder_credential(
        ns,
        workdir=cfg.workdir,
        secret_label="member secret path",
    )
    # NOTE: `--share-path` is the holder's `.share` file and must stay an
    # external path regardless of the credential type (the share itself
    # is never stored on the YubiKey; only the long-term secret is).
    check_sensitive_external_path(
        Path(ns.share_path),
        workdir=cfg.workdir,
        label="member share path",
    )
    confirm_dangerous(ns, "proxy-re-encrypt-share using holder secret/share material", "reencrypt-share")
    if ns.unsafe_skip_attestation:
        confirm_dangerous(ns, "unsafe-skip-attestation disables attestation verification", "unsafe-skip-attestation")
    if ns.unsafe_auto_confirm:
        confirm_dangerous(ns, "unsafe-auto-confirm skips qos_client's share re-encryption prompts", "unsafe-auto-confirm")
    ms = resolve_path(cfg.workdir, cfg.paths()["manifest_set_dir"])
    pcr = resolve_path(cfg.workdir, cfg.paths()["pcr3_preimage_path"])
    mroot = resolve_path(cfg.workdir, cfg.paths()["workdir_manifest_subdir"])
    att_root = resolve_path(cfg.workdir, ns.attest_dir)
    out_root = resolve_path(cfg.workdir, ns.wrapped_out_dir)
    mid = ns.member_index
    ap_alias = approval_alias(ns, cfg)
    for svc in cfg.all_services():
        envpath = mroot / f"{svc['name']}-manifest-envelope.json"
        adoc = att_root / f"{svc['name']}.cose"
        approval = approval_for(mroot, svc, ap_alias)
        dest = out_root / svc["name"] / f"member{mid}_eph_wrapped.share"
        dest.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            str(cfg.qos_client),
            "proxy-re-encrypt-share",
            *cred_argv,
            "--share-path",
            ns.share_path,
            "--alias",
            ns.alias,
            "--attestation-doc-path",
            str(adoc),
            "--eph-wrapped-share-path",
            str(dest),
            "--approval-path",
            str(approval),
            "--manifest-envelope-path",
            str(envpath),
            "--manifest-set-dir",
            str(ms),
            "--pcr3-preimage-path",
            str(pcr),
        ]
        if ns.unsafe_skip_attestation:
            argv.append("--unsafe-skip-attestation")
        if ns.unsafe_auto_confirm:
            argv.append("--unsafe-auto-confirm")
        print(f"== proxy-re-encrypt-share {svc['name']} member{mid}")
        run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)
        audit_file_hash(audit_log, dest)


def parse_int_list(spec: Optional[str]) -> Optional[List[int]]:
    if spec is None or spec.strip() == "":
        return None
    out: List[int] = []
    for part in spec.split(","):
        p = part.strip().lower().replace("m", "")
        if not p.isdigit():
            sys.stderr.write(f"invalid order token: {part!r}\n")
            raise SystemExit(2)
        out.append(int(p))
    return out


def post_order_for_svc(
    svc: Mapping[str, Any],
    global_order: Optional[List[int]],
) -> List[int]:
    v = svc.get("post_share_members_order")
    if isinstance(v, list):
        return [int(x) for x in v]
    if isinstance(v, str):
        o = parse_int_list(v)
        if o:
            return o
    if global_order:
        return global_order
    return [1, 2]


def cmd_ceremony_post(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    confirm_dangerous(ns, "post-share to live enclave pods", "post-share")
    mroot = resolve_path(cfg.workdir, cfg.paths()["workdir_manifest_subdir"])
    wrap_root = resolve_path(cfg.workdir, ns.wrapped_in_dir)
    gorder = parse_int_list(ns.post_global_order)
    ap_alias = approval_alias(ns, cfg)

    for svc in cfg.all_services():
        hip = _svc_host(cfg, svc, ns.resolve_pod_ip)
        order = post_order_for_svc(svc, gorder)
        for mid in order:
            w = wrap_root / svc["name"] / f"member{mid}_eph_wrapped.share"
            if not w.is_file():
                alt = wrap_root / svc["name"] / f"m{mid}_eph_wrapped.share"
                w = alt if alt.is_file() else w
            require_file(w, f"wrapped share {svc['name']} member{mid}")
            approval = approval_for(mroot, svc, ap_alias)
            argv = [
                str(cfg.qos_client),
                "post-share",
                *_boot_uri_args(cfg, svc, hip),
                "--eph-wrapped-share-path",
                str(w),
                "--approval-path",
                str(approval),
            ]
            print(f"== post-share {svc['name']} member{mid}")
            run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)


def cmd_verify(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    kns = cfg.raw["kubernetes_namespace"]
    ver = cfg.raw.get("verification") or {}
    dp = int(ver.get("data_plane_port", 8081))
    if not ver.get("use_kubectl_for_health", True):
        sys.stderr.write("set verification.use_kubectl_for_health=true\n")
        raise SystemExit(2)

    for svc in cfg.all_services():
        name = svc["name"]
        lbl = svc.get("deployment_label_app") or name
        hp = int(svc["host_port_qos"])
        run_process(
            [
                cfg.kubectl(),
                "wait",
                "--for=condition=Ready",
                "pod",
                "-n",
                kns,
                "-l",
                f"app={lbl}",
                "--timeout=90s",
            ],
            dry_run=ns.dry_run,
            cwd=cfg.workdir,
            audit_log=audit_log,
        )
        if ns.dry_run:
            print(f"[dry-run] verify control plane via port-forward deploy/{name} {hp}")
        else:
            with kubectl_port_forward(
                cfg,
                namespace=kns,
                target=f"deploy/{name}",
                remote_port=hp,
                audit_log=audit_log,
            ) as local_port:
                code, body = http_request(
                    "GET",
                    f"http://127.0.0.1:{local_port}/qos/enclave-health",
                )
                print(f"{name} control health HTTP {code}: {body}")
                if code >= 400 or "QuorumKeyProvisioned" not in body:
                    sys.stderr.write(
                        f"{name}: expected QuorumKeyProvisioned in control health\n"
                    )
                    raise SystemExit(1)
        hpath = str(svc.get("data_plane_health_path", "/health"))
        post_p = str(svc.get("data_plane_post_path", "/health"))
        if ns.dry_run:
            print(f"[dry-run] verify data plane via port-forward deploy/{name} {dp}")
        else:
            with kubectl_port_forward(
                cfg,
                namespace=kns,
                target=f"deploy/{name}",
                remote_port=dp,
                audit_log=audit_log,
            ) as local_port:
                code, body = http_request(
                    "GET",
                    f"http://127.0.0.1:{local_port}{hpath}",
                )
                print(f"{name} data health HTTP {code}: {body}")
                if code >= 400:
                    sys.stderr.write(f"{name}: data health failed with HTTP {code}\n")
                    raise SystemExit(1)
                code, body = http_request(
                    "POST",
                    f"http://127.0.0.1:{local_port}{post_p}",
                    data=b"",
                )
                print(f"{name} data POST {post_p} HTTP {code}: {body}")
                if code < 200 or code >= 500:
                    sys.stderr.write(
                        f"{name}: data POST smoke failed with HTTP {code}\n"
                    )
                    raise SystemExit(1)
    print("verify done")


def cmd_keyfwd_boot(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    mroot = resolve_path(cfg.workdir, cfg.paths()["workdir_manifest_subdir"])
    pivots = resolve_path(cfg.workdir, cfg.paths()["pivots_dir"])
    svc = cfg.svc(ns.service)
    hip = ns.host_ip or _svc_host(cfg, svc, ns.resolve_pod_ip)
    argv = [
        str(cfg.qos_client),
        "boot-key-fwd",
        *_boot_uri_args(cfg, svc, hip),
        "--manifest-envelope-path",
        str(mroot / f"{svc['name']}-manifest-envelope.json"),
        "--pivot-path",
        str(pivots / svc["pivot_binary_name"]),
        "--attestation-doc-path",
        str(Path(ns.attestation_out).resolve()),
    ]
    print("== boot-key-fwd")
    run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)
    audit_file_hash(audit_log, Path(ns.attestation_out).resolve())


def cmd_keyfwd_export(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    mroot = resolve_path(cfg.workdir, cfg.paths()["workdir_manifest_subdir"])
    svc_export = cfg.svc(ns.export_service)
    hip = ns.from_host_ip
    svc_new = cfg.svc(ns.service)
    argv = [
        str(cfg.qos_client),
        "export-key",
        *_boot_uri_args(cfg, svc_export, hip),
        "--manifest-envelope-path",
        str(mroot / f"{svc_new['name']}-manifest-envelope.json"),
        "--attestation-doc-path",
        str(Path(ns.attestation_doc).resolve()),
        "--encrypted-quorum-key-path",
        str(Path(ns.encrypted_out).resolve()),
    ]
    print("== export-key")
    run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)
    audit_file_hash(audit_log, Path(ns.encrypted_out).resolve())


def cmd_keyfwd_inject(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    svc = cfg.svc(ns.service)
    hip = ns.host_ip or _svc_host(cfg, svc, ns.resolve_pod_ip)
    argv = [
        str(cfg.qos_client),
        "inject-key",
        *_boot_uri_args(cfg, svc, hip),
        "--encrypted-quorum-key-path",
        str(Path(ns.encrypted_in).resolve()),
    ]
    print("== inject-key")
    run_process(argv, dry_run=ns.dry_run, cwd=cfg.workdir, audit_log=audit_log)


def cmd_bundle_checksums(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    root = resolve_path(cfg.workdir, ns.bundle_dir)
    if ns.dry_run:
        print(f"[dry-run] write SHA256SUMS under {root}")
        return
    sums = write_sha256sums(root)
    print(f"wrote {sums}")
    audit_file_hash(audit_log, sums)


def cmd_bundle_verify(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    root = resolve_path(cfg.workdir, ns.bundle_dir)
    if ns.dry_run:
        print(f"[dry-run] verify SHA256SUMS under {root}")
        return
    verify_sha256sums(root)
    print(f"verified {root / 'SHA256SUMS'}")
    audit_file_hash(audit_log, root / "SHA256SUMS")


def copy_file(src: Path, dest: Path) -> None:
    require_file(src, str(src))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def copy_tree_contents(src: Path, dest: Path) -> None:
    if not src.is_dir():
        sys.stderr.write(f"missing directory: {src}\n")
        raise SystemExit(2)
    for p in sorted(src.rglob("*")):
        if p.is_file():
            copy_file(p, dest / p.relative_to(src))


_ROSTER_SETS_FOR_KIND: Dict[str, Tuple[str, ...]] = {
    "review": ("manifest-set", "share-set", "patch-set"),
    "share-request": ("manifest-set", "share-set"),
    "genesis-output": ("share-set",),
}


def _roster_slice_for_kind(
    cfg: Config, kind: str
) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Return the subset of the roster a given bundle kind should embed.

    Bundles meant for set members carry only the relevant slices so each
    member can verify its own (alias, member_index) without seeing the
    entire roster mapping. Bundles that don't deal with member identity
    (approvals, wrapped-shares) get no roster.
    """
    sets = _ROSTER_SETS_FOR_KIND.get(kind)
    if not sets:
        return None
    paths = cfg.paths()
    roster_path = resolve_path(
        cfg.workdir,
        paths.get("member_roster_path", "shared/member-roster.json"),
    )
    if not roster_path.is_file():
        return None
    full = parse_member_roster(roster_path)
    return {label: full.get(label, []) for label in sets}


def write_bundle_meta(root: Path, kind: str, cfg: Config) -> None:
    meta: Dict[str, Any] = {
        "kind": kind,
        "services": [s["name"] for s in cfg.all_services()],
        "manifest_namespaces": {
            s["name"]: s["manifest_namespace"] for s in cfg.all_services()
        },
        "manifest_nonces": {s["name"]: s.get("manifest_nonce") for s in cfg.all_services()},
    }
    roster_slice = _roster_slice_for_kind(cfg, kind)
    if roster_slice is not None:
        meta["members"] = roster_slice
    (root / "BUNDLE.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def create_bundle(root: Path, kind: str, cfg: Config) -> None:
    if root.exists():
        sys.stderr.write(f"bundle dir already exists: {root}\n")
        raise SystemExit(2)
    root.mkdir(parents=True)
    mroot = resolve_path(cfg.workdir, cfg.paths()["workdir_manifest_subdir"])
    pcr = resolve_path(cfg.workdir, cfg.paths()["pcr3_preimage_path"])
    qkp = resolve_path(cfg.workdir, cfg.paths()["quorum_key_pub_path"])
    hashes = resolve_path(cfg.workdir, cfg.paths()["pivot_hashes_dir"])
    qos_release = resolve_path(cfg.workdir, cfg.paths()["qos_release_dir"])
    att = resolve_path(cfg.workdir, "attestations")

    if kind == "review":
        for svc in cfg.all_services():
            copy_file(mroot / f"{svc['name']}-manifest.json", root / f"{svc['name']}-manifest.json")
        for key in ("manifest_set_dir", "share_set_dir", "patch_set_dir"):
            copy_tree_contents(resolve_path(cfg.workdir, cfg.paths()[key]), root / Path(cfg.paths()[key]).name)
        copy_file(qkp, root / "quorum_key.pub")
        copy_file(pcr, root / "pcr3-preimage.txt")
        copy_tree_contents(hashes, root / "pivot-hashes")
        nitro = qos_release / "nitro.pcrs"
        if nitro.is_file():
            copy_file(nitro, root / "qos-release" / "nitro.pcrs")
    elif kind == "share-request":
        for svc in cfg.all_services():
            copy_file(mroot / f"{svc['name']}-manifest-envelope.json", root / f"{svc['name']}-manifest-envelope.json")
            copy_file(att / f"{svc['name']}.cose", root / "attestations" / f"{svc['name']}.cose")
            copy_tree_contents(mroot / "approvals" / svc["name"], root / "approvals" / svc["name"])
        copy_tree_contents(resolve_path(cfg.workdir, cfg.paths()["manifest_set_dir"]), root / "manifest-set")
        copy_file(pcr, root / "pcr3-preimage.txt")
    elif kind == "approvals":
        copy_tree_contents(mroot / "approvals", root / "approvals")
    elif kind == "wrapped-shares":
        copy_tree_contents(resolve_path(cfg.workdir, "wrapped-shares-out"), root / "wrapped-shares")
    elif kind == "genesis-output":
        # Genesis-output bundle is built on the Coordinator after `ceremony
        # genesis-boot` and shipped to every Share Set member so they can run
        # `ceremony share-extract`. We package the entire qos_client
        # `--namespace-dir` (which holds the per-member encrypted shares,
        # genesis_output, and quorum_key.pub) plus the PCR3 preimage and the
        # qOS release PCR files so members can verify attestation locally.
        gout = resolve_path(cfg.workdir, cfg.paths().get("genesis_output_dir", "genesis-output"))
        copy_tree_contents(gout, root / "genesis-output")
        copy_file(pcr, root / "pcr3-preimage.txt")
        nitro = qos_release / "nitro.pcrs"
        if nitro.is_file():
            copy_file(nitro, root / "qos-release" / "nitro.pcrs")
        aws_pcrs = qos_release / "aws-x86_64.pcrs"
        if aws_pcrs.is_file():
            copy_file(aws_pcrs, root / "qos-release" / "aws-x86_64.pcrs")
    else:
        sys.stderr.write(f"unsupported bundle kind: {kind}\n")
        raise SystemExit(2)
    if kind in _ROSTER_SETS_FOR_KIND:
        roster_path = resolve_path(
            cfg.workdir,
            cfg.paths().get("member_roster_path", "shared/member-roster.json"),
        )
        if roster_path.is_file():
            copy_file(roster_path, root / "member-roster.json")
    write_bundle_meta(root, kind, cfg)
    write_sha256sums(root)


def safe_extract_tar(archive: Path, dest: Path) -> None:
    """Extract `archive` into `dest`, refusing dangerous members.

    Strategy:
    1. On Python 3.12+, prefer `tarfile.extractall(filter="data")` which is
       PEP 706's hardened path: rejects path traversal, symlinks/hardlinks
       outside the destination, device/FIFO members, and tightens permission
       bits to plain data files.
    2. On older Pythons, fall back to a hand-rolled check that:
       - rejects absolute paths and any member starting with `..`
       - rejects all link types (sym/hard) and device/FIFO members
       - resolves each candidate target and ensures it stays under `dest`
       Then extract members one-by-one with that filtered list.
    """
    require_file(archive, "bundle archive")
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()

    with tarfile.open(archive, "r:gz") as tf:
        if hasattr(tarfile, "data_filter"):
            try:
                tf.extractall(dest, filter="data")
                return
            except (tarfile.LinkOutsideDestinationError, tarfile.AbsolutePathError, tarfile.OutsideDestinationError, tarfile.SpecialFileError) as exc:
                sys.stderr.write(f"tar member rejected by data filter: {exc}\n")
                raise SystemExit(2) from exc
            except tarfile.FilterError as exc:
                sys.stderr.write(f"tar member rejected by data filter: {exc}\n")
                raise SystemExit(2) from exc

        safe_members: List[tarfile.TarInfo] = []
        for member in tf.getmembers():
            name = member.name
            if name.startswith("/") or name.startswith("\\"):
                sys.stderr.write(f"refusing absolute-path tar member: {name}\n")
                raise SystemExit(2)
            parts = Path(name).parts
            if any(p == ".." for p in parts):
                sys.stderr.write(f"refusing parent-traversal tar member: {name}\n")
                raise SystemExit(2)
            if member.issym() or member.islnk():
                sys.stderr.write(f"refusing link tar member: {name}\n")
                raise SystemExit(2)
            if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                sys.stderr.write(f"refusing device/FIFO tar member: {name}\n")
                raise SystemExit(2)
            target = (dest / name).resolve()
            try:
                target.relative_to(dest_resolved)
            except ValueError:
                sys.stderr.write(f"tar member escapes destination: {name}\n")
                raise SystemExit(2)
            safe_members.append(member)
        tf.extractall(dest, members=safe_members)


def make_tar_gz(src: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(src, arcname=src.name)


def cmd_bundle_create(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    root = resolve_path(cfg.workdir, ns.bundle_dir)
    if ns.dry_run:
        print(f"[dry-run] create {ns.kind} bundle under {root}")
        if ns.archive:
            print(f"[dry-run] pack bundle archive {resolve_path(cfg.workdir, ns.archive)}")
        return
    create_bundle(root, ns.kind, cfg)
    print(f"created {ns.kind} bundle: {root}")
    audit_file_hash(audit_log, root / "SHA256SUMS")
    if ns.archive:
        archive = resolve_path(cfg.workdir, ns.archive)
        make_tar_gz(root, archive)
        print(f"packed {archive}")
        audit_file_hash(audit_log, archive)


def cmd_bundle_extract(ns: argparse.Namespace, cfg: Config, audit_log: Optional[Path]) -> None:
    archive = resolve_path(cfg.workdir, ns.archive)
    dest = resolve_path(cfg.workdir, ns.bundle_dir)
    if ns.dry_run:
        print(f"[dry-run] extract {archive} to {dest}")
        return
    safe_extract_tar(archive, dest)
    print(f"extracted {archive} to {dest}")
    if (dest / "SHA256SUMS").is_file():
        verify_root = dest
    else:
        children = [p for p in dest.iterdir() if p.is_dir()]
        if len(children) != 1:
            sys.stderr.write(
                f"cannot locate extracted bundle root under {dest}; expected one child directory\n"
            )
            raise SystemExit(2)
        verify_root = children[0]
    verify_sha256sums(verify_root)
    audit_file_hash(audit_log, archive)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--yes",
        action="store_true",
        help="skip only non-dangerous confirms; critical key/deploy operations still require exact typed phrases",
    )
    p.add_argument("--audit-log", type=Path)

    subs = p.add_subparsers(dest="cmd", required=True)

    sp = subs.add_parser("doctor")
    dsubs = sp.add_subparsers(dest="doctor_role", required=True)
    dc = dsubs.add_parser("coordinator")
    dc.set_defaults(handler="doctor_coordinator")
    dh = dsubs.add_parser("holder")
    dh.set_defaults(handler="doctor_holder")

    k = subs.add_parser("key")
    ks = k.add_subparsers(dest="keycmd", required=True)
    kg = ks.add_parser("file-generate")
    kg.add_argument("--master-seed-path", required=True)
    kg.add_argument("--pub-path", required=True)
    kg.set_defaults(handler="key_fg")

    ky = ks.add_parser("yubikey-provision")
    ky.add_argument("--pub-path", required=True)
    ky.set_defaults(handler="key_yk")

    mg = subs.add_parser("manifest")
    ms = mg.add_subparsers(dest="mancmd", required=True)
    mgen = ms.add_parser("generate")
    mgen.set_defaults(handler="manifest_gen")
    apr = ms.add_parser("approve")
    apr.add_argument("--alias", required=True)
    apr.add_argument(
        "--secret-path",
        default=None,
        help="absolute path to the member's long-term secret file (required unless --yubikey)",
    )
    apr.add_argument(
        "--yubikey",
        action="store_true",
        help="sign approval with the YubiKey-backed PIV key (mutually exclusive with --secret-path)",
    )
    apr.add_argument("--service", default=None)
    apr.add_argument("--skip-display", action="store_true")
    apr.add_argument("--unsafe-auto-confirm", action="store_true")
    apr.set_defaults(handler="manifest_apr")
    env = ms.add_parser("envelope")
    env.set_defaults(handler="manifest_env")

    d = subs.add_parser("deploy")
    ds = d.add_subparsers(dest="depcmd", required=True)
    dr = ds.add_parser("render")
    dr.set_defaults(handler="dep_render")
    da = ds.add_parser("apply")
    da.set_defaults(handler="dep_apply")

    ce = subs.add_parser("ceremony")
    cs = ce.add_subparsers(dest="cercmd", required=True)
    cgb = cs.add_parser(
        "genesis-boot",
        help="Coordinator: run qos_client boot-genesis against the Genesis enclave",
    )
    cgb.add_argument(
        "--genesis-endpoint",
        default=None,
        help="host:port of the Genesis enclave (mutually exclusive with --resolve-pod-ip)",
    )
    cgb.add_argument("--resolve-pod-ip", action="store_true")
    cgb.add_argument(
        "--genesis-label",
        default=None,
        help="kubectl label selector for the Genesis pod (e.g. app=qos-genesis)",
    )
    cgb.add_argument(
        "--genesis-namespace",
        default=None,
        help="kubectl namespace for the Genesis pod (defaults to config.kubernetes_namespace)",
    )
    cgb.add_argument(
        "--share-set-dir",
        default=None,
        help="defaults to config.paths.share_set_dir; must contain *.pub and quorum_threshold",
    )
    cgb.add_argument(
        "--namespace-dir",
        default=None,
        help="qos_client output directory (defaults to genesis-output/ in the workdir)",
    )
    cgb.add_argument(
        "--qos-release-dir",
        default=None,
        help="defaults to config.paths.qos_release_dir",
    )
    cgb.add_argument(
        "--pcr3-preimage-path",
        default=None,
        help="defaults to config.paths.pcr3_preimage_path",
    )
    cgb.add_argument(
        "--dr-pub-path",
        default=None,
        help="defaults to config.paths.dr_key_pub_path; required even when defaulted",
    )
    cgb.set_defaults(handler="cer_genesis_boot")
    cse = cs.add_parser(
        "share-extract",
        help="Share Set member: run qos_client after-genesis to extract this member's share",
    )
    cse.add_argument("--alias", required=True)
    cse.add_argument("--member-index", type=int, required=True)
    cse.add_argument(
        "--secret-path",
        default=None,
        help="member's long-term secret path (required unless --yubikey is set)",
    )
    cse.add_argument("--yubikey", action="store_true", help="extract using YubiKey instead of --secret-path")
    cse.add_argument(
        "--share-path",
        required=True,
        help="output path for the member's encrypted share; MUST be outside the role workdir",
    )
    cse.add_argument(
        "--namespace-dir",
        default=None,
        help="extracted Genesis-output namespace directory (defaults to incoming/genesis-output)",
    )
    cse.add_argument(
        "--pcr3-preimage-path",
        default=None,
        help="defaults to config.paths.pcr3_preimage_path",
    )
    cse.set_defaults(handler="cer_share_extract")
    cb = cs.add_parser("boot")
    cb.add_argument("--resolve-pod-ip", action="store_true")
    cb.add_argument("--unsafe-skip-attestation", action="store_true")
    cb.set_defaults(handler="cer_boot")
    ca = cs.add_parser("attestation")
    ca.add_argument("--attest-dir", default="attestations")
    ca.add_argument("--resolve-pod-ip", action="store_true")
    ca.set_defaults(handler="cer_att")
    cr = cs.add_parser("reencrypt")
    cr.add_argument("--alias", required=True)
    cr.add_argument(
        "--secret-path",
        default=None,
        help="absolute path to the member's long-term secret file (required unless --yubikey)",
    )
    cr.add_argument(
        "--yubikey",
        action="store_true",
        help="decrypt with the YubiKey-backed PIV key (mutually exclusive with --secret-path)",
    )
    cr.add_argument("--share-path", required=True)
    cr.add_argument("--member-index", type=int, required=True)
    cr.add_argument("--attest-dir", default="attestations")
    cr.add_argument("--wrapped-out-dir", default="wrapped-shares-out")
    cr.add_argument("--approval-alias", default=None)
    cr.add_argument("--unsafe-skip-attestation", action="store_true")
    cr.add_argument("--unsafe-auto-confirm", action="store_true")
    cr.set_defaults(handler="cer_rec")
    cp = cs.add_parser("post")
    cp.add_argument("--wrapped-in-dir", default="wrapped-shares-coordinator")
    cp.add_argument("--resolve-pod-ip", action="store_true")
    cp.add_argument("--post-global-order", default=None)
    cp.add_argument("--approval-alias", default=None)
    cp.set_defaults(handler="cer_post")

    vf = subs.add_parser("verify")
    vf.set_defaults(handler="verify")

    b = subs.add_parser("bundle")
    bs = b.add_subparsers(dest="bundlecmd", required=True)
    bcr = bs.add_parser("create")
    bcr.add_argument(
        "--kind",
        required=True,
        choices=("review", "share-request", "approvals", "wrapped-shares", "genesis-output"),
    )
    bcr.add_argument("--bundle-dir", required=True)
    bcr.add_argument("--archive", default=None)
    bcr.set_defaults(handler="bundle_create")
    bc = bs.add_parser("checksums")
    bc.add_argument("--bundle-dir", required=True)
    bc.set_defaults(handler="bundle_checksums")
    bv = bs.add_parser("verify")
    bv.add_argument("--bundle-dir", required=True)
    bv.set_defaults(handler="bundle_verify")
    be = bs.add_parser("extract")
    be.add_argument("--archive", required=True)
    be.add_argument("--bundle-dir", required=True)
    be.set_defaults(handler="bundle_extract")

    kf = subs.add_parser("key-forward")
    kfs = kf.add_subparsers(dest="kf", required=True)
    kb = kfs.add_parser("boot")
    kb.add_argument("--service", required=True)
    kb.add_argument("--host-ip", default=None)
    kb.add_argument("--resolve-pod-ip", action="store_true")
    kb.add_argument("--attestation-out", required=True)
    kb.set_defaults(handler="kf_boot")
    kx = kfs.add_parser("export")
    kx.add_argument("--export-service", required=True, help="healthy old pod/service name")
    kx.add_argument("--service", required=True, help="new pod service (manifest envelope svc)")
    kx.add_argument("--from-host-ip", required=True, help="old pod IP / port-forward endpoint")
    kx.add_argument("--attestation-doc", required=True)
    kx.add_argument("--encrypted-out", required=True)
    kx.set_defaults(handler="kf_export")
    ki = kfs.add_parser("inject")
    ki.add_argument("--service", required=True)
    ki.add_argument("--host-ip", default=None)
    ki.add_argument("--resolve-pod-ip", action="store_true")
    ki.add_argument("--encrypted-in", required=True)
    ki.set_defaults(handler="kf_inject")

    return p


def hydrate(ns: argparse.Namespace, cfg: Config) -> argparse.Namespace:
    ns.qos_client = cfg.qos_client
    return ns


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    ns = parser.parse_args(args)
    cfg_path = Path(ns.config).resolve()
    cfg_raw = load_json(cfg_path)
    workdir = Path(ns.workdir).resolve()
    cfg = Config(cfg_raw, workdir=workdir)
    validate_config(cfg)
    hydrate(ns, cfg)

    audit = Path(ns.audit_log).resolve() if ns.audit_log else None
    handlers: Dict[str, Any] = {
        "doctor_coordinator": lambda: cmd_doctor_coordinator(cfg, audit_log=audit),
        "doctor_holder": lambda: cmd_doctor_holder(cfg, audit_log=audit),
        "key_fg": lambda: cmd_key_file_generate(ns, audit),
        "key_yk": lambda: cmd_key_yubikey_provision(ns, audit),
        "manifest_gen": lambda: cmd_manifest_generate(ns, cfg, audit),
        "manifest_apr": lambda: cmd_manifest_approve(ns, cfg, audit),
        "manifest_env": lambda: cmd_manifest_envelope(ns, cfg, audit),
        "dep_render": lambda: cmd_deploy_render(ns, cfg, audit),
        "dep_apply": lambda: cmd_deploy_apply(ns, cfg, audit),
        "cer_genesis_boot": lambda: cmd_ceremony_genesis_boot(ns, cfg, audit),
        "cer_share_extract": lambda: cmd_ceremony_share_extract(ns, cfg, audit),
        "cer_boot": lambda: cmd_ceremony_boot(ns, cfg, audit),
        "cer_att": lambda: cmd_ceremony_attestation(ns, cfg, audit),
        "cer_rec": lambda: cmd_ceremony_reencrypt(ns, cfg, audit),
        "cer_post": lambda: cmd_ceremony_post(ns, cfg, audit),
        "verify": lambda: cmd_verify(ns, cfg, audit),
        "bundle_create": lambda: cmd_bundle_create(ns, cfg, audit),
        "bundle_checksums": lambda: cmd_bundle_checksums(ns, cfg, audit),
        "bundle_verify": lambda: cmd_bundle_verify(ns, cfg, audit),
        "bundle_extract": lambda: cmd_bundle_extract(ns, cfg, audit),
        "kf_boot": lambda: cmd_keyfwd_boot(ns, cfg, audit),
        "kf_export": lambda: cmd_keyfwd_export(ns, cfg, audit),
        "kf_inject": lambda: cmd_keyfwd_inject(ns, cfg, audit),
    }
    handlers[str(ns.handler)]()


if __name__ == "__main__":
    main()
