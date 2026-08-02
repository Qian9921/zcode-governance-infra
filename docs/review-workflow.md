# 评审流程

本文描述一次正式评审从风险分类到闭合的完整合同。所有模型身份都用 **role 名**表示，实际
模型标识来自 `$ZCODE_HOME/gov-config/roles.json`（见
[README.zh-CN.md](../README.zh-CN.md) 的「模型 role 占位符」一节）。

可执行来源：`gov/zgov/review_policy.py`、`gov/zgov/review_runtime.py`、`gov/zgov/trace.py`。
文档可以解释这些枚举与阈值，但不能扩展它们。

## 1. 风险路由

`review_policy.resolve_reviewer(risk)` 按风险选 reviewer role：

| `review_risk` | reviewer role | 默认 `reasoning_effort` | 默认 `required_stages` |
|---|---|---|---|
| `low` | `reviewer_standard` | `high` | `("targeted",)` |
| `medium` | `reviewer_standard` | `high` | `("targeted", "full")` |
| `high` | `reviewer_high` | `xhigh` | `("targeted", "full", "fresh")` |

effort 取自 `roles.json` 的 `efforts` 块，不是硬编码。

**fail-closed 兜底**：`resolve_review_policy()` 在 policy 缺失或无法安全解析时返回
`_legacy_policy()`——`review_risk: "high"`、`required_stages` 为完整三档、
`high_risk_triggers: ["hook_reviewer_model_routing"]`、`legacy_fallback: True`。
也就是说「说不清风险」等于「高风险」。

**writer 不能削弱评审路由**：即使显式 policy 里写了 `reviewer_model`，
`resolve_review_policy()` 也会用 `DEFAULT_REVIEWER[review_risk]` 覆盖它。writer 可以描述
任务偏好模型，但不能改独立评审的路由。

### 11 项 `HIGH_RISK_TRIGGERS`

命中任意一项即为高风险（`review_policy.HIGH_RISK_TRIGGERS`，共 11 项）：

| # | trigger | 覆盖什么 |
|---|---|---|
| 1 | `math_numeric` | 数学/数值算法与精度 |
| 2 | `exact_parity` | 与既有实现的逐位/逐值对齐 |
| 3 | `security` | 安全边界、认证、授权 |
| 4 | `privacy` | 隐私投影、脱敏、数据最小化 |
| 5 | `public_contract` | 对外公开的 API/CLI 契约 |
| 6 | `schema_data_format` | schema 与数据格式 |
| 7 | `irreversible_migration` | 不可逆迁移 |
| 8 | `supply_chain_installer` | 供应链与安装器 |
| 9 | `production_runtime` | 生产运行时 |
| 10 | `formal_research_release` | 正式研究/发布物 |
| 11 | `hook_reviewer_model_routing` | hook、reviewer 与模型路由本身的改动 |

第 11 项意味着：**改本治理包自己就是高风险**。

## 2. 四种 context mode

`review_policy.CONTEXT_MODES` 共 4 项。`context_mode_is_gating(mode)` 的规则是
**除 `author_contextual` 外都是门禁性的**。

| context mode | 门禁性 | 语义 |
|---|---|---|
| `author_contextual` | **非门禁** | 作者自查。可以做，但不能参与 readiness gate。 |
| `independent_clean_room` | 门禁 | 全新 report-only reviewer，拿一份紧凑的 hash 绑定 packet。 |
| `delta_continuation` | 门禁 | 保留 reviewer 连续性，同时绑定新的 run、identity、delta、evidence 与 verdict。 |
| `escalated_fresh` | 门禁 | 仅在显式触发时更换 reviewer。 |

`validate_review_policy()` 额外要求：mission 的正式评审路由**必须**是
`independent_clean_room`（其余三种只能出现在 runtime 编译阶段）。
`compile_review_runtime()` 拒绝 `author_contextual`（`formal gating mode required`）。

各模式的前置条件（`compile_review_runtime`）：

- `independent_clean_room`：禁止携带 prior artifact、reviewer continuity、prior coverage、
  escalation trigger——任何一项非空即报错。
- `delta_continuation`：要求 `prior_coverage_status == "COMPLETE"` 且
  `prior_unreviewed_count == 0`；`contract_drift` 为真必须升级为 `escalated_fresh`；
  必须有 reviewer 连续性（`same_reviewer_available`）、prior artifact 与 continuity id；
  **不得携带 escalation trigger**；delta 规模超过 delta profile 上限时报
  `bounded continuation exceeded; escalated_fresh required`。
- `escalated_fresh`：**必须**有具名 trigger；必须有 prior artifact，且**禁止**复用
  reviewer continuity。

## 3. review-runtime 三档预算

