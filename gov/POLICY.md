# ZCode 治理策略详规（v16）

`AGENTS.md` 回答「必须做什么」；本文件回答「为什么 + 精确定义 + 枚举全集」。

**规范与代码冲突时以代码为准。** 本文所有枚举均从 `gov/zgov/` 源码读出核对，逐项对应关系见末节「本文档的可执行事实来源」。散文不得创造新的枚举身份。

---

## 一、MILESTONE-1 · 任务契约

### 1.1 定义

一个任务（mission）是**一个**任务级目标，配以完整的可执行边界声明。`mission.v16` 的必填字段共 21 个：`schema`、`mission_id`、`milestone`、`objective`、`owner`、`assigned_model`、`role`、`permissions`、`scope`、`reviewer_separation`、`operating_domain`、`invariants`、`counterexamples`、`entrypoints`、`gates`、`acceptance`、`non_goals`、`evidence_budget`、`rollback`、`stop_conditions`、`spark_audits`；可选字段 `review_policy`。

### 1.2 为什么

治理失败的主要来源不是「模型不够强」，而是**边界未声明**：没有 owner 就没有问责，没有非目标就会范围漂移，没有回滚就不敢快速交付，没有停止条件就会烧完预算仍无裁决。任务契约把这些隐含假设变成可被机器校验的字段。

### 1.3 交付形态

- 交付**小而连贯、可叠加**的改动，而不是一个必须的大改动。
- 切分依据是「可独立评审的行为边界」与「可独立回滚的边界」。
- **禁止机械行数配额**。300 行的一个连贯行为改动优于拆成三个互相依赖、单独不可回滚的 100 行 PR。

### 1.4 能力平等原则

任务简报控制 role、权限、范围、评审员分离、授权回退模型、用量上限。**模型名不是能力禁令**：把某个 role 路由给某个模型是默认路由，不是对其他模型的能力断言。ZCode 版进一步把模型名全部参数化为 role（见第六节）。

### 1.5 非目标

融合类 / 项目特定的业务工作不在本策略范围内。本策略只约束治理与证据流程本身。

---

## 二、PRESUBMIT-1 · 证据门

### 2.1 作者预检

作者提交前的 pre-mortem 必须包含两件事：

1. **Anticipated Finding Matrix (AFM)**：作者预先枚举「独立评审员最可能提出的发现」，并对每条给出当前状态（已修 / 已知限制 / 争议）。AFM 的作用是把评审从「发现明显问题」提升到「发现作者没想到的问题」。
2. **`READY_FOR_INDEPENDENT_REVIEW` 门**：一个显式布尔门，只有在证据、覆盖、P1 状态全部满足时才置真。

### 2.2 检查的最低元数据

每条检查必须是**受影响的**（affected）且**确定性的**（deterministic），并携带：WHY-RED（该检查为何能在改动出错时变红）、成本、已知分母、精确 snapshot/head、命令 / cwd / runtime / config、时间戳、退出状态、制品同一性。

### 2.3 计数算术（硬约束）

```text
total = passed + failed + skipped + unknown
ran   = passed + failed
executed_total == frozen_acceptance_denominator
```

绿的判据：算术成立、`total > 0`、`failed = skipped = unknown = xfail = 0`。

**阻断验收的情形**：分母未知、存在 skip、存在 xfail、出现 NaN/Inf、身份陈旧（head/tree/snapshot 与当期不符）、复制的计数、缺失日志、head 漂移、隐私扫描红。

> 与日常 EVID 门的关系：`AGENTS.md` 的 `total=passed+failed+skipped` 是面向人类汇报的最低要求；治理制品在此之上追加 `unknown` 项与 `ran` 项，并要求等于冻结分母。

### 2.4 三条证据路径（与评审路由正交）

| 路径 | 身份基准 | 检查范围 | 正式评审 | 典型用途 |
| --- | --- | --- | --- | --- |
| `FAST` | staged/worktree 精确内容快照 | 仅 targeted 受影响检查 | 无 | 内环迭代 |
| `CANDIDATE` | 冻结的精确干净候选（clean） | targeted + full | 无 | 交付前自检 |
| `FINAL` | 冻结风险策略的 `required_stages` | 全部要求阶段 | 恰好一次 report-only 独立门 | 正式评审 |

`FAST` 细则：脏工作树只有在外部提供匹配的 snapshot 哈希时才允许；runner 制品必须放在仓库之外；执行前后的 snapshot 哈希必须一致。staged 模式运行的是 index 的隔离本地物化，**不是**未暂存的工作树。

### 2.5 风险与阶段的正交性

