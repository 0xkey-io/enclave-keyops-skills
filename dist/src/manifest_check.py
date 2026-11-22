# SPDX-License-Identifier: Apache-2.0
"""Offline manifest inspection for `keyops manifest check` / `manifest generate`.

Stdlib only. Decodes qos `generate-manifest` JSON (v1, camelCase serde) or a
manifest envelope, re-encodes it as borsh v1 and hashes it. sha256(borsh) is
the manifest hash that every `qos_client approve-manifest` signature covers,
so the hash table printed here pins exactly what was reviewed and signed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class ManifestFormatError(ValueError):
    pass


def _hex(v: Any, what: str) -> str:
    if not isinstance(v, str):
        raise ManifestFormatError(f"{what}: expected hex string, got {type(v).__name__}")
    s = v.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) % 2 or not re.fullmatch(r"[0-9a-f]*", s):
        raise ManifestFormatError(f"{what}: not valid hex")
    return s


def _int(v: Any, what: str) -> int:
    # qos serializes nonce / threshold / port with string_or_numeric.
    if isinstance(v, bool):
        raise ManifestFormatError(f"{what}: expected integer")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and re.fullmatch(r"[0-9]+", v):
        return int(v)
    raise ManifestFormatError(f"{what}: expected integer, got {v!r}")


def _members(raw: Any, what: str) -> List[Tuple[str, str]]:
    return [(str(m["alias"]), _hex(m["pubKey"], f"{what}.pubKey")) for m in raw or []]


def normalize(obj: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize a v1 manifest or manifest envelope (JSON) to a flat dict.

    Member lists keep file order: borsh encodes them in that order, and the
    hash would silently change if we sorted here.
    """
    approvals: Optional[int] = None
    if "manifest" in obj and "namespace" not in obj:
        approvals = len(obj.get("manifestSetApprovals") or [])
        obj = obj["manifest"]
    if not isinstance(obj, Mapping) or "namespace" not in obj:
        raise ManifestFormatError("not a qos manifest (no namespace)")
    if obj.get("version") is not None:
        raise ManifestFormatError(f"unsupported manifest version {obj.get('version')!r} (only v1)")
    pivot = obj["pivot"]
    if "bridgeConfig" not in pivot:
        raise ManifestFormatError("v0 manifest (no pivot.bridgeConfig); only v1 is supported")
    bridge = []
    for b in pivot.get("bridgeConfig") or []:
        typ = str(b.get("type", "")).lower()
        if typ not in ("server", "client"):
            raise ManifestFormatError(f"unknown bridgeConfig type {typ!r}")
        bridge.append((typ, _int(b.get("port"), "bridgeConfig.port"), b.get("host")))
    enc = obj["enclave"]
    patch = obj.get("patchSet") or {"threshold": 0, "members": []}
    ns = obj["namespace"]
    return {
        "name": str(ns["name"]),
        "nonce": _int(ns["nonce"], "namespace.nonce"),
        "quorum_key": _hex(ns["quorumKey"], "namespace.quorumKey"),
        "pivot_hash": _hex(pivot["hash"], "pivot.hash"),
        "restart": str(pivot["restart"]).lower(),
        "bridge_config": bridge,
        "debug_mode": bool(pivot.get("debugMode", False)),
        "args": [str(a) for a in pivot.get("args") or []],
        "manifest_set": {
            "threshold": _int(obj["manifestSet"]["threshold"], "manifestSet.threshold"),
            "members": _members(obj["manifestSet"]["members"], "manifestSet"),
        },
        "share_set": {
            "threshold": _int(obj["shareSet"]["threshold"], "shareSet.threshold"),
            "members": _members(obj["shareSet"]["members"], "shareSet"),
        },
        "patch_set": {
            "threshold": _int(patch["threshold"], "patchSet.threshold"),
            "members": [_hex(m["pubKey"], "patchSet.pubKey") for m in patch.get("members") or []],
        },
        "enclave": {
            "pcr0": _hex(enc["pcr0"], "pcr0"),
            "pcr1": _hex(enc["pcr1"], "pcr1"),
            "pcr2": _hex(enc["pcr2"], "pcr2"),
            "pcr3": _hex(enc["pcr3"], "pcr3"),
            "aws_root_certificate": _hex(enc["awsRootCertificate"], "awsRootCertificate"),
            "qos_commit": str(enc.get("qosCommit", "")),
        },
        "manifest_set_approvals": approvals,
    }


