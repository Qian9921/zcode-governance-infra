#!/usr/bin/env python3
"""ZCode hook 统一入口（仅用标准库）。

子命令：
  pre-bash       PreToolUse/Bash：PR 合并硬门禁、GitHub 双身份守卫、凭据保护、rtk 无缝改写
  pre-file       PreToolUse/Read|Edit|Write：凭据保护
  pre-tool       PreToolUse/Agent|mcp__codegraph.*|mcp__semble.*|Grep：委派守卫 + 路由 receipt
  post-agent     PostToolUse/Agent：委派结果 receipt（不阻断）
  session-start  SessionStart：infra 状态自检 + v16 治理情境注入
  review-pass    辅助命令（非 hook）：k3-review APPROVE 后由主智能体显式调用，写合并 marker

设计原则：
  - 快速预筛：无关命令 <5ms 直通；只有命中 gh/git/敏感路径才深查。
  - merge gate fail-closed（查不了就不许合并）；identity guard 用缓存 fail-open（网络抖动不挡工作）。
  - rtk 改写只输出 updatedInput，不带 permissionDecision，用户正常权限确认流程不受影响。
  - stdout 是严格 schema：只允许 hookEventName / permissionDecision /
    permissionDecisionReason / additionalContext / updatedInput。多一个键整个 hook 效果被丢弃。
    所有 decision / reason_code / route 一律下沉到 receipt JSONL，绝不出现在 stdout。
  - 退出码恒为 0（review-pass 除外）；deny 通过 permissionDecision 表达。
  - receipt 审计：hook-receipt.v16 白名单投影，命令原文绝不落盘，best-effort 不影响决策。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_receipt  # noqa: E402
import pre_tool_use_policy as policy  # noqa: E402
import session_context  # noqa: E402
from delegation_contract import FORBIDDEN_CHILD_ROLES  # noqa: E402

HOME = Path.home()
HOOK_DIR = HOME / ".zcode" / "hooks"
STATE_DIR = HOOK_DIR / "state"
MARKER_DIR = STATE_DIR / "review-markers"
RECEIPT_DIR = HOOK_DIR / "receipts"
LOGIN_CACHE = STATE_DIR / "gh-login-cache.json"
LOGIN_CACHE_TTL_SEC = 60
MARKER_TTL_SEC = 7 * 24 * 3600

# ---- 治理/开发身份：延迟读取，独立于 zgov（hook 必须在 zgov 不可用时也能跑）----
# 真实身份只存在于用户本机 gov-config/roles.json 的 identities 块；仓库内一律是占位符。
IDENTITY_PLACEHOLDERS = {"dev": "<TBD:dev_login>", "governance": "<TBD:gov_login>"}
_identity_cache = None


def _identity_path():
    env_path = os.environ.get("ZGOV_ROLES_PATH")
    if env_path:
        return Path(env_path)
    if "ZCODE_HOME" in os.environ:
        return Path(os.environ["ZCODE_HOME"]) / "gov-config" / "roles.json"
    return HOME / ".zcode" / "gov-config" / "roles.json"


def _load_identities():
    """只读 roles.json 的 identities 两个键；任何异常都回落到占位符。"""
    global _identity_cache
    if _identity_cache is not None:
        return _identity_cache
    identities = dict(IDENTITY_PLACEHOLDERS)
    try:
        target = _identity_path()
        if target.is_file():
            block = json.loads(target.read_text(encoding="utf-8")).get("identities")
            if isinstance(block, dict):
                for key in identities:
                    value = block.get(key)
                    if isinstance(value, str) and value:
                        identities[key] = value
    except Exception:
        pass
    _identity_cache = identities
    return identities


def identity_login(kind):
    """返回 dev/governance 身份的登录名（未配置时为占位符）。"""
    return _load_identities()[kind]


def identity_unconfigured(kind):
    """True 表示该身份仍是占位符，无法确认，必须 fail-closed 拦截。"""
    return identity_login(kind).startswith("<TBD")

MAX_AGENT_DEPTH = 1
ADDITIONAL_CONTEXT_LIMIT = session_context.ADDITIONAL_CONTEXT_LIMIT

# ---- 三工具就绪探测（session-start）----
# 每个子进程单独设 timeout；三工具总预算 ≤8s（hook 超时 20000ms 的安全余量）。
# 任何超时/异常一律当作该项 fail，绝不让 hook 挂住或抛出。
PROBE_TIMEOUT = 2.0            # 每个子进程探测超时（秒）
PROBE_BUDGET = 8.0             # 三工具探测总预算（秒）
TOOLCHECK_READY_MSG = "三工具就绪，路由强制生效"
TOOLCHECK_BLOCK_MSG = ("⚠️ 工具未就绪：仓库分析/实现/评审前必须先修复；"
                       "不得以\"工具不可用\"为由跳过路由")
# 预授权提示语。init/sync 用占位拼接，保证源码不含 "codegraph 索引导入/同步" 命令字面量
# （tests/hooks 静态检查证明 hook 只读、绝不自行建索引）。
TOOLCHECK_PREAUTH_MSG = ("索引已预授权：主智能体应立即自动执行 codegraph "
                         "{}/{}，无需再向用户请示").format("init", "sync")

T0 = time.time()


def find_gh():
    for p in (os.environ.get("GH_PATH"), str(HOME / ".local/bin" / "gh"),
              "/usr/local/bin/gh", "/usr/bin/gh"):
        if p and Path(p).exists():
            return p
    return "gh"


GH = find_gh()


# ---------------------------------------------------------------- receipt
def receipt(event, decision, reason_code, label="", command="",
            tool=None, route=None, identifiers=None):
    """写一条 hook-receipt.v16。

    ``label``/``command`` 仅保留调用点兼容性：它们不在 v16 白名单里，因此既不会
    落盘也不会被哈希后落盘（旧格式的 12 位 cmd_sha256 已随白名单一起取消）。
    ``decision`` 只有 ``deny`` 会原样保留，其余（pass/rewrite/gate-pass/warn/…）
    都映射为 ``allow``，精确语义由 ``reason_code`` 承载。
    best-effort：写失败绝不改变 hook 决策。
    """
    try:
        value = hook_receipt.receipt(
            event,
            os.environ.get("ZCODE_MODEL", "unknown"),
            tool=tool,
            decision="deny" if decision == "deny" else "allow",
            reason_code=reason_code,
            route_code=route,
            identifiers=identifiers if isinstance(identifiers, dict) else None,
        )
        hook_receipt.write_receipt(value)
    except Exception:
        pass  # best-effort，永不改变 hook 决策


# ---------------------------------------------------------------- io helpers
def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def deny(reason, reason_code, label="", command="",
         tool=None, route=None, identifiers=None):
    receipt("PreToolUse", "deny", reason_code, label, command,
            tool=tool, route=route, identifiers=identifiers)
    emit({
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    })
    sys.exit(0)


def run(cmd, timeout=8):
    """运行只读命令，返回 (ok, stdout)。任何失败都安静返回。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout or "").strip()
    except Exception:
        return False, ""