- **评审风险选 reviewer**：`low`/`medium` → `reviewer_standard`（默认 effort `high`）；`high` → `reviewer_high`（默认 effort `xhigh`）。
- **`required_stages` 独立冻结证据阶段**：必须是 `STAGE_ORDER = ("targeted", "full", "fresh")` 的**有序非空前缀**，即只允许 `[targeted]`、`[targeted, full]`、`[targeted, full, fresh]` 三种。
- 风险默认阶段（`DEFAULT_STAGES`）仅为建议：`low → (targeted,)`、`medium → (targeted, full)`、`high → (targeted, full, fresh)`。显式路由跟随任务的受影响 WHY-RED 计划，而非评审员方便度。
- **fail-closed**：策略缺失、无效、含歧义或 legacy，一律解析为 `review_risk=high`、`required_stages=[targeted, full, fresh]`、`high_risk_triggers=[hook_reviewer_model_routing]`、`classifier_identity=legacy-fail-closed`、`legacy_fallback=true`。

### 2.6 策略字段与约束

`review-policy.v16` 必填 `review_risk`；可选 `reasons`、`classifier`、`classifier_identity`、`high_risk_triggers`、`required_stages`、`reviewer_model`、`reasoning_effort`、`context_mode`、`fork_turns`、`report_only`。额外属性一律拒绝。

- `low`/`medium` **不得**声明任何 high-risk trigger，且**必须**提供 `reasons` 与 `classifier_identity`。
- `high` **必须**至少声明一个 trigger。
- 任务正式评审路由要求 `context_mode=independent_clean_room`、`fork_turns=none`、`report_only=true`。
- 作者可以写 `reviewer_model` / `reasoning_effort` 作为建议，但**解析器**按风险覆盖它们，作者无法削弱独立评审路由。

### 2.7 review-runtime 契约

每次正式派发必须编译 `review-runtime.v16`，冻结：一次评审调用、零重复全范围评审、file/line/context/tool 预算、soft/hard 截止。

- soft 截止：索取当期正式报告。
- hard 截止：中断并重规划，**不得制造裁决**。
- PARTIAL 报告或运行时超时**不能** approve。
- 范围扩张需要新的可证伪反例；否则停止漫游。
- 运行时资格永不覆盖证据门、P1/BLOCKING 门、覆盖门、lineage 门。
- 运行时/进度校验必须独立接收冻结策略与调用方自有的 context mode、精确 changed-file/line 计数、评审身份、先前制品、reviewer 连续性期望。**自我重算哈希的载荷永不是自身权威**（对应反例 NF-026）。

### 2.8 裁决合同

`APPROVE` 需要同时满足：覆盖完整（`coverage_status` 完整）、`unreviewed_scope` 为空、无活跃 P1/BLOCKING、存在匹配的调用方绑定的独立制品、证据有效。否则用 `REQUEST_CHANGES`；基础设施故障用 `null`。**`REQUEST_CHANGES` 不能通过改阈值或改分母来豁免。**

---

## 三、TOOLING-1 · 工具门

### 3.1 就绪先于路由

在仓库分析、实现、评审、收尾之前，针对**精确 repo/head/worktree/config 身份**运行严格 `tool-preflight.v16`。`status=ready` 要求三个强制工具全部通过：

| 工具 | 就绪判据 |
| --- | --- |
| CodeGraph | 为 ZCode 配置、绑定所属仓库、索引完整、revision 匹配、无污染、通过当期 sentinel |
| Semble | 已配置、可调用、repo-scoped、返回预期 live-source sentinel |
| rtk | 可调用、匹配当前 Git 身份、保留确定性非零失败 |

**目录或二进制存在不是就绪；历史通过也不是就绪。** 这是因为索引可能陈旧、可能绑定到错误仓库、可能被父图污染——这些失败模式全都在「二进制存在」的检查下静默通过。

缓存 receipt 只在 host/runtime/tool/config/repo/head/worktree/index/sentinel 身份全部不变时可复用。doctor 是只读的（hook 内的就绪探测同样只读）；**建索引/同步已预授权，由主智能体自动执行**；安装与配置变更仍需用户授权。

### 3.2 路由表（强制）

| 意图 | 首选工具 | ZCode 工具名 |
| --- | --- | --- |
| 已知 symbol / 调用 / 依赖 / 影响面 | CodeGraph | `mcp__codegraph__codegraph_explore` |
| 未知语义入口 / 相似实现 | Semble | `mcp__semble__search`、`mcp__semble__find_related` |
| 精确字符串 / 错误 / 配置 / 日志 | rg | `Grep`、`Bash` 内的 `rg` |
| 展示给模型的 shell 输出 | rtk | `Bash` 内的 `rtk` |

原始输出在以下场景仍然强制：解析器输入、密码学哈希、精确分母、字节同一性。

### 3.3 回退契约

首选工具只能在满足三条件后绕过：① 真实发生的失败/不可用尝试 ② 稳定 reason code ③ 证据引用。**回退永不声称等价的结构或语义覆盖**（`coverage_equivalent` 不得因回退而置真）。未声明的意图是 `not_declared`，不是伪造的 blocker。

