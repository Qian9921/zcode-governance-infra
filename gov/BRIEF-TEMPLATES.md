# 简报模板（ZCode 治理 v16）

五套模板：任务 packet、工具路由 sidecar、路由与用量 sidecar、嵌套委派 packet、评审 packet。

**模型名一律写 role 引用**（由 `~/.zcode/gov-config/roles.json` 解析）。role 未解析时报 `ROLE_PLACEHOLDER_UNRESOLVED`，不得产出带裁决的制品。字段以 `gov/zgov/contracts/schema_registry.v16.json` 为准。

---

## 1. 任务 packet

`assigned_model` 从本任务的授权在线模型中选出，**不是能力声明**。

### 1.1 精简版（`mission.v1`）

```json
{
  "schema": "mission.v1",
  "milestone": "HARDENING",
  "objective": "<一个垂直切片>",
  "owner": "<task>",
  "assigned_model": "<由 roles.json 的 writer 解析>",
  "role": "execution",
  "permissions": ["read", "write", "test"],
  "scope": {"paths": ["<精确路径>"]},
  "reviewer_separation": {
    "independent": "<由 review_risk 解析：reviewer_standard 或 reviewer_high>",
    "fork_turns": "none",
    "report_only": true
  },
  "review_policy": {
    "review_risk": "medium",
    "reasons": ["<有界的内部行为变更>"],
    "classifier_identity": "<classifier/version>",
    "high_risk_triggers": [],
    "required_stages": ["targeted", "full"],
    "context_mode": "independent_clean_room"
  },
  "operating_domain": "<单位/框架/运行时>",
  "invariants": ["<必须成立>"],
  "non_goals": ["<明确排除>"],
  "evidence_budget": {
    "checks": [{
      "name": "<check>",
      "why_red": "<失败机理>",
      "cost": "<估计>",
      "denominator": "<已知>"
    }]
  },
  "rollback": "<可逆动作>"
}
```

### 1.2 严格版（`mission.v16`）

严格版必填 21 个字段，字段结构与 `gov/zgov/fixtures/mission.valid.json` 一致：

```text
schema  mission_id  milestone  objective  owner  assigned_model  role
permissions  scope  reviewer_separation  operating_domain  invariants
counterexamples  entrypoints  gates  acceptance  non_goals
evidence_budget  rollback  stop_conditions  spark_audits
```

可选字段：`review_policy`。各子对象的必填字段：

| 子对象 | 必填字段 |
| --- | --- |
| `scope` | `paths`；Git 身份下另需 `exact_head`、`tree_sha` |
| `reviewer_separation` | `independent_model`、`fork_turns`、`report_only` |
| `invariants[]` | `id`、`description`、`blocking`、`counterexample_ids` |
| `counterexamples[]` | `id`、`semantics`、`description`、`entrypoint_id`、`gate_id`、`why_red`、`cost`、`denominator`、`expected` |
| `entrypoints[]` | `id`、`argv`、`cwd`、`env`、`timeout_sec`、`stop_conditions` |
| `gates[]` | `id`、`stage`、`depends_on`、`entrypoint_ids`、`blocking`、`reusable`（可选 `read_only`） |
| `acceptance[]` | `id`、`invariant_id`、`counterexample_id`、`entrypoint_id`、`gate_id`、`blocking`、`why_red`、`cost`、`denominator`、`red_meaning`、`green_meaning` |
| `spark_audits[]` | `id`、`domain`、`scope`、`max_findings`、`required`、`request_schema` |

新任务的显式 `review_policy` 示例：`{"review_risk": "high", "reasons": [], "high_risk_triggers": ["hook_reviewer_model_routing"], "required_stages": ["targeted", "full", "fresh"], "context_mode": "independent_clean_room", "fork_turns": "none", "report_only": true}`。

允许的 high-risk trigger 共 11 个：`math_numeric`、`exact_parity`、`security`、`privacy`、`public_contract`、`schema_data_format`、`irreversible_migration`、`supply_chain_installer`、`production_runtime`、`formal_research_release`、`hook_reviewer_model_routing`。low/medium 路由**不得**含任一项；high **必须**至少含一项。缺失、无效、含歧义或 legacy 的策略一律 fail-closed 解析为 high。