def gh_login(fresh=False):
    """当前 active login。fresh=False 时可用 60s 缓存。"""
    if not fresh and LOGIN_CACHE.exists():
        try:
            c = json.loads(LOGIN_CACHE.read_text())
            if time.time() - c.get("ts", 0) < LOGIN_CACHE_TTL_SEC and c.get("login"):
                return c["login"]
        except Exception:
            pass
    ok, out = run([GH, "api", "user", "--jq", ".login"], timeout=8)
    if not ok or not out:
        return None
    login = out.splitlines()[0].strip()
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LOGIN_CACHE.write_text(json.dumps({"login": login, "ts": time.time()}))
        os.chmod(LOGIN_CACHE, 0o600)
    except Exception:
        pass
    return login


# ---------------------------------------------------------------- credential guard
SENSITIVE_PATHS = [
    "/.ssh/", "/.gnupg/", "/.aws/credentials", "/.netrc",
    "/.config/gh/hosts.yml", "/.codex/gateway-credential",
    "/.zcode/cli/config.json", "/etc/shadow", "/etc/sudoers",
]
# 受限例外：本仓库的 hooks.events 合并器只能改 ZCode 的 hook 注册块。
ZCODE_CONFIG_PATH = "/.zcode/cli/config.json"
REGISTER_HOOKS_RE = re.compile(r"register-hooks\.py")


def expand_cmd(cmd):
    return cmd.replace("$HOME", str(HOME)).replace("~/", str(HOME) + "/")


def credential_hits(text):
    """返回命中的全部敏感路径（保序）。"""
    t = expand_cmd(text)
    return [p for p in SENSITIVE_PATHS
            if p in t or (p.startswith("/.") and (HOME.as_posix() + p) in t)]


def credential_hit(text):
    hits = credential_hits(text)
    return hits[0] if hits else None


def credential_allowlisted(cmd, hits):
    """仅当唯一命中是 ZCode config.json 且命令是 register-hooks.py 时放行。"""
    return hits == [ZCODE_CONFIG_PATH] and bool(REGISTER_HOOKS_RE.search(cmd))


# ---------------------------------------------------------------- merge gate
MERGE_RE = re.compile(r"\bgh\s+pr\s+merge\b")


def extract_pr_number(cmd):
    m = re.search(r"/pull/(\d+)", cmd)
    if m:
        return m.group(1)
    seg = cmd[MERGE_RE.search(cmd).end():] if MERGE_RE.search(cmd) else ""
    m = re.search(r"(?<![\w-])(\d+)(?![\w-])", seg)
    return m.group(1) if m else None


def marker_path(repo_slug, pr_number):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", repo_slug)
    return MARKER_DIR / f"{safe}__pr{pr_number}.json"