### 3.4 使用绑定

`tool-usage.v16` 把每条已声明路由绑定到一次成功且**任务相关**的调用，附证据引用与 hook receipt 哈希。以下调用**不能**满足路由：未使用、错工具、失败、未声明、无 receipt。**对每个工具各打一次无关的卡不是合规**——每次调用必须实质性地决定了发现、结构/影响、上下文展示或字面真相。

hook 只强化已声明路由并存储归一化的隐私安全 receipt；不从原始参数推断语义，也不一律拒绝合法调用。CodeGraph 状态属于所属子仓库，绝不写入父图。

---

## 四、DELEGATE-1 · 委派契约

### 4.1 责任与并发

持久父智能体负全责。默认约束：`max_depth=1`、最多两个并发写专家、独占路径租约、父子不写同一文件、单一 Git owner（Qian9921）。独立只读通道可在任务显式并发与用量预算内 fan out。

### 4.2 子智能体禁令

子智能体**不得** review、approve、merge，或执行任何 Git / GitHub 动作。`delegation.v1` 的 `forbidden_permissions` 至少包含 `git`、`github`、`review`、`merge`；`permissions` 中出现这四者之一即拒绝 packet。**子完成不等于集成完成。**

### 4.3 父方校验清单

父必须校验结构化 packet 与 result：身份（`parent_task_id` / `child_task_id`）、请求与实际模型、任务、深度（`max_depth=1` 且 `depth=1`）、租约、权限、重试计数（`retry_used ∈ {0,1}`）、`changed_paths`（必须落在租约路径内）、计数算术（`total=passed+failed+skipped`、`ran=passed+failed`）。`contamination=true` 一律拒收。任一不符即 `NESTED_CHILD_CONTRACT_REJECTED`。

### 4.4 路由与回退

路由由**任务与预算**驱动，不由 slug 驱动。role 默认值是默认，不是能力禁令。非评审的执行回退只在简报授权模型集合内、且 role/权限/范围不变时合法，并必须记录请求模型、实际模型与原因。**required 独立 reviewer 没有静默回退。**

### 4.5 用量 sidecar

每个任务简报提供硬上限：`max_model_calls`、`max_review_calls`、`max_parallel_agents`、`max_input_tokens`、`max_output_tokens`、`max_total_tokens`。**超硬上限前停止。** 不得跑宽泛模型基准、重复审计，或在没有可证伪决策需求时提高推理 effort。

派发前按保守的单次调用上限预留额度；完成时用提供方报告的输入/输出计数结算，同时释放活跃 agent 槽位。计数不可得时按预留上限结算，**不得编造更小的数字**。只持久化聚合 ledger receipt。

**订阅价不是每次调用价**：在提供方暴露精确套餐级归因之前，`usd_cost` 保持 `null`/`unavailable`。不得推断 API 价格，也不得把月订阅费除以猜测的调用数。

---

## 五、V16 生产力契约

### 5.1 就绪状态机（8 态，单调 +1）

| # | 状态 | 前置条件（进入该状态必须满足） |
| --- | --- | --- |
| 1 | `DRAFT` | 初始态，由 `initial_state()` 创建，绑定 `mission_id` / `base_sha` / `tree_sha` / 冻结的 review policy |
| 2 | `COUNTEREXAMPLES_FROZEN` | 反例 ID 集合冻结；head 仍为 base |
| 3 | `BASELINE_REPRODUCED` | `counterexample_ids` 非空，`red_counterexamples` 非空且与 `counterexample_ids` 精确相等；`head_sha` 必须等于 `base_sha` |
| 4 | `IMPLEMENTING` | 唯一允许引入候选 head 的一步；此后任何 head 变化都是 `candidate head drift` |
| 5 | `INNER_AUDIT_COMPLETE` | 必须显式给出 `spark_audit_count ∈ [0,3]`；零审计时不得携带 findings/dispositions；每条 Spark finding 必须被 disposition，且取值仅限 `FIXED` / `DISAGREE` / `FOLLOW_UP` |
| 6 | `LOCAL_READY` | 存在校验过的 `author_closure_sha256`；`targeted` 阶段每阶段恰好一个绑定到已校验制品的 evidence receipt；RED 集合等于 GREEN 集合；无活跃 `FOLLOW_UP` |
| 7 | `FRESH_READY` | `required_stages` 全部阶段的 evidence receipt 与 gate receipt 齐备且身份匹配；receipt 阶段不得超出冻结策略；RED=GREEN；无活跃 `FOLLOW_UP` |
| 8 | `REVIEW_READY` | 必须提供作者 review packet + 正式独立制品 + lineage（`dispatch_transcript_sha256` / `task_id` / `parent_task_id` / `sender`）+ 独立评审覆盖范围；packet 的 `decision_basis` 必须与冻结策略身份逐项匹配 |

