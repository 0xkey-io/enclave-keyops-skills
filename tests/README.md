# Tests

Stdlib `unittest` suite for the skill. The skill itself stays
dependency-free (no `pyproject.toml`, no `pip install -e .`); these tests
load `scripts/*.py` via `importlib.util` from `_helpers.py`.

## Run

From the skill root:

```bash
python3 -m unittest discover -t . -s tests -v
```

The `-t .` flag makes `tests` an importable package so `from ._helpers
import ...` works without packaging metadata.

## Coverage

| Test file | Module under test | What it covers |
|-----------|-------------------|----------------|
| `test_sanitize_argv.py` | `enclave_keyops.sanitize_argv` | Redaction of `.secret` / `.share` / `.pem` / `seed` / `password` / `token` flags in audit logs |
| `test_safe_extract_tar.py` | `enclave_keyops.safe_extract_tar` | PEP 706 data-filter behavior: normal extract, path traversal rejected, symlinks/hardlinks escaping dest rejected, special files rejected, absolute-path members re-rooted under dest |
| `test_approval_for.py` | `enclave_keyops.approval_for` | Exact alias/namespace/nonce match for approval bundles; rejects mismatches and ambiguous prefixes |
| `test_parse_int_list.py` | `enclave_keyops.parse_int_list`, `post_order_for_svc` | `m1,m2` and `1,2,3` parsing; per-svc override > global > default fallback |
| `test_quorum_threshold.py` | `enclave_keyops.parse_quorum_threshold` | Single-line decimal int format; rejects `=2`, YAML-like, multi-line, comments, `0`, negatives |
| `test_member_roster.py` | `enclave_keyops.parse_member_roster`, `_check_roster_against_pub_dir` | JSON shape, alias filename-safety + uniqueness, share-set `member_index` 1..N consecutive + bool/zero rejection, `<alias>.pub` ↔ roster alias one-to-one (extras and missing both rejected) |
| `test_role_init_paths.py` | `role_init.find_enclosing_git_root`, `refuse_under_cwd` | Workspace safety net for repo-external role workdirs |

## Notes

- These tests are pure unit tests; they do not invoke `qos_client` or
  `kubectl`. They can run on any developer machine with Python 3.11+.
- The skill's `scripts/` modules are intentionally single-file so that
  agents on any platform can run them as `python3 path/to/script.py` —
  the test loader honors that and never rewrites import paths.