数值直接来自 `review_runtime._INITIAL_PROFILES` 与 `_DELTA_PROFILE`。
这些是**路由/SLO 阈值，永远不是 acceptance 阈值**：超限只会选更强的路由或触发 replan，
不能豁免或制造 verdict。

| 字段 | 初始 low / medium | 初始 high | `delta_continuation` |
|---|---|---|---|
| `soft_deadline_sec` | 180 | 300 | 90 |
| `hard_deadline_sec` | 480 | 900 | 240 |
| `max_files` | 20 | 24 | 12 |
| `max_changed_lines` | 3000 | 5000 | 800 |
| `max_context_chars` | 20000 | 24000 | 12000 |
| `max_tool_calls` | 12 | 16 | 8 |

`low` 与 `medium` 的 profile 完全相同——风险等级的差异体现在 `required_stages` 与
（当为 `high` 时）reviewer role 上，而不是初始预算上。

另外三个字段在所有档位下恒定（`compile_review_runtime` 直接写死）：

```text
max_review_calls: 1
duplicate_full_scope_reviews: 0
timeout_action: "return-partial-then-interrupt-replan"
```

即：**一次任务只有一次正式评审调用，且不允许重复的全量评审。**

`escalated_fresh` 使用初始 profile（按 `review_risk` 选 low/medium 或 high），不是 delta
profile。`compile_review_runtime` 最后强制 `hard_deadline_sec > soft_deadline_sec`。

## 4. 7 档决策优先级

`review_runtime.review_progress_decision()` 是一条严格有序的 if/elif 链，先命中先返回：

| 优先级 | 条件 | `action` | `reason_code` |
|---|---|---|---|
| 1 | `exceeded`（见下） | `INTERRUPT_REPLAN` | `HARD_RUNTIME_BUDGET_EXCEEDED` |
| 2 | `context_mode == "delta_continuation"` 且出现新的可证伪证据 | `ESCALATE_FRESH` | `NEW_FALSIFIABLE_EVIDENCE` |
| 3 | 请求扩大 scope 但没有反例 | `STOP_SCOPE_EXPANSION` | `SCOPE_EXPANSION_LACKS_COUNTEREXAMPLE` |
| 4 | 有 verdict 且 coverage 完整且 `unreviewed_count == 0` | `ACCEPT_REPORT` | `COMPLETE_REPORT_AVAILABLE` |
| 5 | 有 verdict 但覆盖不完整 | `RETURN_PARTIAL` | `VERDICT_WITH_INCOMPLETE_COVERAGE` |
| 6 | `elapsed >= soft_deadline_sec` | `REQUEST_REPORT` | `SOFT_DEADLINE_REACHED` |
| 7 | 其余 | `CONTINUE` | `WITHIN_RUNTIME_BUDGET` |

`exceeded` 为真的条件（任一成立）：

```text
tool_calls  > max_tool_calls
files_read  > max_files
context_chars > max_context_chars
review_calls > max_review_calls
verdict_present 且 review_calls != max_review_calls     ← 有裁决却不是恰好一次评审调用
duplicate_full_scope_reviews > duplicate_full_scope_reviews(=0)
elapsed_sec >= hard_deadline_sec
```

`approval_eligible` 只有在 `action == "ACCEPT_REPORT"` **且** coverage 完整 **且**
`unreviewed_count == 0` **且** 未超预算时才为真。注意第 4 档与 `approval_eligible` 的条件
是重复计算的——`validate_review_progress()` 会从 runtime 合同与观测值**重新推导**这个决策，
不接受被校验对象的自述。

## 5. 16 项 `_ESCALATION_TRIGGERS`

`escalated_fresh` 必须携带具名 trigger，取值必须来自 `trace._ESCALATION_TRIGGERS`
（共 16 项）：

| # | trigger | 含义 |
|---|---|---|
| 1 | `RISK_ESCALATION` | 风险等级上调 |
| 2 | `CONTRACT_DISPUTE` | 对契约本身产生争议 |
| 3 | `REVIEW_POLICY_DRIFT` | 评审 policy 漂移 |
| 4 | `ACCEPTANCE_ENVELOPE_DRIFT` | acceptance 包络漂移 |
| 5 | `REFERENCE_DOMAIN_THRESHOLD_DRIFT` | 参考域/阈值漂移 |
| 6 | `PATH_SET_SCOPE_DRIFT` | 路径集合 scope 漂移 |
| 7 | `MATERIAL_REWRITE` | 实质性重写 |
| 8 | `PACKET_EVIDENCE_IDENTITY_INVALIDATED` | packet/证据 identity 失效 |
| 9 | `LINEAGE_LOSS` | lineage 丢失 |
| 10 | `PRIOR_COVERAGE_INCOMPLETE` | 前次覆盖不完整 |
| 11 | `ORIGINAL_SCOPE_MISSED_P1` | 原 scope 内漏掉 P1 |
| 12 | `NEW_FALSIFIABLE_P1_EVIDENCE` | 出现新的可证伪 P1 证据 |
| 13 | `POST_REVIEW_INCIDENT` | 评审后事故 |
| 14 | `REVIEWER_PARTICIPATED` | reviewer 参与了实现，独立性丧失 |
| 15 | `REVIEW_HOOK_ROUTING_GOVERNANCE_CHANGE` | 评审/hook/路由/治理本身发生变更 |
| 16 | `TWO_ROUND_NON_CONVERGENCE` | 两轮不收敛 |