约束：只许 +1 跳（`illegal state jump` 否则报错）、`base_sha` 不得漂移、时间戳必须比上一态新（无补记）、每次 transition 都把 `review_ready` 与已批准制品哈希清零。**作者不得自称 review_ready**——它只能由调用方绑定的正式独立制品驱动。

### 5.2 Spark 内环审计

零上下文审计 `0..3` 次，按真实风险与范围选择，独立时可并行，未变的审计范围按精确内容哈希复用。**不为凑数而加。** 并行审计绝不产生竞争性门禁裁决——正式裁决只属于恰好一个风险路由的 report-only reviewer。

### 5.3 gate 执行约束

gate 命令是直接 argv 数组（`shell=False`），运行在自有前台进程中；禁止包管理执行、网络执行、后台执行。就绪的只读 gate 可并发；有依赖或写集冲突的必须串行。head/tree 或 snapshot 哈希、UTC 时间戳、runtime/config、日志 SHA 必须当期且机器派生。

### 5.4 渲染与指标

渲染器只产出净化后的 author/reviewer packet；不调用 GitHub、不 approve、不 merge、不部署。生产力指标由任务/证据/评审制品派生，其阈值是**策略目标而不是已达成结果**。首过通过率与 tokens/秒是诊断量，不是通过激励。

### 5.5 优化顺序

**正确性与证据有效性是硬门禁。** 第一优化目标是 `time_to_correct_verdict` 与 `time_to_correct_merge`；token 与调用成本次之。

缺失的裁决、事件、观测窗口或用量数据一律 `None`/`unavailable`，**绝不合成为零**，并对验收保持可见。

---

## 六、完整枚举表

以下枚举全部从代码读出。**任何未列于此的身份都不存在。**

### 6.1 HIGH_RISK_TRIGGERS（11）

来源：`zgov.review_policy.HIGH_RISK_TRIGGERS`

| trigger | 含义 |
| --- | --- |
| `exact_parity` | 需要与参考实现逐位/逐值一致 |
| `formal_research_release` | 正式研究性发布 |
| `hook_reviewer_model_routing` | 改动 hook、评审员或模型路由本身 |
| `irreversible_migration` | 不可逆迁移 |
| `math_numeric` | 数学/数值计算正确性 |
| `privacy` | 隐私数据处理 |
| `production_runtime` | 生产运行时 |
| `public_contract` | 对外公开契约 |
| `schema_data_format` | schema 或数据格式 |
| `security` | 安全边界 |
| `supply_chain_installer` | 供应链与安装器 |

`low`/`medium` 不得声明任何一项；`high` 必须至少声明一项。

### 6.2 _ESCALATION_TRIGGERS（16）

来源：`zgov.trace._ESCALATION_TRIGGERS`。任一成立即选择 `escalated_fresh`（换新 reviewer）。

| trigger | 含义 |
| --- | --- |
| `ACCEPTANCE_ENVELOPE_DRIFT` | 验收信封漂移 |
| `CONTRACT_DISPUTE` | 契约层面的争议 |
| `LINEAGE_LOSS` | 派发血缘丢失 |
| `MATERIAL_REWRITE` | 实质性重写 |
| `NEW_FALSIFIABLE_P1_EVIDENCE` | 出现新的可证伪 P1 证据 |
| `ORIGINAL_SCOPE_MISSED_P1` | 原范围内漏掉的 P1 |
| `PACKET_EVIDENCE_IDENTITY_INVALIDATED` | packet 证据身份失效 |
| `PATH_SET_SCOPE_DRIFT` | 路径集合范围漂移 |
| `POST_REVIEW_INCIDENT` | 评审后发生事故 |
| `PRIOR_COVERAGE_INCOMPLETE` | 先前覆盖不完整 |
| `REFERENCE_DOMAIN_THRESHOLD_DRIFT` | 参考域或阈值漂移 |
| `REVIEWER_PARTICIPATED` | 评审员参与了实现（独立性破坏） |
| `REVIEW_HOOK_ROUTING_GOVERNANCE_CHANGE` | 评审/hook/路由治理变更 |
| `REVIEW_POLICY_DRIFT` | 评审策略漂移 |
| `RISK_ESCALATION` | 风险等级升级 |
| `TWO_ROUND_NON_CONVERGENCE` | 两轮未收敛 |

### 6.3 _FINDING_LABELS（6）

来源：`zgov.trace._FINDING_LABELS`

| label | 含义 |
| --- | --- |
| `BLOCKING` | 阻断合并 |
| `CONTRACT_CHALLENGE` | 对契约本身提出挑战 |
| `FOLLOW_UP` | 需后续跟进 |
| `NIT` | 吹毛求疵级 |
| `NON_BLOCKING` | 不阻断 |
| `QUESTION` | 提问，待作者澄清 |

