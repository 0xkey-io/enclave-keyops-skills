# Pre-collaboration provisioning matrix

After the role is identified, verify the user has the inputs that role
requires before initializing or running anything. When an input is missing,
route the user to the **producer** via the **transit** path; do not ask the
user to fabricate it.

| Artifact | Producer | Transit | Consumer |
|----------|----------|---------|----------|
| `qos_client` (operator-platform binary) + SHA256 | Builder | Coordinator forwards Builder's release-channel URL in builder-handoff | Manifest / Share / Coordinator |
| qOS PCR files (`nitro.pcrs`, `aws-x86_64.pcrs`) | Builder | review bundle | Manifest |
| pivot binaries + `pivot-hash.txt` / image digests | Builder | review bundle | Manifest |
| `member-roster.json` (alias / member-index assignment) | Coordinator (issued before any `.pub` is collected) | signed announcement before bundles, then embedded in review / share-request / genesis-output bundles | Manifest / Share / Coordinator |
| `quorum_key.pub` | Coordinator (output of `ceremony genesis-boot`) | review bundle | Manifest |
| `dr-key.pub` | DR holder (external; not modeled as a skill role) | Coordinator collects directly | Coordinator (input to `ceremony genesis-boot`) |
| `<alias>.secret` / `<alias>.pub` | Member, in their external vault — alias MUST come from the roster | `.secret` never leaves the vault; `.pub` may travel any channel | Coordinator (`.pub` only) |
| `<alias>.share` (Genesis distribution) | Coordinator (output of Genesis) | `genesis-output` bundle | Share Member (decrypts via `ceremony share-extract`) |
| Review bundle / share-request bundle | Coordinator | any transport | Manifest / Share Member |
| Approvals bundle / wrapped-shares bundle | Manifest / Share Member | any transport | Coordinator |

If the user identifies as a **DR holder** they map to "external producer of
`dr-key.pub`" — not a skill role. Stop and ask whether they will hand the
`dr-key.pub` directly to a Coordinator, or designate someone to play
Coordinator for them.

Each role doc's **State Detection** table also contains a
`waiting-for-qos-client` row that maps the missing-binary case to the right
peer (Coordinator) to ask, plus a state for the missing Genesis output bundle
(`waiting-for-genesis-output-bundle` for Share members,
`collecting-genesis-materials` / `ready-for-genesis` for Coordinator).
