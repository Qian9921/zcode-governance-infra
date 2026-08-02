# ZCode Governance Infrastructure v16

简体中文 · [English](README.md)

这是一套只面向 **ZCode 客户端**的治理 starter：在不牺牲正确性、证据和 GitHub 审批边界的
前提下，加快开发与审查。它由 Codex Governance Infrastructure v16 移植而来，Python 包名从
`codex.v16` 改为 `zgov`，运行时环境从 `$CODEX_HOME` 改为 `$ZCODE_HOME`。

本包提供：

- 可持续的 ZCode 全局规则与任务 brief（`gov/AGENTS.md`、`gov/POLICY.md`、
  `gov/BRIEF-TEMPLATES.md`）；
- affected-first 测试（FAST / CANDIDATE / FINAL 三级证据分层），而不是动不动全量 rebuild；
- CodeGraph、Semble、`rtk` 的强制就绪检查（`tool-preflight.v16`）与实际使用证据
  （`tool-usage.v16`）；
- 单一风险路由的独立 reviewer，以及同 reviewer 的 delta-only 复审；
- privacy-safe hook receipts（白名单投影，写入 `~/.zcode/hooks/receipts/`）；
- 确定性 package verifier、隔离试用安装器和可逆的 hook 注册器。

它不声称兼容 Codex、Claude Code、Kimi Code 或其他 agent runtime。

> **安全边界**
>
> `$ZCODE_HOME` 本身是活动目录（`cli/` 存配置与数据库、`hooks/receipts/` 存追加写的
> receipt、`server/` 与 `v2/` 存运行时状态），因此安装器**绝不重命名、替换或删除**它。
>
> - **只有 `$ZCODE_HOME/gov/` 一个目录**做整目录原子替换；
> - `$ZCODE_HOME/AGENTS.md` 与 `$ZCODE_HOME/agents/*.md` 逐文件写入，写前把已存在的文件
>   复制为 `<name>.zgov-backup`；
> - `$ZCODE_HOME/gov-config/` 属于用户：文件**仅在缺失时**生成，**永不覆盖**、永不备份、
>   永不删除。
>
> 仓库不会复制 credential、session、memory、plugin、connection、model cache 或其他私人
> 数据。

## 十分钟上手

### 1. Clone 并验证

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

只有 verifier 输出 `"status":"GREEN"`，且三个测试套件均为零 failure、零 error 时才继续。

### 2. 缺少工具时安装

安装软件前先审阅 upstream 指令：

```bash
# CodeGraph
npm install -g @colbymchenry/codegraph

# Semble
uv tool install semble

# rtk
cargo install --git https://github.com/rtk-ai/rtk
```

Upstream：

