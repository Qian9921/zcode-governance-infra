# 强制工具链：先就绪，再路由

本文是 CodeGraph、Semble、`rtk` 的操作合同。快速路径在 [README.zh-CN.md](../README.zh-CN.md)。

权威来源是 `gov/zgov/tool_preflight.py`（reason code 全集）与
`gov/zgov/tool_routing.py`（意图路由）。本文只解释它们，不扩展它们。

## 两道门

1. `tool-preflight.v16` 证明这些工具对**一个精确的仓库 identity** 而言是当前的、可信的。
2. `tool-usage.v16` 证明每一条任务声明的 route 都被真正使用过，并产出了与任务相关、有
   receipt 背书的结果。

`PATH` 上有 binary 不等于就绪。每个工具无关地调用一次不等于使用合规。

## 配置 ZCode

运行任何命令前先审阅。这些命令配置的是**已经安装好**的工具，它们不会让一个不存在的产品
能力凭空出现。

ZCode 的 MCP 配置是 JSON，位于 `$ZCODE_HOME/cli/config.json` 的 `mcp.servers` 键下：

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

注意事项（来自本机官方 `zcode-guide` 插件的 `diagnosing-mcp` skill）：

- 配置文件里的 server schema **严格**：出现一个未知键，该 server 被整体丢弃；
- 配置文件里的 `${...}` 模板变量**不会**展开（只有插件提供的 MCP server 才展开），因此
  这里要用绝对路径；
- `stdio` 需要 `command`，可选 `args[]` / `cwd` / `env` / `enabled` / `timeoutMs`；
  `http` / `sse` 需要 `url`；默认超时 30000 ms；
- 也可以在客户端 **Settings → MCP** 里查看状态并修复。

`tool_preflight._zcode_mcp_servers()` 对布局是容错的（fail-closed）：它接受
`mcp.servers`、`mcp.mcpServers`、扁平的 `mcp.<name>`（跳过 `enabled` 键）以及顶层
`mcpServers`；解析失败或结构无法识别时返回空集合。匹配是**大小写不敏感的子串匹配**——
server 名里包含 `codegraph` / `semble` 即算已配置。

`rtk` 不是 MCP，而是 shell 输出包装器，按其自身的初始化流程配置。

配置变更后**重启 ZCode 客户端并开启新会话**。

## 准备一个仓库

CodeGraph 状态属于 owning repository。绝不能把父 workspace 的图当作 child repo 的真值。

```bash
cd /path/to/repository

# 只读检查
codegraph status --json .

# 仅当仓库未初始化且索引已获授权
codegraph init .

# 结构改动后，仅当同步已获授权
codegraph sync .
```

索引与同步是持久化 mutation。doctor 从不执行它们（报告里的 `mutations` 恒为空列表）。

## 运行严格 doctor

选一个**行为描述**和一个实现该行为的、当前存在的仓库相对路径。不要只把文件名当 query。

```bash
python3 scripts/toolchain-doctor.py \
  --repo . \
  --semantic-query "deterministic inspection intent router" \
  --expected-path gov/zgov/tool_routing.py
```

`toolchain-doctor.py` 的完整 CLI（实测自 `--help`）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `--repo REPO` | 否（默认 `.`） | 目标 Git 仓库 |
| `--semantic-query SEMANTIC_QUERY` | **是** | 仓库专属的语义 sentinel query |
| `--expected-path EXPECTED_PATH` | **是** | CodeGraph 与 Semble 都必须找到的仓库相对文件 |
| `--config CONFIG` | 否 | ZCode CLI 配置文件；默认取 `$ZCODE_HOME` 下的 CLI 配置 |
| `--timeout-sec TIMEOUT_SEC` | 否（默认 20） | 单条命令超时 |
| `--advisory` | 否 | 把失败报成 `degraded` 而不是 `blocked` |

退出码 `0` 且 `"status":"ready"` 要求已知分母 `3/3`：