**解析器（而非作者）固定评审路由**：low/medium → role `reviewer_standard`（默认 effort `high`）；high → role `reviewer_high`（默认 effort `xhigh`，适用于初评与 escalated 高风险评审）。契约稳定的 `delta_continuation` 保留同一 reviewer，按 `efforts.delta_continuation` 使用较低 effort，并受其有界运行时契约约束。

`required_stages` 是独立冻结的证据路由，必须是有序前缀之一：`[targeted]`、`[targeted, full]`、`[targeted, full, fresh]`。默认值受风险影响，但显式路由跟随任务的受影响 WHY-RED 计划，而不是评审员方便度。可执行枚举源是 `zgov.review_policy.HIGH_RISK_TRIGGERS` 与 `zgov.trace._ESCALATION_TRIGGERS`；散文不创造新身份。

Spark 审计按真实风险与范围选 0..3 个，**不为凑数而加**。每条阻断性不变量/反例都映射到 entrypoint 与 gate，并带 WHY-RED、成本、已知分母、红/绿含义；观测到的 gate 总数必须等于映射的验收分母。

编译（只编译，绝不执行 argv）：

```text
python3 -m zgov.compiler gov/zgov/fixtures/mission.valid.json -o plan.json
```

完整的干净候选 / fresh 工作流（安装后为 `python3 ~/.zcode/gov/scripts/presubmit.py --repo .`）：

```text
python3 scripts/presubmit.py --repo .
```

执行档位：

- `FAST`：针对精确 staged/worktree 内容快照跑 targeted gate。脏工作树只有在外部提供匹配 snapshot 哈希时允许；runner 制品放在仓库外；执行前后 snapshot 哈希必须一致。staged 模式跑的是 index 的隔离本地物化，不是未暂存工作树。
- `CANDIDATE`：在精确干净候选上跑 targeted + full。
- `FINAL`：风险策略要求的当期证据，随后恰好一次 `independent_clean_room` report-only 评审。

正式评审员在 `REVIEW_READY` 对冻结的精确范围**覆盖一次**。普通修复以 `delta_continuation` 回到同一 reviewer；escalation trigger 选择新 reviewer。**评审不是迭代调试**，`REQUEST_CHANGES` 不能通过改阈值或改分母豁免。

---

## 2. 工具路由 sidecar

### 2.1 preflight（声明路由之前）

把严格只读 preflight 绑定到精确 repo/head/worktree/ZCode-config 身份。`tool-preflight.v16` 必填字段：`schema`、`status`、`strict`、`repo_identity`、`config_identity`、`tools`、`counts`、`denominator`、`denominator_known`、`cache`、`mutations`。

```json
{
  "schema": "tool-preflight.v16",
  "status": "ready",
  "strict": true,
  "repo_identity": {"repo": "<repo>", "head": "<40-hex>", "worktree_sha256": "<64-hex>"},
  "config_identity": {"config_path": "~/.zcode/cli/config.json", "config_sha256": "<64-hex>"},
  "tools": {"codegraph": "ready", "semble": "ready", "rtk": "ready"},
  "counts": {"total": 3, "ran": 3, "passed": 3, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0},
  "denominator": 3,
  "denominator_known": true,
  "cache": {"key_sha256": "<64-hex>", "reused": false},
  "mutations": []
}
```

**目录或二进制存在不是就绪。** CodeGraph 必须证明正确的 project/index/revision 加一个 sentinel；Semble 必须证明已配置的 live repo-scoped 检索；`rtk` 必须证明正输出与非零失败保留。只有在 host/runtime/tool/config/repo/head/worktree/index/sentinel 身份全部不变时才允许复用。

语义 sentinel 用「行为描述」而非仅文件名：`{"semantic_sentinel": {"query": "<行为描述，不能只是文件名>", "expected_path": "<仓库相对的当期源码路径>"}}`。

### 2.2 declared_intents