def merge_gate(cmd):
    """PR 合并硬门禁。fail-closed：身份未配置或任何环节查不了都不许合并。"""
    label = "gh pr merge"
    # 1. 身份：合并是治理动作，必须由治理身份（identities.governance）执行。
    #    身份未配置时无法确认 login，fail-closed 拦截，绝不因未配置而放行。
    if identity_unconfigured("governance"):
        deny("治理身份未配置：请在 gov-config/roles.json 的 identities 里填写（governance），"
             "配置前 PR 合并一律拦截（fail-closed）。",
             "merge_gate.identity_unconfigured", label, cmd, tool="Bash")
    gov_login = identity_login("governance")
    login = gh_login(fresh=True)
    if login != gov_login:
        deny(f"PR 合并必须由治理身份 {gov_login} 执行，当前 active login 是 {login or '未知'}。"
             f"直接运行 gh auth switch --user {gov_login} 后重试（切换已预授权）。",
             "merge_gate.identity", label, cmd, tool="Bash")

    # 2. 解析 PR 并 live 查 head（不用缓存，merge 罕见且必须新鲜）
    pr_num = extract_pr_number(cmd)
    view_cmd = [GH, "pr", "view"] + ([pr_num] if pr_num else []) + \
        ["--json", "number,headRefOid,url"]
    ok, out = run(view_cmd, timeout=15)
    if not ok:
        deny("无法查询 PR 状态（不在仓库中、PR 不存在或网络故障）。"
             "merge gate 为 fail-closed：查不了就不许合并。",
             "merge_gate.pr_lookup_failed", label, cmd, tool="Bash")
    try:
        pr = json.loads(out)
        live_sha = pr["headRefOid"]
        number = str(pr["number"])
        m = re.search(r"github\.com/([^/]+/[^/]+)/pull/", pr.get("url", ""))
        repo_slug = m.group(1) if m else "unknown/unknown"
    except Exception:
        deny("PR 查询结果无法解析。merge gate fail-closed。",
             "merge_gate.pr_parse_failed", label, cmd, tool="Bash")

    # 3. 校验 marker：存在、未过期、head SHA 精确匹配
    mp = marker_path(repo_slug, number)
    if not mp.exists():
        deny(f"未找到 gov-reviewer 的 APPROVE marker（PR #{number}）。流程："
             f"① 用 Agent 工具调用 gov-reviewer 审查此 PR；② 结论为 APPROVE 后运行："
             f"python3 ~/.zcode/hooks/zcode_hook.py review-pass --repo {repo_slug} --pr {number} --sha <Head-SHA>；"
             f"③ 重新执行合并。", "merge_gate.no_marker", label, cmd, tool="Bash")
    try:
        marker = json.loads(mp.read_text())
    except Exception:
        deny("marker 文件损坏，需重新审查并重新登记。",
             "merge_gate.marker_corrupt", label, cmd, tool="Bash")
    if time.time() - marker.get("approved_at", 0) > MARKER_TTL_SEC:
        deny("marker 已过期（>7 天），需重新审查。",
             "merge_gate.marker_expired", label, cmd, tool="Bash")
    if marker.get("head_sha") != live_sha:
        deny(f"head 漂移，verdict 作废：APPROVE 时 SHA={marker.get('head_sha', '?')[:12]}，"
             f"当前 SHA={live_sha[:12]}。必须重新调用 gov-reviewer 审查新 head，"
             f"通过后重新登记 marker。", "merge_gate.head_drift", label, cmd, tool="Bash")

    receipt("PreToolUse", "gate-pass", "merge_gate.ok", label, cmd, tool="Bash")
    # 不输出 permissionDecision=allow：走用户正常权限确认流程，安静放行
    sys.exit(0)


# ---------------------------------------------------------------- identity guard
GOV_ACTION_RE = re.compile(r"\bgh\s+pr\s+(review|merge)\b")
DEV_ACTION_RE = re.compile(
    r"\bgh\s+(pr\s+(create|edit|comment|ready|close|reopen|lock)"
    r"|issue\s+(create|edit|comment|close|reopen|delete)"
    r"|release\s+(create|edit|delete|upload)"
    r"|repo\s+(create|fork|delete|archive|rename))"
    r"|\bgit\s+push\b")


def strip_rtk(cmd):
    return re.sub(r"^\s*rtk\s+", "", cmd)


