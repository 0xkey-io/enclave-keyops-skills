# Agent Instructions — 0xkey Enclave KeyOps Skills

> These instructions apply to every AI agent (Cursor, Codex, Claude Code,
> Gemini, OpenClaw, etc.) that clones or reads this repository.

## TL;DR

1. **Install the matching role skill** with `npx skills add` — do not clone
   this repository as your working method.
2. **Use the `keyops` binary** for all operations — never execute the Python
   files in `dist/src/` directly.

---

## Role skills

This repository ships four role-scoped skill packages. Pick exactly one that
matches the operator's ceremony seat:

| Role | Install command |
|------|-----------------|
| Coordinator | `npx skills add 0xkey-io/enclave-keyops-skills --skill 0xkey-keyops-coordinator` |
| Builder / Release | `npx skills add 0xkey-io/enclave-keyops-skills --skill 0xkey-keyops-builder` |
| Manifest Set member | `npx skills add 0xkey-io/enclave-keyops-skills --skill 0xkey-keyops-manifest` |
| Share Set member | `npx skills add 0xkey-io/enclave-keyops-skills --skill 0xkey-keyops-share` |

After installation, start from the role's `SKILL.md` and follow its action
whitelist. Do not install multiple role skills in the same operator session.

## Using the keyops binary

All operator actions go through the self-contained `keyops` binary. It bundles
a complete Python runtime, so no Python installation is required on the
operator machine.

### Download (one-time setup)

```bash
# Detect platform automatically and download the matching binary
PLATFORM="$(uname -s | tr A-Z a-z)-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')"
curl -fLO "https://github.com/0xkey-io/enclave-keyops-skills/releases/latest/download/keyops.${PLATFORM}"
curl -fLO "https://github.com/0xkey-io/enclave-keyops-skills/releases/latest/download/keyops.${PLATFORM}.sha256"
shasum -a 256 -c "keyops.${PLATFORM}.sha256"
install -m 0755 "keyops.${PLATFORM}" ./bin/keyops   # or any directory on $PATH
```

### Verify

```bash
./bin/keyops --version
```

## What `dist/src/` is — and is not

`dist/src/` contains the **PyInstaller build source** for the `keyops`
binary. These Python files are compiled into the binary by CI and are **not
an operator interface**.

- **Do NOT run** `python3 dist/src/role_init.py`, `python3 dist/src/enclave_keyops.py`, or any other file in `dist/src/` directly.
- Doing so will be rejected with an error message.
- If `keyops` is not yet on `$PATH`, download it from GitHub Releases (see above) before proceeding.

## Repository layout overview

```
skills/                  ← installable role skill packages (use these)
  0xkey-keyops-coordinator/
  0xkey-keyops-manifest/
  0xkey-keyops-share/
  0xkey-keyops-builder/

dist/                    ← keyops binary build system (maintainer only)
  src/                   ← PyInstaller source scripts (NOT for direct use)
    keyops_main.py
    enclave_keyops.py
    role_init.py
    fetch_keyops.py
    fetch_qos_client.py
  keyops.spec
  build.sh

core/                    ← shared content source, synced into skills/
  references/
    roles/               ← per-role runbooks
    source-invocation.md ← Python source-mode reference (maintainers only)
```

## If the operator has not installed a skill yet

Ask which ceremony role they hold, then run the corresponding
`npx skills add` command above. Do not guess the role. Do not install all
four skills unless the operator explicitly asks for a maintainer/test
environment.

## Maintainers and CI

Source-mode Python invocation (e.g., running scripts for development or CI
unit tests) requires setting the environment variable:

```bash
export KEYOPS_SOURCE_MODE=1
```

Without this variable, direct `python3` invocations of any file in
`dist/src/` are blocked by a runtime guard to prevent accidental misuse.