只声明任务真正需要的检查意图。每一行都通过 `zgov.tool_routing.route_tool` 解析，**不得手写成功决策**。

合法 intent 与首选工具（`zgov.tool_routing.PREFERRED_TOOL`）：

| intent | preferred_tool | ZCode 工具 | used_for |
| --- | --- | --- | --- |
| `known_symbol` | `codegraph` | `mcp__codegraph__codegraph_explore` | `structure` |
| `known_call` | `codegraph` | `mcp__codegraph__codegraph_explore` | `structure` |
| `blast_radius` | `codegraph` | `mcp__codegraph__codegraph_explore` | `structure` |
| `semantic_entry` | `semble` | `mcp__semble__search` | `discovery` |
| `similar_implementation` | `semble` | `mcp__semble__find_related` | `discovery` |
| `shell_output` | `rtk` | `Bash` 内的 `rtk` | `context_display` |
| `exact_string` | `rg` | `Grep` / `Bash` 内的 `rg` | `literal` |
| `exact_error` | `rg` | `Grep` / `Bash` 内的 `rg` | `literal` |
| `config` | `rg` | `Grep` / `Bash` 内的 `rg` | `literal` |
| `log` | `rg` | `Grep` / `Bash` 内的 `rg` | `literal` |

```json
{
  "declared_intents": [
    {"intent": "known_symbol", "preferred_tool": "codegraph"},
    {"intent": "semantic_entry", "preferred_tool": "semble"},
    {"intent": "shell_output", "preferred_tool": "rtk"},
    {"intent": "exact_error", "preferred_tool": "rg"}
  ],
  "fallback_contract": {
    "requires_preferred_attempt": true,
    "requires_reason_code": true,
    "requires_evidence_ref": true,
    "silent_fallback": false
  }
}
```

单条决策制品 `tool-route-decision.v16` 必填：`schema`、`intent`、`declared`、`preferred_tool`、`selected_tool`、`decision`、`status`、`fallback`、`attempted_preferred`、`reason_code`、`evidence_ref`。稳定 reason code：`PREFERRED_AVAILABLE`、`PREFERRED_NOT_ATTEMPTED`、`FALLBACK_EVIDENCE_REQUIRED`、`FALLBACK_UNAVAILABLE`。

未声明的 intent 是 `not_declared`，不是伪造的 blocker。首选工具只能在「真实失败/不可用观测 + 稳定 reason code + 证据引用」之后回退；**回退不声称等价的结构或语义覆盖**。下游机器输入或精确分母计算允许原始输出。

### 2.3 收尾时绑定实际使用

`tool-usage.v16` 必填：`schema`、`status`、`routing_compliant`、`coverage_equivalent`、`preflight_cache_key_sha256`、`preflight_artifact_sha256`、`hook_snapshot_sha256`、`task_id_sha256`、`receipt_set_sha256`、`evidence_set_sha256`、`routes`、`calls`、`counts`、`denominator`、`denominator_known`、`violations`。

`calls[]` 字段：`intent`、`tool`、`status`、`evidence_ref`、`evidence_sha256`、`receipt_sha256`、`tool_call_id_sha256`、`used_for`。

```json
{
  "schema": "tool-usage.v16",
  "status": "compliant",
  "routing_compliant": true,
  "coverage_equivalent": true,
  "preflight_cache_key_sha256": "<与当期 preflight 相同>",
  "preflight_artifact_sha256": "<64-hex>",
  "hook_snapshot_sha256": "<64-hex>",
  "task_id_sha256": "<64-hex>",
  "receipt_set_sha256": "<64-hex>",
  "evidence_set_sha256": "<64-hex>",
  "routes": [{"intent": "semantic_entry", "selected_tool": "semble", "decision": "route", "reason_code": "PREFERRED_AVAILABLE"}],
  "calls": [{
    "intent": "semantic_entry",
    "tool": "semble",
    "status": "success",
    "evidence_ref": "<候选 path/line 制品>",
    "evidence_sha256": "<64-hex>",
    "receipt_sha256": "<隐私安全的 hook receipt 行哈希>",
    "tool_call_id_sha256": "<64-hex>",
    "used_for": "discovery"
  }],
  "counts": {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0},
  "denominator": 1,
  "denominator_known": true,
  "violations": []
}
```