def identity_guard(cmd):
    """双身份守卫。身份未配置时 fail-closed（无法确认身份，绝不放行）；
    gh 查询失败时 fail-open（网络抖动不挡工作），仅记 receipt。"""
    c = strip_rtk(cmd)
    need = None
    if GOV_ACTION_RE.search(c):
        need = ("governance", "治理动作（review/approve/merge）")
    elif DEV_ACTION_RE.search(c):
        need = ("dev", "开发动作（push/开 PR/评论/release）")
    if not need:
        return
    if identity_unconfigured(need[0]):
        deny(f"GitHub 身份未配置：{need[1]}需要 {need[0]} 身份，但 gov-config/roles.json 的 "
             f"identities.{need[0]} 仍是占位符。请在 gov-config/roles.json 的 identities 里填写。",
             "identity.unconfigured", need[1], cmd, tool="Bash")
    expected = identity_login(need[0])
    login = gh_login(fresh=False)
    if login is None:
        receipt("PreToolUse", "warn", "identity.gh_unreachable", need[1], cmd, tool="Bash")
        return
    if login != expected:
        deny(f"GitHub 双身份隔离：{need[1]}必须由 {expected} 执行，当前 active login 是 {login}。"
             f"直接运行 gh auth switch --user {expected} 后重试（切换已预授权）；但绝不用一个身份干另一个身份的活。",
             "identity.mismatch", need[1], cmd, tool="Bash")
    receipt("PreToolUse", "pass", "identity.ok", need[1], cmd, tool="Bash")


# ---------------------------------------------------------------- rtk rewrite
RTK_GIT_SUB = {"status", "log", "diff", "push"}
RTK_SIMPLE_CMDS = {"pytest", "jest", "tsc", "ls", "grep", "find"}
RTK_TWO_WORD = {("go", "test"), ("cargo", "test"), ("cargo", "clippy"),
                ("ruff", "check"), ("docker", "ps")}
COMPOUND_CHARS = re.compile(r"[|&;><`$()\n]")


def rtk_rewrite(tool_input):
    """简单受支持命令 → rtk 前缀。只输出 updatedInput，不动权限流程。"""
    cmd = (tool_input.get("command") or "").strip()
    if not cmd or cmd.startswith("rtk ") or COMPOUND_CHARS.search(cmd):
        return None
    tokens = cmd.split()
    if not tokens or "=" in tokens[0] or tokens[0] in ("sudo", "time", "xargs"):
        return None
    hit = False
    if tokens[0] == "git" and len(tokens) > 1 and tokens[1] in RTK_GIT_SUB:
        hit = True
    elif tokens[0] in RTK_SIMPLE_CMDS:
        hit = True
    elif len(tokens) > 1 and (tokens[0], tokens[1]) in RTK_TWO_WORD:
        hit = True
    if not hit:
        return None
    new_input = dict(tool_input)
    new_input["command"] = "rtk " + cmd
    receipt("PreToolUse", "rewrite", "rtk.auto_rewrite", tokens[0], cmd,
            tool="Bash", route="rtk")
    return new_input


# ---------------------------------------------------------------- pre-bash
def pre_bash():
    data = stdin_json()
    tool_input = data.get("tool_input") or {}
    cmd = tool_input.get("command") or ""
    if not cmd:
        sys.exit(0)

    # 1. 凭据保护（最高优先）
    hits = credential_hits(cmd)
    if hits:
        if credential_allowlisted(cmd, hits):
            receipt("PreToolUse", "pass", "credential.allowlisted_register_hooks",
                    ZCODE_CONFIG_PATH, cmd, tool="Bash", identifiers=data)
        else:
            deny(f"禁止读写凭据/敏感文件（命中 {hits[0]}）。如确属必要，请向用户请示。",
                 "credential.blocked", hits[0], cmd, tool="Bash", identifiers=data)

    # 2. PR 合并硬门禁
    if MERGE_RE.search(cmd):
        merge_gate(cmd)  # 通过则内部 sys.exit(0)

    # 3. 双身份守卫
    if "gh " in cmd or cmd.startswith("gh") or "git push" in cmd:
        identity_guard(cmd)

    # 4. rtk 无缝改写
    new_input = rtk_rewrite(tool_input)
    if new_input:
        emit({"hookEventName": "PreToolUse", "updatedInput": new_input})
    sys.exit(0)


# ---------------------------------------------------------------- pre-file
def pre_file():
    data = stdin_json()
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not path:
        sys.exit(0)
    hit = credential_hit(os.path.expanduser(path))
    if hit:
        deny(f"禁止读写凭据/敏感文件（命中 {hit}）。如确属必要，请向用户请示。",
             "credential.blocked", hit, path, tool="Read", identifiers=data)
    sys.exit(0)


# ---------------------------------------------------------------- pre-tool
AGENT_TOOL_NAMES = {"agent", "task"}


def agent_depth():
    """当前进程所处的委派深度；缺省/不可解析视为 0（顶层）。"""
    try:
        return max(0, int(os.environ.get("ZGOV_AGENT_DEPTH") or 0))
    except (TypeError, ValueError):
        return 0


