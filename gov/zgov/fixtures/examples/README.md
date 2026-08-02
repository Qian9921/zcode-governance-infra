# Example / reference artifacts (NON-AUTHORITATIVE)

The files in this directory are **verbatim copies of artifacts produced by the
upstream Codex `v16` milestone**. They are checked in byte-for-byte as
*examples and format references only*.

## They are not this repository's frozen facts

These files are **not** an authoritative source of truth for `zgov`. Nothing in
this repository reads them to decide whether a governance check passes.

This repository's frozen facts are carried by the milestone configuration at:

    ~/.zcode/gov-config/milestone.json

(resolved via `ZGOV_MILESTONE_PATH`, then `$ZCODE_HOME/gov-config/milestone.json`,
then `~/.zcode/gov-config/milestone.json` — see `zgov.milestone`). The validators
in `zgov.spark` read their expected identities from that configuration, never
from this directory.

## What they are good for

* Constructing test inputs with a realistic shape.
* Checking the field layout and naming of a dispatch transcript, a normalized
  Spark result, an author closure plan, or a snapshot path-hash set.

## Consequences

* Their embedded identities (SHA values, `codex/v16/...` artifact paths, task
  IDs) describe the **upstream** milestone, not any milestone of this
  repository. Do not copy those values into a `milestone.json` and expect them
  to mean anything here.
* Because they are byte-identical to upstream, they must not be edited. If an
  upstream artifact changes, re-copy it rather than hand-patching.

## Inventory

| File | Upstream origin (`codex/v16/contracts/`) |
| --- | --- |
| `v16_dispatch_transcript.json` | Route-2 dispatch transcript |
| `v16_spark_audit_closure.json` | Spark audit closure record |
| `author_closure_plan.v16.json` | Author closure plan |
| `spark_result_A.v16.json` | Normalized Spark result (audit A) |
| `spark_result_B.v16.json` | Normalized Spark result (audit B) |
| `spark_result_C.v16.json` | Normalized Spark result (audit C) |
| `snapshot_path_hash_set.v16.txt` | Snapshot path-hash set |

### Mission fixtures (upstream origin `codex/v16/fixtures/`)

The five mission fixtures below were moved here from `gov/zgov/fixtures/` for the
same reason: they carry Codex-specific identities (`gpt-5.6-luna` /
`gpt-5.6-sol` models, `codex/v16` scope paths, upstream `exact_head`). They are
kept byte-identical to upstream as format references.

| File | Purpose |
| --- | --- |
| `mission.valid.json` | A fully populated, schema-valid mission |
| `mission.invalid.missing.json` | Mission with required fields removed |
| `mission.invalid.extra.json` | Mission with an unexpected extra field |
| `mission.invalid.bool-int.json` | Counterexample with a bool where an int is required |
| `mission.invalid.cycle.json` | Mission whose gate `depends_on` graph is cyclic |

`gov/zgov/fixtures/` now holds `zgov`-native fixtures under the same five names.
They are structurally identical to these copies; only the Codex-specific values
were rewritten (`codex/v16` → `zgov/v16`, `codex/AGENTS.md` → `gov/AGENTS.md`,
`codex/BRIEF-TEMPLATES.md` → `gov/BRIEF-TEMPLATES.md`, and the two model names).
The 40-hex `exact_head` / `tree_sha` values are carried over unchanged: they are
shape-valid placeholders, and substituting different hex digits would convey no
additional meaning.

The model names in the native fixtures (`zgov-writer-model`,
`zgov-reviewer-model`) are **neutral placeholders, not real identities**. They
are plain literals rather than `<TBD:...>` markers because `contracts.ID_RE`
requires an alphanumeric leading character and forbids angle brackets, so a
`<TBD:...>` value would make the "valid" fixture invalid. Actual role identity is
decided by `roles.json`; the test suite rebinds these values at runtime through
`support.mission_with_current_reviewer()`, so nothing asserts against the
literals.