**对每个工具各打一次无关的卡不是合规。** 每次调用必须匹配所选工具、成功、带 receipt/证据引用，并实质性地决定发现、结构/影响、上下文展示或字面真相。`used_for` 必须精确等于该 intent 在 `USAGE_PURPOSE` 中的值。

---

## 3. 路由与用量 sidecar

这是 `zgov.metrics.choose_model` 与 `BudgetLedger` 的输入。分数与 `token_cost_rank` 是**相对的当期控制面元数据**，不是硬编码的模型能力，也不是编造的美元价格。

```json
{
  "task_kind": "implementation",
  "risk": "medium",
  "authorized_models": ["<由 roles.json 的 writer 解析>", "<由 roles.json 的 executor 解析>"],
  "live_models": {
    "<由 roles.json 的 writer 解析>": {
      "available": true,
      "risks": ["low", "medium", "high"],
      "token_cost_rank": 1
    },
    "<由 roles.json 的 executor 解析>": {
      "available": true,
      "risks": ["low", "medium"],
      "token_cost_rank": 2
    }
  },
  "preferences": {
    "<由 roles.json 的 writer 解析>": {"implementation:medium": 10, "default": 0},
    "<由 roles.json 的 executor 解析>": {"implementation:medium": 8, "default": 0}
  },
  "limits": {
    "max_model_calls": 4,
    "max_review_calls": 1,
    "max_parallel_agents": 2,
    "max_input_tokens": 60000,
    "max_output_tokens": 20000,
    "max_total_tokens": 80000
  }
}
```

派发前用 `BudgetLedger.reserve()` 预留保守的单次调用上限。完成时用提供方报告的输入/输出计数 `settle()` 该预留，同时释放其活跃 agent 槽位。计数不可得时按预留上限结算（`counts_available=false`），**不要编造更小的数字**。只持久化聚合的 `BudgetLedger.usage()` receipt。订阅制下 `usd_cost` 保持 `null`，除非提供方暴露精确的套餐级归因；**不得推断 API 价格，也不得把月订阅费除以猜测的调用数。**

---

## 4. 嵌套委派 packet

`delegation.v1` 必填 14 个字段；`delegation-result.v1` 必填 11 个字段。

```json
{
  "schema": "delegation.v1",
  "parent_task_id": "<parent>",
  "child_task_id": "<child>",
  "assigned_model": "<由 roles.json 的 executor 解析>",
  "role": "specialist",
  "max_depth": 1,
  "depth": 1,
  "permissions": ["read", "write_paths"],
  "forbidden_permissions": ["git", "github", "review", "merge"],
  "lease": {"paths": ["<独占路径>"]},
  "retry_budget": {"semantic_contamination": 1},
  "active_mission_lock": true,
  "plugin_inventory": "informational",
  "result_schema": "delegation-result.v1"
}
```

结果 packet：

```json
{
  "schema": "delegation-result.v1",
  "parent_task_id": "<parent>",
  "child_task_id": "<child>",
  "assigned_model": "<与 packet 完全一致>",
  "task_id": "<必须等于 child_task_id>",
  "depth": 1,
  "changed_paths": ["<必须落在 lease.paths 内>"],
  "counts": {"total": 3, "ran": 3, "passed": 3, "failed": 0, "skipped": 0},
  "retry_used": 0,
  "contamination": false,
  "status": "completed"
}
```

校验硬约束（`gov/hooks/delegation_contract.py`）：`max_depth` 与 `depth` 都必须为 `1`；`active_mission_lock` 为真且 `plugin_inventory="informational"`；`permissions` 中出现 `git`/`github`/`review`/`merge` 任一即拒；`lease.paths` 非空；`retry_budget.semantic_contamination` 必须为 `1`；`retry_used ∈ {0,1}`；`total=passed+failed+skipped` 且 `ran=passed+failed`；`changed_paths` 越出租约即拒；`contamination=true` 即拒。任一不符报 `NESTED_CHILD_CONTRACT_REJECTED`。

