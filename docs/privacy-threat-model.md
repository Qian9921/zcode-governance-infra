# 隐私威胁模型

本文描述本包在隐私上防什么、怎么防、以及**明确不防什么**。

## 威胁

1. **会话/receipt 误导出**：把 ZCode session、prompt、transcript 或 receipt JSONL 提交进
   仓库。
2. **凭据/token 泄漏**：GitHub token、OAuth state、cookie 出现在被跟踪文件里。
3. **机器路径暴露**：个人家目录绝对路径、私有仓库结构出现在文档或 fixture 里。
4. **安装器越权**：安装器写到、或删除了它不该碰的目录。
5. **诊断数据外溢**：doctor 或 hook 把命令原文、参数、环境变量、prompt 落盘。

## 控制措施

### 白名单投影，而不是黑名单过滤

`gov/hooks/hook_receipt.py` 的 `_SAFE_FIELDS` 是 **20 个字段的白名单**：

```text
schema  schema_version  utc  event  model  tool_name
decision  reason  reason_code  route  route_code
snapshot_sha256  identifiers_sha256  session_id_sha256  turn_id_sha256
tool_call_id_sha256  source  pid  ppid  receipt_status
```

投影逻辑是 `{key: value[key] for key in _SAFE_FIELDS if key in value}`——**只挑出白名单里
的键**，而不是「删掉已知敏感键」。因此上游新增任何字段都不会意外落盘：黑名单会漏，白名单
不会。

### 标识符只存 sha256

session、turn、tool call、task 这些标识符只以 `*_sha256` 形式存在，原值绝不落盘。
`ZGOV_TASK_ID` 同理，写入前先哈希。

### receipt 文件系统硬约束

`write_receipt()` 对目标目录与文件强制：

- 目录若已存在且是 symlink → 抛 `receipt directory is a symlink`；
- 目录以 `mode=0o700` 创建，并再 `chmod(0o700)`；
- 复核 `stat`：不是目录或权限不是 `0700` → 拒绝；
- 文件用 `os.open` + `O_NOFOLLOW`（平台支持时）以 `0o600` 打开，再 `fchmod(0o600)`；
- 写失败时 `receipt_status` 记为 `write_failed`，**不影响 hook 的决策**（best-effort
  审计），但 runtime-proof acceptance 会因此被阻塞。

默认目录是 `~/.zcode/hooks/receipts/`；`ZGOV_HOOK_SOURCE=test` 时改用
`ZGOV_HOOK_RECEIPT_DIR`，并把 `source` 标记为 `test`，测试数据永不混入 runtime 审计。

### doctor 只存哈希与 reason code

`gov/zgov/tool_preflight.py` 的报告字段是固定集合（`REPORT_FIELDS`），内容只有：

- `repo_identity`：`root_sha256` / `head_sha` / `dirty` / `worktree_sha256`；
- `config_identity`：`path_sha256` / `content_sha256` / `present`——**配置路径本身也只存
  哈希**；
- `tools[].checks[]`：`name` / `status` / `reason_code`；
- `tools[].evidence_sha256`：所有 stdout/stderr 拼接后的单个哈希；
- `cache.key_sha256` 与 `invalidated_by`；
- `counts` / `denominator` / `mutations`（恒为空列表）。

**不存**：命令原始输出、绝对路径、prompt、环境变量、credential。
`_version_line()` 还额外限制版本行长度 ≤ 120 字符，避免把整段输出当"版本"塞进报告。

### 安装器 allowlist

见 [deployment.md](deployment.md) 第 0 节。要点：唯一可整体替换的目录是
`$ZCODE_HOME/gov/`；每一个 `rmtree`/`rename` 都必须经过
`_assert_safe_destructive_target()`；`gov-config/` 永不覆盖；`cli/`、`server/`、`v2/`、
`hooks/receipts/` 绝不触碰。

### tracked 文件扫描

`scripts/verify-governance.py` 对每一个 tracked 文件做两类检查。

**禁止路径片段**（`FORBIDDEN_PARTS`，命中即 RED）：

```text
sessions   receipts   plugins   connections   models_cache.json   .env
```

