# 部署

本文覆盖安装、注册 hook、回滚与升级的完整流程。

权威来源是 `scripts/install-governance.py` 与 `scripts/register-hooks.py`；下面所有参数
均实测自各脚本的 `--help`。

## 0. 安全边界：安装器绝不碰什么

`$ZCODE_HOME` 本身是**活动目录**，因此安装器**绝不重命名、替换或删除它**。
这与 Codex 版有根本差别：Codex 版把整个 `$CODEX_HOME` 当作一个可整体替换的目标目录。

| 路径 | 安装器行为 |
|---|---|
| `$ZCODE_HOME/gov/` | **唯一**做整目录原子替换的目录 |
| `$ZCODE_HOME/AGENTS.md` | 逐文件写入；写前把已存在文件复制为 `AGENTS.md.zgov-backup` |
| `$ZCODE_HOME/agents/*.md` | 逐文件写入；同样先写 `.zgov-backup` |
| `$ZCODE_HOME/gov-config/` | **仅在文件缺失时**生成；**永不覆盖**、永不备份、永不删除 |
| `$ZCODE_HOME/cli/` | **绝不触碰**（配置与数据库） |
| `$ZCODE_HOME/server/` | **绝不触碰**（运行时状态） |
| `$ZCODE_HOME/v2/` | **绝不触碰**（运行时状态） |
| `$ZCODE_HOME/hooks/receipts/` | **绝不触碰**（追加写的审计 receipt） |
| 其它任何 `$ZCODE_HOME` 子目录 | **绝不触碰** |

代码层的强制点是 `_assert_safe_destructive_target()`：模块里**每一个** `rmtree` 与
`rename` 都必须经过它，而它只放行两类目标——

1. `$ZCODE_HOME/gov` 与 `$ZCODE_HOME/gov.zgov-backup`；
2. 安装器自己在系统临时目录下、且名字以 `TMP_PREFIX` 开头创建的暂存目录。

其余一律 `SystemExit("unsafe destructive target: ...")`。**绝不 rmtree `$ZCODE_HOME`
或它的其它子目录。**

`gov-config/` 的种子映射（`GOV_CONFIG_SEEDS`）：

```text
gov/roles.example.json      → $ZCODE_HOME/gov-config/roles.json      (0600)
gov/milestone.example.json  → $ZCODE_HOME/gov-config/milestone.json  (0600)
```

已存在则跳过，输出里 `gov_config` 报 `preserved`；新建则报 `created`。因此**升级治理包
不会冲掉你填好的 role 与 milestone**。

## 1. 先在隔离目录试安装

```bash
cd /path/to/zcode-governance-infra

export ZGOV_TRIAL_HOME="$(mktemp -d)"

python3 scripts/install-governance.py --source . --zcode-home "$ZGOV_TRIAL_HOME" --dry-run
```

dry-run 只打印计划，不写任何文件，输出形如：

```json
{"destination":"$ZCODE_HOME","gov_config":"created","hashes":{...},
 "managed_files":N,"sidecar_files":["AGENTS.md", ...],"status":"DRY_RUN"}
```

- `managed_files` 是 manifest 声明的 `gov/**` 文件数（managed denominator）；
- `sidecar_files` 是同时要写到 `$ZCODE_HOME/` 根与 `agents/` 下的文件；
- `hashes` 是逐文件 sha256；
- dry-run 的 `destination` 是**占位符** `$ZCODE_HOME`，不泄露真实路径。

确认无误后执行真实安装：

```bash
python3 scripts/install-governance.py --source . --zcode-home "$ZGOV_TRIAL_HOME"
```

冒烟测试 hook（用测试 receipt 目录，不污染真实审计目录）：

```bash
ZGOV_HOOK_SOURCE=test \
ZGOV_HOOK_RECEIPT_DIR="$ZGOV_TRIAL_HOME/receipts" \
python3 "$ZGOV_TRIAL_HOME/gov/hooks/zcode_hook.py" session-start <<'JSON'
{"hook_event_name":"SessionStart","model":"trial"}
JSON
```

预期：

- 所有写入都在 `$ZGOV_TRIAL_HOME` 之内；
- 真实的 ZCode home 完全没有被修改；
- hook stdout 是合法的严格 schema JSON（只含 `hookEventName` 与 `additionalContext`）。

### 安装器的完整性检查

`collect()` 只接受 manifest 里以 `gov/` 开头的条目，并对每一条强制：

- 路径规范（无反斜杠、无空段、无 `.`/`..`）；
- `resolve(strict=True)` 后仍在 `gov/` 之内（拒绝路径逃逸与 symlink）；
- 必须是普通文件、非 symlink，且 **sha256 与 manifest 完全一致**；
- 集合非空。

因此 manifest 漂移会让安装直接失败（`manifest mismatch:<path>`），而不是装出一个不确定的
包。安装前请先跑：