- CodeGraph：MCP 已配置、project 正确、索引完整且 fresh、索引路径安全、sentinel query 命中；
- Semble：MCP 已配置、command surface 可调用、结果限定在仓库内、命中预期的当前源码 sentinel；
- `rtk`：可调用、能复现当前 Git head、仓库命令成功、确定性缺失 ref 的命令仍为非零退出。

报告只保存 hash 与规范化 reason code，不保存命令原始输出、绝对路径、prompt、环境变量或
credential，且不做任何写入。

strict 模式下 Git identity 校验失败会直接抛 `PreflightError`（`strict preflight requires a
valid Git repository identity`），不产出报告。

## 检查项与 reason code 全集

下表逐项对应 `tool_preflight.py` 里的 `_check(name, passed, ok, failed)` 调用。
每个 check 有一对 pass/fail code，另有三个工具级聚合 code。**共 47 个 reason code。**

### CodeGraph（8 项 check + 1 个聚合 = 17 个 code）

| check name | pass code | fail code | fail 时的处置 |
|---|---|---|---|
| `binary` | `CODEGRAPH_BINARY_FOUND` | `CODEGRAPH_NOT_FOUND` | 安装 CodeGraph，再配置 ZCode MCP。 |
| `zcode_mcp_config` | `CODEGRAPH_MCP_CONFIGURED` | `CODEGRAPH_MCP_NOT_CONFIGURED` | 在 `mcp.servers` 下加 CodeGraph server，重启客户端并开新会话。 |
| `version` | `CODEGRAPH_VERSION_OK` | `CODEGRAPH_VERSION_FAILED` | `--version` 不可用或输出不可解析：修复安装，别把损坏的 binary 当就绪。 |
| `project_scope` | `CODEGRAPH_PROJECT_MATCH` | `CODEGRAPH_WRONG_PROJECT` | 停止；索引属于另一个仓库，把 doctor/query 指向 owning child repo。 |
| `index_state` | `CODEGRAPH_INDEX_COMPLETE` | `CODEGRAPH_INDEX_INVALID` | 索引不完整/状态不可解析：授权后重建（`codegraph init .`）。 |
| `indexed_files` | `CODEGRAPH_FILES_CURRENT` | `CODEGRAPH_FILES_INVALID` | 索引文件列表查询失败，或包含仓库外/不安全路径：停止并修复 scope。 |
| `revision_freshness` | `CODEGRAPH_REVISION_MATCH` | `CODEGRAPH_STALE` | 索引状态与 worktree 不一致：审阅变化，授权后 `codegraph sync .`。 |
| `sentinel_query` | `CODEGRAPH_SENTINEL_MATCH` | `CODEGRAPH_SENTINEL_MISMATCH` | 没找到预期的当前源码：检查 index、query、path 与 revision。 |
| （聚合） | `CODEGRAPH_READY` | —— | 全部 check 通过时的工具级 code；任一失败则取第一个失败 check 的 code。 |

### Semble（7 项 check + 1 个聚合 = 15 个 code）

| check name | pass code | fail code | fail 时的处置 |
|---|---|---|---|
| `binary` | `SEMBLE_BINARY_FOUND` | `SEMBLE_NOT_FOUND` | CLI 能力不可用：安装 Semble 并配置 MCP。 |
| `zcode_mcp_config` | `SEMBLE_MCP_CONFIGURED` | `SEMBLE_MCP_NOT_CONFIGURED` | 在 `mcp.servers` 下加 Semble server，重启客户端并开新会话。 |
| `semantic_query` | `SEMBLE_QUERY_DECLARED` | `SEMBLE_QUERY_REQUIRED` | 没有声明 `--semantic-query`：补一个描述行为的语义 query，别用文件名。 |
| `expected_path` | `SEMBLE_EXPECTED_PATH_BOUND` | `SEMBLE_EXPECTED_PATH_INVALID` | `--expected-path` 为空、绝对路径、含 `..`、含反斜杠或逃出仓库：改成合法的仓库相对路径。 |
| `command_surface` | `SEMBLE_COMMAND_SURFACE_OK` | `SEMBLE_COMMAND_SURFACE_FAILED` | `--help` 非零或不含 `search` 子命令：版本不兼容，升级 Semble。 |
| `repo_scope` | `SEMBLE_REPO_SCOPE_MATCH` | `SEMBLE_SCOPE_CONTAMINATION` | 搜索返回了仓库外/不安全路径：停止，修复 repository scope。 |
| `sentinel_query` | `SEMBLE_SENTINEL_MATCH` | `SEMBLE_SENTINEL_MISMATCH` | 没返回预期的当前源码：改善语义 sentinel 或修复搜索状态；不得宣称 ready。 |
| （聚合） | `SEMBLE_READY` | —— | 全部 check 通过时的工具级 code。 |