**禁止内容正则**（`FORBIDDEN_RE`，5 条）：

| # | 模式 | 拦什么 |
|---|---|---|
| 1 | `gh[pso]_[A-Za-z0-9]{20,}` | GitHub token（`ghp_`/`ghs_`/`gho_`） |
| 2 | `(?:session\|turn\|prompt\|transcript)[_-]?id\s*[:=]\s*[A-Za-z0-9-]{12,}` | 会话/轮次/prompt/transcript 标识符赋值 |
| 3 | `/` + `home/` 前缀后跟非空白非引号字符 | 个人家目录绝对路径 |
| 4 | `/` + `.zcode/cli/config.json` | ZCode 用户配置文件的绝对/波浪号路径 |
| 5 | `\bsess_[0-9a-f-]{8,}\b` | ZCode 会话标识 |

第 4、5 条是 **ZCode 专属**的（Codex 版对应的是 Codex 配置路径与其会话格式）。因此本仓库的
文档在需要指代用户配置文件时一律写成 `$ZCODE_HOME/cli/config.json` 这种占位符形式，
而不是带 `.zcode` 的具体路径。

豁免范围仅三处：`PRIVACY.md`、`SECURITY.md`（它们必须**描述**这些模式），以及
`gov/zgov/fixtures/examples/`（示例制品）。豁免只对内容正则生效，路径片段检查依旧执行。

同一个脚本还做精确 manifest 边界校验：tracked 集合与 manifest 声明集合必须**完全相等**
（多一个报 `manifest extra`，少一个报 `manifest missing`），每个文件 sha256 必须一致，
并且 `REQUIRED_PATHS` 里的关键文件必须存在。

## 明确不防：协同伪造

**本地校验器不防「协同伪造」。**

如果攻击者能够**同时**重写以下全部内容：

- pre-execution closure authority；
- dispatch 记录（dispatch transcript）；
- 候选 packet（review packet）；
- 证据（evidence envelope / gate result）；
- reviewer 记录（independent review artifact）；

那么所有本地哈希会互相自洽，校验器会返回绿。本包的信任链只能证明**这些制品彼此一致**，
不能证明**它们没有被同一只手一起改过**。

这是设计上的已知边界，不是缺陷遗漏：

- 本包的所有校验都在**同一台机器、同一个信任域**内完成；
- 它没有任何外部锚点（无签名密钥、无第三方时间戳、无远端只追加日志）。

因此以下能力属于**独立范围**，不在本包内：

- 制品签名（对 authority / transcript / review artifact 做不可否认签名）；
- 透明日志（把关键哈希写入外部只追加、可公开审计的日志）；
- 硬件或远端见证。

如果你的威胁模型包含「有本机写权限的内部攻击者」，必须在本包之外补上签名与透明日志。

## 残余风险

- **扫描器未覆盖的新型 secret 格式**：`FORBIDDEN_RE` 只有 5 条模式，新的 token 前缀不会被
  拦。任何新增的、含数据的 fixture 都必须人工审阅。
- **best-effort 审计**：receipt 写失败不阻断 hook 决策，因此在磁盘满/权限错的情况下会出现
  「有动作、无 receipt」的窗口。这类窗口对 acceptance 是可见的
  （`receipt_status=write_failed` 会让 runtime-proof acceptance 失败），但对实时阻断无效。
- **identity guard fail-open**：GitHub 身份守卫在 `gh` 查询失败时放行（只记 receipt）。
  这是刻意的可用性取舍；merge gate 则相反，是 fail-closed 的。
- **豁免文件**：`PRIVACY.md` 与 `SECURITY.md` 不受内容正则约束，评审时必须逐字看。

## 禁止提交清单

- API / GitHub token、OAuth state、cookie、任何 credential；
- ZCode session、prompt、transcript、memory；
- receipt JSONL（`hooks/receipts/**`）与 review marker；
- plugin / connection / model cache、browser profile；
- `models_cache.json`、`.env` 及任何同类运行时状态；
- 个人家目录绝对路径、私有仓库内容；
- 任何未经审阅的、含真实数据的 fixture。