```bash
python3 scripts/verify-governance.py --repo .
python3 scripts/generate-manifest.py --repo . --check
```

## 2. 正式安装

```bash
python3 scripts/install-governance.py --source . --zcode-home "$HOME/.zcode" --dry-run
# 逐项审阅 managed_files / sidecar_files / gov_config 后：
python3 scripts/install-governance.py --source . --zcode-home "$HOME/.zcode"
```

`--zcode-home` 是**必填**参数——没有隐式默认值，避免误装。目标必须已经是一个存在的目录，
否则报 `zcode home is not a directory`。

安装步骤（`main()`）：

1. 全部 `gov/**` 复制到系统临时暂存目录，逐文件 `chmod`（`.json` 为 0600，其余 0644）；
2. 若存在旧的 `gov.zgov-backup` 则删除；
3. 把现有 `gov/` 改名为 `gov.zgov-backup`；
4. 把暂存目录原子 rename 为 `gov/`（跨设备时退化为 copytree + 删暂存）；
5. sidecar 逐文件：先 `.zgov-backup` 备份，再复制、再 chmod；
6. 最后 seed `gov-config/`（仅缺失时）。

失败时暂存目录被清理并向上抛出，`gov/` 保持在步骤 3 之前或之后的一致状态。

## 2.5 填写 `roles.json`（必填，参数化配置）

安装器把 `gov/roles.example.json` 播种为 `~/.zcode/gov-config/roles.json`（0600，仅缺失时
创建，之后**永不覆盖**）。仓库内**不携带任何私有值**——模板里五个 model role 与两个
GitHub 身份全是 `<TBD:*>` 占位符。**安装后必须手工编辑 `~/.zcode/gov-config/roles.json`**
把占位符全部替换掉：

1. `roles` 五个键：`writer` / `executor` / `reviewer_standard` / `reviewer_high` /
   `auditor_spark` —— 填你的 ZCode surface 实际暴露的模型标识；
2. `identities` 两个键：
   - `dev` —— 开发身份（branch / commit / push / 开 PR）的 GitHub 登录名；
   - `governance` —— 治理身份（review / approve / merge）的 GitHub 登录名。

`efforts` / `agents` 保持模板默认值即可。编辑完成后**必须重启客户端并开新会话**（见第 4
节），否则解析结果不生效。

**未填时的行为（fail-closed，不是放行）**：

- 任一 role 仍是占位符：任何**带裁决的制品**被 `ROLE_PLACEHOLDER_UNRESOLVED` 阻断
  （`resolve_role()` / `require_roles_resolved()` 抛 `RolePlaceholderUnresolved`）——可以
  写、可以跑测试，但不能出 approve；
- `identities` 未配置（仍是占位符）：hook 的 merge gate 与身份守卫对 `gh pr merge` /
  `gh pr review` / `gh pr create` / `git push` 等动作 **fail-closed 拦截**，deny 理由为
  「治理身份未配置：请在 gov-config/roles.json 的 identities 里填写」，绝不因未配置而
  放行。

## 3. 注册 hook

```bash
python3 scripts/register-hooks.py --dry-run
python3 scripts/register-hooks.py
```

