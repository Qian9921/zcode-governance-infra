# 架构

本包分八层：前六层继承自 Codex v16，后两层是 ZCode 移植专属。

1. 规范与简报模板层；
2. 严格契约层（mission / evidence / review 契约）；
3. 工具就绪与路由层（CodeGraph / Semble / rtk / rg 的确定性路由与健康证据）；
4. 内容寻址的 FAST / CANDIDATE / FINAL 执行引擎；
5. 能力中立的模型路由与硬 call/concurrency/token 预算；
6. 隐私安全 hook 与 allowlist 安装器 / 验证器；
7. **参数化层**（role 与 milestone）；
8. **ZCode hook 映射层**。

---

## 1. 规范与简报模板层

`gov/AGENTS.md`、`gov/POLICY.md`、`gov/BRIEF-TEMPLATES.md`。

`AGENTS.md` 与 `agents/*.md` 是 **sidecar**：既留在托管副本 `$ZCODE_HOME/gov/` 里，也逐文件
写到 `$ZCODE_HOME/AGENTS.md` 与 `$ZCODE_HOME/agents/*.md`，让 ZCode 客户端能直接读到。

> 这三个文件与 `gov/agents/` 由并行任务提供，本文只描述它们在架构中的位置，不描述其正文。

## 2. 严格契约层

`gov/zgov/contracts.py` + `gov/zgov/contracts/schema_registry.v16.json`。

注册表共 **26 条 schema**，每条声明一个 `validation_mode`，三种取值：

| `validation_mode` | 条数 | 含义 |
|---|---|---|
| `standalone` | 10 | 单独一个对象即可完成校验 |
| `caller-bound` | 12 | 必须由调用方额外提供绑定输入（上游 identity / packet / 期望值）才能校验 |
| `source-bound` | 4 | 必须重新从源数据推导才能校验（不接受被校验对象的自述） |

26 条 schema 及其 mode：

```text
standalone (10)   mission.v16  review-policy.v16  invariant.v16  counterexample.v16
                  gate.v16  acceptance.v16  spark-audit-request.v16
                  review-packet.v16  tool-route-decision.v16  tool-health.v16
caller-bound (12) spark-audit-result.v16  compiled-plan.v16  gate-result.v16
                  closure-binding-receipt.v16  pre-execution-closure-authority.v16
                  readiness-state.v16  evidence-envelope.v16  independent-review.v16
                  review-runtime.v16  review-runtime-progress.v16
                  dispatch-transcript.v16  tool-usage.v16
source-bound (4)  metrics.v16  review-efficiency.v16  negative-matrix.v16
                  tool-preflight.v16
```

分级的意义：`caller-bound` 与 `source-bound` 的对象**不能自证**。一个只有 verdict 的
`independent-review.v16` 无法通过 readiness；一个 `tool-preflight.v16` 报告必须能从源观测
重新推导出同样的 counts 与 cache key。

## 3. 工具就绪与路由层

`gov/zgov/tool_preflight.py`（Gate 1，深度就绪）与 `gov/zgov/tool_routing.py`（Gate 2，路由
与实际使用）。

工具路由与模型路由**刻意分离**。声明为 known symbol / call / dependency / blast-radius 的
意图选中当前 revision 的 CodeGraph；语义发现与相似实现选中 Semble；精确文本选中 `rg`；
展示给模型的 shell 输出选中 `rtk`。

路由原语是纯 stdlib 策略对象：**不启动工具、不改索引、不执行 shell**。它只校验选择、
四工具健康分母，以及 evidence-backed 的 fallback。CLI 探针覆盖 CodeGraph/rtk/rg；Semble 是
MCP 能力，在 orchestrator 提供真实能力观测之前保持 `unknown`。

`normalize_zcode_tool()` 把 ZCode 工具名归一化到 `TOOLS = ("codegraph","semble","rtk","rg")`：
MCP 精确名与 `mcp__codegraph*` / `mcp__semble*` 前缀直接命中；`Grep`/`grep`/`ripgrep` → `rg`；
`Bash` 仅在**单条简单命令**且首 token basename 为 `rtk`/`rg` 时归一化，复合命令
（含 `| & ; > < \` $(` 或换行）一律返回 `None`。归一化是**纯词法**的，不表达允许/健康/
语义恰当性。

hook 记录归一化后的 route/reason code，绝不记录 raw prompt、参数、cwd、token、credential
或私人标识。语义意图无法从每一次底层调用可靠推断，因此强制手段是「brief 显式声明 + 机械
的可用性/fallback 校验」，而不是一刀切拒绝。

