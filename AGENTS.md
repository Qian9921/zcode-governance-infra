# Repository contribution rules

- Keep governance portable and privacy-safe; never commit live sessions, receipts, review markers, secrets, or machine paths.
- Python implementation uses the standard library only; the package is `zgov`, the runtime root is `$ZCODE_HOME`.
- Changes must preserve the v16 acceptance envelope, exact identity, lease, and review gates.
- Model identities and milestone facts are configuration, not code. Never hard-code a model name or a frozen hash: use `zgov.roles` and `zgov.milestone`. Unresolved roles must fail closed with `ROLE_PLACEHOLDER_UNRESOLVED`; an unfrozen milestone must fail closed with `MILESTONE_NOT_FROZEN`, never green.
- Hook stdout is a strict schema (`hookEventName`, `permissionDecision`, `permissionDecisionReason`, `additionalContext`, `updatedInput`). Any extra key discards the whole hook effect; route/reason codes belong in the receipt JSONL.
- Known symbol/call/dependency/impact work uses this repository's CodeGraph (`mcp__codegraph__codegraph_explore`); unknown semantic entrypoints/similar implementations use Semble (`mcp__semble__search`, `mcp__semble__find_related`); exact text uses `rg`/`Grep`; shell output shown to the model uses `rtk`. Fall back only after a real failure with a reason code and evidence reference.
- `.codegraph/` is local generated state. Build or sync only with explicit authorization, then refresh after edits; never substitute another project's index.
- Any change that adds, removes, or modifies a tracked file must regenerate `manifest.json` (`python3 scripts/generate-manifest.py --repo .`) in the same change; `--check` must report no drift.
- Before review run `python3 scripts/verify-governance.py --repo .` and all three suites: `tests/gov`, `tests/hooks`, `tests/scripts` (`PYTHONPATH=gov python3 -m unittest discover -s tests/<dir> -p 'test_*.py' -t tests/<dir>`).
- Compile and obey `zgov.review_runtime`: one formal review call, bounded delta/context/tool scope, soft report deadline, hard interrupt-and-replan deadline, and no duplicate full-scope review. Runtime budgets select routing; they never waive correctness or evidence.
- Documentation may explain `review_policy.HIGH_RISK_TRIGGERS`, `trace._ESCALATION_TRIGGERS`, `tool_preflight` reason codes, and `tool_routing.PREFERRED_TOOL`, but may never extend them.
- The repository is authored by Qian9921; Liang9921 is the independent governance reviewer and the only identity permitted to review, approve, or merge.