这 16 项覆盖显式的 risk、contract、scope、lineage、incident、P1、governance 与
non-convergence 触发条件。文档不能新增。

## 6. 单调闭合与 `FIXED`

`trace._DISPOSITIONS` 共 4 种：`FIXED`、`DISAGREE`、`FOLLOW_UP`、`OPEN`。
`FIXED` 之外的都算 **active finding**（`active = [f for f in findings if
f["disposition"] != "FIXED"]`）。

### closure matrix 的强制项

`_closure_matrix()` 对每一条 entry 强制：

- 字段集必须**恰好**等于 `_CLOSURE_ENTRY_FIELDS`（`prior_disposition`、`disposition`、
  `evidence_ref`、`counterexample_recheck`）；
- `prior_disposition` 与 `disposition` 都必须在 4 种 disposition 之内；
- `disposition == "FIXED"` 时，`evidence_ref` 与 `counterexample_recheck` **都不能为空**
  （`closed finding requires evidence and counterexample recheck`）。

### 单调性

`_validate_prior_closure()` 要求续审的 closure matrix **覆盖全部 prior finding**：
`set(matrix) != set(prior_findings)` 即报 `prior finding closure matrix is incomplete`。
没有 `closure_matrix_sha256` 的 legacy receipt，只要存在 prior finding，就不能被用作新的
续审批准（`continuation requires prior closure matrix`）。

也就是说：**闭合只能单调推进**——你必须对每一条既有 finding 表态，不能靠省略让它消失。

### `FIXED` 需要 pre-execution closure authority

只要有任何一条 finding 从「非 `FIXED`」变成 `FIXED`，就必须同时提供
`closure_binding_receipt` 与 `closure_authority`，否则报

```text
FIXED closure requires decision-basis pre-execution closure authority
```

两者必须**成对出现**（缺一即报
`closure receipt and decision-basis authority must be supplied together`）。
`closure_authority` 会先经 `validate_pre_execution_closure_authority()`，再用它里面的
`closure_binding_receipt_sha256` / `compiled_plan_sha256` / `closure_plan_sha256` /
`closure_plan_file_sha256` / `dispatch_transcript_file_sha256` 反过来校验
`closure_binding_receipt`。

`FIXED` 的 finding 另有硬要求（`trace` 的 finding 级校验）：结构化的 evidence reference、
结构化的 counterexample recheck、evidence identity、recheck identity、可执行的 recheck
kind；且该 finding 必须出现在 pre-execution closure binding receipt 里
（`finding absent from pre-execution closure binding receipt`）。

**语义**：闭合的授权必须在**执行之前**就被冻结。事后补一份「我已经修好了」的记录无法通过
——授权、计划、transcript 与 receipt 四者的哈希必须互相咬合。

> 这些校验中与 milestone 冻结事实相关的部分（dispatch transcript、author closure、
> closure binding receipt）在 `milestone.json` 未冻结时会 fail-closed 抛
> `MILESTONE_NOT_FROZEN`，绝不返回绿。

## 7. 与 ZCode 现有评审流程的衔接

这一节是本仓库特有的（Codex 源仓库没有对应内容）。ZCode 侧已经有一套基于
`gov-reviewer` agent + marker 文件 + merge gate 的 PR 审批流程，
`gov/hooks/zcode_hook.py` 把它接进 v16 治理。

### 双身份

两个 GitHub 账号的登录名**不在代码里**，由本机 `~/.zcode/gov-config/roles.json` 的
`identities` 块配置（`gov/hooks/zcode_hook.py` 每次延迟读取，从不 import `zgov`）：

```json
"identities": {
  "dev": "<开发身份的 GitHub 登录名>",
  "governance": "<治理身份的 GitHub 登录名>"
}
```

- `identities.dev` = 开发身份：branch / commit / push / 开 PR；
- `identities.governance` = 治理身份：review / approve / merge。

`identity_guard()` 在 `PreToolUse`/`Bash` 上对 `gh pr review|merge`（治理动作）与
`gh pr create|edit|comment|ready|close|lock` / `gh issue *` / `gh release *` / `gh repo *` /
`git push`（开发动作）分别要求对应身份。身份未配置（仍是占位符）时 **fail-closed 拦截**
（无法确认身份，绝不放行）；`gh` 查询失败时 fail-open（网络抖动不该挡住工作），只记 receipt。

