# Security

Report suspected vulnerabilities privately to the repository owner. Do not include credentials, session content, hook receipts, review markers, or personal data in issues.

The installer operates on an explicit manifest allowlist: it verifies every source path and sha256 before copying, refuses path escapes and symlinks, and routes every destructive operation through a guard that only permits `$ZCODE_HOME/gov`, `$ZCODE_HOME/gov.zgov-backup`, and its own temporary staging directory. `$ZCODE_HOME` itself, and `cli/`, `server/`, `v2/`, `hooks/receipts/`, and `gov-config/` beneath it, are never renamed, replaced, or removed. Files written outside `$ZCODE_HOME/gov` are backed up per file as `<name>.zgov-backup`, and `--rollback` restores them. `gov-config/` is user-owned and is never overwritten.

The hook registrar edits only entries under `hooks.events` whose `args[0]` points at `zcode_hook.py`, preserves every other configuration key byte-for-byte with post-write verification, and never prints a configuration value.

No secret or authentication state is required at runtime. The strict toolchain doctor is read-only and stores hashes and reason codes only.

Known and accepted limitation: local validators cannot detect coordinated forgery — an attacker able to rewrite the pre-execution closure authority, dispatch record, candidate packet, evidence, and reviewer record together will produce a self-consistent green result. Artifact signing and transparency logging are out of scope for this package. See `docs/privacy-threat-model.md`.
