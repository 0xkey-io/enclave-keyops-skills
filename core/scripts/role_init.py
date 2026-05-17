#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Initialize one local 0xkey Enclave KeyOps role workspace.

This helper intentionally creates directories and non-secret config only. It
never creates, reads, prints, or copies `.secret` / `.share` material.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

# Local helper. Imported lazily inside main() so that `role_init.py --help`
# stays usable on machines that don't have the fetch helper for some reason.


ROLE_CHOICES = ("coordinator", "manifest-set-member", "share-set-member", "builder")
DEFAULT_SERVICES = ("signer", "policy-engine", "notarizer", "tls-fetcher", "transaction-parser")

def skill_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def expand_abs(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def find_enclosing_git_root(start: Path) -> Path | None:
    """Walk upward from `start` looking for a `.git` entry. Returns the first
    directory that contains one, or None if no Git working tree is found
    before reaching the filesystem root.
    """
    current = start
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def refuse_under_cwd(path: Path, force: bool) -> None:
    """Refuse to create a role workspace inside an enclosing Git working tree.

    The most common mistake is running `role_init.py` from inside the source
    repo and accidentally writing the workdir into a tracked path (which
    risks committing secrets or bundles). The skill is repo-layout-agnostic,
    so we cannot assume any specific super-repo path; instead we walk upward
    from CWD looking for a `.git` entry and only refuse when both CWD and the
    requested workdir live under the same Git working tree. Use
    `--i-know-unsafe-repo-path` to override for throwaway tests.
    """
    if force:
        return
    cwd = Path(os.getcwd()).resolve()
    git_root = find_enclosing_git_root(cwd)
    if git_root is None:
        return
    try:
        path.resolve().relative_to(git_root)
    except ValueError:
        return
    sys.stderr.write(
        f"refusing to initialize role workspace inside a Git working tree: {path}\n"
        f"enclosing Git root: {git_root}\n"
        "choose an external path such as ~/0xkey/keyops/<env>/<role>, "
        "or pass --i-know-unsafe-repo-path for a throwaway test only.\n"
    )
    raise SystemExit(2)


def chmod_private(path: Path) -> None:
    path.chmod(stat.S_IRWXU)


def write_text(path: Path, text: str, *, mode: int | None = None, force: bool = False) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def default_alias(role: str, member_index: int | None) -> str:
    if role == "coordinator":
        return "coordinator"
    if role == "manifest-set-member":
        return "manifester1"
    if role == "share-set-member":
        return f"share-member{member_index or 1}"
    return "builder"


def load_template() -> dict[str, Any]:
    template = skill_dir() / "config.prod.example.json"
    with template.open("r", encoding="utf-8") as f:
        return json.load(f)


def configure_json(
    *,
    role: str,
    alias: str,
    member_index: int | None,
    account_id: str | None,
    region: str | None,
    cluster: str | None,
    kubectl_context_alias: str | None,
    enclave_role_name: str | None,
    qos_client_sha256: str | None,
    kustomize_overlay_path: str | None,
    # Release-channel metadata. role_init.py always populates these (the
    # default first-init path is "auto-fetch latest from 0xkey-io/qos");
    # they remain Optional in the signature so unit tests can construct a
    # config without exercising the network.
    qos_client_release_tag: str | None = None,
    qos_client_release_resolved_tag: str | None = None,
    qos_client_release_repo: str | None = None,
    qos_client_release_platform: str | None = None,
) -> dict[str, Any]:
    data = load_template()

    # Builder PRODUCES the binary into `out/qos_client`; everyone else
    # CONSUMES the binary from `shared/qos_client` after the Coordinator
    # forwards Builder's operator-client release. Keeping the path role-aware
    # means `doctor holder` checks the right file in both directions without
    # the operator having to remember a `--qos-client-path` override.
    if role == "builder":
        # When a release_tag + platform pair is known up front we point
        # `qos_client_path` at the per-platform copy (`out/qos_client.<plat>`)
        # because that's the binary Builder will actually execute on this
        # machine to compute pivot hashes. The bare `out/qos_client` symlink
        # / copy is the linux-amd64 reference client and may not be runnable
        # on this host (e.g. macOS arm64 Builder).
        if qos_client_release_platform:
            data["qos_client_path"] = f"out/qos_client.{qos_client_release_platform}"
        else:
            data["qos_client_path"] = "out/qos_client"
    else:
        data["qos_client_path"] = "shared/qos_client"
    data["qos_client_sha256_expected"] = qos_client_sha256

    # Persist release-channel metadata so downstream `doctor holder` /
    # `doctor coordinator` can re-emit a precise, copy-pasteable fetch
    # command. `tag` records whatever the operator typed (often nothing,
    # which we serialize as null = "latest at the time"); `resolved_tag`
    # records the concrete tag the GitHub API returned at init time so
    # subsequent re-fetches stay reproducible even if a newer release
    # ships. We write the metadata block whenever role_init knew enough
    # about the platform / repo to make the doctor hint useful — which
    # includes the `--no-qos-client-fetch` path. When even the platform
    # is unknown (uname unsupported) we leave the field null so `doctor`
    # falls back to the generic "fetch latest" hint.
    if (
        qos_client_release_tag
        or qos_client_release_resolved_tag
        or qos_client_release_platform
    ):
        data["qos_client_release"] = {
            "tag": qos_client_release_tag,
            "resolved_tag": qos_client_release_resolved_tag,
            "repo": qos_client_release_repo or "0xkey-io/qos",
            "platform": qos_client_release_platform,
        }
    else:
        # Explicit `null` keeps the schema stable across role workspaces;
        # `doctor` still falls back to a `--release-tag latest` hint
        # when this is null (see _qos_client_fetch_hint).
        data["qos_client_release"] = None
    data["kubernetes_namespace"] = "0xkey-enclave"

    # Single-role workspaces are self-contained: shared/ lives next to inbox/,
    # outbox/ and secrets/. Bundles can still be copied in/out by humans.
    paths = data["paths"]
    paths["workdir_manifest_subdir"] = "manifest"
    if role == "builder":
        # Builder is the only role that PRODUCES the artifacts other roles
        # consume. Keep its products under `out/` so a Builder workspace is
        # never confused with a Coordinator's `shared/` (which is a consumer
        # layout). This also makes "did the Builder finish a build?"
        # decidable by `ls $WORKDIR/out/qos_client` without false positives
        # from leftover `shared/` placeholders.
        paths["qos_release_dir"] = "out/qos-release"
        paths["pcr3_preimage_path"] = "out/pcr3-preimage.txt"
        paths["quorum_key_pub_path"] = "out/quorum_key.pub"
        paths["dr_key_pub_path"] = "out/dr-key.pub"
        paths["pivots_dir"] = "out/pivots"
        paths["pivot_hashes_dir"] = "out/pivot-hashes"
    else:
        paths["qos_release_dir"] = "shared/qos-release"
        paths["pcr3_preimage_path"] = "shared/pcr3-preimage.txt"
        paths["quorum_key_pub_path"] = "shared/quorum_key.pub"
        paths["dr_key_pub_path"] = "shared/dr-key.pub"
        paths["pivots_dir"] = "shared/pivots"
        paths["pivot_hashes_dir"] = "shared/pivot-hashes"
    paths["manifest_set_dir"] = "shared/manifest-set"
    paths["share_set_dir"] = "shared/share-set"
    paths["patch_set_dir"] = "shared/patch-set"

    if role == "coordinator":
        # Caller is responsible for ensuring all coordinator flags are set;
        # see require_coordinator_flags().
        assert account_id and region and cluster and enclave_role_name and kustomize_overlay_path
        kctx = f"arn:aws:eks:{region}:{account_id}:cluster/{cluster}"
        kctx_allowlist = [kctx]
        if kubectl_context_alias and kubectl_context_alias not in kctx_allowlist:
            kctx_allowlist.append(kubectl_context_alias)
        role_arn = f"arn:aws:iam::{account_id}:role/{enclave_role_name}"
        data["kustomize_overlay_path"] = str(Path(os.path.expanduser(kustomize_overlay_path)))
        data["approval_alias"] = None
        data["kubectl_context_allowlist"] = kctx_allowlist
        data["role_init_note"] = {
            "role": role,
            "alias": alias,
            "kubectl_context": kctx,
            "kubectl_context_allowlist": kctx_allowlist,
            "pcr3_role_arn": role_arn,
        }
    elif role == "builder":
        data.pop("kustomize_overlay_path", None)
        data["kubectl_context_allowlist"] = []
        data["kubectl_path"] = "/dev/null"
        data["role_init_note"] = {"role": role, "alias": alias, "requires_eks": False}
    else:
        data.pop("kustomize_overlay_path", None)
        data["kubectl_context_allowlist"] = []
        data["kubectl_path"] = "/dev/null"
        data["role_init_note"] = {
            "role": role,
            "alias": alias,
            "member_index": member_index,
            "requires_eks": False,
        }

    return data


def init_common(
    root: Path,
    *,
    role: str,
    account_id: str | None,
    enclave_role_name: str | None,
    force: bool,
) -> None:
    # Layout is split by role intent:
    #
    # * Coordinator / Manifest / Share are CONSUMERS — they read Builder
    #   artifacts and Coordinator-issued bundles, then return signed material.
    #   They use `shared/<set>/` for public-key sets and `shared/qos-release`
    #   for inbound PCR / pivot artifacts.
    #
    # * Builder is the sole PRODUCER — its artifacts go to `out/` to keep the
    #   directory semantics unambiguous and to match the Builder runbook
    #   (`builder.md` "Expected output layout"). Builder must not pretend to
    #   own a Coordinator-style `shared/<set>/` skeleton.
    common_dirs = [
        "secrets",
        "inbox",
        "outbox",
        "incoming",
        "bundles",
        "manifest",
    ]
    if role == "builder":
        dirs = common_dirs + [
            "out",
            "out/pivots",
            "out/pivot-hashes",
            "out/qos-release",
            "metadata",
            "logs",
        ]
    else:
        dirs = common_dirs + [
            "shared/manifest-set",
            "shared/share-set",
            "shared/patch-set",
            "shared/pivot-hashes",
            "shared/pivots",
            "shared/qos-release",
        ]
    if role == "coordinator":
        # Genesis output is produced by the Coordinator and bundled out to
        # Share members; keep an empty directory so the structure exists
        # before `ceremony genesis-boot` runs.
        dirs.append("genesis-output")
    for d in dirs:
        p = root / d
        p.mkdir(parents=True, exist_ok=True)
        if d == "secrets":
            chmod_private(p)

    if role == "coordinator" and account_id and enclave_role_name:
        role_arn = f"arn:aws:iam::{account_id}:role/{enclave_role_name}"
        write_text(root / "shared/pcr3-preimage.txt", role_arn, mode=0o644, force=force)
    if role == "builder":
        write_text(
            root / "out/qos-release/README.md",
            "Builder writes the verifiable qOS build outputs here:\n"
            "  - nitro.pcrs\n"
            "  - aws-x86_64.pcrs\n"
            "These are then shipped to the Coordinator via the review bundle.\n",
            force=force,
        )
        write_text(
            root / "out/README.md",
            "# Builder output root\n\n"
            "Everything the Builder produces lives under this `out/` tree so a\n"
            "Builder workspace stays distinguishable from a Coordinator's\n"
            "`shared/` consumer layout. Expected contents:\n\n"
            "  out/qos_client                  # release/reference client\n"
            "  out/qos_client.sha256\n"
            "  out/qos_client.<plat>           # per-operator native client(s)\n"
            "  out/qos_client.<plat>.sha256\n"
            "  out/qos-release/nitro.pcrs\n"
            "  out/qos-release/aws-x86_64.pcrs\n"
            "  out/pivots/<service>            # five pivot binaries\n"
            "  out/pivot-hashes/<service>-pivot-hash.txt\n"
            "  out/images.json                 # ECR tag + digest table\n"
            "  out/builder-handoff.{json,md}   # final handoff to Coordinator\n",
            force=force,
        )
    else:
        write_text(
            root / "shared/qos-release/README.md",
            "Drop nitro.pcrs and aws-x86_64.pcrs from the verifiable qOS build here.\n",
            force=force,
        )

    if role == "coordinator":
        # Placeholder so the Coordinator workflow can validate `dr-key.pub`
        # presence early and prompt the operator to fetch the real DR public
        # key from the external DR holder before `ceremony genesis-boot`.
        write_text(
            root / "shared/dr-key.pub.PLACEHOLDER",
            "Replace with the disaster-recovery (DR) public key (260-hex).\n"
            "Then `mv dr-key.pub.PLACEHOLDER dr-key.pub` (file name without suffix).\n"
            "DR private key / master seed MUST live in an external vault, never in this workspace.\n",
            mode=0o644,
            force=force,
        )

        # Member roster placeholder. The Coordinator MUST publish a roster
        # before any member submits a `.pub`; aliases and (for share-set)
        # member-indexes are assigned here and become permanent for the
        # quorum_key produced by Genesis. See
        # references/roles/coordinator.md `Alias / member-index assignment`.
        roster_example = {
            "$schema_comment": (
                "Coordinator-issued roster: one source of truth for "
                "(alias, member_index) assignments. Copy to "
                "shared/member-roster.json and edit. Aliases must be "
                "filename-safe and unique within each set; share-set "
                "member_index values must be a 1..N consecutive sequence "
                "with no gaps. <alias>.pub filenames in shared/<set>/ MUST "
                "match the aliases listed here."
            ),
            "ceremony": "0xkey-<env>-<yyyymm>-<seq>",
            "issued_at": "2026-01-01T00:00:00Z",
            "manifest_set": [
                {"alias": "manifester1", "owner": "Alice (alice@example.com)"},
                {"alias": "manifester2", "owner": "Bob (bob@example.com)"},
                {"alias": "manifester3", "owner": "Carol (carol@example.com)"},
            ],
            "share_set": [
                {"member_index": 1, "alias": "share-member1", "owner": "Alice"},
                {"member_index": 2, "alias": "share-member2", "owner": "Bob"},
                {"member_index": 3, "alias": "share-member3", "owner": "Carol"},
            ],
            "patch_set": [],
        }
        write_text(
            root / "shared/member-roster.example.json",
            json.dumps(roster_example, indent=2) + "\n",
            mode=0o644,
            force=force,
        )

    # quorum_threshold placeholders (canvas-quorum-threshold-format).
    # The skill expects each set's threshold to live in a single-line plain
    # integer file at shared/<set>/quorum_threshold. We don't write the real
    # file here so that `manifest generate` can't accidentally pick up a
    # default; the operator must consciously copy/edit the placeholder.
    #
    # Builder never consumes thresholds — those files are a Coordinator /
    # Manifest / Share concern. Skip writing them on a Builder workspace so
    # the `out/` vs `shared/` separation stays clean.
    if role != "builder":
        threshold_help = (
            "# Replace this file with `quorum_threshold` (no .example suffix).\n"
            "# Format: a single line containing a decimal integer, no whitespace,\n"
            "# no comments. Example contents (just the number, no leading hash):\n"
            "2\n"
        )
        for set_dir in ("manifest-set", "share-set", "patch-set"):
            write_text(
                root / "shared" / set_dir / "quorum_threshold.example",
                threshold_help,
                mode=0o644,
                force=force,
            )


def readme_for(role: str, alias: str, member_index: int | None) -> str:
    if role == "coordinator":
        return f"""# Coordinator workspace

Alias: `{alias}`

This is the only role that needs AWS/EKS/kubectl access.

Inputs to place here:
- `shared/qos_client` plus `config.json.qos_client_sha256_expected`
- `shared/qos-release/nitro.pcrs`
- `shared/pivots/<service>` and `shared/pivot-hashes/<service>-pivot-hash.txt`
- `shared/{{manifest-set,share-set,patch-set}}/*.pub`
- `shared/{{manifest-set,share-set,patch-set}}/quorum_threshold` (single-line
  decimal integer; copy `quorum_threshold.example` and edit; recommended
  values live in SECURITY.md `Threshold 推荐`)
- `shared/dr-key.pub` (260-hex DR public key from the external DR holder;
  the placeholder file is `shared/dr-key.pub.PLACEHOLDER`)
- `shared/member-roster.json` (Coordinator-issued alias / member-index
  assignment table; copy `shared/member-roster.example.json` and edit
  before any member submits a .pub. See coordinator.md
  `Alias / member-index assignment`.)
- `shared/quorum_key.pub` (produced by Genesis if running it for the first
  time; otherwise import from the previous ceremony)
- member approval bundles copied into `inbox/`
- member wrapped-share bundles copied into `inbox/`

Outputs:
- Genesis output bundle: `bundles/genesis-output-*.tgz` (only on first ceremony)
- review bundle: `bundles/manifest-review-*.tgz`
- share-request bundle: `bundles/share-request-*.tgz`
- final verification logs and audit log

Do not place `.secret` or `.share` files in this workspace. The DR private
key / master seed must live in an external vault; never copy it here.
"""
    if role == "manifest-set-member":
        return f"""# Manifest Set member workspace

Alias: `{alias}`

This role does not need AWS, EKS, kubectl, kubeconfig, or VPN.

User-provided inputs:
- `shared/qos_client`
- `secrets/{alias}.secret` (chmod 600; never paste contents into chat)
- review bundle from coordinator in `inbox/manifest-review-*.tgz`

Agent-run workflow:
1. `doctor holder`
2. `bundle extract` the review bundle into `incoming/review`
3. `bundle verify` the extracted bundle
4. show the manifest summary and ask for human confirmation
5. run `manifest approve --alias {alias}` for all five services
6. run `bundle create --kind approvals`

Output to send back to coordinator:
- `outbox/{alias}-approvals-*.tgz`
- matching `.sha256` or checksum line
"""
    if role == "share-set-member":
        idx = member_index or 1
        return f"""# Share Set member workspace

Alias: `{alias}`
Member index: `{idx}`

This role does not need AWS, EKS, kubectl, kubeconfig, or VPN.

User-provided inputs:
- `shared/qos_client`
- `secrets/{alias}.secret` (chmod 600; never paste contents into chat)
- `secrets/{alias}.share` (chmod 600; never paste contents into chat)
- share-request bundle from coordinator in `inbox/share-request-*.tgz`

Agent-run workflow:
1. `doctor holder`
2. `bundle extract` the share-request bundle into `incoming/share-request`
3. `bundle verify` the extracted bundle
4. inspect attestation metadata and ask for human confirmation
5. run `ceremony reencrypt --alias {alias} --member-index {idx}`
6. run `bundle create --kind wrapped-shares`

Output to send back to coordinator:
- `outbox/{alias}-wrapped-shares-*.tgz`
- matching `.sha256` or checksum line
"""
    return """# Builder workspace

This role prepares verifiable build artifacts. It does not hold quorum
`.secret` or `.share` material. Everything Builder produces lives under
`out/` so the workspace stays distinguishable from a Coordinator's
`shared/` consumer layout.

Inputs the operator must collect into `metadata/build-config.json` BEFORE
any build runs (see `references/roles/builder.md`
`Build / Push Checklist (prod, default)` for the field schema):

- `env`, `aws_account_id`, `aws_region`, `ecr_registry`, `tag`
- `enclave_repo_ref` and `qos_vendored_ref`
- `services_repo_ref`
- `target_platforms[]` (final list comes from the Coordinator's member
  roster — operator platforms determine which native `qos_client.<plat>`
  builds are required)

Expected outputs for Coordinator / members, all under `out/`:

- `out/qos_client` + `out/qos_client.sha256` (release/reference client)
- `out/qos_client.<platform>` + matching `.sha256` for each platform in
  `target_platforms[]`
- `out/qos-release/nitro.pcrs` and `out/qos-release/aws-x86_64.pcrs`
- `out/pivots/<service>` for each of the five services
- `out/pivot-hashes/<service>-pivot-hash.txt` (computed with the same
  `qos_client` produced in this build)
- `out/images.json` (ECR repo + tag + digest per pushed image, using the
  stable `0xkey/<component>` repository names — never put environment
  names in the repo path)
- `out/builder-handoff.{json,md}` (final handoff to Coordinator)

Hard rules:

- Never commit `qos_client` (or any binary) into a git repo; that
  defeats SHA256 audit. Distribute through the channels listed in
  `references/roles/builder.md` `Operator-client release channels`.
- Never ship a binary without its matching `.sha256`.
- Never mix qOS revisions inside one ceremony. If a CVE forces a switch,
  abort the in-progress ceremony first.
- Builder does not run `kubectl` and does not hold member key material.
  `config.json` hard-wires `kubectl_path=/dev/null` and
  `kubectl_context_allowlist=[]` so accidental EKS calls fail at the
  tool layer.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--role", required=True, choices=ROLE_CHOICES)
    p.add_argument("--root", required=True, help="role workspace root, outside the source repo")
    p.add_argument("--alias", default=None)
    p.add_argument("--member-index", type=int, default=None)
    # Coordinator-only fields. Required for --role coordinator.
    p.add_argument("--account-id", default=None)
    p.add_argument("--region", default=None)
    p.add_argument("--cluster", default=None)
    p.add_argument(
        "--kubectl-context-alias",
        default=None,
        help="optional local kubeconfig alias to allow in addition to the EKS ARN context",
    )
    p.add_argument("--enclave-role-name", default=None)
    p.add_argument(
        "--qos-client-release-tag",
        default=None,
        help=(
            "GitHub release tag for the Builder-published qos_client (e.g. "
            "0xkey-qos_client-v0.1.0). Defaults to the latest stable release "
            "from --qos-client-release-repo (resolved via GitHub's "
            "/releases/latest API). Pass an explicit tag only when you need "
            "to pin a specific version for a ceremony."
        ),
    )
    p.add_argument(
        "--qos-client-release-repo",
        default=None,
        help="GitHub repo for the qos_client release (default: 0xkey-io/qos).",
    )
    p.add_argument(
        "--no-qos-client-fetch",
        action="store_true",
        help=(
            "Skip the auto-fetch entirely and only scaffold the workspace. "
            "Use this on offline machines or when the binary will be shuttled "
            "in by a separate handoff. config.json will record the release "
            "metadata so `doctor` prints an exact manual-fetch command."
        ),
    )
    p.add_argument(
        "--kustomize-overlay-path",
        default=None,
        help="absolute filesystem path to the K8s overlay directory; required for --role coordinator",
    )
    p.add_argument("--force", action="store_true", help="overwrite generated README/config files")
    p.add_argument("--i-know-unsafe-repo-path", action="store_true")
    return p


def require_coordinator_flags(ns: argparse.Namespace) -> None:
    missing: list[str] = []
    for flag, value in (
        ("--account-id", ns.account_id),
        ("--region", ns.region),
        ("--cluster", ns.cluster),
        ("--enclave-role-name", ns.enclave_role_name),
        ("--kustomize-overlay-path", ns.kustomize_overlay_path),
    ):
        if not value:
            missing.append(flag)
    if missing:
        sys.stderr.write(
            "--role coordinator requires the following flags: "
            + ", ".join(missing)
            + "\n"
        )
        raise SystemExit(2)
    if not Path(os.path.expanduser(ns.kustomize_overlay_path)).is_absolute():
        sys.stderr.write(
            f"--kustomize-overlay-path must be an absolute path, got {ns.kustomize_overlay_path!r}.\n"
        )
        raise SystemExit(2)


def main() -> None:
    ns = build_parser().parse_args()
    if ns.role == "coordinator":
        require_coordinator_flags(ns)
    root = expand_abs(ns.root)
    refuse_under_cwd(root, ns.i_know_unsafe_repo_path)
    alias = ns.alias or default_alias(ns.role, ns.member_index)
    member_index = ns.member_index
    if ns.role == "share-set-member" and member_index is None:
        # Infer common `share-memberN` aliases.
        suffix = alias.removeprefix("share-member")
        member_index = int(suffix) if suffix.isdigit() else 1

    root.mkdir(parents=True, exist_ok=True)
    chmod_private(root)
    init_common(
        root,
        role=ns.role,
        account_id=ns.account_id,
        enclave_role_name=ns.enclave_role_name,
        force=ns.force,
    )

    # Auto-fetch the operator client. The default first-init path is
    # "pull the latest stable release from 0xkey-io/qos" — there is no
    # separate manual flow. Operators who genuinely need an offline init
    # pass --no-qos-client-fetch; everyone else gets a verified binary on
    # disk by the end of `role_init.py` without having to remember any
    # SHA256 flags.
    fetch_failure: str | None = None
    fetched_platform: str | None = None
    fetched_sha256: str | None = None
    resolved_release_tag: str | None = None
    repo = ns.qos_client_release_repo or "0xkey-io/qos"
    requested_tag = ns.qos_client_release_tag  # may be None == "latest"

    if not ns.no_qos_client_fetch:
        try:
            from fetch_qos_client import (
                FetchError,
                detect_platform,
                fetch_binary,
                print_manual_fallback,
                resolve_release_tag,
            )
        except ImportError as e:  # pragma: no cover - shipped alongside this script
            sys.stderr.write(f"failed to import fetch_qos_client: {e}\n")
            raise SystemExit(2)

        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        try:
            fetched_platform = detect_platform()
        except FetchError as e:
            sys.stderr.write(f"qos_client auto-fetch refused: {e}\n")
            fetch_failure = str(e).splitlines()[0]

        if fetched_platform:
            try:
                resolved_release_tag = resolve_release_tag(
                    repo, want=requested_tag, token=token, timeout=60.0
                )
            except FetchError as e:
                sys.stderr.write(f"qos_client release lookup failed: {e}\n")
                fetch_failure = str(e).splitlines()[0]

        if fetched_platform and resolved_release_tag and not fetch_failure:
            if ns.role == "builder":
                target = root / f"out/qos_client.{fetched_platform}"
            else:
                target = root / "shared/qos_client"
            try:
                fetched_sha256 = fetch_binary(
                    repo=repo,
                    tag=resolved_release_tag,
                    plat=fetched_platform,
                    out=target,
                    expected_sha256=None,
                    token=token,
                    timeout=60.0,
                )
                print(f"fetched qos_client: {target}")
                print(f"release: {resolved_release_tag}  ({repo})")
                print(f"sha256: {fetched_sha256}")
            except FetchError as e:
                # Init is not blocked by a fetch failure: we still write
                # a complete role workspace and record the release tag,
                # then surface a precise fetch command in the todos
                # block. The SECURITY.md §3 red line is upheld either
                # way — sha mismatches quarantine the bad download
                # instead of installing it (see fetch_qos_client).
                print_manual_fallback(
                    reason=str(e).splitlines()[0],
                    repo=repo,
                    tag=resolved_release_tag,
                    plat=fetched_platform,
                    out=target,
                )
                sys.stderr.write(f"detail:\n{e}\n")
                fetch_failure = str(e).splitlines()[0]
    else:
        # --no-qos-client-fetch: still record platform + (if explicit) tag
        # so config.json gives `doctor` enough info to print an exact
        # manual-fetch command later. We don't hit the network here.
        try:
            from fetch_qos_client import FetchError, detect_platform
            fetched_platform = detect_platform()
        except (FetchError, ImportError):
            fetched_platform = None
        # `resolved_release_tag` stays None when the operator typed
        # "latest" but explicitly opted out of fetching; doctor will
        # display the literal "latest" sentinel from `tag` instead.

    cfg = configure_json(
        role=ns.role,
        alias=alias,
        member_index=member_index,
        account_id=ns.account_id,
        region=ns.region,
        cluster=ns.cluster,
        kubectl_context_alias=ns.kubectl_context_alias,
        enclave_role_name=ns.enclave_role_name,
        qos_client_sha256=fetched_sha256,
        qos_client_release_tag=requested_tag,
        qos_client_release_resolved_tag=resolved_release_tag,
        qos_client_release_repo=repo,
        qos_client_release_platform=fetched_platform,
        kustomize_overlay_path=ns.kustomize_overlay_path,
    )
    config_path = root / "config.json"
    if config_path.exists() and not ns.force:
        print(f"kept existing config: {config_path}")
    else:
        write_text(config_path, json.dumps(cfg, indent=2) + "\n", mode=0o600, force=True)
        print(f"wrote config: {config_path}")

    write_text(root / "README.md", readme_for(ns.role, alias, member_index), force=ns.force)
    print(f"initialized {ns.role} workspace: {root}")

    # Surface follow-up gates that the agent is likely to forget about. These
    # are not errors — they are reminders aimed at the agent's next turn so
    # the operator does not get a silently-incomplete workspace.
    todos: list[str] = []

    # Helper: rebuild the exact `fetch_qos_client.py` command line that
    # would install the binary at the role-correct path on this host.
    def _fetch_command() -> str:
        if ns.role == "builder" and fetched_platform:
            target_path = f"$WORKDIR/out/qos_client.{fetched_platform}"
        else:
            target_path = "$WORKDIR/shared/qos_client"
        # Prefer the resolved tag when we have it (concrete + reproducible);
        # fall back to whatever the operator typed, and finally to the
        # "latest" sentinel that fetch_qos_client.py also accepts as default.
        tag_for_cmd = resolved_release_tag or requested_tag or "latest"
        repo_arg = f" --repo {repo}" if repo != "0xkey-io/qos" else ""
        return (
            f"python3 $SKILL_DIR/scripts/fetch_qos_client.py"
            f" --release-tag {tag_for_cmd}{repo_arg} --out {target_path}"
        )

    if fetch_failure:
        todos.append(
            f"qos_client auto-fetch failed ({fetch_failure}). Re-run the "
            f"fetch once the network is available:\n      {_fetch_command()}\n"
            f"    Then re-run role_init with --force to refresh "
            f"qos_client_sha256_expected in config.json. Do NOT bypass the "
            f"sha256 check — see SECURITY.md §3."
        )
    elif ns.no_qos_client_fetch:
        todos.append(
            "--no-qos-client-fetch was passed; the workspace has no "
            f"qos_client binary yet. Install it with:\n      {_fetch_command()}\n"
            "    Then re-run role_init with --force to record the verified "
            "sha256 in config.json."
        )
    if ns.role == "coordinator":
        dr_placeholder = root / "shared" / "dr-key.pub.PLACEHOLDER"
        if dr_placeholder.exists() and not (root / "shared" / "dr-key.pub").exists():
            todos.append(
                "shared/dr-key.pub is still a PLACEHOLDER; replace it with the "
                "real 260-hex DR public key from the external DR holder before "
                "running `ceremony genesis-boot`. The DR private key MUST stay "
                "in an external vault, never in this workdir."
            )
        roster_example = root / "shared" / "member-roster.example.json"
        if roster_example.exists() and not (root / "shared" / "member-roster.json").exists():
            todos.append(
                "shared/member-roster.json is not published yet; copy "
                "shared/member-roster.example.json, edit aliases / "
                "member-indexes for the real participants, and broadcast it "
                "BEFORE collecting any <alias>.pub. See "
                "references/roles/coordinator.md `Alias / member-index assignment`."
            )
    if ns.role == "builder":
        todos.append(
            "Builder produces artifacts under $WORKDIR/out/; record source "
            "revisions / ECR config under $WORKDIR/metadata/build-config.json "
            "BEFORE running any build. See "
            "references/roles/builder.md `Build / Push Checklist (prod, default)`."
        )

    print("next: read this role's references/roles/*.md workflow before running commands")
    for i, msg in enumerate(todos, 1):
        print(f"  todo {i}: {msg}")


if __name__ == "__main__":
    main()