详见 [TOOLCHAIN.md](TOOLCHAIN.md)。

## 4. 内容寻址的 FAST / CANDIDATE / FINAL 执行引擎

`gov/zgov/compiler.py`、`runner.py`、`evidence.py`、`state.py`、`presubmit.py`。

`compiler.py` 的 `EXECUTION_TIERS`：

```text
FAST      → ("targeted",)
CANDIDATE → ("targeted", "full")
FINAL     → ("targeted", "full", "fresh")
```

每一档都与 plan 冻结的 `required_stages` 前缀求交，因此低风险路由不会仅仅因为调用方选了
FINAL 就获得更晚的 gate。

FAST 刻意没有 commit/clean-tree 前置条件：`GateRunner` 把它绑定到精确的 staged 或 worktree
内容快照。dirty receipt 只有在 runner 记录了 snapshot mode/hash **且**消费者独立提供同一
hash 时才有效。CANDIDATE 在 clean exact candidate 上补齐冻结路由剩余的本地 gate。FINAL 再
加上仍然必需的 fresh portability flow 与一次风险路由的独立评审。评审路由与证据分档是正交的。

可复用 gate 按内容快照、编译后 plan、argv、完整的非机密有效环境输入、cwd、timeout、
解释器、runtime 版本做缓存。runner 制品落在仓库之外，使 receipt 写入不污染内容 identity；
执行后 runner 重新哈希所选快照并拒绝漂移。

staged 模式执行一次隔离的本地物化，其 HEAD、index 与 worktree identity 必须保持不变；它
从不执行未 staged 的 worktree 字节。staged 物化保持串行，因为同一快照上的 gate 否则会争抢
唯一的确定性执行根。mission entrypoint 与 gate 只能通过**双方**都显式声明
`read_only: true` 才能在 worktree 或 clean-candidate 模式下进入有界并行。

`state.py` 的就绪状态机是 8 态、单步向前：

```text
DRAFT → COUNTEREXAMPLES_FROZEN → BASELINE_REPRODUCED → IMPLEMENTING
      → INNER_AUDIT_COMPLETE → LOCAL_READY → FRESH_READY → REVIEW_READY
```

`_ALLOWED` 只允许严格的下一态，跳级或回退都被拒绝。

### runner 沙箱三件套

这是**全系统唯一的运行时强制点**，其余所有模块都是校验器（validator）：它们检查别人交上来
的记录是否自洽，但不亲自约束进程。因此这三件套单独成段。

**（一）环境白名单：不继承 PATH / 代理 / 凭据。**
`GateRunner._capture_environment` 从宿主环境里**只**取 `LANG`、`LC_ALL`、`TZ` 三个键，然后
强制写入 `PATH=/usr/local/bin:/usr/bin:/bin`、`PYTHONUNBUFFERED=1`、
`PYTHONDONTWRITEBYTECODE=1`、`PYTHONNOUSERSITE=1`。宿主 `PATH` 从不继承（argv 会被归一化到
精确解释器路径）。entrypoint 自己声明的 `env` 若出现 `HOME`、`ZCODE_HOME`、`CODEX_HOME`、
`PYTHONPATH`、`ALL_PROXY`/`HTTP_PROXY`/`HTTPS_PROXY`（及小写形式）任意一个，直接抛
`forbidden environment override`；声明键不在白名单内抛 `environment key outside explicit
allowlist`。捕获后的环境是 `MappingProxyType`，缓存 identity 与 `Popen` 用的是同一份冻结
副本。

**（二）注入 `sitecustomize`：封 socket + 封进程组逃逸。**
`_prepare_environment` 在 runner 私有制品目录下生成 `offline-site/sitecustomize.py`
（0600），内容做三件事：

- `socket.socket` 与 `socket.create_connection` 被替换为抛
  `offline socket denied` / `offline network denied` 的桩；
- `os.setsid` 与 `os.setpgid` 被替换为抛 `process-group escape denied`；
- `subprocess.Popen` 被 `_GuardedPopen` 子类替换：只要传了 `start_new_session=True` 或
  非空 `preexec_fn`，构造时即抛 `process-group escape denied`。

被测进程因此既不能联网，也不能脱离 runner 拥有的进程组。