ZCode 中委派守卫是 `PreToolUse` matcher `Agent`，子结果校验是 `PostToolUse` matcher `Agent`（ZCode 没有 SubagentStart 事件）。

---

## 5. 评审 packet

冻结精确 Git head 或非 Git 快照、Acceptance Envelope、风险决策、覆盖、直接依赖、带已知分母的证据信封、外部交付的 lineage、先前 findings/dispositions。**派发前对 packet 取哈希。**

`review-packet.v16` 必填：`schema`、`mission_id`、`author_login`、`reviewer_login`、`base_sha`、`head_sha`、`tree_sha`、`lineage_mode`、`coverage_status`、`reviewed_scope`、`unreviewed_scope`、`checks`、`findings`、`closures`、`verdict`、`round`、`body_sha256`。可选：`independent_artifact_sha256`、`expected_scope`、`incident`、`decision_basis`、`identity_mode`、`snapshot_sha256`、`prior_snapshot_sha256`、`prior_head_sha`、`delta_sha256`。

评审员制品（`independent-review.v16`）必须绑定：packet 哈希、Acceptance Envelope、base/head/tree/diff 或 snapshot 身份、已审范围、证据制品哈希、**评审员自有的** findings/limitations、context mode、escalation 原因与证据引用、裁决。**光有裁决或直接采用作者提供的发现集合永远不够。**

### 5.1 四种 context mode

- `author_contextual`：仅作者预检；**永远不是门**。
- `independent_clean_room`：全新初评 report-only 门，`fork_turns=none`，精选 packet。
- `delta_continuation`：同一 reviewer 连续性身份，独立的新 run 与新裁决；old→new delta、先前 findings/dispositions、受影响证据。
- `escalated_fresh`：触发任一 `zgov.trace._ESCALATION_TRIGGERS`（16 项，见 POLICY.md §6.2）后换新 reviewer。

### 5.2 运行时编译

派发前编译 `zgov.review_runtime.compile_review_runtime`，记录得到的 `review-runtime.v16` 哈希。控制器必须独立提供：

```text
context_mode
changed_files / changed_lines
review_identity_sha256
prior_review_artifact_sha256 / reviewer_continuity_id
prior_coverage_status / prior_unreviewed_count
same_reviewer_available
contract_drift
escalation_triggers
```

契约稳定的续评使用原 reviewer、独立的 run 与 `efforts.delta_continuation` 指定的 effort；只发送精确 delta、先前 findings/dispositions、复用证据与直接受影响边界，**不得重跑原全范围评审**。遵守编译出的 file/line/context/tool 上限与 soft/hard 截止：soft 截止索取报告，hard 截止用 `INTERRUPT_REPLAN`。**PARTIAL 报告或运行时超时不能 approve。** 新的可证伪证据选择 `escalated_fresh`；无支撑的范围扩张必须停止。

预算画像（`zgov.review_runtime`）：

| 画像 | soft (s) | hard (s) | max_files | max_changed_lines | max_context_chars | max_tool_calls |
| --- | --- | --- | --- | --- | --- | --- |
| low / medium | 180 | 480 | 20 | 3000 | 20000 | 12 |
| high | 300 | 900 | 24 | 5000 | 24000 | 16 |
| delta | 90 | 240 | 12 | 800 | 12000 | 8 |

### 5.3 进度 receipt

每个进度 receipt 必须携带观测到的 `context_chars`、`review_calls`、`duplicate_full_scope_reviews`；校验必须**分开**提供冻结的评审策略与调用方自有的 context mode、精确 changed-file/line 计数、评审身份、先前制品、reviewer 连续性期望。**不要把运行时载荷或它自己的摘要当作自身权威。**

### 5.4 裁决

`APPROVE` 需要：覆盖完整、`unreviewed_scope` 为空、无活跃 P1/BLOCKING、有匹配的调用方绑定独立制品、证据有效。否则用 `REQUEST_CHANGES`；基础设施故障用 `null`。