def pre_tool():
    """PreToolUse/Agent|mcp__codegraph.*|mcp__semble.*|Grep：委派守卫 + 路由 receipt。"""
    data = stdin_json()
    tool_name = data.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        tool_name = data.get("tool") if isinstance(data.get("tool"), str) else ""
    tool_input = data.get("tool_input")
    if tool_input is None:
        tool_input = data.get("args")
    if not isinstance(tool_input, dict):
        tool_input = {}

    # 路由只看归一化后的工具名（Bash 只认直接可执行文件），参数对判定零影响。
    route = policy.route_for(tool_name, tool_input)
    routed_reason = ("pre_tool.routed" if policy.routing_available()
                     else "routing_module_unavailable")

    result = policy.decide(tool_name, tool_input)
    if result["decision"] == "deny":
        deny("该工具属于显式子动作，必须由父级授权后执行。",
             result["reason_code"], tool_name[:80], "",
             tool=tool_name, route=route, identifiers=data)

    if tool_name.strip().lower() in AGENT_TOOL_NAMES:
        if agent_depth() >= MAX_AGENT_DEPTH:
            deny(f"嵌套委派深度超限（max_depth={MAX_AGENT_DEPTH}）：子智能体不得再 spawn Agent。",
                 "delegation.depth_exceeded", tool_name[:80], "",
                 tool=tool_name, route=route, identifiers=data)
        subagent_type = tool_input.get("subagent_type")
        if isinstance(subagent_type, str) and subagent_type in FORBIDDEN_CHILD_ROLES:
            deny("该子智能体角色已被治理配置禁止委派。",
                 "delegation.forbidden_child_role", tool_name[:80], "",
                 tool=tool_name, route=route, identifiers=data)

    receipt("PreToolUse", "pass", routed_reason, tool_name[:80], "",
            tool=tool_name, route=route, identifiers=data)
    # 安静放行：不输出 permissionDecision，走用户正常权限确认流程。
    sys.exit(0)


# ---------------------------------------------------------------- post-agent
def post_agent():
    """PostToolUse/Agent：只记 receipt，绝不阻断（工具已执行完，阻断无意义）。"""
    data = stdin_json()
    tool_name = data.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        tool_name = "Agent"
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    subagent_type = tool_input.get("subagent_type")
    digest = hashlib.sha256(str(subagent_type or "").encode("utf-8")).hexdigest()[:16]
    ok = data.get("success")
    if ok is None:
        ok = not data.get("error") and not data.get("is_error")
    # subagent_type 只以哈希形式出现，且必须借道白名单内的 reason_code 字段。
    receipt("PostToolUse", "pass",
            f"post_agent.{'ok' if ok else 'fail'}.{digest}",
            tool_name[:80], "", tool=tool_name, identifiers=data)
    sys.exit(0)


# ---------------------------------------------------------------- 三工具就绪探测
def _probe_run(cmd, deadline, cwd=None):
    """运行一次只读探测。超时/异常→(-1,"")；预算耗尽→None（调用方按 fail 处理）。"""
    remaining = deadline - time.monotonic()
    if remaining <= 0.05:
        return None
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                           timeout=min(PROBE_TIMEOUT, remaining))
        return p.returncode, (p.stdout or "")
    except Exception:
        return -1, ""


def _git_toplevel(cwd, deadline):
    """返回 cwd 所属 git 仓库根；非 git 仓库或预算耗尽返回 None。"""
    r = _probe_run(["git", "rev-parse", "--show-toplevel"], deadline, cwd=cwd)
    if not r or r[0] != 0:
        return None
    lines = r[1].strip().splitlines()
    return lines[0] if lines else None


def probe_rtk(cwd, in_git, deadline):
    """rtk 就绪探测。3 项全过才算 ready；不在 git 仓库时只验版本，状态 degraded。"""
    binary = shutil.which("rtk")
    if not binary:
        return {"tool": "rtk", "status": "blocked", "reason": "RTK_NOT_FOUND", "version": None}
    rc, out = _probe_run([binary, "--version"], deadline, cwd) or (-1, "")
    if rc != 0 or not out.strip():
        return {"tool": "rtk", "status": "blocked", "reason": "RTK_VERSION_FAILED", "version": None}
    first = out.strip().splitlines()[0].split()
    version = first[-1] if first else None
    if not in_git:
        # 第 2/3 项需要 git 仓库，跳过；只报第 1 项，状态记 degraded 而非 fail。
        return {"tool": "rtk", "status": "degraded", "reason": "RTK_VERSION_OK", "version": version}
    bare = _probe_run(["git", "rev-parse", "HEAD"], deadline, cwd)
    if bare is None:
        # 预算耗尽：fail-closed，与 rtk 故障同等对待。
        return {"tool": "rtk", "status": "blocked", "reason": "RTK_OUTPUT_MISMATCH", "version": version}
    if bare[0] != 0:
        # 裸 git 自己也拿不到 HEAD（零 commit 仓库的 unborn HEAD）：不存在可比对基线，
        # rtk 没撒谎，只是仓库没有 HEAD。第 2 项输出比对跳过（RTK_HEAD_UNBORN_MATCH_SKIPPED），
        # 第 1/3 项照常执行——第 3 项失败保留仍是真正的证据链保护。
        head_unborn = True
    else:
        head_unborn = False
        positive = _probe_run([binary, "git", "rev-parse", "HEAD"], deadline, cwd)
        if (positive is None or positive[0] != 0 or not positive[1].strip()
                or positive[1].strip() != bare[1].strip()):
            # 裸 git 拿得到 HEAD 而 rtk 失败/输出不同：这才是真正的 rtk 故障，
            # 绝不能被 unborn 分支掩盖，必须仍报 RTK_OUTPUT_MISMATCH。
            return {"tool": "rtk", "status": "blocked", "reason": "RTK_OUTPUT_MISMATCH", "version": version}
    negative = _probe_run([binary, "git", "rev-parse", "--verify",
                           "refs/heads/__zgov_probe_missing__"], deadline, cwd)
    if negative is None or negative[0] == 0:
        # 失败被吞成 0 即 false-green，整个 shell 证据链作废。
        return {"tool": "rtk", "status": "blocked", "reason": "RTK_FALSE_GREEN", "version": version}
    if head_unborn:
        return {"tool": "rtk", "status": "ready", "reason": "RTK_HEAD_UNBORN_MATCH_SKIPPED", "version": version}
    return {"tool": "rtk", "status": "ready", "reason": "RTK_READY", "version": version}