**（三）TERM → KILL 进程组终止收据。**
gate 进程本身由 runner 以 `start_new_session=True` 启动（runner 自己拥有该组，被测代码则被
禁止再开新组）。`_terminate_group_receipt` 独立于 leader 的 `poll()` 状态检查进程组：先
`killpg(SIGTERM)`，有界等待，若组仍存在则 `killpg(SIGKILL)`，最后返回结构化三元组
`(no_survivor, term_sent, kill_sent)` 写入 gate receipt。`PermissionError`（无法证明所有权）
被当作**存活**处理——fail closed。父进程提前退出而孤儿子进程忽略 TERM 的情况因此无法伪装成
干净退出。

## 5. 能力中立的模型路由与硬预算

`gov/zgov/metrics.py`。

`choose_model(task_kind, risk, authorized_models, live_models, preferences)` **不看模型名的
形态**（不做 slug 规则）。它按 brief 授权顺序遍历，跳过 `available is not True` 的模型，
跳过 `risks` 非空且不含当前 risk 的模型，再按 `(-偏好分, token_cost_rank, 授权顺序)` 取最
小值。`token_cost_rank` 是**相对**的当期元数据，绝不发明美元价格。

`BudgetLedger` 是硬本地上限，**它本身从不发起模型调用**。它累计计数 call，在 dispatch 前
按保守上界预留 token，事后用 provider 上报的实际 input/output 结算，分别跟踪 active 与
peak 并发，且绝不虚构 USD cost。初始用量若已超预算，构造即抛
`initial routing usage exceeds budget`。

## 6. 隐私安全 hook 与安装器 / 验证器

`gov/hooks/` 与 `scripts/`。

`hook_receipt.py` 用 `_SAFE_FIELDS` 做**白名单投影**（20 个字段），标识符只存 sha256，
receipt 目录 0700、文件 0600、`O_NOFOLLOW`、拒绝 symlink。

`scripts/install-governance.py` 用 manifest 声明的 allowlist 复制，逐文件校验 hash，拒绝
路径逃逸与 symlink。`scripts/verify-governance.py` 做精确 path/hash 边界校验加隐私正则扫描。
`scripts/register-hooks.py` 只增删 `hooks.events` 下指向 `zcode_hook.py` 的条目，其余键
逐字节保留并在写后校验。

详见 [deployment.md](deployment.md) 与 [privacy-threat-model.md](privacy-threat-model.md)。

---

## 7. 参数化层（ZCode 专属）

`gov/zgov/roles.py` 与 `gov/zgov/milestone.py`。

### 为什么要参数化

Codex 版把两类**部署相关的事实**硬编码进了代码：

- 模型身份（`gpt-5.6-terra` / `gpt-5.6-sol` / `gpt-5.6-luna` / spark 一档）；
- 某次具体 milestone 的冻结事实（`spark.py` 里的 3 个平台哈希、19 项 finding、分母 18；
  `presubmit.py` 里的 `BASE_SHA` / `BASE_TREE`）。

这两类事实在 ZCode 上都不成立：ZCode surface 暴露的模型集合不同，而 milestone 是本仓库
自己的、尚未发生的事实。硬编码会造成两种坏结果——要么代码报假绿（拿别人的哈希当自己的
真值），要么必须改代码才能部署（治理包不可移植）。因此二者都下沉为**用户自有配置**：

| 事实 | 配置文件 | 出厂值 |
|---|---|---|
| 模型身份 | `$ZCODE_HOME/gov-config/roles.json` | 五个 role→模型映射（未配置时用出厂默认；占位符 `<TBD:*>` 会阻断裁决） |
| milestone 冻结事实 | `$ZCODE_HOME/gov-config/milestone.json` | `"frozen": false` |

安装器对 `gov-config/` 的语义是「仅缺失时生成，永不覆盖」，所以升级治理包不会冲掉用户填好
的值。

### fail-closed 边界在哪

**role 侧。** `roles.py` 区分两种读法：

- `role_or_placeholder(name)` 返回原始值（可能是占位符）——用于展示与结构校验；
- `resolve_role(name)` 遇到占位符抛 `RolePlaceholderUnresolved`，消息以
  `ROLE_PLACEHOLDER_UNRESOLVED:` 开头；
- `require_roles_resolved()` 只要有任意一个 role 未解析就抛，并列出全部未解析 role。

因此边界是：**结构校验照常运行**（模块在占位符状态下跳过 identity 断言，测试可跑），但
**带裁决的制品被阻断**。`efforts` 不允许占位符，且 `delta_continuation` 必须等于
`reviewer_standard`——低/中风险的 delta 续审必须同时满足两条 effort 规则。

**milestone 侧。** `milestone.py` 同样两种读法：