严重度取值：`P1`、`P2`、`P3`。**`P1 ⇒ BLOCKING`，且只有 P1 可以是 BLOCKING。**

### 6.4 _DISPOSITIONS（4）

来源：`zgov.trace._DISPOSITIONS`

| disposition | 含义 |
| --- | --- |
| `DISAGREE` | 作者不同意，附理由 |
| `FIXED` | 已修复 |
| `FOLLOW_UP` | 转为后续项 |
| `OPEN` | 未处理 |

就绪门禁额外约束：进入 `INNER_AUDIT_COMPLETE` 时 Spark disposition 只允许 `FIXED` / `DISAGREE` / `FOLLOW_UP`（不允许 `OPEN`）；进入 `LOCAL_READY` / `FRESH_READY` / `REVIEW_READY` 时**存在任一 `FOLLOW_UP` 即阻断**。

### 6.5 _BLOCKER_ADMISSIONS（3）

来源：`zgov.trace._BLOCKER_ADMISSIONS`。用于说明一个新 blocker 为何在此轮才出现。

| admission | 含义 |
| --- | --- |
| `DELTA_INTRODUCED` | 由本轮 delta 新引入 |
| `NEW_FALSIFIABLE_EVIDENCE` | 由新的可证伪证据揭示 |
| `ORIGINAL_SCOPE_MISSED` | 原范围内漏掉的（计入 P1 miss 指标） |

### 6.6 CONTEXT_MODES（4）

来源：`zgov.review_policy.CONTEXT_MODES`。门禁性由 `context_mode_is_gating()` 判定。

| mode | 门禁性 | 说明 |
| --- | --- | --- |
| `author_contextual` | **非门禁** | 仅作者预检，永远不能作为门；不能 approve |
| `independent_clean_room` | 门禁 | 全新初评，`fork_turns=none`，report-only，精选 packet；任务正式路由**必须**是它 |
| `delta_continuation` | 门禁 | 同一 reviewer 连续性身份，独立的新 run 与新裁决；只看 old→new delta、先前 findings/dispositions 与受影响证据；**不得重走原全范围** |
| `escalated_fresh` | 门禁 | 触发任一 escalation trigger 后换新 reviewer |

### 6.7 STAGE_ORDER 与 DEFAULT_STAGES

来源：`zgov.review_policy.STAGE_ORDER` / `DEFAULT_STAGES`

- `STAGE_ORDER = ("targeted", "full", "fresh")`
- `DEFAULT_STAGES = {"low": ("targeted",), "medium": ("targeted", "full"), "high": ("targeted", "full", "fresh")}`
- `required_stages` 必须是 `STAGE_ORDER` 的有序非空前缀。

### 6.8 review-runtime 三档预算画像

来源：`zgov.review_runtime._INITIAL_PROFILES` / `_DELTA_PROFILE`

| 画像 | soft (s) | hard (s) | max_files | max_changed_lines | max_context_chars | max_tool_calls |
| --- | --- | --- | --- | --- | --- | --- |
| low | 180 | 480 | 20 | 3000 | 20000 | 12 |
| medium | 180 | 480 | 20 | 3000 | 20000 | 12 |
| high | 300 | 900 | 24 | 5000 | 24000 | 16 |
| delta（`delta_continuation`） | 90 | 240 | 12 | 800 | 12000 | 8 |

这些是**路由/SLO 阈值，永远不是验收阈值**。超界只会选择更强路由或触发重规划，不能豁免或制造裁决。delta 画像被超出（files 或 lines 超限）时，路由退回初评画像。

### 6.9 review_progress_decision 的 7 档决策（按优先级顺序）

来源：`zgov.review_runtime.review_progress_decision`

| 优先级 | action | reason_code | 触发条件 |
| --- | --- | --- | --- |
| 1 | `INTERRUPT_REPLAN` | `HARD_RUNTIME_BUDGET_EXCEEDED` | tool_calls / files / context / review_calls / duplicate_full_scope_reviews 任一超限，或 `verdict ∧ review_calls ≠ max_review_calls`，或 `elapsed ≥ hard_deadline_sec` |
| 2 | `ESCALATE_FRESH` | `NEW_FALSIFIABLE_EVIDENCE` | `context_mode = delta_continuation` 且出现新可证伪证据 |
| 3 | `STOP_SCOPE_EXPANSION` | `SCOPE_EXPANSION_LACKS_COUNTEREXAMPLE` | 请求扩范围但无新可证伪证据 |
| 4 | `ACCEPT_REPORT` | `COMPLETE_REPORT_AVAILABLE` | 有裁决 ∧ 覆盖完整 ∧ `unreviewed_count = 0` |
| 5 | `RETURN_PARTIAL` | `VERDICT_WITH_INCOMPLETE_COVERAGE` | 有裁决但覆盖不完整 |
| 6 | `REQUEST_REPORT` | `SOFT_DEADLINE_REACHED` | 无裁决且 `elapsed ≥ soft_deadline_sec` |
| 7 | `CONTINUE` | `WITHIN_RUNTIME_BUDGET` | 其余情况 |