### rtk（5 项 check + 1 个聚合 = 11 个 code）

| check name | pass code | fail code | fail 时的处置 |
|---|---|---|---|
| `binary` | `RTK_BINARY_FOUND` | `RTK_NOT_FOUND` | `rtk` 不可用：安装并初始化其 ZCode 引导。 |
| `version` | `RTK_VERSION_OK` | `RTK_VERSION_FAILED` | `--version` 不可用或输出不可解析：修复安装。 |
| `positive_command` | `RTK_OUTPUT_MATCH` | `RTK_OUTPUT_MISMATCH` | 包装后的 Git identity 与裸 Git 不一致：修复前不得把 rtk 输出当证据。 |
| `repository_command` | `RTK_REPO_COMMAND_OK` | `RTK_REPO_COMMAND_FAILED` | 仓库内命令失败：检查 rtk 与仓库的绑定。 |
| `failure_exit_status` | `RTK_FAILURE_PRESERVED` | `RTK_FALSE_GREEN` | **硬停止**。一个必然失败的命令被报成成功，rtk 无法支撑任何 acceptance。 |
| （聚合） | `RTK_READY` | —— | 全部 check 通过时的工具级 code。 |

### Git identity（2 项 check = 4 个 code）

| check name | pass code | fail code | fail 时的处置 |
|---|---|---|---|
| `git_head` | `GIT_HEAD_BOUND` | `GIT_HEAD_UNAVAILABLE` | `git rev-parse HEAD` 失败或不是 40 位 hex：在真实仓库根运行；strict 模式直接拒绝。 |
| `git_worktree` | `GIT_WORKTREE_BOUND` | `GIT_WORKTREE_UNAVAILABLE` | `git status --porcelain` 或文件列举失败：修复仓库状态；strict 模式直接拒绝。 |

### 计数

```text
CODEGRAPH_*  17     SEMBLE_*  15     RTK_*  11     GIT_*  4       合计 47
```

`--advisory` 只用于诊断。advisory 失败返回 `degraded`，永远不是 `ready`，不能满足任何正式
gate。

## 缓存与失效

preflight receipt 只在其 `cache.key_sha256` 仍然绑定同一 identity 时可复用。
`cache.invalidated_by` 显式列出 **10 项**失效来源：

| # | `invalidated_by` 条目 | 含义 |
|---|---|---|
| 1 | `host_or_runtime_change` | 宿主/解释器 runtime identity 变了 |
| 2 | `tool_version_change` | CodeGraph / Semble / rtk 任一版本行变了 |
| 3 | `zcode_config_change` | ZCode CLI 配置内容或路径变了（Codex 版此项为 Codex 配置） |
| 4 | `repo_root_change` | 仓库根变了 |
| 5 | `git_head_change` | Git head 变了 |
| 6 | `worktree_bytes_change` | worktree 字节变了 |
| 7 | `codegraph_index_change` | CodeGraph 索引变了 |
| 8 | `semantic_query_change` | `--semantic-query` 变了 |
| 9 | `expected_path_change` | `--expected-path` 变了 |
| 10 | `sentinel_evidence_change` | sentinel 证据哈希变了 |

任一变化都使 receipt 失效。**不要在每次工具调用前重跑 doctor**；只在 identity 变化时、
以及正式 approve 前若当前 receipt 已 stale 时重跑。

## 就绪之后的强制路由