def _u32(n: int) -> bytes:
    return int(n).to_bytes(4, "little")


def _bytes(b: bytes) -> bytes:
    return _u32(len(b)) + b


def _str(s: str) -> bytes:
    return _bytes(s.encode("utf-8"))


def borsh_encode_v1(m: Mapping[str, Any]) -> bytes:
    """Borsh of qos_core::Manifest (v1); field order follows the Rust struct."""
    out = bytearray()
    out += _str(m["name"]) + _u32(m["nonce"]) + _bytes(bytes.fromhex(m["quorum_key"]))
    ph = bytes.fromhex(m["pivot_hash"])
    if len(ph) != 32:
        raise ManifestFormatError("pivot hash is not 32 bytes")
    if m["restart"] not in ("always", "never"):
        raise ManifestFormatError(f"unknown restart policy {m['restart']!r}")
    out += ph + bytes([1 if m["restart"] == "always" else 0])
    out += _u32(len(m["bridge_config"]))
    for typ, port, host in m["bridge_config"]:
        if typ == "server":
            out += b"\x00" + int(port).to_bytes(2, "little") + _str(str(host))
        else:
            out += b"\x01" + int(port).to_bytes(2, "little")
            out += (b"\x01" + _str(str(host))) if host is not None else b"\x00"
    out += bytes([1 if m["debug_mode"] else 0])
    out += _u32(len(m["args"])) + b"".join(_str(a) for a in m["args"])
    for key in ("manifest_set", "share_set"):
        s = m[key]
        out += _u32(s["threshold"]) + _u32(len(s["members"]))
        for alias, pk in s["members"]:
            out += _str(alias) + _bytes(bytes.fromhex(pk))
    e = m["enclave"]
    for k in ("pcr0", "pcr1", "pcr2", "pcr3", "aws_root_certificate"):
        out += _bytes(bytes.fromhex(e[k]))
    out += _str(e["qos_commit"])
    ps = m["patch_set"]
    out += _u32(ps["threshold"]) + _u32(len(ps["members"]))
    for pk in ps["members"]:
        out += _bytes(bytes.fromhex(pk))
    return bytes(out)


def manifest_sha256(m: Mapping[str, Any]) -> str:
    return hashlib.sha256(borsh_encode_v1(m)).hexdigest()


def load_manifest(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ManifestFormatError(f"{path}: not a JSON manifest ({e})") from e
    m = normalize(obj)
    m["path"] = str(path)
    m["manifest_sha256"] = manifest_sha256(m)
    return m


def parse_pivot_args(spec: str) -> List[str]:
    """Mirror qos_client's `--pivot-args "[a,b,c]"` parsing."""
    s = spec.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    if not s:
        return []
    return s.split(",")


def flag_map(args: Sequence[str]) -> Dict[str, List[Optional[str]]]:
    """`--flag value` pairs; a flag followed by another flag (or nothing) maps to None."""
    out: Dict[str, List[Optional[str]]] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            val: Optional[str] = None
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                val = args[i + 1]
                i += 1
            out.setdefault(a, []).append(val)
        i += 1
    return out


def diff_args(old: Sequence[str], new: Sequence[str]) -> List[str]:
    """Human-readable flag-level diff, used to show the pivot-arg delta vs live."""
    a, b = flag_map(old), flag_map(new)
    lines: List[str] = []
    for f in sorted(set(a) | set(b)):
        if f not in a:
            lines.append(f"+ {f} {b[f]}")
        elif f not in b:
            lines.append(f"- {f} {a[f]}")
        elif a[f] != b[f]:
            lines.append(f"~ {f} {a[f]} -> {b[f]}")
    return lines


def read_hex_file(path: Path, what: str) -> str:
    return _hex(path.read_text(encoding="utf-8").strip(), what)


def read_set_dir(d: Path) -> Dict[str, Any]:
    """Expected set from a `<alias>.pub` + `quorum_threshold` directory.

    qos_client uses the file stem as the member alias and sorts members by
    file name; an absent or pub-less directory means a disabled set.
    """
    pubs = sorted(d.glob("*.pub")) if d.is_dir() else []
    members = [(p.stem, read_hex_file(p, str(p))) for p in pubs]
    thr_path = d / "quorum_threshold"
    threshold = int(thr_path.read_text(encoding="utf-8").strip()) if thr_path.is_file() else 0
    return {"threshold": threshold, "members": members}


def read_nitro_pcrs(path: Path) -> Dict[str, str]:
    """`nitro.pcrs`: lines of `<hex> PCR<n>`."""
    out: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"PCR[0-9]+", parts[1]):
            out[parts[1].lower()] = _hex(parts[0], f"{path}:{parts[1]}")
    return out