CLI（实测自 `--help`）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--config CONFIG` | `$ZCODE_HOME` 下的 CLI 配置 | 目标配置文件 |
| `--zcode-home ZCODE_HOME` | 由 `--config` 推导（其祖父目录） | 决定 `gov-config/displaced-hooks.json` 落在哪里 |
| `--hook-path HOOK_PATH` | `$ZCODE_HOME/gov/hooks/zcode_hook.py` | hook 入口脚本 |
| `--python PYTHON` | `/usr/bin/python3` | 用哪个解释器运行 hook |
| `--hooks-manifest HOOKS_MANIFEST` | 已安装的 `hooks.json` | 声明式 hook 清单 |
| `--dry-run` | —— | 只打印计划 |
| `--unregister` | —— | 移除治理 hook |

它的边界非常窄：

- **只**增删 `hooks.events` 下 `args[0]` 指向 `zcode_hook.py` 的条目；
- 生成的条目是 `{"type":"process","command":<python>,"args":[<hook-path>,<subcommand>],
  "timeoutMs":<ms>}`——`process` 类型不经过 shell，最可移植；
- 把 `hooks.enabled` 强制置为 `true`（**配置文件 hook 默认关闭**，不置真就完全不触发）；
- 被顶替掉的现役条目（同样指向某个 `zcode_hook.py` 的旧 hook）连同 `hooks.enabled`
  的原值，会以 0600 权限存入 `$ZCODE_HOME/gov-config/displaced-hooks.json`，供
  `--unregister` 原样还原；该文件**已存在时不覆盖**（输出标 `displaced_sidecar:
  "preserved"`），以免第二次注册冲掉最初的现场。若既无条目被顶替、`hooks.enabled`
  原本又已是 `true`，则不创建该文件。它只存 hook 条目，不含任何其它配置键；
- 其余每一个键——`provider`、`model`、`modelCatalog`、`plugins`、`mcp`，以及 `hooks` 下
  所有非 `events` 的键——都被逐字节保留，并在写入后用规范化 JSON 快照比对验证
  （`_snapshot_non_events`，比对时排除 `hooks.enabled`）；
- **从不打印任何配置的值**，只打印键名与计数。

### 验证注册结果

```bash
python3 scripts/register-hooks.py --dry-run   # 幂等：已注册时计划应为空/无变化
```

也可以在客户端 **Settings → Plugin Management** 里查看 hook 是否可运行；hook 的触发、
超时、阻断都会记录在 ZCode 日志里（含来源、matcher、结果、耗时与 stderr 预览）。

## 4. 重启客户端与开新会话（必需）

安装与注册**都不会**影响正在运行的会话：

- hook 注册表在会话/客户端启动时读取；
- MCP server 在会话启动时连接；
- `$ZCODE_HOME/AGENTS.md` 与 `agents/*.md` 在会话启动时载入；
- `gov-config/roles.json` 的解析结果会进入 SessionStart 注入的情境。

因此每次做完下列任一变更，都必须**重启 ZCode 客户端并开启新会话**：

- 安装/升级治理包；
- 注册/注销 hook；
- 修改 MCP 配置；
- 填写或修改 `roles.json` / `milestone.json`。

不重启的典型症状：hook 明明注册了却不触发；`mcp__codegraph__*` 工具不出现；
SessionStart 情境仍是旧的 role 状态。

## 5. 回滚

```bash
python3 scripts/register-hooks.py --unregister
python3 scripts/install-governance.py --source . --zcode-home "$HOME/.zcode" --rollback
```

顺序建议先注销 hook 再回滚文件，避免出现「hook 指向已被移走的脚本」的中间态。

`--rollback` 的语义：

- 若存在 `$ZCODE_HOME/gov.zgov-backup`：删除当前 `gov/`，把备份改名回 `gov/`；
- 若不存在备份：直接删除当前 `gov/`（即回到未安装状态）；
- sidecar 逐个从 `<name>.zgov-backup` 还原，输出里 `sidecar_files` 列出被还原的文件；
- `gov-config/` 报 `preserved`——**回滚不会删除你的配置**；
- 输出 `{"status":"ROLLED_BACK", ...}`。

`--unregister` 从 `hooks.events` 里剥离指向 `zcode_hook.py` 的条目，其余键不动；随后若
`$ZCODE_HOME/gov-config/displaced-hooks.json` 存在，就把注册时被顶替的现役条目**按事件
追加回各自数组末尾**（因此同一事件内的相对顺序可能与注册前不同，但条目集合完全一致），
并把 `hooks.enabled` 还原为注册前的原值，最后删除该 sidecar。输出里 `restored` 是还原的
条目数；sidecar 不存在时 `restored: 0`，行为与只剥离一致。

回滚后同样需要重启客户端并开新会话。

## 6. 升级

升级就是「重新装一遍」：

```bash
git -C /path/to/zcode-governance-infra pull
cd /path/to/zcode-governance-infra
python3 scripts/verify-governance.py --repo .
python3 scripts/generate-manifest.py --repo . --check
python3 scripts/install-governance.py --source . --zcode-home "$HOME/.zcode" --dry-run
python3 scripts/install-governance.py --source . --zcode-home "$HOME/.zcode"
python3 scripts/register-hooks.py --dry-run
python3 scripts/register-hooks.py
```

要点：

- `gov/` 被整体替换，上一版自动留在 `gov.zgov-backup`（只保留一份）；
- sidecar 的上一版留在 `<name>.zgov-backup`；
- `gov-config/` 原样保留；如果新版本引入了新的配置字段，需要**手工**参照
  `gov/roles.example.json` / `gov/milestone.example.json` 补齐——安装器不会替你改。
- hook 清单变化时必须重跑 `register-hooks.py`，否则新事件不会生效。

## 7. 本包不做的事

本包安装的是**路由策略与校验器**。它不 vendor、也不静默安装 CodeGraph、Semble、`rtk`
或 `rg`。

- 本地 CLI 的存在性可以只读探测；Semble 通常是 MCP 能力，在 orchestrator 提供当期观测
  之前保持 `unknown`；
- CodeGraph 索引的构建/同步是**独立的、项目本地的、需授权的** mutation，doctor 从不执行；
- 未知或 degraded 的健康状态保持可见，fallback 需要真实 reason code 加 evidence
  reference。

本包也不能授予当前 ZCode surface 没有暴露的模型或工具。