def probe_codegraph(repo, deadline):
    """codegraph 就绪探测（对当前 cwd 所属仓库）。只读，绝不 init/sync。"""
    binary = shutil.which("codegraph")
    if not binary:
        return {"tool": "codegraph", "status": "blocked", "reason": "CODEGRAPH_NOT_FOUND", "version": None}
    rc, out = _probe_run([binary, "status", "--json", repo], deadline) or (-1, "")
    if rc != 0 or not out.strip():
        return {"tool": "codegraph", "status": "blocked", "reason": "CODEGRAPH_INDEX_INVALID", "version": None}
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return {"tool": "codegraph", "status": "blocked", "reason": "CODEGRAPH_INDEX_INVALID", "version": None}
    if not isinstance(data, dict):
        return {"tool": "codegraph", "status": "blocked", "reason": "CODEGRAPH_INDEX_INVALID", "version": None}
    version = data.get("version") if isinstance(data.get("version"), str) else None
    if data.get("initialized") is not True or not (Path(repo) / ".codegraph").is_dir():
        return {"tool": "codegraph", "status": "degraded", "reason": "CODEGRAPH_NOT_INDEXED", "version": version}
    index = data.get("index")
    if not (isinstance(index, dict) and index.get("state") == "complete"):
        return {"tool": "codegraph", "status": "blocked", "reason": "CODEGRAPH_INDEX_INVALID", "version": version}
    pending = data.get("pendingChanges")
    if not (isinstance(pending, dict)
            and all(isinstance(pending.get(k), int) and pending.get(k) == 0
                    for k in ("added", "modified", "removed"))):
        return {"tool": "codegraph", "status": "degraded", "reason": "CODEGRAPH_STALE", "version": version}
    project = data.get("projectPath")
    if not (isinstance(project, str) and Path(project).resolve(strict=False) == Path(repo).resolve()):
        return {"tool": "codegraph", "status": "blocked", "reason": "CODEGRAPH_WRONG_PROJECT", "version": version}
    return {"tool": "codegraph", "status": "ready", "reason": "CODEGRAPH_READY", "version": version}


def probe_semble(deadline):
    """semble 就绪探测。只做 --help 表面校验；完整 sentinel 校验留给 toolchain-doctor。"""
    binary = shutil.which("semble")
    if not binary:
        return {"tool": "semble", "status": "blocked", "reason": "SEMBLE_NOT_FOUND", "version": None}
    rc, out = _probe_run([binary, "--help"], deadline) or (-1, "")
    if rc != 0 or "search" not in out:
        return {"tool": "semble", "status": "blocked", "reason": "SEMBLE_COMMAND_SURFACE_FAILED", "version": None}
    return {"tool": "semble", "status": "ready", "reason": "SEMBLE_READY", "version": None}


def probe_toolchain(cwd):
    """三工具就绪探测。总预算 ≤PROBE_BUDGET；超时/异常一律 fail-closed。只读。"""
    deadline = time.monotonic() + PROBE_BUDGET
    git_root = _git_toplevel(cwd, deadline)
    repo = git_root or str(Path(cwd).resolve())
    results = [
        probe_codegraph(repo, deadline),
        probe_semble(deadline),
        probe_rtk(cwd, git_root is not None, deadline),
    ]
    results.sort(key=lambda r: {"rtk": 0, "codegraph": 1, "semble": 2}[r["tool"]])
    return results