`approval_eligible` 仅在 `action = ACCEPT_REPORT` ∧ 覆盖完整 ∧ `unreviewed_count = 0` ∧ 未超预算时为真。

### 6.10 R1 负例矩阵（28 例）

来源：`zgov.r1.NEGATIVE_FAMILIES`。每一例都是一个**必须变红**的机制性负例。

| id | family | 机理 |
| --- | --- | --- |
| `NF-001` | spark-fabricated-task | 编造的任务身份缺少 transcript 绑定 |
| `NF-002` | spark-missing-result | 缺失的 Spark 结果没有精确制品 |
| `NF-003` | spark-fourth-audit | 第四次审计超出恰好三次的预算 |
| `NF-004` | gate-head-mutation | gate 变更了候选 head 或 tree |
| `NF-005` | gate-disconnected-component | 断连的 gate 不在已执行 DAG 内 |
| `NF-006` | shell-absolute-alias | 绝对路径 basename 或 shell 模式绕过 |
| `NF-007` | invariant-without-acceptance | 阻断性不变量没有对应验收行 |
| `NF-008` | fresh-bypasses-full | fresh 阶段跳过传递性的 full 前置 |
| `NF-009` | missing-green-or-p1-followup | 就绪放行了缺失 GREEN 或活跃 P1 FOLLOW_UP |
| `NF-010` | fake-receipt | 就绪接受了任意的证据/gate receipt ID |
| `NF-011` | inherited-secret | runner 继承了带密钥的环境 |
| `NF-012` | socket-access | runner 允许网络/socket 访问 |
| `NF-013` | background-survivor | 超时后仍有子进程存活 |
| `NF-014` | skipped-green | skip 行被当作绿 |
| `NF-015` | tree-mismatch | 行的 tree 与信封 tree 不一致 |
| `NF-016` | timestamp-contradiction | 时间戳与 elapsed 字段自相矛盾 |
| `NF-017` | expected-denied-string | 真值非布尔的 `expected_denied` 绕过类型契约 |
| `NF-018` | independent-approval-fabrication | 作者 packet 合成了 APPROVE |
| `NF-019` | metrics-missing-or-nan | 缺失来源或非有限指标被接受 |
| `NF-020` | manifest-row-deletion | manifest 遗漏未被判为 exact-set RED |
| `NF-021` | gate-missing-artifact | gate receipt 漏掉一条必需的已编译 entrypoint 行 |
| `NF-022` | gate-duplicate-artifact | gate receipt 重复一条 entrypoint 行并漏掉另一条 |
| `NF-023` | review-runtime-partial-approval | 不完整的评审覆盖被判为可批准 |
| `NF-024` | review-runtime-hard-timeout | hard 截止后仍允许继续或批准 |
| `NF-025` | review-runtime-delta-roam | delta 评审在无新可证伪证据时扩范围 |
| `NF-026` | review-runtime-policy-rehash | 自洽的 high→medium 路由改写并重算哈希绕过冻结策略 |
| `NF-027` | review-runtime-delta-underreport | 调用方低报精确 changed-file / changed-line 分母 |
| `NF-028` | review-runtime-hidden-budget | 进度上报隐藏了 context 或重复正式评审预算 |

---

## 七、占位符与冻结事实

### 7.1 roles.json（`gov-roles.v1`）

路径解析顺序：`ZGOV_ROLES_PATH` → `$ZCODE_HOME/gov-config/roles.json` → `~/.zcode/gov-config/roles.json`。文件不存在时返回内置占位默认值。

顶层键恰好四个：`schema`、`roles`、`efforts`、`agents`。

| 段 | 键 | 约束 |
| --- | --- | --- |
| `roles` | `writer`、`executor`、`reviewer_standard`、`reviewer_high`、`auditor_spark` | 键集合必须**精确等于**这 5 个；值为非空字符串 |
| `efforts` | `reviewer_standard`、`reviewer_high`、`delta_continuation`、`auditor_spark` | 键集合必须精确等于这 4 个；值 ∈ `{low, medium, high, xhigh}`；**不得**为占位符；`delta_continuation` 必须等于 `reviewer_standard` |
| `agents` | 任意合法 key | 值为非空字符串；占位符视为未配置（`agent_for()` 返回 `None`） |

占位符格式：`<TBD>` 或 `<TBD:suffix>`（`suffix` 匹配 `[A-Za-z0-9_.-]+`）。

**未解析时的行为**：`resolve_role()` 抛 `RolePlaceholderUnresolved`，错误码 `ROLE_PLACEHOLDER_UNRESOLVED`。