- [CodeGraph](https://github.com/colbymchenry/codegraph)
- [Semble](https://github.com/MinishLab/semble)
- [rtk](https://github.com/rtk-ai/rtk)

### 3. 配置 ZCode

ZCode 的 MCP 配置是 **JSON**，位于用户配置文件 `$ZCODE_HOME/cli/config.json` 的
`mcp.servers` 键下（Codex 版用的是 `config.toml` 的 `[mcp_servers.x]` TOML 表）。可以在
客户端 **Settings → MCP** 里查看状态并修复，也可以直接编辑配置文件：

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

> 配置文件里的 MCP server schema 是**严格**的：多一个未知键，该 server 会被整体丢弃；
> 配置文件里的 `${...}` 模板变量**不会**展开，请使用绝对路径。
> 具体命令与参数以各工具自身的安装/初始化文档为准；`tool-preflight.v16` 只检查
> `mcp.servers`（以及 `mcp.mcpServers`、顶层 `mcpServers` 等兼容布局）里是否存在名字包含
> `codegraph` / `semble` 的 server。

`rtk` 不是 MCP，而是 shell 输出包装器，按其自身的初始化流程配置即可。

审阅配置变化后，**重启 ZCode 客户端并开启新会话**。

### 4. 准备当前仓库的 CodeGraph 索引

```bash
codegraph status --json .
```

仓库尚未初始化且已授权索引时：

```bash
codegraph init .
```

结构改动后，仅在已授权时同步：

```bash
codegraph sync .
```

索引只属于 owning repository，绝不能把父 workspace 的图当作 child repo 真值。

### 5. 运行严格 toolchain doctor

```bash
python3 scripts/toolchain-doctor.py \
  --repo . \
  --semantic-query "deterministic inspection intent router" \
  --expected-path gov/zgov/tool_routing.py
```

唯一通过条件是退出码 `0`、`"status":"ready"`、分母 `3/3`：

- CodeGraph 已配置、绑定当前 repo、索引完整且 fresh，并能找到预期当前源码；
- Semble 已配置、可调用、repo scope 正确，语义 query 能返回预期源码；
- `rtk` 能复现当前 Git identity，并保持确定性失败命令的非零退出码。

binary 存在不等于就绪。doctor 只读，只保存 hash 与 reason code，不保存 raw output、
绝对路径、prompt、环境变量或 credential。完整 reason code 表见
[docs/TOOLCHAIN.md](docs/TOOLCHAIN.md)。

### 6. 在隔离目录试安装

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

预期：

- dry-run 给出 managed file denominator；
- 所有安装文件只在隔离目录；
- 真实的 ZCode home 完全没有被修改。

Rollback：

```bash
python3 scripts/install-governance.py --source . --zcode-home "$ZGOV_TRIAL_HOME" --rollback
```

只有之前 `$ZCODE_HOME/gov` 确实被备份为 `gov.zgov-backup` 时才能 rollback。

### 7. 正式安装并注册 hook

```bash
python3 scripts/install-governance.py --source . --zcode-home "$HOME/.zcode" --dry-run
python3 scripts/install-governance.py --source . --zcode-home "$HOME/.zcode"

python3 scripts/register-hooks.py --dry-run
python3 scripts/register-hooks.py
```

`register-hooks.py` 只增删 `hooks.events` 下 `args[0]` 指向 `zcode_hook.py` 的条目，并把
`hooks.enabled` 置为 `true`（配置文件 hook 默认关闭）。其余每一个键——包括 `provider`、
`model`、`modelCatalog`、`plugins`、`mcp` 以及 `hooks` 下所有非 `events` 键——都逐字节保留
并在写入后校验。它从不打印任何配置的**值**，只打印键名与计数。

完整流程与回滚见 [docs/deployment.md](docs/deployment.md)。

### 8. 填写模型 role

```bash
$EDITOR "$HOME/.zcode/gov-config/roles.json"
```

见下面的「模型 role 占位符」一节。**不填就不能出带裁决的制品。**

## 「强制工具」的两道门

### Gate 1：就绪（`tool-preflight.v16`）

绑定当前 host/runtime、工具版本、ZCode config、repo root、Git head、worktree、CodeGraph
index 和 semantic sentinel。任一 identity 变化都使旧 receipt 失效。

### Gate 2：实际使用（`tool-usage.v16`）

把每条 declared route 绑定到成功且与任务相关的调用、evidence reference 和 privacy-safe
hook receipt hash。

意图 → 工具路由（ZCode 工具名，源自 `gov/zgov/tool_routing.py` 的 `PREFERRED_TOOL`）：

| 任务意图（`Intent`） | 强制路由 | ZCode 工具名 |
|---|---|---|
| `known_symbol` / `known_call` / `blast_radius` | CodeGraph | `mcp__codegraph__codegraph_explore` |
| `semantic_entry` / `similar_implementation` | Semble | `mcp__semble__search`、`mcp__semble__find_related` |
| `shell_output` | `rtk` | `Bash`（命令首 token 为 `rtk`） |
| `exact_string` / `exact_error` / `config` / `log` | `rg` | `Grep`，或 `Bash`（命令首 token 为 `rg`） |
| hash、parser input、byte identity、精确 denominator | raw command | `Bash` |

`normalize_zcode_tool()` 负责把工具名归一化：MCP 精确名与 `mcp__codegraph*` /
`mcp__semble*` 前缀直接命中；`Grep` / `grep` / `ripgrep` → `rg`；`Bash` 只在命令是**单条
简单命令**且首 token 的 basename 为 `rtk` 或 `rg` 时才归一化——含 `| & ; > < \` $( ` 或
换行的复合命令一律返回 `None`（表示 unspecified，不是违规）。

无关地把每个工具调用一次属于违规。只有 preferred tool 真实失败，并留下 reason code 和
evidence reference 后才能 fallback；fallback 不得冒充等价语义或结构覆盖。

详细合同与修复方法：[docs/TOOLCHAIN.md](docs/TOOLCHAIN.md)。

## 开发和审查模型

1. 冻结 objective、scope、invariants、non-goals、exact identity 和 evidence budget。
2. 只运行具备具体 WHY-RED 与已知 denominator 的 affected checks（FAST 只跑 `targeted`）。
3. 每个任务只有一个 independent reviewer：
   - 低/中风险：`reviewer_standard` role（默认 effort `high`）；
   - 高风险：fresh `reviewer_high` role（默认 effort `xhigh`）。
4. 稳定修复由同一 reviewer 做 delta-only 闭环（`delta_continuation`）。
5. coverage complete、unreviewed scope 为空、无 active P1/`BLOCKING`、evidence 与 exact
   head 匹配时才 approve。
6. 使用 expected-head/match-head guard 合并；本包的 `pre-bash` hook 额外提供 PR 合并硬
   门禁。

正确性与证据是硬门。第一优化目标是得到正确判断或正确合并的时间，token/call cost 排第二。
详见 [docs/review-workflow.md](docs/review-workflow.md)。

## 模型 role 占位符

Codex 版把模型名硬编码在代码里。ZCode 版**全部参数化**为 role，配置文件是
`$ZCODE_HOME/gov-config/roles.json`（种子来自 `gov/roles.example.json`）：

| role | 用途 | 当前值 |
|---|---|---|
| `writer` | 写 brief/mission 的模型 | `tuzi-direct-1m/claude-tuzi/claude-opus-5` |
| `executor` | 执行实现的模型 | `e8ed5e30-e95d-45dc-b265-37acf2ba2583/deepseek-v4-flash` |
| `reviewer_standard` | 低/中风险独立 reviewer | `tuzi-direct-1m/claude-tuzi/claude-fable-5` |
| `reviewer_high` | 高风险独立 reviewer | `tuzi-direct-1m/claude-tuzi/claude-fable-5` |
| `auditor_spark` | Spark 内审 auditor | `tuzi-direct-1m/claude-tuzi/claude-fable-5` |

`efforts` 已有真实默认值（`reviewer_standard: high`、`reviewer_high: xhigh`、
`delta_continuation: high`、`auditor_spark: high`），**effort 不允许是占位符**，且
`delta_continuation` 必须等于 `reviewer_standard`。`agents` 里 `reviewer_standard` 已绑定
到 ZCode 现有的 `gov-reviewer`，`executor` 绑定到 `gov-executor`。

填法：把 `<TBD:*>` 换成你的 ZCode surface 实际暴露的模型标识，然后重启客户端、开新会话。

**未填时的行为**：结构校验照常运行（模块在占位符状态下跳过 identity 断言），但任何**带
裁决的制品**会被 `ROLE_PLACEHOLDER_UNRESOLVED` 阻断——`resolve_role()` 和
`require_roles_resolved()` 抛 `RolePlaceholderUnresolved`。也就是说：可以写、可以跑测试，
但不能出 approve。

## milestone 冻结事实

Codex 版把三个平台哈希、19 项 finding、分母 18 硬编码在 `spark.py`，把 `BASE_SHA` /
`BASE_TREE` 硬编码在 `presubmit.py`。ZCode 版**全部参数化**到
`$ZCODE_HOME/gov-config/milestone.json`（种子来自 `gov/milestone.example.json`），出厂值是
`"frozen": false`。

未冻结时，以下信任链校验一律 **fail-closed 抛 `MILESTONE_NOT_FROZEN`，绝不返回绿**：

| 校验点 | 代码位置 |
|---|---|
| dispatch transcript 校验 | `gov/zgov/spark.py`（`require_frozen`） |
| author closure 校验 | `gov/zgov/spark.py`（`require_frozen`） |
| closure binding receipt 校验 | `gov/zgov/spark.py`（`require_frozen`） |
| presubmit 全流程 | `gov/zgov/presubmit.py`（`require_frozen`） |

`gov/zgov/r1.py` 的负例矩阵在未冻结时把相关用例标为
`SKIPPED_MILESTONE_NOT_FROZEN`，并计入 `skipped` 而不是 `passed`——分母保持可见。

冻结方法：填好 `milestone.json` 的 `milestone_id` / `repo` / `base_sha` / `base_tree` /
`spark.*` / `transcript.*`，把 `frozen` 置为 `true`。安装器**永不覆盖**这个文件。

## 完整验证

开发中先跑最小 affected checks。冻结 clean candidate 后运行：

```bash
git status --short
python3 scripts/verify-governance.py --repo .
python3 scripts/presubmit.py --repo .
git diff --check
```

测试套件与分母（下列数字是撰写本文时的实测值；分母会随代码演进变化，以你本机实际输出为准）：

```bash
PYTHONPATH=gov python3 -m unittest discover -s tests/gov     -p 'test_*.py' -t tests/gov      # 197 tests
PYTHONPATH=gov python3 -m unittest discover -s tests/hooks   -p 'test_*.py' -t tests/hooks    #  45 tests
PYTHONPATH=gov python3 -m unittest discover -s tests/scripts -p 'test_*.py' -t tests/scripts  #  31 tests
```

manifest 是精确 tracked path/hash 边界。新增、删除或修改 tracked 文件后必须同步：

```bash
python3 scripts/generate-manifest.py --repo .          # 重新生成
python3 scripts/generate-manifest.py --repo . --check  # 只报告漂移，不写
```

## 隐私与限制

绝不能提交：

- API/GitHub token、OAuth state、cookie 或 credential；
- ZCode session、prompt、transcript、memory 或 receipt JSONL；
- plugin/connection/model cache 或 browser profile；
- 个人绝对路径（任何用户家目录下的机器路径）或私有仓库内容。

`scripts/verify-governance.py` 会对每个 tracked 文件扫描上述模式，命中即 RED。只有
`PRIVACY.md`、`SECURITY.md` 和 `gov/zgov/fixtures/examples/` 被豁免。

本包不能授予当前 ZCode surface 没有暴露的模型或工具。治理、MCP、hook 或 model-routing 变化
后，**重启 ZCode 客户端并开启新会话**。

详见 [SECURITY.md](SECURITY.md)、[PRIVACY.md](PRIVACY.md) 和
[docs/privacy-threat-model.md](docs/privacy-threat-model.md)。

## 故障排查

reason code 全集见 [docs/TOOLCHAIN.md](docs/TOOLCHAIN.md)，此处只列最常见的。

| 现象 | 处理 |
|---|---|
| `CODEGRAPH_NOT_FOUND` | 安装 CodeGraph，再配置 ZCode MCP。 |
| `CODEGRAPH_MCP_NOT_CONFIGURED` | 在 `mcp.servers` 里加 CodeGraph server，重启客户端。 |
| `CODEGRAPH_WRONG_PROJECT` | 停止，把 doctor/query 指向 owning child repo。 |
| `CODEGRAPH_STALE` | 审阅变化，授权后运行 `codegraph sync .`。 |
| `CODEGRAPH_SENTINEL_MISMATCH` | 检查 index、query、path 与 revision。 |
| `SEMBLE_MCP_NOT_CONFIGURED` | 在 `mcp.servers` 里加 Semble server，重启客户端。 |
| `SEMBLE_SCOPE_CONTAMINATION` | 停止，修复 repository scope。 |
| `SEMBLE_SENTINEL_MISMATCH` | 改善 semantic query 或修复 repo/index scope；不得宣称 ready。 |
| `RTK_FALSE_GREEN` | 硬停止；修复前不得接受 shell evidence。 |
| `GIT_HEAD_UNAVAILABLE` | strict doctor 拒绝非 Git 目录；在真实仓库根运行。 |
| `ROLE_PLACEHOLDER_UNRESOLVED` | 填 `gov-config/roles.json` 的 `<TBD:*>`。 |
| `MILESTONE_NOT_FROZEN` | 填并冻结 `gov-config/milestone.json`。 |
| `receipt_status=write_failed` | 修复私有 receipt 目录权限（0700/0600）；runtime-proof acceptance 被阻塞。 |
| hook 完全不触发 | 配置文件 hook 默认关闭；确认 `hooks.enabled` 为 `true` 且重启过客户端。 |
| hook 输出被整体丢弃 | stdout 多了非白名单键；只允许 5 个键（见下）。 |
| Manifest verifier 为 RED | 停止，逐项检查，经审查更新后重跑 `generate-manifest.py`。 |

## ZCode hook 映射

ZCode 只有**恰好 7 个** hook 事件：`SessionStart`、`UserPromptSubmit`、`PreToolUse`、
`PermissionRequest`、`PostToolUse`、`PostToolUseFailure`、`Stop`。**没有 `SubagentStart`。**

本包当前注册 3 个事件（见 `gov/hooks/hooks.json`）：

| 事件 | matcher | 子命令 | 超时 | 作用 |
|---|---|---|---|---|
| `SessionStart` | —— | `session-start` | 20000 ms | infra 自检 + v16 治理情境注入 |
| `PreToolUse` | `Bash` | `pre-bash` | 30000 ms | PR 合并硬门禁、GitHub 双身份守卫、凭据保护、rtk 无缝改写 |
| `PreToolUse` | `Read\|Edit\|Write` | `pre-file` | 5000 ms | 凭据保护 |
| `PreToolUse` | `Agent\|mcp__codegraph.*\|mcp__semble.*\|Grep` | `pre-tool` | 10000 ms | 委派守卫 + 路由 receipt |
| `PostToolUse` | `Agent` | `post-agent` | 5000 ms | 委派结果 receipt（不阻断） |

- Codex 的 **SubagentStart ACTIVE-MISSION-LOCK** 映射到 `PreToolUse` + matcher `Agent`；
- Codex 的**子结果校验**映射到 `PostToolUse` + matcher `Agent`；
- 委派深度上限 `max_depth: 1`，嵌套 spawn 一律 denied（环境变量 `ZGOV_AGENT_DEPTH`）。

**stdout 是严格 schema**，只允许这 5 个键：

```text
hookEventName | permissionDecision | permissionDecisionReason | additionalContext | updatedInput
```

多一个键，整个 hook 效果被丢弃。因此所有 `decision` / `reason_code` / `route` 一律下沉到
receipt JSONL（`~/.zcode/hooks/receipts/<UTC-date>.jsonl`），绝不出现在 stdout。
退出码恒为 `0`（`review-pass` 辅助命令除外）；deny 通过 `permissionDecision` 表达。
matcher 是**大小写敏感的正则**，别名 `Task` → `Agent`、`ApplyPatch` → `Write|Edit`；
非法正则会静默地永不匹配。

## 环境变量

| 变量 | 用途 |
|---|---|
| `ZCODE_HOME` | ZCode 运行时根目录，默认 `~/.zcode` |
| `ZGOV_ROLES_PATH` | 覆盖 `roles.json` 路径 |
| `ZGOV_MILESTONE_PATH` | 覆盖 `milestone.json` 路径 |
| `ZGOV_HOOK_SOURCE` | 置为 `test` 时 receipt 走测试目录并标记 `source=test` |
| `ZGOV_HOOK_RECEIPT_DIR` | 测试模式下的 receipt 目录 |
| `ZGOV_RECEIPT_PATH` | 显式 receipt 文件路径 |
| `ZGOV_TASK_ID` | 写入 receipt 的任务标识（只存 sha256） |
| `ZGOV_AGENT_DEPTH` | 当前委派深度，用于 `max_depth: 1` 守卫 |

## 仓库结构

```text
gov/                                  可安装治理包（安装到 $ZCODE_HOME/gov/）
  AGENTS.md                           安装到 $ZCODE_HOME/ 的全局规则（sidecar）
  POLICY.md
  BRIEF-TEMPLATES.md
  roles.example.json                  → gov-config/roles.json 的种子
  milestone.example.json              → gov-config/milestone.json 的种子
  agents/                             agent 定义（sidecar，安装到 $ZCODE_HOME/agents/）
  hooks/                              hooks.json + 5 个 hook 模块
    hooks.json  zcode_hook.py  hook_receipt.py
    pre_tool_use_policy.py  session_context.py  delegation_contract.py
  zgov/                               18 个 stdlib-only 模块
    contracts.py  contracts/schema_registry.v16.json（26 条 schema）
    compiler.py  runner.py  evidence.py  state.py  presubmit.py
    tool_preflight.py  tool_routing.py
    review_policy.py  review_runtime.py  trace.py
    metrics.py  spark.py  r1.py  checker.py  milestone.py  roles.py
    fixtures/                         mission fixture 与 examples/
docs/                                 architecture / TOOLCHAIN / review-workflow /
                                      deployment / privacy-threat-model
scripts/                              install-governance.py  register-hooks.py
                                      verify-governance.py   toolchain-doctor.py
                                      presubmit.py           generate-manifest.py
tests/gov/  tests/hooks/  tests/scripts/
manifest.json                         精确 tracked path/hash 边界
```

## ZCode 官方参考

官方 `zcode-guide` 插件在本机提供可引用的 skill 文档（不联网）：

```text
~/.zcode/cli/plugins/cache/zcode-plugins-official/zcode-guide/<version>/skills/
  zcode-configuration-guide/SKILL.md   配置总览：MCP / 命令 / skill / hook / 插件的作用域与优先级
  diagnosing-mcp/SKILL.md              MCP 配置位置、严格 schema、模板变量、排障流程
  diagnosing-hooks/SKILL.md            7 个事件名、matcher 语义、超时单位、严格 stdout schema
  diagnosing-skills/SKILL.md           skill 发现与遮蔽
  diagnosing-commands/SKILL.md         slash 命令优先级与 frontmatter
  diagnosing-plugins/SKILL.md          插件与 marketplace
```

本 README 里关于「7 个事件」「严格 stdout schema」「`mcp.servers` 位置」「配置文件 hook
默认关闭」的表述均来自上述本地官方文档。