def toolchain_line(results):
    """[工具就绪] 一行：ready 显示 ready[(版本)]，不 ready 显示 reason code。"""
    segs = []
    for r in results:
        if r["status"] == "ready":
            if r["reason"] == "RTK_HEAD_UNBORN_MATCH_SKIPPED":
                # 零 commit（unborn HEAD）仓库：无 HEAD 可比对基线，输出比对跳过，
                # 显式标注让状态可见而不是静默。
                note = "HEAD未出生跳过输出比对"
                segs.append(f"{r['tool']} ready("
                            + (f"{r['version']}, {note})" if r["version"] else f"{note})"))
            else:
                segs.append(f"{r['tool']} ready"
                            + (f"({r['version']})" if r["version"] else ""))
        elif r["status"] == "degraded" and r["reason"] == "RTK_VERSION_OK":
            # rtk 不在 git 仓库：只报版本，状态 degraded。
            segs.append(f"{r['tool']} degraded"
                        + (f"({r['version']})" if r["version"] else ""))
        else:
            segs.append(f"{r['tool']} {r['reason']}")
    line = "[工具就绪] " + " | ".join(segs)
    if all(r["status"] == "ready" for r in results):
        line += " || " + TOOLCHECK_READY_MSG
    else:
        line += " || " + TOOLCHECK_BLOCK_MSG
    if any(r["tool"] == "codegraph"
           and r["reason"] in ("CODEGRAPH_NOT_INDEXED", "CODEGRAPH_STALE")
           for r in results):
        line += " || " + TOOLCHECK_PREAUTH_MSG
    return line


def toolchain_status(results):
    """聚合状态：全 ready→ready；有 blocked→blocked；否则 degraded。"""
    if all(r["status"] == "ready" for r in results):
        return "ready"
    if any(r["status"] == "blocked" for r in results):
        return "blocked"
    return "degraded"


def _toolcheck_receipt(status, results, data):
    """探测结果 receipt：reason_code=session.toolcheck.<状态>，reason=三工具聚合。
    只放工具名 + reason code（无原始输出、路径）。best-effort 不改变 hook 决策。
    """
    try:
        value = hook_receipt.receipt(
            "SessionStart", os.environ.get("ZCODE_MODEL", "unknown"),
            decision="allow", reason_code="session.toolcheck." + status,
            identifiers=data,
        )
        value["reason"] = ".".join(f"{r['tool']}:{r['reason']}" for r in results)
        hook_receipt.write_receipt(value)
    except Exception:
        pass


# ---------------------------------------------------------------- session-start
def cmd_session_start():
    data = stdin_json()
    parts = []

    login = gh_login(fresh=False)
    ok, auth_out = run([GH, "auth", "status"], timeout=8)
    accounts = re.findall(r"account (\S+)", auth_out) if ok else []
    id_state = f"gh active={login or '未知'}"
    if accounts:
        id_state += f"（已登录: {', '.join(sorted(set(accounts)))}）"
        if not identity_unconfigured("dev") and identity_login("dev") not in accounts:
            id_state += f"；⚠️ 开发身份 {identity_login('dev')} 未登录，开发类 GitHub 动作将被门禁拦截"
    parts.append(id_state)

    cwd = data.get("cwd") or os.getcwd()
    results = probe_toolchain(cwd)
    parts.append(toolchain_line(results))

    if (Path(cwd) / ".git").exists():
        ok, branch = run(["git", "-C", cwd, "branch", "--show-current"], timeout=3)
        if ok and branch:
            ok2, dirty = run(["git", "-C", cwd, "status", "--porcelain"], timeout=5)
            n = len([l for l in dirty.splitlines() if l.strip()]) if ok2 else 0
            parts.append(f"仓库分支: {branch}" + (f"（{n} 处未提交改动）" if n else "（干净）"))

    markers = list(MARKER_DIR.glob("*.json")) if MARKER_DIR.exists() else []
    fresh_markers = [m for m in markers
                     if time.time() - m.stat().st_mtime < MARKER_TTL_SEC]
    if fresh_markers:
        parts.append(f"有效 review marker: {len(fresh_markers)} 个")

    ctx = "[infra 状态] " + " | ".join(parts) + " || " + v16_context()
    ctx = ctx[:ADDITIONAL_CONTEXT_LIMIT]
    _toolcheck_receipt(toolchain_status(results), results, data)
    emit({"hookEventName": "SessionStart", "additionalContext": ctx})
    sys.exit(0)