| 能力 | roles 未解析时 |
| --- | --- |
| 校验 schema / 契约 / 计数算术 | 可用 |
| 编译任务计划、跑 gate、生成证据信封 | 可用 |
| 编译 `review-runtime.v16`（模型字段为占位符） | 可用，但不可作为裁决权威 |
| 产出带裁决的制品（`independent-review.v16`、readiness 到 `REVIEW_READY`） | **fail-closed 阻断** |

当前 `gov/roles.example.json` 的五个 role 均已解析为真实模型标识：`writer`→`tuzi-direct-1m/claude-tuzi/claude-opus-5`、`executor`→`e8ed5e30-e95d-45dc-b265-37acf2ba2583/deepseek-v4-flash`、`reviewer_standard`→`tuzi-direct-1m/claude-tuzi/claude-fable-5`、`reviewer_high`→`tuzi-direct-1m/claude-tuzi/claude-fable-5`、`auditor_spark`→`tuzi-direct-1m/claude-tuzi/claude-fable-5`；`agents` 映射为 `reviewer_standard`→`gov-reviewer`、`reviewer_high`→`gov-reviewer`、`executor`→`gov-executor`、`auditor_spark`→`gov-spark-audit`。安装器只在 `gov-config/roles.json` 缺失时用它做种子，已存在则永不覆盖。

### 7.2 milestone.json（`gov-milestone.v1`）

路径解析同上（`gov-config/milestone.json`）。字段：

| 字段 | 说明 |
| --- | --- |
| `frozen` | 严格布尔。为 `false` 时任务事实未冻结 |
| `milestone_id` / `repo` | 冻结时必填 |
| `base_sha` / `base_tree` | 40 位 SHA；冻结时必须成对提供 |
| `spark.audit_ids` | 冻结时必须等于 `spark.expected_raw_platform_sha256` 的键集合 |
| `spark.normalized_finding_ids` | 键必须是 `audit_ids` 的子集 |
| `spark.author_closure_denominator` | 必须等于 `len(spark.author_finding_ids)` |
| `spark.historical_findings` / `expected_historical` / `expected_current` | 历史与当期审计期望 |
| `transcript.*` | `mission_scope_sha256`、`compiled_plan_sha256`、`audited_parent_sha`、`author_closure_plan_sha256` |
| `gate_stage_map` | gate id → stage（`targeted` / `full` / `fresh`） |

**未冻结时的行为**：`require_frozen()` 抛 `MilestoneNotFrozen`。依赖冻结基线身份的能力（基线复现校验、dispatch transcript 绑定、Spark 闭环校验、`REVIEW_READY` 的 lineage 绑定）全部 fail-closed 阻断；不依赖冻结事实的 schema 校验与本地 gate 仍可用。

---

## 八、ZCode 移植说明（与 Codex v16 的差异）

### 8.1 hook 事件映射

ZCode 恰好有 7 个 hook 事件：`SessionStart`、`UserPromptSubmit`、`PreToolUse`、`PermissionRequest`、`PostToolUse`、`PostToolUseFailure`、`Stop`。

| Codex v16 语义 | ZCode 实现 |
| --- | --- |
| 子智能体启动守卫（Codex 的 SubagentStart） | ZCode **没有** SubagentStart 事件；映射为 `PreToolUse` + matcher `Agent` |
| 子智能体结果校验 | `PostToolUse` + matcher `Agent` |
| 命令拦截 / 改写 | `PreToolUse` + matcher `Bash` |
| 文件凭据守卫 | `PreToolUse` + matcher `Read\|Edit\|Write` |
| 会话开局注入 | `SessionStart` |

matcher 契约：大小写敏感正则；匹配的是工具名；省略 matcher 匹配一切；**无效正则静默地永不匹配**（因此 matcher 写错不会报错，只会失效——这是必须警惕的失败模式）。工具名别名：`Task → Agent`、`ApplyPatch → Write|Edit`。

### 8.2 hook stdout 严格 schema

只允许 5 个键：`hookEventName`、`permissionDecision`、`permissionDecisionReason`、`additionalContext`、`updatedInput`。任何额外键都会导致校验失败。退出码：`0`=pass、`2`=block、其他=error。`additionalContext` 长度上限 1200。receipt 落盘于 `~/.zcode/hooks/receipts/<UTC-date>.jsonl`。

### 8.3 工具名

Codex 版的抽象工具名在 ZCode 中落为具体工具：`mcp__codegraph__codegraph_explore`、`mcp__semble__search`、`mcp__semble__find_related`、`Bash`、`Grep`、`Read`、`Agent`。

### 8.4 安装语义