| 意图（`Intent`） | 必须先用的工具 | ZCode 工具名 | 必须保留的结果 |
|---|---|---|---|
| `semantic_entry`、`similar_implementation` | Semble | `mcp__semble__search`、`mcp__semble__find_related` | 用于选择下一步检查的候选 path/line |
| `known_symbol`、`known_call`、`blast_radius` | CodeGraph | `mcp__codegraph__codegraph_explore` | 当前的结构 path / impact 结果 |
| `shell_output` | `rtk` | `Bash`（首 token 为 `rtk`） | 紧凑的人类上下文输出 |
| `exact_string`、`exact_error`、`config`、`log` | `rg` | `Grep`，或 `Bash`（首 token 为 `rg`） | 字面匹配 |
| hash、parser input、byte identity、精确分母 | raw command | `Bash` | 未经修改的机器证据 |

`USAGE_PURPOSE` 把意图归入 `structure` / `discovery` / `context_display` / `literal` 等
用途类别，用于在 `tool-usage.v16` 里检查「这次调用是否服务于它声明的目的」。

fallback 需要：**真实发生过的失败尝试** + **稳定的 reason code** + **evidence reference**。
它绝不宣称等价的结构或语义覆盖。

闭合时，`tool-usage.v16` 把每条 declared route 绑定到：

- 选中的工具；
- 成功/失败；
- 与任务相关的用途；
- evidence reference；
- privacy-safe hook receipt hash；
- 当期 preflight cache key。

缺失、用错工具、失败、未声明、无 receipt 或无关的打卡式调用，都是违规。

## hook receipt 与 route 的交叉校验

`tool_routing._normalize_receipts()` 对每一条 hook receipt 强制：

- 字段集必须**恰好**等于 `hook-receipt.v16` 的 20 字段；
- `event == "PreToolUse"` 且 `decision == "allow"`；
- `receipt_status == "written"`；
- `route == route_code` 且属于 `{codegraph, semble, rtk, rg}`；
- `route_code` 与 `tool_name` 必须相容（`_receipt_route_matches_tool_name`）：
  - `codegraph` ← `codegraph` / `codegraph_explore` / `mcp__codegraph__codegraph_explore`
  - `semble` ← `semble` / `semble_search` / `mcp__semble__search` / `mcp__semble__find_related`
  - `rtk` ← `rtk`
  - `rg` ← `rg` / `ripgrep` / `grep`
  - 另外，`rtk` 与 `rg` 允许通过通用执行工具名（`bash` / `shell` / `exec_command` /
    `functions.exec_command`）承载；
- `snapshot_sha256` 与治理快照一致、`identifiers_sha256` 与任务标识一致；
- `source` ∈ `{runtime, test}`，`pid`/`ppid` 为非负整数；
- 重复 receipt（同一规范化 JSONL 哈希）被拒。

## 工具名归一化

`normalize_zcode_tool(tool_name, tool_input=None)` 按顺序：

1. `tool_name` 小写后在 `ZCODE_TOOL_ALIASES` 里直接命中 → 返回；
2. 小写以 `mcp__codegraph` 开头 → `codegraph`；以 `mcp__semble` 开头 → `semble`；
3. 小写等于 `bash` 且 `tool_input` 是 Mapping：取 `command`（回退 `cmd`），
   **含 `| & ; > < \` $(` 或换行的复合命令一律返回 `None`**，否则 `shlex.split(posix=True)`，
   取 `tokens[0]` 的 basename 小写，仅当等于 `rtk` 或 `rg` 时返回对应值；
4. 其余 → `None`（表示 *unspecified*，**不是**违规）。

`ZCODE_TOOL_ALIASES` 的完整表：

```text
mcp__codegraph__codegraph_explore → codegraph
mcp__semble__search               → semble
mcp__semble__find_related         → semble
grep / ripgrep / rg               → rg
rtk                               → rtk
codegraph                         → codegraph
semble                            → semble
```

归一化是**纯词法**的：它不表达某个工具是否被允许、是否健康、对某意图是否语义恰当。