- `is_frozen()` 返回布尔，不抛异常——用于展示与「本次是否 skip」的判断；
- `require_frozen()` 未冻结时抛 `MilestoneNotFrozen("MILESTONE_NOT_FROZEN: ...")`。

`require_frozen()` 的调用点即 fail-closed 边界，当前有 5 处：`spark.py` 四处（dispatch
transcript、author closure、closure binding receipt 及相关信任链校验）与 `presubmit.py`
一处（整个 presubmit 流程）。**未冻结时这些校验绝不返回绿，而是抛出异常。**
`r1.py` 的负例矩阵不抛异常，而是把相关用例记为 `SKIPPED_MILESTONE_NOT_FROZEN`，计入
`skipped` 而非 `passed`——分母保持可见、不虚增通过数。

## 8. ZCode hook 映射（ZCode 专属）

### 7 个事件

ZCode 只有恰好 7 个 hook 事件：`SessionStart`、`UserPromptSubmit`、`PreToolUse`、
`PermissionRequest`、`PostToolUse`、`PostToolUseFailure`、`Stop`。

Codex 版依赖 `SessionStart` / `SubagentStart` / `PreToolUse` 三个事件。**ZCode 没有
`SubagentStart`**，所以委派语义必须重新落点：

| Codex 语义 | ZCode 落点 |
|---|---|
| SubagentStart ACTIVE-MISSION-LOCK（派发前守卫） | `PreToolUse` + matcher `Agent`（子命令 `pre-tool`） |
| 子结果校验（派发后校验） | `PostToolUse` + matcher `Agent`（子命令 `post-agent`，不阻断） |

深度控制由 `hooks.json` 的 `delegation` 块声明：`max_depth: 1`、`spawn_tool: "Agent"`、
`depth_env: "ZGOV_AGENT_DEPTH"`、`nested_spawn: "denied"`。

### matcher

matcher 是**大小写敏感的正则**，对工具事件匹配**工具名**；省略 matcher 匹配一切；
非法正则静默地永不匹配。别名：`Task` ↔ `Agent`，`ApplyPatch` → `Write|Edit`。

当前注册（`gov/hooks/hooks.json`）：

```text
SessionStart                                             → session-start (20000 ms)
PreToolUse  Bash                                         → pre-bash     (30000 ms)
PreToolUse  Read|Edit|Write                              → pre-file     ( 5000 ms)
PreToolUse  Agent|mcp__codegraph.*|mcp__semble.*|Grep    → pre-tool     (10000 ms)
PostToolUse Agent                                        → post-agent   ( 5000 ms)
```

`hooks.json` 是给人与安装器看的声明式清单；**权威注册**在
`$ZCODE_HOME/cli/config.json` 的 `hooks.events` 下，由 `scripts/register-hooks.py` 写入，
并把 `hooks.enabled` 置为 `true`（配置文件 hook 默认关闭）。

### 严格 stdout schema

ZCode 解析 hook 的 stdout 为 JSON，且 schema **严格**：只允许

```text
hookEventName | permissionDecision | permissionDecisionReason | additionalContext | updatedInput
```

**多一个键，整个 hook 效果被丢弃**（不是「忽略多余键」，而是整体失效）。

这一点直接改变了 Codex 版的设计：Codex hook 的 stdout 是自由字段，可以顺手带上 `route`、
`reason_code`、`receipt_status` 等诊断信息。ZCode 下这些字段全部**下沉到 receipt JSONL**
（`~/.zcode/hooks/receipts/<UTC-date>.jsonl`），stdout 只保留决策本身。

配套约定：

- 退出码恒为 `0`（`review-pass` 辅助命令除外）；deny 通过 `permissionDecision: "deny"`
  表达，而不是靠退出码 2；
- rtk 改写只输出 `updatedInput`，**不带** `permissionDecision`，用户正常的权限确认流程
  不受影响；
- 放行时也不输出 `permissionDecision: "allow"`——安静放行，把权限决定交还给用户；
- `additionalContext` 有 1200 字符上限（`session_context.ADDITIONAL_CONTEXT_LIMIT`）。

---

## 可执行来源

`gov/zgov/review_policy.HIGH_RISK_TRIGGERS` 与 `gov/zgov/trace._ESCALATION_TRIGGERS` 是风险
与 fresh-review 升级身份的**可执行来源**。文档可以解释这些枚举，但不能扩展它们。
同理，`gov/zgov/tool_preflight.py` 是 reason code 的权威来源，
`gov/zgov/tool_routing.PREFERRED_TOOL` 是意图路由的权威来源。