| 位置 | 内容 | 可写性 |
| --- | --- | --- |
| `~/.zcode/gov/` | 托管治理包（本仓库 `gov/` 的安装产物） | 由安装器管理，勿手改 |
| `~/.zcode/gov-config/` | 用户自有 `roles.json`、`milestone.json` | 用户所有，安装器不覆盖 |
| `~/.zcode/AGENTS.md` | 由 `gov/AGENTS.md` 安装 | 安装器管理 |
| `~/.zcode/agents/*.md` | 子智能体定义 | 安装器管理 |
| `~/.zcode/cli/config.json` 的 `hooks.events` | hook 注册（权威来源） | 需授权；`gov/hooks/hooks.json` 只是给人和安装器看的声明式清单 |
| `~/.zcode/hooks/receipts/` | 审计日志 | 只追加 |

安装器必须：版本化、allowlist、支持 dry-run、原子、有备份、校验哈希与权限、可回滚。

### 8.5 模型 role 参数化

Codex v16 原文在策略正文中硬编码了具体模型名与 reviewer 路由。ZCode 版**全部改为 role 引用**（`writer` / `executor` / `reviewer_standard` / `reviewer_high` / `auditor_spark`），由 `roles.json` 解析。规范文本中不出现任何具体模型名；`gov-reviewer` 与 `gov-executor` 是 **agent 名**，通过 `roles.json` 的 `agents` 映射与 role 关联。

### 8.6 冻结事实参数化

Codex v16 把 base SHA、tree SHA、Spark 审计 ID 与期望哈希硬编码在 `codex/v16/` 常量中。ZCode 版全部抽到 `milestone.json`（`gov-milestone.v1`），并以 `frozen` 布尔控制 fail-closed 行为，使同一套代码可服务多个仓库与多个里程碑。

### 8.7 命名空间

Python 包从 `codex.v16.*` 更名为 `zgov.*`（安装后为 `~/.zcode/gov/zgov/`）。所有策略引用统一写 `zgov.<module>.<SYMBOL>`。

---

## 九、本文档的可执行事实来源

| 本文枚举 / 表 | 代码文件 | 符号 |
| --- | --- | --- |
| §6.1 HIGH_RISK_TRIGGERS（11） | `gov/zgov/review_policy.py` | `HIGH_RISK_TRIGGERS` |
| §6.2 ESCALATION_TRIGGERS（16） | `gov/zgov/trace.py` | `_ESCALATION_TRIGGERS` |
| §6.3 FINDING_LABELS（6） | `gov/zgov/trace.py` | `_FINDING_LABELS` |
| §6.4 DISPOSITIONS（4） | `gov/zgov/trace.py` | `_DISPOSITIONS` |
| §6.5 BLOCKER_ADMISSIONS（3） | `gov/zgov/trace.py` | `_BLOCKER_ADMISSIONS` |
| §6.6 CONTEXT_MODES（4）与门禁性 | `gov/zgov/review_policy.py` | `CONTEXT_MODES`、`context_mode_is_gating` |
| §6.7 STAGE_ORDER / DEFAULT_STAGES | `gov/zgov/review_policy.py` | `STAGE_ORDER`、`DEFAULT_STAGES` |
| §2.5 fail-closed legacy 路由 | `gov/zgov/review_policy.py` | `_legacy_policy`、`resolve_review_policy` |
| §2.6 策略字段集合 | `gov/zgov/review_policy.py` | `POLICY_FIELDS`、`RISK_LEVELS`、`validate_review_policy` |
| §6.8 三档 + delta 预算画像 | `gov/zgov/review_runtime.py` | `_INITIAL_PROFILES`、`_DELTA_PROFILE` |
| §6.9 7 档决策 | `gov/zgov/review_runtime.py` | `review_progress_decision` |
| §6.10 R1 负例矩阵（28） | `gov/zgov/r1.py` | `NEGATIVE_FAMILIES` |
| §5.1 8 态状态机与前置条件 | `gov/zgov/state.py` | `STATES`、`_ALLOWED`、`transition` |
| §7.1 roles.json 契约 | `gov/zgov/roles.py` | `ROLE_NAMES`、`EFFORT_NAMES`、`VALID_EFFORTS`、`DEFAULT_ROLES`、`validate_roles`、`RolePlaceholderUnresolved` |
| §7.2 milestone.json 契约 | `gov/zgov/milestone.py` | `MILESTONE_SCHEMA`、`TRANSCRIPT_FIELDS`、`validate_milestone`、`require_frozen` |
| §4.2–4.3 委派契约 | `gov/hooks/delegation_contract.py` | `REQUIRED_PACKET`、`REQUIRED_RESULT`、`validate_packet`、`validate_result` |
| §8.1–8.2 hook 事件、matcher、stdout schema | `gov/hooks/hooks.json`、`gov/hooks/zcode_hook.py` | `events`、`matcher_contract`、`output_contract` |
| §1.1 mission.v16 字段集合 | `gov/zgov/contracts/schema_registry.v16.json` | `schemas["mission.v16"]` |
| §2.8 裁决合同字段 | `gov/zgov/contracts/schema_registry.v16.json` | `schemas["independent-review.v16"]` |