def v16_context():
    """v16 治理上下文（路由表 / preflight / review-runtime / roles）。"""
    routing = session_context.ROUTING_GUIDANCE
    preflight = session_context.TOOL_PREFLIGHT_GUIDANCE
    review = session_context.REVIEW_RUNTIME_GUIDANCE
    chunks = [
        "[v16 路由] 结构/符号/调用/影响→CodeGraph({kg}); 语义/相似实现→Semble({sm});"
        " shell 输出→{sh}; 精确文本/日志/配置→{rg}".format(
            kg=routing["known_structure"], sm=routing["unknown_semantic_or_similar"],
            sh=routing["shell_display"], rg=routing["exact_text_log_config"]),
        "[preflight] 仓库工作前需 {schema} status={status}，{tools} 三工具强制；"
        "随后需 receipt-backed {usage}".format(
            schema=preflight["schema"], status=preflight["strict_ready_status"],
            tools="/".join(preflight["mandatory_tools"]), usage=preflight["usage_schema"]),
        "[review-runtime] 初次/升级高风险→{hi}；契约稳定 delta→{dl}；正式 review {n} 次、"
        "重复全量 review {d} 次".format(
            hi=review["initial_high"], dl=review["delta_continuation"],
            n=review["formal_review_calls"], d=review["duplicate_full_scope_reviews"]),
        "[委派] max_depth=1，子智能体不得再 spawn Agent",
    ]
    resolved = session_context.roles_resolved_state()
    if resolved is not None:
        chunks.append("[roles] " + ("已解析" if resolved else "未解析（含占位符）"))
    return " | ".join(chunks)


# ---------------------------------------------------------------- review-pass helper
def cmd_review_pass(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="zcode_hook.py review-pass")
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--sha", required=True, help="k3-review 结论中记录的 40 位 Head-SHA")
    ap.add_argument("--skip-verify", action="store_true",
                    help="跳过 live 校验（仅限离线等特殊情况，且须用户明确同意）")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        print("错误：--sha 必须是 40 位小写 hex commit SHA", file=sys.stderr)
        sys.exit(1)
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", args.repo):
        print("错误：--repo 必须是 owner/name 形式", file=sys.stderr)
        sys.exit(1)

    if not args.skip_verify:
        ok, out = run([GH, "pr", "view", str(args.pr), "--repo", args.repo,
                       "--json", "headRefOid"], timeout=15)
        if not ok:
            print("错误：无法查询 PR（网络/权限问题），未写 marker。", file=sys.stderr)
            sys.exit(1)
        live = json.loads(out)["headRefOid"]
        if live != args.sha:
            print(f"错误：live head SHA={live} 与登记 SHA 不一致，head 已漂移。"
                  f"请对最新 head 重新审查后再登记。", file=sys.stderr)
            sys.exit(1)

    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    mp = marker_path(args.repo, args.pr)
    mp.write_text(json.dumps({
        "repo": args.repo,
        "pr": args.pr,
        "head_sha": args.sha,
        "reviewer": "gov-reviewer",
        "approved_at": time.time(),
        "verified_live": not args.skip_verify,
    }, ensure_ascii=False, indent=2))
    os.chmod(mp, 0o600)
    receipt("review-pass", "pass", "marker.written", f"{args.repo}#pr{args.pr}")
    if identity_unconfigured("governance"):
        print(f"✅ marker 已登记：{args.repo} PR #{args.pr} @ {args.sha[:12]}。"
              f"在 head 不变的前提下，merge gate 将放行本次合并（仍需在 gov-config/roles.json 的 "
              f"identities.governance 配置治理身份后才能合并）。")
    else:
        print(f"✅ marker 已登记：{args.repo} PR #{args.pr} @ {args.sha[:12]}。"
              f"在 head 不变的前提下，merge gate 将放行本次合并（仍需治理身份 {identity_login('governance')}）。")


# ---------------------------------------------------------------- main
_STDIN_CACHE = None


def stdin_json():
    global _STDIN_CACHE
    if _STDIN_CACHE is None:
        try:
            _STDIN_CACHE = json.loads(sys.stdin.read() or "{}")
        except Exception:
            _STDIN_CACHE = {}
        if not isinstance(_STDIN_CACHE, dict):
            _STDIN_CACHE = {}
    return _STDIN_CACHE


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    sub = sys.argv[1]
    try:
        if sub == "pre-bash":
            pre_bash()
        elif sub == "pre-file":
            pre_file()
        elif sub == "pre-tool":
            pre_tool()
        elif sub == "post-agent":
            post_agent()
        elif sub == "session-start":
            cmd_session_start()
        elif sub == "review-pass":
            cmd_review_pass(sys.argv[2:])
        else:
            print(f"未知子命令: {sub}", file=sys.stderr)
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        # 兜底：merge 命令 fail-closed，其余 fail-open（hook 出 bug 不能瘫痪整个会话）
        receipt(sub or "unknown", "error", f"internal.{type(e).__name__}")
        if sub == "pre-bash":
            cmd = ((stdin_json().get("tool_input") or {}).get("command")) or ""
            if MERGE_RE.search(cmd):
                deny(f"hook 内部错误（{type(e).__name__}），merge gate fail-closed 拦截。"
                     f"请检查 hook 脚本或向用户报告。", "hook.internal_error",
                     "gh pr merge", cmd, tool="Bash")
        sys.exit(0)


if __name__ == "__main__":
    main()