### role 绑定

`roles.example.json` 的 `agents` 块把 v16 的 role 映射到 ZCode 现有 agent：

```json
"agents": {
  "reviewer_standard": "gov-reviewer",
  "reviewer_high":     "gov-reviewer",
  "executor":          "gov-executor",
  "auditor_spark":     "gov-spark-audit"
}
```

`roles.agent_for(name)` 在值缺失或仍是占位符时返回 `None`。因此低/中风险的独立评审当前落到
`gov-reviewer`；高风险 reviewer 的 agent 尚未绑定。

### `review-pass` marker

`gov-reviewer` 给出 APPROVE 后，由主智能体显式登记 marker：

```bash
python3 ~/.zcode/gov/hooks/zcode_hook.py review-pass \
  --repo <owner>/<name> --pr <number> --sha <40-hex-head-sha>
```

- `--sha` 必须是 40 位小写 hex；`--repo` 必须是 `owner/name`；
- 默认会 **live 校验**：`gh pr view --json headRefOid` 取回当前 head，与 `--sha` 不一致就
  拒绝写 marker（head 已漂移）；
- `--skip-verify` 仅限离线等特殊情况，且须用户明确同意；
- marker 写到 `~/.zcode/hooks/state/review-markers/<repo>__pr<N>.json`，权限 0600，内容
  含 `repo` / `pr` / `head_sha` / `reviewer: "gov-reviewer"` / `approved_at` /
  `verified_live`；
- 这是**唯一一个**退出码非 0 的子命令（参数错误或校验失败时退出 1）。

### merge gate（fail-closed）

`merge_gate()` 在 `PreToolUse`/`Bash` 命中 `gh pr merge` 时执行 4 步，任一步查不了就
**不许合并**：

| 步骤 | 失败 reason code | 说明 |
|---|---|---|
| 1. 身份 | `merge_gate.identity` / `merge_gate.identity_unconfigured` | 必须是治理身份（`identities.governance`）；身份未配置时同样拦截并提示去 `gov-config/roles.json` 填写。 |
| 2. 解析 PR | `merge_gate.pr_lookup_failed` / `merge_gate.pr_parse_failed` | live 查询 `number,headRefOid,url`，不用缓存。 |
| 3. marker 存在性 | `merge_gate.no_marker` / `merge_gate.marker_corrupt` | 没有 APPROVE marker 就给出三步补救流程。 |
| 4. 新鲜度与 head 绑定 | `merge_gate.marker_expired` / `merge_gate.head_drift` | marker TTL 7 天；`head_sha` 必须精确等于 live head。 |

全部通过时写 `merge_gate.ok` receipt 并**安静放行**——不输出
`permissionDecision: "allow"`，让用户正常的权限确认流程继续生效。

**head 漂移即 verdict 作废**：APPROVE 时的 SHA 与当前 SHA 不同，必须对新 head 重新调用
`gov-reviewer`，通过后重新登记 marker。这正是 v16「evidence 与 exact head 匹配」在
GitHub 侧的落地。

### 两套流程的对应关系

| v16 概念 | ZCode 侧落地 |
|---|---|
| `independent_clean_room` 首轮评审 | 用 `Agent` 工具调用 `gov-reviewer`（`reviewer_standard`） |
| 评审 verdict | k3-review 结论 + `review-pass` marker |
| exact-head / match-head guard | marker 的 `head_sha` 与 live `headRefOid` 精确比对 |
| 「只有一次正式评审调用」 | `max_review_calls: 1`；重复全量评审 `duplicate_full_scope_reviews: 0` |
| approve 前 coverage 完整、无 active P1 | `approval_eligible` + closure matrix 单调性 |
| 合并授权 | merge gate 的治理身份 + marker + TTL + head 绑定 |

> 注意范围差异：merge gate 与 marker 是 **GitHub 合并侧**的门禁，它不替代
> `readiness-state.v16` / `independent-review.v16` 的**制品侧**门禁。两者都必须过。

## 8. SessionStart 注入的评审情境

`session_context.REVIEW_RUNTIME_GUIDANCE` 每次会话开始时注入以下事实（受 1200 字符上限
约束）：

```text
initial_high                 fresh reviewer_high xhigh
delta_continuation           same reviewer and model; high-risk reviewer_high,
                             low/medium reviewer_standard; delta-only; 90s soft/240s hard
escalated_high               fresh reviewer_high xhigh
formal_review_calls          1
duplicate_full_scope_reviews 0
```

这些数值与本文第 3、4 节完全一致，来源同为 `review_runtime`。
