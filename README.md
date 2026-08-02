# ZCode Governance Infrastructure v16

English · [简体中文](README.zh-CN.md)

A governance starter for **ZCode clients only**: it speeds up development and review
without sacrificing correctness, evidence, or the GitHub approval boundary. It is a port
of Codex Governance Infrastructure v16; the Python package was renamed from `codex.v16`
to `zgov`, and the runtime root moved from `$CODEX_HOME` to `$ZCODE_HOME`.

This package provides:

- durable ZCode global rules and task briefs (`gov/AGENTS.md`, `gov/POLICY.md`,
  `gov/BRIEF-TEMPLATES.md`);
- affected-first testing (FAST / CANDIDATE / FINAL evidence tiers) instead of blanket
  rebuilds;
- mandatory readiness checks for CodeGraph, Semble, and `rtk` (`tool-preflight.v16`) plus
  proof of actual usage (`tool-usage.v16`);
- one risk-routed independent reviewer, and delta-only re-review by the same reviewer;
- privacy-safe hook receipts (allowlist projection, written to `~/.zcode/hooks/receipts/`);
- a deterministic package verifier, an isolated trial installer, and a reversible hook
  registrar.

It does not claim compatibility with Codex, Claude Code, Kimi Code, or any other agent
runtime.

> **Safety boundary**
>
> `$ZCODE_HOME` is a live directory (`cli/` holds config and databases, `hooks/receipts/`
> holds append-only receipts, `server/` and `v2/` hold runtime state), so the installer
> **never renames, replaces, or removes it**.
>
> - **Exactly one directory**, `$ZCODE_HOME/gov/`, is replaced atomically;
> - `$ZCODE_HOME/AGENTS.md` and `$ZCODE_HOME/agents/*.md` are written file-by-file, and
>   any pre-existing file is copied to `<name>.zgov-backup` first;
> - `$ZCODE_HOME/gov-config/` is user-owned: files are created **only when absent** and are
>   **never overwritten**, backed up, or removed.
>
> The repository never copies credentials, sessions, memory, plugins, connections, model
> caches, or other private data.

## Ten-minute start

### 1. Clone and verify

```bash
git clone <your-fork-or-mirror> zcode-governance-infra
cd zcode-governance-infra

git rev-parse HEAD
git status --short
python3 scripts/verify-governance.py --repo .
PYTHONPATH=gov python3 -m unittest discover -s tests/gov -p 'test_*.py' -t tests/gov
PYTHONPATH=gov python3 -m unittest discover -s tests/hooks -p 'test_*.py' -t tests/hooks
PYTHONPATH=gov python3 -m unittest discover -s tests/scripts -p 'test_*.py' -t tests/scripts
```

Continue only when the verifier prints `"status":"GREEN"` and all three suites report zero
failures and zero errors.

### 2. Install missing tools

Review upstream instructions before installing software:

```bash
# CodeGraph
npm install -g @colbymchenry/codegraph

# Semble
uv tool install semble

# rtk
cargo install --git https://github.com/rtk-ai/rtk
```

Upstream:

- [CodeGraph](https://github.com/colbymchenry/codegraph)
- [Semble](https://github.com/MinishLab/semble)
- [rtk](https://github.com/rtk-ai/rtk)

### 3. Configure ZCode

ZCode MCP configuration is **JSON**, under the `mcp.servers` key of the user config file
`$ZCODE_HOME/cli/config.json` (the Codex original used TOML `[mcp_servers.x]` tables in
`config.toml`). You can inspect and repair status in the client under **Settings → MCP**,
or edit the file directly:

```jsonc
{
  "mcp": {
    "servers": {
      "codegraph": { "type": "stdio", "command": "codegraph", "args": ["mcp"] },
      "semble":    { "type": "stdio", "command": "semble",    "args": ["mcp"] }
    }
  }
}
```

> The configuration-file MCP server schema is **strict**: one unknown key drops the whole
> server. `${...}` template variables are **not** expanded in configuration-file servers —
> use absolute paths. Exact commands and arguments follow each tool's own installation
> documentation; `tool-preflight.v16` only checks whether a server whose name contains
> `codegraph` / `semble` exists under `mcp.servers` (or the compatible `mcp.mcpServers` /
> top-level `mcpServers` layouts).

`rtk` is not an MCP server but a shell-output wrapper; configure it through its own
initialization flow.

After reviewing configuration changes, **restart the ZCode client and start a fresh
session**.

### 4. Prepare the CodeGraph index for this repository

```bash
codegraph status --json .
```

Only when the repository is not initialized and indexing is authorized:

```bash
codegraph init .
```

After structural edits, only when synchronization is authorized:

```bash
codegraph sync .
```

An index belongs to its owning repository. Never treat a parent workspace graph as
child-repository truth.

### 5. Run the strict toolchain doctor

```bash
python3 scripts/toolchain-doctor.py \
  --repo . \
  --semantic-query "deterministic inspection intent router" \
  --expected-path gov/zgov/tool_routing.py
```

The only passing condition is exit code `0`, `"status":"ready"`, and a known denominator of
`3/3`:

- CodeGraph configured, bound to this repository, index complete and fresh, and the
  expected current source found;
- Semble configured, callable, repository-scoped, and the semantic query returns the
  expected source;
- `rtk` reproduces the current Git identity and preserves the non-zero exit status of a
  deterministically failing command.

A binary on `PATH` is not readiness. The doctor is read-only and stores hashes and reason
codes, never raw output, absolute paths, prompts, environment variables, or credentials.
The full reason-code table is in [docs/TOOLCHAIN.md](docs/TOOLCHAIN.md).

### 6. Trial-install into an isolated directory

```bash
export ZGOV_TRIAL_HOME="$(mktemp -d)"

python3 scripts/install-governance.py --source . --zcode-home "$ZGOV_TRIAL_HOME" --dry-run
python3 scripts/install-governance.py --source . --zcode-home "$ZGOV_TRIAL_HOME"

ZGOV_HOOK_SOURCE=test \
ZGOV_HOOK_RECEIPT_DIR="$ZGOV_TRIAL_HOME/receipts" \
python3 "$ZGOV_TRIAL_HOME/gov/hooks/zcode_hook.py" session-start <<'JSON'
{"hook_event_name":"SessionStart","model":"trial"}
JSON
```

Expected:

- the dry run prints the managed-file denominator;
- every installed file stays inside the isolated directory;
- the real ZCode home is untouched.

Rollback:

```bash
python3 scripts/install-governance.py --source . --zcode-home "$ZGOV_TRIAL_HOME" --rollback
```

Rollback is possible only when a previous install backed `$ZCODE_HOME/gov` up as
`gov.zgov-backup`.

### 7. Install for real and register hooks

```bash
python3 scripts/install-governance.py --source . --zcode-home "$HOME/.zcode" --dry-run
python3 scripts/install-governance.py --source . --zcode-home "$HOME/.zcode"

python3 scripts/register-hooks.py --dry-run
python3 scripts/register-hooks.py
```

`register-hooks.py` only adds or removes entries under `hooks.events` whose `args[0]`
points at `zcode_hook.py`, and forces `hooks.enabled` to `true` (configuration-file hooks
are disabled by default). Every other key — including `provider`, `model`, `modelCatalog`,
`plugins`, `mcp`, and every non-`events` key under `hooks` — is preserved byte-for-byte and
verified after the write. It never prints a configuration *value*, only key names and
counts.

The full flow and rollback are in [docs/deployment.md](docs/deployment.md).

### 8. Fill in the model roles

```bash
$EDITOR "$HOME/.zcode/gov-config/roles.json"
```

See "Model role placeholders" below. **Until they are filled in, no verdict-bearing
artifact can be produced.**

## What "mandatory tooling" actually means

### Gate 1: readiness (`tool-preflight.v16`)

Binds the current host/runtime, tool versions, ZCode configuration, repository root, Git
head, worktree, CodeGraph index, and semantic sentinel. Any identity change invalidates the
receipt.

### Gate 2: actual usage (`tool-usage.v16`)

Binds every declared route to a successful, task-relevant call, an evidence reference, and a
privacy-safe hook receipt hash.

Intent-to-tool routing (ZCode tool names; source of truth is `PREFERRED_TOOL` in
`gov/zgov/tool_routing.py`):

| Intent | Required tool | ZCode tool name |
|---|---|---|
| `known_symbol` / `known_call` / `blast_radius` | CodeGraph | `mcp__codegraph__codegraph_explore` |
| `semantic_entry` / `similar_implementation` | Semble | `mcp__semble__search`, `mcp__semble__find_related` |
| `shell_output` | `rtk` | `Bash` (first token is `rtk`) |
| `exact_string` / `exact_error` / `config` / `log` | `rg` | `Grep`, or `Bash` (first token is `rg`) |
| Hash, parser input, byte identity, exact denominator | raw command | `Bash` |

`normalize_zcode_tool()` performs the normalization: exact MCP names and the
`mcp__codegraph*` / `mcp__semble*` prefixes match directly; `Grep` / `grep` / `ripgrep` map
to `rg`; `Bash` is normalized only when the command is a **single simple command** whose
first token basename is `rtk` or `rg` — any compound command containing
`` | & ; > < ` $( `` or a newline returns `None` (meaning unspecified, not a violation).

Calling each tool once, irrelevantly, is a violation. Fallback is allowed only after the
preferred tool genuinely failed, with a reason code and an evidence reference, and never
claims equivalent semantic or structural coverage.

Full contract and remediation: [docs/TOOLCHAIN.md](docs/TOOLCHAIN.md).

## Development and review model

1. Freeze the objective, scope, invariants, non-goals, exact identity, and evidence budget.
2. Run only affected checks with a concrete WHY-RED and a known denominator (FAST runs only
   the `targeted` stage).
3. Exactly one independent reviewer per task:
   - low/medium risk: the `reviewer_standard` role (default effort `high`);
   - high risk: a fresh `reviewer_high` role (default effort `xhigh`).
4. Stable fixes close out delta-only with the same reviewer (`delta_continuation`).
5. Approve only when coverage is complete, unreviewed scope is empty, no active
   P1/`BLOCKING` finding remains, and evidence matches the exact head.
6. Merge behind an expected-head / match-head guard; the `pre-bash` hook additionally
   enforces a hard PR merge gate.

Correctness and evidence are hard gates. The first optimization target is time to a correct
verdict or a correct merge; token/call cost is second. See
[docs/review-workflow.md](docs/review-workflow.md).

## Model role placeholders and GitHub identities

The Codex original hard-coded model names and GitHub usernames. The ZCode port
**parameterizes all of them** as roles and identities configured in
`$ZCODE_HOME/gov-config/roles.json` (seeded from `gov/roles.example.json`). The repository
**never carries private values**: the seed template ships `<TBD:*>` placeholders, and the
installer `preserved` a user's real `roles.json` (it never overwrites it).

| Role | Purpose | Seed value (replace with your own) |
|---|---|---|
| `writer` | writes briefs/missions | `<TBD:writer>` |
| `executor` | performs the implementation | `<TBD:executor>` |
| `reviewer_standard` | low/medium-risk independent reviewer | `<TBD:reviewer_standard>` |
| `reviewer_high` | high-risk independent reviewer | `<TBD:reviewer_high>` |
| `auditor_spark` | Spark inner-audit auditor | `<TBD:auditor_spark>` |

`efforts` already carry real defaults (`reviewer_standard: high`, `reviewer_high: xhigh`,
`delta_continuation: high`, `auditor_spark: high`). **Effort values may never be
placeholders**, and `delta_continuation` must equal `reviewer_standard`. Under `agents`,
`reviewer_standard` is bound to the existing ZCode agent `gov-reviewer` and `executor` to
`gov-executor`.

The top-level `identities` block holds the two GitHub accounts used by the `pre-bash` hook's
dual-identity guard:

| Identity | Purpose | Seed value (replace with your own) |
|---|---|---|
| `identities.dev` | development identity: branch / commit / push / open PR | `<TBD:dev_login>` |
| `identities.governance` | governance identity: review / approve / merge | `<TBD:gov_login>` |

**How to fill them in**: after installing, edit `~/.zcode/gov-config/roles.json` and replace
every `<TBD:*>` with (a) the five model identifiers your ZCode surface actually exposes and
(b) the two GitHub logins in `identities`. The installer never overwrites this file, so your
values survive upgrades. Then restart the client and start a fresh session.

**Behavior while unresolved**:

- Structural validation still runs (modules skip identity assertions on placeholders), but any
  **verdict-bearing artifact** is blocked with `ROLE_PLACEHOLDER_UNRESOLVED` —
  `resolve_role()` and `require_roles_resolved()` raise `RolePlaceholderUnresolved`. You can
  author and test; you cannot approve.
- With unconfigured identities (placeholders), the hook's merge gate and identity guard
  **fail closed**: `gh pr merge`, `gh pr review`, `gh pr create`, `git push`, and other
  guarded GitHub actions are denied with a "身份未配置：请在 gov-config/roles.json 的
  identities 里填写" reason instead of being allowed through.

## Frozen milestone facts

The Codex original hard-coded three platform hashes, 19 findings, and a denominator of 18
in `spark.py`, plus `BASE_SHA` / `BASE_TREE` in `presubmit.py`. The ZCode port
**parameterizes all of them** into `$ZCODE_HOME/gov-config/milestone.json` (seeded from
`gov/milestone.example.json`), shipped with `"frozen": false`.

While unfrozen, the following trust-chain validations **fail closed with
`MILESTONE_NOT_FROZEN` and never return green**:

| Validation | Location |
|---|---|
| dispatch transcript validation | `gov/zgov/spark.py` (`require_frozen`) |
| author closure validation | `gov/zgov/spark.py` (`require_frozen`) |
| closure binding receipt validation | `gov/zgov/spark.py` (`require_frozen`) |
| whole presubmit flow | `gov/zgov/presubmit.py` (`require_frozen`) |

The negative matrix in `gov/zgov/r1.py` marks affected cases
`SKIPPED_MILESTONE_NOT_FROZEN` and counts them under `skipped`, never `passed`, so the
denominator stays visible.

To freeze: fill in `milestone_id`, `repo`, `base_sha`, `base_tree`, `spark.*`, and
`transcript.*`, then set `frozen` to `true`. The installer never overwrites this file.

## Full verification

During development run the smallest affected checks. Once a clean candidate is frozen:

```bash
git status --short
python3 scripts/verify-governance.py --repo .
python3 scripts/presubmit.py --repo .
git diff --check
```

Test suites and denominators (the numbers below were measured when this document was written; they change as the code evolves — trust your own run):

```bash
PYTHONPATH=gov python3 -m unittest discover -s tests/gov     -p 'test_*.py' -t tests/gov      # 197 tests
PYTHONPATH=gov python3 -m unittest discover -s tests/hooks   -p 'test_*.py' -t tests/hooks    #  45 tests
PYTHONPATH=gov python3 -m unittest discover -s tests/scripts -p 'test_*.py' -t tests/scripts  #  31 tests
```

The manifest is the exact tracked path/hash boundary. Adding, deleting, or modifying tracked
files requires regenerating it:

```bash
python3 scripts/generate-manifest.py --repo .          # regenerate
python3 scripts/generate-manifest.py --repo . --check  # report drift only, no write
```

## Privacy and limits

Never commit:

- API/GitHub tokens, OAuth state, cookies, or credentials;
- ZCode sessions, prompts, transcripts, memory, or receipt JSONL;
- plugin/connection/model caches or browser profiles;
- personal absolute machine paths under a home directory, or private repository content.

`scripts/verify-governance.py` scans every tracked file for these patterns and turns RED on
a hit. Only `PRIVACY.md`, `SECURITY.md`, and `gov/zgov/fixtures/examples/` are exempt.

This package cannot grant a model or tool that the current ZCode surface does not expose.
After governance, MCP, hook, or model-routing changes, **restart the ZCode client and start
a fresh session**.

See [SECURITY.md](SECURITY.md), [PRIVACY.md](PRIVACY.md), and
[docs/privacy-threat-model.md](docs/privacy-threat-model.md).

## Troubleshooting

The complete reason-code set is in [docs/TOOLCHAIN.md](docs/TOOLCHAIN.md); the most common
ones are listed here.

| Symptom | Action |
|---|---|
| `CODEGRAPH_NOT_FOUND` | Install CodeGraph, then configure the ZCode MCP server. |
| `CODEGRAPH_MCP_NOT_CONFIGURED` | Add a CodeGraph server under `mcp.servers`; restart the client. |
| `CODEGRAPH_WRONG_PROJECT` | Stop; point the doctor/query at the owning child repository. |
| `CODEGRAPH_STALE` | Review the diff, then run `codegraph sync .` once authorized. |
| `CODEGRAPH_SENTINEL_MISMATCH` | Check the index, query, path, and revision. |
| `SEMBLE_MCP_NOT_CONFIGURED` | Add a Semble server under `mcp.servers`; restart the client. |
| `SEMBLE_SCOPE_CONTAMINATION` | Stop; repair the repository scope. |
| `SEMBLE_SENTINEL_MISMATCH` | Refine the semantic query or repair repo/index scope; do not claim ready. |
| `RTK_FALSE_GREEN` | Hard stop; shell evidence is unusable until repaired. |
| `GIT_HEAD_UNAVAILABLE` | The strict doctor refuses non-Git directories; run at a real repository root. |
| `ROLE_PLACEHOLDER_UNRESOLVED` | Fill the `<TBD:*>` values in `gov-config/roles.json`. |
| `MILESTONE_NOT_FROZEN` | Fill in and freeze `gov-config/milestone.json`. |
| `receipt_status=write_failed` | Repair private receipt directory permissions (0700/0600); runtime-proof acceptance is blocked. |
| Hooks never fire | Configuration-file hooks are off by default; confirm `hooks.enabled` is `true` and restart the client. |
| Hook output silently discarded | stdout contained a non-allowlisted key; only five keys are allowed (below). |
| Manifest verifier RED | Stop, inspect each error, then regenerate with `generate-manifest.py` after review. |

## ZCode hook mapping

ZCode has **exactly seven** hook events: `SessionStart`, `UserPromptSubmit`, `PreToolUse`,
`PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `Stop`. There is **no
`SubagentStart`**.

This package currently registers three events (see `gov/hooks/hooks.json`):

| Event | Matcher | Subcommand | Timeout | Purpose |
|---|---|---|---|---|
| `SessionStart` | — | `session-start` | 20000 ms | infra self-check + v16 governance context injection |
| `PreToolUse` | `Bash` | `pre-bash` | 30000 ms | PR merge gate, GitHub dual-identity guard, credential protection, transparent rtk rewrite |
| `PreToolUse` | `Read\|Edit\|Write` | `pre-file` | 5000 ms | credential protection |
| `PreToolUse` | `Agent\|mcp__codegraph.*\|mcp__semble.*\|Grep` | `pre-tool` | 10000 ms | delegation guard + routing receipt |
| `PostToolUse` | `Agent` | `post-agent` | 5000 ms | delegation result receipt (non-blocking) |

- Codex's **SubagentStart ACTIVE-MISSION-LOCK** maps to `PreToolUse` with matcher `Agent`;
- Codex's **sub-result validation** maps to `PostToolUse` with matcher `Agent`;
- delegation depth is capped at `max_depth: 1`; nested spawn is always denied (environment
  variable `ZGOV_AGENT_DEPTH`).

**Hook stdout is a strict schema** with exactly these five allowed keys:

```text
hookEventName | permissionDecision | permissionDecisionReason | additionalContext | updatedInput
```

One extra key discards the entire hook effect. Every `decision` / `reason_code` / `route`
therefore sinks into the receipt JSONL (`~/.zcode/hooks/receipts/<UTC-date>.jsonl`) and
never appears on stdout. The exit code is always `0` (except for the `review-pass` helper
command); a denial is expressed through `permissionDecision`. Matchers are **case-sensitive
regular expressions** with aliases `Task` → `Agent` and `ApplyPatch` → `Write|Edit`; an
invalid expression silently never matches.

## Environment variables

| Variable | Purpose |
|---|---|
| `ZCODE_HOME` | ZCode runtime root; defaults to `~/.zcode` |
| `ZGOV_ROLES_PATH` | Override the `roles.json` path |
| `ZGOV_MILESTONE_PATH` | Override the `milestone.json` path |
| `ZGOV_HOOK_SOURCE` | Set to `test` to route receipts to a test directory and mark `source=test` |
| `ZGOV_HOOK_RECEIPT_DIR` | Receipt directory in test mode |
| `ZGOV_RECEIPT_PATH` | Explicit receipt file path |
| `ZGOV_TASK_ID` | Task identifier written into receipts (stored only as sha256) |
| `ZGOV_AGENT_DEPTH` | Current delegation depth, used by the `max_depth: 1` guard |

## Repository layout

```text
gov/                                  installable governance package (installs to $ZCODE_HOME/gov/)
  AGENTS.md                           global rules installed to $ZCODE_HOME/ (sidecar)
  POLICY.md
  BRIEF-TEMPLATES.md
  roles.example.json                  seed for gov-config/roles.json
  milestone.example.json              seed for gov-config/milestone.json
  agents/                             agent definitions (sidecar, installs to $ZCODE_HOME/agents/)
  hooks/                              hooks.json + five hook modules
    hooks.json  zcode_hook.py  hook_receipt.py
    pre_tool_use_policy.py  session_context.py  delegation_contract.py
  zgov/                               18 stdlib-only modules
    contracts.py  contracts/schema_registry.v16.json (26 schemas)
    compiler.py  runner.py  evidence.py  state.py  presubmit.py
    tool_preflight.py  tool_routing.py
    review_policy.py  review_runtime.py  trace.py
    metrics.py  spark.py  r1.py  checker.py  milestone.py  roles.py
    fixtures/                         mission fixtures and examples/
docs/                                 architecture / TOOLCHAIN / review-workflow /
                                      deployment / privacy-threat-model
scripts/                              install-governance.py  register-hooks.py
                                      verify-governance.py   toolchain-doctor.py
                                      presubmit.py           generate-manifest.py
tests/gov/  tests/hooks/  tests/scripts/
manifest.json                         exact tracked path/hash boundary
```

## ZCode official references

The official `zcode-guide` plugin ships local, offline skill documents you can cite:

```text
~/.zcode/cli/plugins/cache/zcode-plugins-official/zcode-guide/<version>/skills/
  zcode-configuration-guide/SKILL.md   configuration overview: scope and precedence for MCP, commands, skills, hooks, plugins
  diagnosing-mcp/SKILL.md              MCP locations, strict schema, template variables, triage flow
  diagnosing-hooks/SKILL.md            the seven event names, matcher semantics, timeout units, strict stdout schema
  diagnosing-skills/SKILL.md           skill discovery and shadowing
  diagnosing-commands/SKILL.md         slash-command precedence and frontmatter
  diagnosing-plugins/SKILL.md          plugins and marketplaces
```

The statements above about "exactly seven events", the strict stdout schema, the
`mcp.servers` location, and configuration-file hooks being disabled by default all come
from those local official documents.