def pcr3_from_preimage(path: Path) -> str:
    """PCR3 as qos_client derives it: sha384(48 zero bytes || first line of the preimage)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0]:
        raise ManifestFormatError(f"{path}: empty pcr3 preimage")
    return hashlib.sha384(b"\0" * 48 + lines[0].encode("utf-8")).hexdigest()


class Report:
    def __init__(self) -> None:
        self.rows: List[Dict[str, str]] = []

    def add(self, status: str, svc: str, check: str, detail: str = "") -> None:
        self.rows.append({"status": status, "service": svc, "check": check, "detail": detail})

    def eq(self, svc: str, check: str, expected: Any, actual: Any, detail: str = "") -> bool:
        ok = expected == actual
        if not ok and not detail:
            detail = f"expected {_short(expected)} got {_short(actual)}"
        self.add("PASS" if ok else "FAIL", svc, check, detail)
        return ok

    @property
    def failures(self) -> List[Dict[str, str]]:
        return [r for r in self.rows if r["status"] == "FAIL"]

    def render(self) -> str:
        w_svc = max([len(r["service"]) for r in self.rows] + [7])
        w_chk = max([len(r["check"]) for r in self.rows] + [5])
        lines = [f"{'STATUS':6}  {'SERVICE':{w_svc}}  {'CHECK':{w_chk}}  DETAIL"]
        for r in self.rows:
            lines.append(
                f"{r['status']:6}  {r['service']:{w_svc}}  {r['check']:{w_chk}}  {r['detail']}"
            )
        return "\n".join(lines)


def _short(v: Any, n: int = 16) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= 2 * n + 1 else f"{s[:n]}…{s[-n:]}"


def check_service(
    rep: Report,
    svc: str,
    m: Mapping[str, Any],
    *,
    namespace: str,
    nonce: int,
    quorum_key: str,
    pivot_hash: str,
    pivot_binary_sha256: Optional[str],
    args: Sequence[str],
    restart: str,
    debug_mode: bool,
    bridge_config: Sequence[Tuple[str, int, Any]],
    manifest_set: Mapping[str, Any],
    share_set: Mapping[str, Any],
    patch_set: Mapping[str, Any],
    pcrs: Mapping[str, str],
    pcr3: Optional[str] = None,
    forbidden_args: Sequence[str] = (),
) -> None:
    """Compare one generated manifest against the workspace inputs it was built from."""
    rep.eq(svc, "namespace", namespace, m["name"])
    rep.eq(svc, "nonce", nonce, m["nonce"])
    rep.eq(svc, "quorum_key", quorum_key, m["quorum_key"])
    rep.eq(svc, "pivot_hash", pivot_hash, m["pivot_hash"])
    if pivot_binary_sha256 is not None:
        rep.eq(svc, "pivot_hash=sha256(binary)", pivot_binary_sha256, m["pivot_hash"])
    rep.eq(svc, "pivot_args", list(args), list(m["args"]))
    present = flag_map(m["args"])
    for f in forbidden_args:
        rep.eq(svc, f"forbidden {f}", False, f in present)
    rep.eq(svc, "restart", restart.lower(), m["restart"])
    rep.eq(svc, "debug_mode", bool(debug_mode), m["debug_mode"])
    rep.eq(
        svc,
        "bridge_config",
        [list(b) for b in bridge_config],
        [list(b) for b in m["bridge_config"]],
    )
    for key in ("manifest_set", "share_set"):
        exp = {"manifest_set": manifest_set, "share_set": share_set}[key]
        rep.eq(svc, f"{key}.threshold", exp["threshold"], m[key]["threshold"])
        rep.eq(svc, f"{key}.members", sorted(exp["members"]), sorted(m[key]["members"]))
    rep.eq(svc, "patch_set.threshold", patch_set["threshold"], m["patch_set"]["threshold"])
    rep.eq(
        svc,
        "patch_set.members",
        sorted(pk for _, pk in patch_set["members"]),
        sorted(m["patch_set"]["members"]),
    )
    for k in ("pcr0", "pcr1", "pcr2"):
        if k in pcrs:
            rep.eq(svc, k, pcrs[k], m["enclave"][k])
        else:
            rep.add("FAIL", svc, k, "missing from qos-release nitro.pcrs")
    if pcr3 is not None:
        rep.eq(svc, "pcr3=sha384(preimage)", pcr3, m["enclave"]["pcr3"])


LIVE_INVARIANTS = ("quorum_key", "manifest_set", "share_set", "patch_set", "pcr3", "namespace")


def check_against_live(
    rep: Report,
    svc: str,
    m: Mapping[str, Any],
    live: Mapping[str, Any],
    *,
    nonce_delta: int,
    allow_diff: Sequence[str] = (),
) -> None:
    """A follow-up ceremony must keep the quorum identity and bump the nonce."""
    rep.eq(svc, f"nonce=live+{nonce_delta}", live["nonce"] + nonce_delta, m["nonce"])

    def inv(field: str, a: Any, b: Any) -> None:
        if field in allow_diff and a != b:
            rep.add(
                "WARN", svc, f"live.{field}", "differs from live (allowed by --allow-live-diff)"
            )
        else:
            rep.eq(svc, f"live.{field}", a, b)

    inv("namespace", live["name"], m["name"])
    inv("quorum_key", live["quorum_key"], m["quorum_key"])
    for key in ("manifest_set", "share_set"):
        inv(
            key,
            {"threshold": live[key]["threshold"], "members": sorted(live[key]["members"])},
            {"threshold": m[key]["threshold"], "members": sorted(m[key]["members"])},
        )
    inv(
        "patch_set",
        {
            "threshold": live["patch_set"]["threshold"],
            "members": sorted(live["patch_set"]["members"]),
        },
        {"threshold": m["patch_set"]["threshold"], "members": sorted(m["patch_set"]["members"])},
    )
    inv("pcr3", live["enclave"]["pcr3"], m["enclave"]["pcr3"])
    for line in diff_args(live["args"], m["args"]):
        rep.add("INFO", svc, "pivot_args vs live", line)
    if live["pivot_hash"] != m["pivot_hash"]:
        rep.add(
            "INFO",
            svc,
            "pivot_hash vs live",
            f"{_short(live['pivot_hash'])} -> {_short(m['pivot_hash'])}",
        )
    for k in ("pcr0", "pcr1", "pcr2"):
        if live["enclave"][k] != m["enclave"][k]:
            rep.add(
                "INFO",
                svc,
                f"{k} vs live",
                f"{_short(live['enclave'][k])} -> {_short(m['enclave'][k])}",
            )


def find_live_manifest(live_dir: Path, namespace: str) -> Optional[Path]:
    """Match by namespace.name, never by file name (envelopes and bare manifests both accepted)."""
    hits: List[Path] = []
    for p in sorted(live_dir.glob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            m = normalize(obj)
        except (ManifestFormatError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if m["name"] == namespace:
            hits.append(p)
    if len(hits) > 1:
        envelopes = [p for p in hits if "envelope" in p.name]
        if len(envelopes) == 1:
            return envelopes[0]
        names = [p.name for p in hits]
        raise ManifestFormatError(f"{live_dir}: {len(hits)} manifests for {namespace!r}: {names}")
    return hits[0] if hits else None
