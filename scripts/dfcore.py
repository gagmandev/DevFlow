#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DevFlow 核心库：路径解析、原子 IO、配置、任务状态机、证据校验。

只依赖 Python 3.8+ 标准库。所有脚本共用这一份实现，避免多处逻辑漂移。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = "1.4"
DF_DIRNAME = ".devflow"

PROJECT_STATUSES = [
    "initialized",
    "analyzing",
    "clarifying",
    "requirements_confirmed",
    "planned",
    "in_progress",
    "regression",
    "release_candidate",
    "release_ready",
    "accepted",
    "released",  # 兼容别名，迁移时映射为 accepted
]

# 旧状态 → 新状态。只在显式迁移或读取时使用，不静默改写用户文件。
PROJECT_STATUS_MIGRATIONS = {"released": "accepted"}

# 项目合法状态迁移表（INV-015 / D-06）。任何表外迁移都必须拒绝。
PROJECT_TRANSITIONS = {
    "initialized": {"analyzing", "clarifying", "requirements_confirmed", "planned", "in_progress"},
    "analyzing": {"clarifying", "requirements_confirmed", "planned", "in_progress"},
    "clarifying": {"requirements_confirmed", "planned", "in_progress"},
    "requirements_confirmed": {"requirements_confirmed", "planned", "in_progress"},
    "planned": {"planned", "in_progress", "regression"},
    "in_progress": {"planned", "in_progress", "regression"},
    "regression": {"regression", "release_candidate", "release_ready", "in_progress", "planned"},
    "release_candidate": {"release_candidate", "release_ready", "regression", "in_progress", "planned"},
    "release_ready": {"release_ready", "accepted", "release_candidate", "regression", "in_progress", "planned"},
    "accepted": {"in_progress", "planned"},
}

NODE_STATUSES = [
    "pending",
    "ready",
    "in_progress",
    "needs_review",
    "completed",
    "blocked",
    "failed",
    "superseded",
    "cancelled",
]

TERMINAL_STATUSES = {"completed", "superseded", "cancelled"}

# 合法状态迁移。任何不在这里的迁移都会被拒绝。
TRANSITIONS = {
    "pending": {"ready", "blocked", "cancelled", "superseded"},
    "ready": {"in_progress", "pending", "blocked", "cancelled", "superseded"},
    "in_progress": {"needs_review", "failed", "blocked", "cancelled", "ready"},
    "needs_review": {"completed", "in_progress", "failed", "blocked"},
    "failed": {"in_progress", "blocked", "cancelled", "superseded"},
    "blocked": {"ready", "in_progress", "failed", "cancelled", "superseded"},
    "completed": {"superseded"},
    "superseded": set(),
    "cancelled": set(),
}

PHASES = ["Foundation", "Shared", "Business", "Integration", "Release"]
PRIORITIES = ["P0", "P1", "P2", "P3"]


class DevFlowError(Exception):
    """可预期的业务错误：打印信息后以非零码退出，不打印堆栈。"""


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def dwidth(text: str) -> int:
    """终端显示宽度：中日韩全角字符算 2 列。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    text = "" if text is None else str(text)
    return text + " " * max(0, width - dwidth(text))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path):
    try:
        return json.loads(read_text(path))
    except FileNotFoundError:
        raise DevFlowError(f"文件不存在：{path}")
    except json.JSONDecodeError as exc:
        raise DevFlowError(f"JSON 解析失败：{path}\n  {exc}")


def dump_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def atomic_write_text(path: Path, text: str) -> None:
    """先写临时文件再 os.replace，避免中断导致半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, data) -> None:
    atomic_write_text(path, dump_json(data))


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


#: 质量配置档位。项目在 project.json 里显式选择，不再"一套阈值走天下"。
QUALITY_PROFILES = ("development", "production")


def project_profile(root: Path | None) -> tuple[str, bool]:
    """读项目声明的质量档位，返回 (profile, declared)。

    `declared=False` 表示旧项目没有这个字段——调用方要给出提示，
    不能让它默默以为自己跑的是生产门禁。
    """
    if root is None:
        return "development", False
    path = Path(root) / DF_DIRNAME / "project.json"
    if not path.exists():
        return "development", False
    try:
        data = load_json(path)
    except DevFlowError:
        return "development", False
    value = data.get("quality_profile")
    if isinstance(value, str) and value in QUALITY_PROFILES:
        return value, True
    return "development", False


def resolve_gates(config: dict, profile: str) -> dict:
    """把 quality-gates 配置按档位展开（base + profile 覆盖）。"""
    base = dict(config or {})
    profiles = base.pop("profiles", {}) or {}
    entry = profiles.get(profile) or {}
    override = entry.get("gates") if isinstance(entry, dict) else None
    if not isinstance(override, dict) or not override:
        override = {}
    merged = deep_merge(base, {"gates": override}) if override else dict(base)
    merged["profile"] = profile
    return merged


# --------------------------------------------------------------------------
# 跨进程写锁
# --------------------------------------------------------------------------
#
# 用操作系统的排他锁（Windows: msvcrt.locking / POSIX: fcntl.flock），
# 而不是"锁文件存在即占用"：
#   - 进程崩溃/被 kill 时，OS 自动释放，不需要靠文件年龄猜陈旧；
#   - 锁文件本身不删除，避免"删了别人的锁"这类竞态；
#   - 释放时校验 token，确认自己仍是持有者。
#
# 同一进程内可重入（命令内部再调命令不会死锁）。


class LockTimeout(DevFlowError):
    """拿不到锁。"""


class ProjectLock:
    def __init__(self, path: Path, timeout: float | None = None):
        self.path = Path(path)
        self.timeout = (
            float(os.environ.get("DEVFLOW_LOCK_TIMEOUT", "15"))
            if timeout is None
            else float(timeout)
        )
        self.token = uuid.uuid4().hex
        self.fd: int | None = None
        self._locked = False

    # -- 平台相关 ----------------------------------------------------------

    def _try_lock(self, fd: int) -> bool:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(self, fd: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

    # -- 对外 --------------------------------------------------------------

    def acquire(self, timeout: float | None = None) -> None:
        wait = self.timeout if timeout is None else float(timeout)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.time() + wait
        while True:
            if self._try_lock(self.fd):
                self._locked = True
                break
            if time.time() >= deadline:
                holder = self._holder_info()
                os.close(self.fd)
                self.fd = None
                raise LockTimeout(
                    "无法获取 DevFlow 写锁（另一个进程正在修改项目状态）。\n"
                    f"  锁文件：{self.path}\n"
                    f"  持有者：{holder}\n"
                    "  等待超时后退出，未做任何修改。确认没有其他 df.py 在跑，或稍后重试。"
                )
            time.sleep(0.05)

        payload = json.dumps(
            {
                "token": self.token,
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "acquired_at": now_iso(),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.ftruncate(self.fd, 0)
        os.write(self.fd, payload)
        os.fsync(self.fd)

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            owner = self._read_token()
            if self._locked and owner not in (None, self.token):
                # 不应该发生：说明文件被外部改写。不动别人的内容，只释放自己的 OS 锁。
                print(f"! 锁归属异常（期望 {self.token[:8]}，实际 {str(owner)[:8]}），仅释放本进程锁")
            if self._locked:
                self._unlock(self.fd)
        finally:
            os.close(self.fd)
            self.fd = None
            self._locked = False

    def _read_raw(self) -> str | None:
        """读锁文件内容。

        Windows 的文件锁是强制的：**另一个句柄**连读都会被拒绝。
        所以优先用自己持有的 fd 读；拿不到时再尝试独立打开。
        """
        if self.fd is not None:
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                return os.read(self.fd, 4096).decode("utf-8", "replace")
            except OSError:
                pass
        try:
            fd = os.open(str(self.path), os.O_RDONLY)
        except OSError:
            return None
        try:
            return os.read(fd, 4096).decode("utf-8", "replace")
        except OSError:
            return None
        finally:
            os.close(fd)

    def metadata(self) -> dict:
        """当前锁文件里记录的持有者信息（诊断用）。"""
        raw = (self._read_raw() or "").strip()
        if not raw.startswith("{"):
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    def _read_token(self) -> str | None:
        raw = (self._read_raw() or "").strip()
        if not raw:
            return None
        if raw.startswith("{"):
            return self.metadata().get("token")
        return raw

    def _holder_info(self) -> str:
        data = self.metadata()
        if not data:
            return "未知（可能是刚启动的进程，或 Windows 强制锁导致无法读取）"
        return f"pid={data.get('pid')} host={data.get('host')} since={data.get('acquired_at')}"

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


#: (锁文件路径, 线程 id) → (锁对象, 重入深度)
#: 按线程区分：同一线程可重入，不同线程必须去争 OS 锁，不能共用 depth。
#: 项目锁槽位：每个锁文件一个。
#: - `rlock` 只负责"同一进程内不同线程互斥"，且天然支持同线程重入
#: - `lock` 是跨进程的 OS 文件锁，只在最外层持有时拿一次
#: 关键点：等待 `rlock` 时必须**在全局 guard 之外**，否则持有者释放时会被卡住。
class _LockSlot:
    __slots__ = ("rlock", "lock", "depth")

    def __init__(self, path: Path, timeout: float | None):
        self.rlock = threading.RLock()
        self.lock = ProjectLock(path, timeout)
        self.depth = 0


_LOCK_SLOTS: dict[str, _LockSlot] = {}
_LOCK_SLOTS_GUARD = threading.Lock()


def _lock_slot(df_dir: Path, timeout: float | None) -> _LockSlot:
    key = str(Path(df_dir).resolve())
    with _LOCK_SLOTS_GUARD:
        slot = _LOCK_SLOTS.get(key)
        if slot is None:
            slot = _LockSlot(Path(df_dir) / ".lock", timeout)
            _LOCK_SLOTS[key] = slot
        return slot


@contextmanager
def project_lock(df_dir: Path, timeout: float | None = None):
    """同一线程可重入 + 跨线程/跨进程排他的项目写锁。

    线程 A 持锁时线程 B 阻塞在 `slot.rlock` 上（不占全局 guard），
    A 释放后 B 立即拿到；不同进程仍由 OS 文件锁互斥。
    """
    wait = float(os.environ.get("DEVFLOW_LOCK_TIMEOUT", "15")) if timeout is None else float(timeout)
    slot = _lock_slot(Path(df_dir), timeout)
    if not slot.rlock.acquire(timeout=max(wait, 0.01)):
        raise LockTimeout(
            "无法获取 DevFlow 写锁（同一进程内的其他线程正在修改项目状态）。\n"
            f"  锁文件：{Path(df_dir) / '.lock'}\n"
            f"  等待 {wait:g}s 超时后退出，未做任何修改。"
        )
    acquired_os = False
    try:
        if slot.depth == 0:
            slot.lock.acquire(timeout=wait)
            acquired_os = True
        slot.depth += 1
        try:
            yield slot.lock
        finally:
            slot.depth -= 1
            if slot.depth == 0 and acquired_os:
                slot.lock.release()
    finally:
        slot.rlock.release()


def lock_slot_state(df_dir: Path) -> dict:
    """排查用：当前锁槽位深度与是否持有 OS 锁。"""
    key = str(Path(df_dir).resolve())
    with _LOCK_SLOTS_GUARD:
        slot = _LOCK_SLOTS.get(key)
    if slot is None:
        return {"exists": False, "depth": 0, "locked": False}
    return {"exists": True, "depth": slot.depth, "locked": slot.lock._locked}


# 兼容旧调用
locked = project_lock


# --------------------------------------------------------------------------
# 极简 YAML（只支持本项目 config 用到的子集）
# --------------------------------------------------------------------------


def _yaml_scalar(raw: str):
    text = raw.strip()
    if not text:
        return None
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if text in ("{}", "{ }"):
        return {}
    if text in ("[]", "[ ]"):
        return []
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def mini_yaml_load(text: str):
    """解析缩进映射 + `- ` 列表。不支持多行字符串、锚点、flow 语法。"""
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        body = raw.strip()
        if body.startswith("#"):
            continue
        # 去掉行尾注释（引号内的 # 保留）
        if " #" in body:
            quote = None
            cut = None
            for i, ch in enumerate(body):
                if ch in "\"'":
                    quote = None if quote == ch else (ch if quote is None else quote)
                elif ch == "#" and quote is None and i > 0 and body[i - 1] == " ":
                    cut = i
                    break
            if cut is not None:
                body = body[:cut].rstrip()
        lines.append((indent, body))

    def parse_block(index: int, indent: int):
        # 判断是列表还是映射
        if index >= len(lines):
            return None, index
        if lines[index][1].startswith("- "):
            items = []
            while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
                item_body = lines[index][1][2:].strip()
                index += 1
                if not item_body:
                    value, index = parse_block(index, indent + 2)
                    items.append(value)
                elif ":" in item_body and not item_body.startswith(("'", '"')):
                    # 列表项是映射（只支持单行键 + 后续缩进子块）
                    key, _, rest = item_body.partition(":")
                    item = {}
                    rest = rest.strip()
                    if rest:
                        item[key.strip()] = _yaml_scalar(rest)
                    else:
                        value, index = parse_block(index, indent + 2)
                        item[key.strip()] = value
                    # 同一列表项的后续键
                    while index < len(lines) and lines[index][0] == indent + 2 and not lines[index][1].startswith("- "):
                        k, _, r = lines[index][1].partition(":")
                        index += 1
                        r = r.strip()
                        if r:
                            item[k.strip()] = _yaml_scalar(r)
                        else:
                            value, index = parse_block(index, indent + 4)
                            item[k.strip()] = value
                    items.append(item)
                else:
                    items.append(_yaml_scalar(item_body))
            return items, index

        mapping = {}
        while index < len(lines) and lines[index][0] == indent and not lines[index][1].startswith("- "):
            body = lines[index][1]
            if ":" not in body:
                raise DevFlowError(f"YAML 子集解析失败，缺少冒号：{body}")
            key, _, rest = body.partition(":")
            key = key.strip()
            rest = rest.strip()
            index += 1
            if rest:
                mapping[key] = _yaml_scalar(rest)
            else:
                value, index = parse_block(index, indent + 2)
                mapping[key] = {} if value is None else value
        return mapping, index

    if not lines:
        return {}
    value, _ = parse_block(0, lines[0][0])
    return value


def load_config_file(path: Path):
    if path.suffix == ".json":
        return load_json(path)
    return mini_yaml_load(read_text(path))


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------


class Paths:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.df = self.root / DF_DIRNAME
        self.project = self.df / "project.json"
        self.decisions = self.df / "decisions.md"
        self.requirements = self.df / "requirements"
        self.req_source = self.requirements / "source"
        self.clarification = self.requirements / "clarification.md"
        self.traceability = self.requirements / "traceability.json"
        self.plans = self.df / "plans"
        self.nodes = self.df / "nodes"
        self.changes = self.df / "changes"
        self.regression = self.df / "regression"
        self.releases = self.df / "releases"
        self.sessions = self.df / "sessions"

    def confirmed_requirements(self, version: int) -> Path:
        return self.requirements / f"confirmed-requirements-v{version}.md"

    def backlog(self, version: int) -> Path:
        return self.plans / f"backlog-v{version}.json"

    def board(self, version: int) -> Path:
        return self.plans / f"board-v{version}.md"

    def graph(self, version: int) -> Path:
        return self.plans / f"dependency-graph-v{version}.md"

    def node_dir(self, node_id: str) -> Path:
        return self.nodes / node_id

    def change_dir(self, change_id: str) -> Path:
        return self.changes / change_id

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()


def find_root(start: Path | None = None) -> Path:
    cur = Path(start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / DF_DIRNAME / "project.json").exists():
            return candidate
    raise DevFlowError(
        f"当前目录不在 DevFlow 项目内（未找到 {DF_DIRNAME}/project.json）。先运行 df.py init。"
    )


# --------------------------------------------------------------------------
# 修改范围（files_scope）
# --------------------------------------------------------------------------
#
# files_scope 的每一项可以是：
#   - 精确文件：      src/service.py
#   - 目录：          src/service        （匹配 src/service/ 下任意文件）
#   - 通配符：        src/**/*.py        （** 跨目录，* 不跨目录）
#
# 匹配前统一规范化：反斜杠转正斜杠、去掉 ./、拒绝绝对路径与 `..`。
# 目录匹配要求 `entry + "/"` 前缀，避免 src/service 放行 src/service-backup/x.py。

_ABS_PATH_RE = re.compile(r"^(?:[A-Za-z]:|/|\\\\)")
_GLOB_CHARS = re.compile(r"[*?\[]")


def normalize_rel(raw) -> str:
    """把用户/Agent 给的路径规范成项目内的相对 POSIX 路径。

    拒绝：空、绝对路径、盘符、UNC、任何 `..` 段。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("路径为空")
    text = raw.strip().replace("\\", "/")
    if _ABS_PATH_RE.match(text):
        raise ValueError("不允许绝对路径")
    parts = []
    for seg in text.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise ValueError("不允许包含 .. 路径段")
        parts.append(seg)
    if not parts:
        raise ValueError("路径为空")
    return "/".join(parts)


def _glob_to_regex(pattern: str) -> re.Pattern:
    out = ["^"]
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 2] == "**":
                if pattern[i : i + 3] == "**/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if ch == "?":
            out.append("[^/]")
            i += 1
            continue
        if ch == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(r"\[")
                i += 1
                continue
            out.append(pattern[i : end + 1])
            i = end + 1
            continue
        out.append(re.escape(ch))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def scope_match(path: str, entry: str) -> bool:
    """path 是否落在 scope 项内。两者都应已规范化。"""
    if _GLOB_CHARS.search(entry):
        return bool(_glob_to_regex(entry).match(path))
    entry = entry.rstrip("/")
    if path == entry:
        return True
    return path.startswith(entry + "/")


def _symlink_escape(rel: str, root: Path) -> str | None:
    """检查路径（含其最近的已存在祖先）是否经符号链接跑到项目根之外。

    判定用真实路径的**祖先关系**，不是字符串包含——
    `/tmp/proj-evil` 包含 `/tmp/proj` 前缀，但它不在项目里面。
    """
    root_real = os.path.normcase(os.path.realpath(str(root)))
    probe = root / rel
    while not probe.exists() and probe != root:
        probe = probe.parent
    if not probe.exists():
        return None
    real = os.path.normcase(os.path.realpath(str(probe)))
    if real == root_real:
        return None
    try:
        common = os.path.commonpath([root_real, real])
    except ValueError:  # Windows 不同盘符
        return "路径经符号链接逃逸出项目根"
    if os.path.normcase(common) == root_real:
        return None
    return "路径经符号链接逃逸出项目根"


def scope_violations(
    node: dict,
    modified: list,
    root: Path,
    empty_policy: str = "outputs_only",
) -> list[str]:
    """返回所有越界原因（空列表=合规）。

    empty_policy（与 config/quality-gates.yaml 的 empty_scope 一一对应）：
      deny         没有 files_scope 就拒绝验收
      outputs_only 没有 files_scope 时，只允许改 outputs 中列出、且开工前不存在的文件
      allow        没有 files_scope 视为不限制
    旧配置里的 `warn` 按 outputs_only 处理（它从来不是"真的放行"）。
    """
    problems: list[str] = []
    scope = node.get("files_scope") or []
    if not scope:
        if empty_policy == "deny":
            problems.append("节点未定义 files_scope，按策略拒绝验收")
            return problems
        if empty_policy in ("warn", "outputs_only"):
            allowed = [p for p in deliverable_paths(node) if p not in
                       set(node.get("scope_baseline") or [])]
            outputs = [p for p in (node.get("outputs") or []) if isinstance(p, str)]
            if not allowed or not outputs:
                if modified:
                    problems.append(
                        "节点未定义 files_scope，且没有可用的 outputs 新文件，"
                        "无法校验修改范围"
                    )
                return problems
            for item in modified:
                try:
                    rel = normalize_rel(item)
                except ValueError as exc:
                    problems.append(f"{item!r}: {exc}")
                    continue
                if not any(scope_match(rel, entry) for entry in allowed):
                    problems.append(
                        f"{rel}: 不在 files_scope 内，也不属于本节点新增的 outputs"
                    )
            return problems
        return problems

    entries = []
    for entry in scope:
        try:
            entries.append(normalize_rel(entry))
        except ValueError as exc:
            problems.append(f"files_scope 项 {entry!r} 非法：{exc}")

    for raw in modified:
        try:
            rel = normalize_rel(raw)
        except ValueError as exc:
            problems.append(f"{raw!r}: {exc}")
            continue
        escaped = _symlink_escape(rel, root)
        if escaped:
            problems.append(f"{rel}: {escaped}")
            continue
        if not any(scope_match(rel, entry) for entry in entries):
            problems.append(f"{rel}: 不在 files_scope 内")
    return problems


# --------------------------------------------------------------------------
# 轻量事务（多文件原子提交）
# --------------------------------------------------------------------------


class Transaction:
    """快照 + 日志 + 回滚。用于 change-apply 这类跨文件提交。

    提交顺序：快照 → 写日志 → 改文件 → 标记 committed。
    任何一步失败都可以按快照回滚；committed 之后不再回滚。
    """

    def __init__(self, paths: "Paths", name: str, files: list[Path], meta: dict | None = None):
        self.paths = paths
        self.name = name
        self.files = [Path(f) for f in files]
        self.meta = meta or {}
        self.txn_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.dir = paths.df / ".txn" / self.txn_id
        self.journal_path = self.dir / "journal.json"
        self.journal: dict = {}

    # -- 内部 --------------------------------------------------------------

    def _write_journal(self) -> None:
        self.journal["updated_at"] = now_iso()
        atomic_write_json(self.journal_path, self.journal)

    def _snapshot_path(self, rel: str) -> Path:
        return self.dir / "files" / rel

    # -- 生命周期 ----------------------------------------------------------

    def begin(self) -> "Transaction":
        self.dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for path in self.files:
            rel = self.paths.rel(path)
            existed = path.exists()
            entries.append({"path": rel, "existed": existed})
            if existed:
                target = self._snapshot_path(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        self.journal = {
            "txn_id": self.txn_id,
            "name": self.name,
            "status": "open",
            "phase": "snapshot",
            "created_at": now_iso(),
            "files": entries,
            "meta": self.meta,
        }
        self._write_journal()
        return self

    def phase(self, name: str) -> None:
        self.journal["phase"] = name
        self._write_journal()

    def commit(self) -> None:
        self.journal["status"] = "committed"
        self.journal["phase"] = "done"
        self.journal["committed_at"] = now_iso()
        self._write_journal()

    def rollback(self, reason: str = "") -> list[str]:
        restored = []
        for entry in self.journal.get("files", []):
            rel = entry["path"]
            target = self.paths.root / rel
            if entry.get("existed"):
                snap = self._snapshot_path(rel)
                if snap.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(snap, target)
                    restored.append(rel)
            else:
                if target.exists():
                    target.unlink()
                    restored.append(f"{rel}（删除）")
        self.journal["status"] = "rolled_back"
        self.journal["phase"] = "rolled_back"
        self.journal["rollback_reason"] = reason
        self._write_journal()
        return restored


def pending_transactions(paths: "Paths") -> list[dict]:
    root = paths.df / ".txn"
    if not root.exists():
        return []
    out = []
    for journal in sorted(root.glob("*/journal.json")):
        try:
            data = load_json(journal)
        except DevFlowError:
            continue
        if data.get("status") in ("open", "failed"):
            data["_dir"] = journal.parent
            out.append(data)
    return out


def load_transaction(paths: "Paths", txn_id: str) -> Transaction:
    journal_path = paths.df / ".txn" / txn_id / "journal.json"
    if not journal_path.exists():
        raise DevFlowError(f"事务不存在：{txn_id}")
    data = load_json(journal_path)
    files = [paths.root / entry["path"] for entry in data.get("files", [])]
    txn = Transaction(paths, data.get("name", "unknown"), files, data.get("meta"))
    txn.txn_id = txn_id
    txn.dir = journal_path.parent
    txn.journal_path = journal_path
    txn.journal = data
    return txn


def prune_transactions(paths: "Paths", keep: int = 20) -> None:
    root = paths.df / ".txn"
    if not root.exists():
        return
    dirs = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name)
    for old in dirs[:-keep] if len(dirs) > keep else []:
        shutil.rmtree(old, ignore_errors=True)


def migrate_project_data(data: dict) -> list[str]:
    """把旧版 project.json 就地升级，返回变更说明。"""
    changes = []
    status = data.get("status")
    if status in PROJECT_STATUS_MIGRATIONS:
        data["status"] = PROJECT_STATUS_MIGRATIONS[status]
        changes.append(f"status: {status} → {data['status']}")
    if data.get("schema_version") != SCHEMA_VERSION:
        changes.append(f"schema_version: {data.get('schema_version')} → {SCHEMA_VERSION}")
        data["schema_version"] = SCHEMA_VERSION
    return changes


# --------------------------------------------------------------------------
# Git 实际变更核对（可选能力，不是硬依赖）
# --------------------------------------------------------------------------


def _git(root: Path, *args, timeout: int = 15):
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _parse_porcelain_z(out: str) -> list[str]:
    parts = out.split("\0")
    files = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry or len(entry) < 4:
            continue
        code = entry[:2]
        path = entry[3:]
        if "R" in code or "C" in code:
            i += 1  # 重命名/复制：下一段是原路径
        files.append(path.replace("\\", "/"))
    return files


def git_snapshot(root: Path) -> dict | None:
    """记录执行前的 Git 基准。不在 git 仓库里返回 None（降级）。"""
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if not inside or inside.strip() != "true":
        return None
    top = _git(root, "rev-parse", "--show-toplevel")
    # 项目不是仓库根目录时，路径换算容易出错，直接降级
    if not top or Path(os.path.realpath(top.strip())) != Path(os.path.realpath(str(root))):
        return None
    head = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain", "-z") or ""
    return {
        "head": head.strip() if head else None,
        "dirty_files": _parse_porcelain_z(status),
        "captured_at": now_iso(),
    }


def git_changes_since(root: Path, baseline: dict | None) -> list[str] | None:
    """相对基准的实际变更。

    关键：减去 `baseline.dirty_files` —— 用户在执行任务前就存在的未提交修改
    不能被算到这个节点头上。
    """
    if not baseline:
        return None
    current = git_snapshot(root)
    if current is None:
        return None

    changes: set[str] = set(current["dirty_files"])
    if baseline.get("head"):
        diff = _git(root, "diff", "--name-only", baseline["head"], "--")
        if diff:
            changes |= {line.strip() for line in diff.splitlines() if line.strip()}

    protected = {p.replace("\\", "/") for p in baseline.get("dirty_files", [])}
    changes -= protected
    return sorted(
        p for p in changes
        if p and not p.startswith(DF_DIRNAME + "/") and not p.startswith("./")
    )


# --------------------------------------------------------------------------
# 交付物快照：下游开工前检查依赖产物是否变了
# --------------------------------------------------------------------------
#
# 只在**声明的依赖产物**上做比对（node.inputs + 依赖节点的 outputs），
# 不对整个工作区做"文件一变就全部失效"——文档改一行不应该让所有下游重做。

#: 单个文件做快照的大小上限，超过只记大小和 mtime（避免给大二进制算哈希）
SNAPSHOT_MAX_BYTES = 5 * 1024 * 1024


def file_fingerprint(path: Path) -> dict | None:
    """文件指纹：小文件记 sha256，大文件记 size+mtime。文件不存在返回 None。"""
    try:
        if not path.is_file():
            return None
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size > SNAPSHOT_MAX_BYTES:
        return {"size": stat.st_size, "mtime": int(stat.st_mtime)}
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return None
    return {"sha256": digest.hexdigest(), "size": stat.st_size}


def deliverable_paths(node: dict) -> list[str]:
    """一个节点承诺的交付物：outputs 优先，没有就退回 files_scope 里的具体文件。"""
    paths: list[str] = []
    for raw in (node.get("outputs") or []) + (node.get("files_scope") or []):
        if not isinstance(raw, str):
            continue
        try:
            norm = normalize_rel(raw)
        except ValueError:
            continue
        if any(ch in norm for ch in "*?"):
            continue
        if norm not in paths:
            paths.append(norm)
    return paths


CONTRACT_KINDS = ("api_contract", "db_object", "shared_config")


def contract_bindings(node: dict) -> list[dict]:
    """规范化后的契约绑定（只保留 kind/id/files 齐全的项）。"""
    out: list[dict] = []
    for entry in node.get("contract_bindings") or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        cid = entry.get("id")
        if kind not in CONTRACT_KINDS or not cid:
            continue
        files: list[str] = []
        for raw in entry.get("files") or []:
            try:
                files.append(normalize_rel(raw))
            except ValueError:
                continue
        if files:
            out.append({"kind": kind, "id": cid, "files": files})
    return out


def deliverable_snapshot(paths: Paths, node: dict) -> dict:
    """记录节点当前交付物与契约文件指纹。只记真实存在的文件。"""
    files = {}
    for rel in deliverable_paths(node):
        fingerprint = file_fingerprint(paths.root / rel)
        if fingerprint:
            files[rel] = fingerprint

    contracts: dict[str, dict] = {}
    for binding in contract_bindings(node):
        key = f"{binding['kind']}:{binding['id']}"
        recorded = {
            rel: fp
            for rel, fp in (
                (rel, file_fingerprint(paths.root / rel)) for rel in binding["files"]
            )
            if fp
        }
        if recorded:
            contracts[key] = recorded

    return {
        "files": files,
        "contracts": contracts,
        "plan_version": None,
        "recorded_at": now_iso(),
        "git_head": (git_snapshot(paths.root) or {}).get("head"),
    }


def _same_fingerprint(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return False
    if "sha256" in a or "sha256" in b:
        return a.get("sha256") == b.get("sha256") and a.get("sha256") is not None
    return a.get("size") == b.get("size") and a.get("mtime") == b.get("mtime")


def context_changes(paths: Paths, plan: dict, node: dict) -> list[str]:
    """开工前检查：声明依赖过的产物是否在执行期间被改过。

    判定范围：本节点 `inputs` 中、且被某个直接依赖记录为交付物的文件。
    `inputs` 为空时不做推断（没有声明就不猜，避免误伤）。
    """
    by_id = {n["node_id"]: n for n in plan.get("nodes", [])}
    inputs = []
    for raw in node.get("inputs") or []:
        if not isinstance(raw, str):
            continue
        try:
            inputs.append(normalize_rel(raw))
        except ValueError:
            continue
    if not inputs:
        return []

    problems: list[str] = []
    for dep_id in node.get("dependencies") or []:
        dep = by_id.get(dep_id)
        if not dep:
            continue
        recorded = (dep.get("deliverables") or {}).get("files") or {}
        for rel in inputs:
            if rel not in recorded:
                continue
            current = file_fingerprint(paths.root / rel)
            if current is None:
                problems.append(f"{rel} 已不存在（{dep_id} 交付时存在）")
            elif not _same_fingerprint(recorded[rel], current):
                problems.append(f"{rel} 在 {dep_id} 完成之后被修改过")

        # 契约级：依赖声明的 API / 表结构文件变了，下游同样要重新评估
        contracts = (dep.get("deliverables") or {}).get("contracts") or {}
        for key, files in contracts.items():
            for rel, fingerprint in (files or {}).items():
                current = file_fingerprint(paths.root / rel)
                if current is None:
                    problems.append(f"契约 {key} 的文件 {rel} 已被删除（{dep_id} 交付时存在）")
                elif not _same_fingerprint(fingerprint, current):
                    problems.append(f"契约 {key} 的文件 {rel} 在 {dep_id} 完成之后被修改过")
    return problems


# --------------------------------------------------------------------------
# 源代码快照：回归结论必须绑定"当时那份代码"
# --------------------------------------------------------------------------

#: 计算快照时永远排除的目录（DevFlow 自己的产物 + 版本库 + 常见构建输出）
SNAPSHOT_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", DF_DIRNAME,
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "env",
    "dist", "build", "target", "coverage", ".next", ".nuxt",
}


def _snapshot_files(root: Path, extra_exclude: list[str] | None = None) -> list[str]:
    exclude = set(SNAPSHOT_EXCLUDE_DIRS)
    for item in extra_exclude or []:
        try:
            exclude.add(normalize_rel(item).split("/")[0])
        except ValueError:
            continue
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in exclude)
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(filenames):
            if name.endswith((".tmp", ".lock")):
                continue
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            found.append(rel.replace("\\", "/"))
    return found


def source_snapshot(root: Path, extra_exclude: list[str] | None = None) -> dict:
    """源代码快照。

    Git 项目：HEAD + 未提交改动的指纹（不是只看 mtime）。
    非 Git 项目：按文件内容哈希整棵树（同样排除 .git / .devflow / 构建产物）。
    """
    root = Path(root)
    head = None
    inner = _git(root, "rev-parse", "--is-inside-work-tree")
    if inner and inner.strip() == "true":
        top = _git(root, "rev-parse", "--show-toplevel")
        if top and Path(os.path.realpath(top.strip())) == Path(os.path.realpath(str(root))):
            head_out = _git(root, "rev-parse", "HEAD")
            head = head_out.strip() if head_out else None

    digest = hashlib.sha256()
    count = 0
    for rel in _snapshot_files(root, extra_exclude):
        fingerprint = file_fingerprint(root / rel)
        if fingerprint is None:
            continue
        if "sha256" in fingerprint:
            token = fingerprint["sha256"]
        else:
            token = f"{fingerprint.get('size')}:{fingerprint.get('mtime')}"
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(token.encode("utf-8"))
        digest.update(b"\n")
        count += 1
    dirty_digest = digest.hexdigest()

    kind = "git" if head is not None else "files"
    snapshot_id = hashlib.sha256(
        f"{kind}:{head or '-'}:{dirty_digest}".encode("utf-8")
    ).hexdigest()
    return {
        "type": kind,
        "head": head,
        "dirty_digest": dirty_digest,
        "snapshot_id": snapshot_id,
        "file_count": count,
        "captured_at": now_iso(),
    }


# --------------------------------------------------------------------------
# 提示词版本绑定
# --------------------------------------------------------------------------

PROMPT_META_KEYS = (
    "project_id",
    "node_id",
    "plan_version",
    "requirement_version",
    "prompt_version",
)


def parse_prompt_meta(text: str) -> dict:
    """读取提示词头部的 `---` 元数据块。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    meta = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


def prompt_binding_problems(meta: dict, project_data: dict, plan: dict, node: dict) -> list[str]:
    """提示词是否与当前项目/计划/需求一致。"""
    if not meta:
        return ["提示词没有版本元数据（--- 头），无法确认它属于当前计划；请重新生成 prompt-init --force"]
    expected = {
        "project_id": project_data.get("project_id"),
        "node_id": node["node_id"],
        "plan_version": str(plan.get("plan_version")),
        "requirement_version": str(plan.get("requirement_version")),
        "prompt_version": str(int(node.get("prompt_version", 0) or 0)),
    }
    problems = []
    for key, value in expected.items():
        got = meta.get(key)
        if got != str(value):
            problems.append(f"提示词的 {key}={got!r}，当前应为 {value!r}")
    return problems


# --------------------------------------------------------------------------
# 项目与计划
# --------------------------------------------------------------------------


class Project:
    def __init__(self, root: Path):
        self.paths = Paths(root)
        self.data = load_json(self.paths.project)

    @property
    def root(self) -> Path:
        return self.paths.root

    def reload(self) -> "Project":
        """加锁后重新读取，避免用加锁前的旧快照覆盖别人的修改。"""
        self.data = load_json(self.paths.project)
        return self

    def effective_status(self) -> str:
        return PROJECT_STATUS_MIGRATIONS.get(self.data.get("status"), self.data.get("status"))

    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        atomic_write_json(self.paths.project, self.data)

    def plan_versions(self) -> list[int]:
        versions = []
        if self.paths.plans.exists():
            for p in self.paths.plans.glob("backlog-v*.json"):
                m = re.fullmatch(r"backlog-v(\d+)\.json", p.name)
                if m:
                    versions.append(int(m.group(1)))
        return sorted(versions)

    def next_plan_version(self) -> int:
        versions = self.plan_versions()
        return (max(versions) + 1) if versions else 1

    def load_plan(self, version: int) -> dict:
        return load_json(self.paths.backlog(version))

    def current_plan(self, required: bool = True) -> dict | None:
        version = self.data.get("current_plan_version")
        if not version:
            if required:
                raise DevFlowError("当前没有生效的计划。先 plan-import 生成草稿，再由用户确认后 plan-activate。")
            return None
        plan = self.load_plan(version)
        if plan.get("status") != "active":
            if required:
                raise DevFlowError(
                    f"计划 v{version} 的状态是 {plan.get('status')}，不是 active。"
                    "草稿计划不能执行，需先 plan-activate。"
                )
        return plan

    def find_node(self, node_id: str, plan: dict | None = None) -> dict:
        plan = plan or self.current_plan()
        for node in plan["nodes"]:
            if node["node_id"] == node_id:
                return node
        raise DevFlowError(f"计划 v{plan['plan_version']} 中没有节点 {node_id}")

    def save_plan(self, plan: dict) -> None:
        atomic_write_json(self.paths.backlog(plan["plan_version"]), plan)

    def requirement_version(self) -> int | None:
        return self.data.get("current_requirement_version")


def load_traceability(paths: Paths) -> dict | None:
    if paths.traceability.exists():
        return load_json(paths.traceability)
    return None


# --------------------------------------------------------------------------
# 状态与依赖
# --------------------------------------------------------------------------


def dependents_map(nodes: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {n["node_id"]: [] for n in nodes}
    for node in nodes:
        for dep in node.get("dependencies", []):
            out.setdefault(dep, []).append(node["node_id"])
    return out


def topological_order(nodes: list[dict]) -> list[str]:
    """返回拓扑序；存在环时抛错。"""
    indeg = {n["node_id"]: 0 for n in nodes}
    deps = {n["node_id"]: [d for d in n.get("dependencies", []) if d in indeg] for n in nodes}
    for nid, dl in deps.items():
        indeg[nid] = len(set(dl))
    queue = sorted([nid for nid, deg in indeg.items() if deg == 0])
    order = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for other in nodes:
            if nid in deps[other["node_id"]]:
                indeg[other["node_id"]] -= 1
                if indeg[other["node_id"]] == 0:
                    queue.append(other["node_id"])
        queue.sort()
    if len(order) != len(nodes):
        remaining = [nid for nid, deg in indeg.items() if deg > 0]
        raise DevFlowError(f"任务依赖存在循环，涉及节点：{', '.join(sorted(remaining))}")
    return order


def find_cycles(nodes: list[dict]) -> list[list[str]]:
    graph = {n["node_id"]: list(n.get("dependencies", [])) for n in nodes}
    cycles: list[list[str]] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in graph}
    stack: list[str] = []

    def dfs(node: str):
        color[node] = GRAY
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if color[dep] == GRAY:
                idx = stack.index(dep)
                cycles.append(stack[idx:] + [dep])
            elif color[dep] == WHITE:
                dfs(dep)
        stack.pop()
        color[node] = BLACK

    for nid in sorted(graph):
        if color[nid] == WHITE:
            dfs(nid)
    return cycles


def unmet_dependencies(node: dict, plan: dict) -> list[str]:
    by_id = {n["node_id"]: n for n in plan["nodes"]}
    missing = []
    for dep in node.get("dependencies", []):
        target = by_id.get(dep)
        if target is None or target.get("status") != "completed":
            missing.append(dep)
    return missing


def recompute_ready(plan: dict) -> list[tuple[str, str, str]]:
    """把依赖已全部完成的 pending 节点提升为 ready。

    只做 pending -> ready 的单向提升；不会把 ready 退回 pending。
    返回 [(node_id, old, new)]。
    """
    changes = []
    for node in plan["nodes"]:
        if node.get("status") != "pending":
            continue
        if not unmet_dependencies(node, plan):
            node["status"] = "ready"
            changes.append((node["node_id"], "pending", "ready"))
    return changes


def ready_nodes(plan: dict) -> list[dict]:
    return [
        n
        for n in plan["nodes"]
        if n.get("status") == "ready" and not unmet_dependencies(n, plan)
    ]


# --------------------------------------------------------------------------
# 需求退役：改成非 active 不是逃生门
# --------------------------------------------------------------------------

RETIRED_REQUIREMENT_STATUSES = ("deferred", "out_of_scope", "removed")


def requirement_retirement_problems(req: dict, by_req: dict | None = None) -> list[str]:
    """非 active 需求是否留下了合法凭据。

    - deferred / out_of_scope / removed：必须写 reason 和 confirmed_by
    - changed：必须 superseded_by 指向一条存在且 active 的需求
    - 其他未知状态：一律要求 reason + confirmed_by（不能靠改状态混过去）
    """
    rid = req.get("req_id") or "?"
    status = req.get("status")
    if status == "active":
        return []

    if status == "changed":
        target = str(req.get("superseded_by") or "").strip()
        if not target:
            return [
                f"{rid} 标记为 changed，但没有 superseded_by（旧需求必须指向替代需求）"
            ]
        if by_req is None:
            return []
        replacement = by_req.get(target)
        if replacement is None:
            return [f"{rid} 由 {target} 替代，但需求表里没有 {target}"]
        if replacement.get("status") != "active":
            return [
                f"{rid} 由 {target} 替代，但 {target} 当前是 {replacement.get('status')}，"
                "不能作为有效覆盖"
            ]
        return []

    if not str(req.get("reason") or "").strip() or \
            not str(req.get("confirmed_by") or "").strip():
        return [
            f"{rid} 标记为 {status}，但缺少 reason / confirmed_by"
            "（不能靠改状态绕过覆盖检查）"
        ]
    return []


# --------------------------------------------------------------------------
# 并行安全：同组节点能不能真的同时开工
# --------------------------------------------------------------------------
#
# 拓扑分层只说明"没有依赖"，不说明"代码不打架"。两个节点同层完全可以改同一个文件，
# 所以并行组必须单独检查依赖可达性与资源冲突。

#: 节点上的"共享资源"字段 → 面向人的说明
PARALLEL_RESOURCE_FIELDS = (
    ("db_objects", "数据库对象"),
    ("api_contracts", "API 契约"),
    ("shared_config", "共享配置"),
)


def node_reaches(by_id: dict, start: str, target: str) -> bool:
    """start 是否（间接）依赖 target。"""
    seen: set[str] = set()
    stack = [start]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        for dep in by_id.get(nid, {}).get("dependencies") or []:
            if dep == target:
                return True
            stack.append(dep)
    return False


def _has_glob(text: str) -> bool:
    return any(ch in text for ch in "*?")


def _literal_prefix(pattern: str) -> list[str]:
    """通配符之前的固定目录段。"""
    prefix: list[str] = []
    for seg in pattern.split("/"):
        if _has_glob(seg):
            break
        prefix.append(seg)
    return prefix


def _pattern_matches(pattern: str, path: str) -> bool:
    try:
        return bool(_glob_to_regex(pattern).match(path))
    except re.error:
        return True  # 解析不了就按冲突处理


def _patterns_intersect(a: str, b: str) -> bool:
    """两个 scope 条目是否可能落到同一个文件上。

    无法证明不相交就算相交——并行冲突宁可误报，也不能漏报。
    """
    if a == b:
        return True
    # 目录包含：src 与 src/api.py、src 与 src/api
    if a.startswith(b + "/") or b.startswith(a + "/"):
        return True

    a_glob, b_glob = _has_glob(a), _has_glob(b)
    if not a_glob and not b_glob:
        return False
    if a_glob and not b_glob:
        return _pattern_matches(a, b) or _pattern_matches(a, b + "/x")
    if b_glob and not a_glob:
        return _pattern_matches(b, a) or _pattern_matches(b, a + "/x")
    # 两边都是通配：固定前缀一旦分叉就一定不相交，否则保守判相交
    pa, pb = _literal_prefix(a), _literal_prefix(b)
    for x, y in zip(pa, pb):
        if x != y:
            return False
    return True


def scope_overlap(a: dict, b: dict) -> set[str]:
    """两个节点 files_scope 的重叠项（含通配符范围）。返回命中的条目文本。"""
    out: set[str] = set()
    for x in a.get("files_scope") or []:
        for y in b.get("files_scope") or []:
            if not isinstance(x, str) or not isinstance(y, str):
                continue
            try:
                nx = normalize_rel(x)
                ny = normalize_rel(y)
            except ValueError:
                continue
            if _patterns_intersect(nx, ny):
                out.add(nx if len(nx) >= len(ny) else ny)
    return out


def pair_conflicts(a: dict, b: dict) -> list[str]:
    """两个节点的资源冲突说明（空列表表示这一对没有已知冲突）。"""
    conflicts: list[str] = []
    overlap = scope_overlap(a, b)
    if overlap:
        conflicts.append(f"文件范围重叠（{', '.join(sorted(overlap)[:3])}）")
    for field, label in PARALLEL_RESOURCE_FIELDS:
        shared = sorted(set(a.get(field) or []) & set(b.get(field) or []))
        if shared:
            conflicts.append(f"同时修改{label}（{', '.join(shared[:3])}）")
    return conflicts


def parallel_exceptions(plan: dict) -> dict[str, dict]:
    """组名 → 已记录（有理由且有人批准）的例外。"""
    out: dict[str, dict] = {}
    for entry in plan.get("parallel_exceptions") or []:
        if not isinstance(entry, dict):
            continue
        group = entry.get("group")
        if not group or not str(entry.get("reason") or "").strip():
            continue
        if not str(entry.get("approved_by") or "").strip():
            continue
        out[group] = entry
    return out


def _exception_covers(entry: dict, a: str, b: str) -> bool:
    """例外必须点名节点组合，不能"一个组豁免全部冲突"。"""
    nodes = entry.get("nodes") or []
    if not isinstance(nodes, list) or not nodes:
        return False
    allowed = set(nodes)
    return a in allowed and b in allowed


def parallel_issues(plan: dict, max_group_size: int = 3) -> list[dict]:
    """检查所有并行组，返回 [{severity, group, nodes, kind, message}]。

    severity=error 的项表示"不能这样并行"；warn 只是提示（例如已记录例外）。
    供 validate（是否阻止激活）和 dependency_check（诊断报告）共用，
    避免两个入口对同一条规则给出不同结论。
    """
    nodes = plan.get("nodes") or []
    by_id = {n["node_id"]: n for n in nodes}
    groups: dict[str, list[dict]] = {}
    for node in nodes:
        # 废弃/取消的节点不会再执行，不参与并行冲突判定
        if node.get("status") in ("superseded", "cancelled"):
            continue
        if node.get("parallel_group"):
            groups.setdefault(node["parallel_group"], []).append(node)

    exceptions = parallel_exceptions(plan)
    issues: list[dict] = []

    for name, members in groups.items():
        ids = [m["node_id"] for m in members]
        exception = exceptions.get(name)

        if len(members) > max_group_size:
            issues.append({
                "severity": "warn", "group": name, "nodes": ids, "kind": "group_size",
                "message": f"并行组 {name} 有 {len(members)} 个节点，超过建议上限 {max_group_size}",
            })

        for member in members:
            for dep in member.get("dependencies") or []:
                if dep in ids:
                    issues.append({
                        "severity": "error", "group": name, "nodes": [member["node_id"], dep],
                        "kind": "direct_dependency",
                        "message": f"并行组 {name} 内 {member['node_id']} 依赖 {dep}，不能并行执行",
                    })

        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                an, bn = a["node_id"], b["node_id"]
                if node_reaches(by_id, an, bn) or node_reaches(by_id, bn, an):
                    issues.append({
                        "severity": "error", "group": name, "nodes": [an, bn],
                        "kind": "indirect_dependency",
                        "message": f"并行组 {name}: {an} 与 {bn} 存在间接依赖，不能并行",
                    })
                for text in pair_conflicts(a, b):
                    message = f"并行组 {name}: {an} / {bn} {text}"
                    if exception and _exception_covers(exception, an, bn):
                        issues.append({
                            "severity": "warn", "group": name, "nodes": [an, bn],
                            "kind": "resource_conflict",
                            "message": message + f"（已记录例外：{exception.get('reason')}）",
                        })
                    else:
                        issues.append({
                            "severity": "error", "group": name, "nodes": [an, bn],
                            "kind": "resource_conflict",
                            "message": message
                            + "；确认安全请加 parallel_exceptions"
                            "（group + nodes + reason + approved_by，必须点名这两个节点）",
                        })

        statuses = {m.get("status") for m in members}
        if "pending" in statuses and statuses & {"ready", "in_progress"}:
            issues.append({
                "severity": "warn", "group": name, "nodes": ids, "kind": "status_mismatch",
                "message": f"并行组 {name} 内状态不一致，检查依赖是否漏配",
            })

    return issues


def migration_problems(old_plan: dict, new_plan: dict, change_id: str | None = None) -> list[str]:
    """新版计划的节点迁移是否合理。

    - 旧节点不能凭空消失（要么保留，要么显式 superseded）
    - 已完成的节点被返工，必须写清是哪个变更触发的
    - 新计划里不能凭空出现 completed 节点
    """
    problems: list[str] = []
    old_nodes = {n["node_id"]: n for n in old_plan.get("nodes", [])}
    new_nodes = {n["node_id"]: n for n in new_plan.get("nodes", [])}
    new_version = new_plan.get("plan_version")

    for nid, old in old_nodes.items():
        if old.get("status") in ("superseded", "cancelled"):
            continue
        if nid not in new_nodes:
            problems.append(
                f"{nid} 在新版计划里消失了；不需要就显式标 superseded 并说明原因"
            )
            continue
        new = new_nodes[nid]
        if old.get("status") == "completed" and new.get("status") not in ("completed", "superseded"):
            remark = str(new.get("remark") or "")
            if change_id and change_id not in remark:
                problems.append(
                    f"{nid} 在 v{old_plan.get('plan_version')} 已完成，"
                    f"新版变成 {new.get('status')}，但 remark 没有写 {change_id} 的返工依据"
                )
            elif not remark:
                problems.append(
                    f"{nid} 已完成却被返工（{new.get('status')}），必须在 remark 写明原因"
                )

    for nid, new in new_nodes.items():
        if new.get("status") != "completed":
            continue
        old = old_nodes.get(nid)
        if old is None:
            problems.append(f"{nid} 在新版里直接标 completed，但它不是旧计划里的节点")
        elif old.get("status") != "completed":
            problems.append(
                f"{nid} 在新版里标 completed，但旧计划里它是 {old.get('status')}"
            )

    for nid, new in new_nodes.items():
        if nid in old_nodes:
            continue
        created = new.get("created_in_plan")
        if created not in (None, new_version):
            problems.append(f"{nid} 是新增节点，但 created_in_plan={created} ≠ v{new_version}")
    return problems


def stats(plan: dict) -> dict:
    nodes = plan["nodes"]
    counts = {status: 0 for status in NODE_STATUSES}
    for node in nodes:
        counts[node.get("status", "pending")] = counts.get(node.get("status", "pending"), 0) + 1
    effective = [
        n for n in nodes if n.get("status") not in ("superseded", "cancelled")
    ]
    done = counts.get("completed", 0)
    percent = round(done * 100.0 / len(effective), 1) if effective else 0.0
    return {
        "total": len(nodes),
        "effective": len(effective),
        "counts": counts,
        "completed": done,
        "percent": percent,
        "ready": len(ready_nodes(plan)),
        "groups": parallel_groups(plan),
    }


def parallel_groups(plan: dict) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for node in plan["nodes"]:
        group = node.get("parallel_group")
        if group and node.get("status") in ("ready", "pending", "in_progress"):
            groups.setdefault(group, []).append(node["node_id"])
    return {k: sorted(v) for k, v in sorted(groups.items())}


# --------------------------------------------------------------------------
# 证据
# --------------------------------------------------------------------------


def report_path(paths: Paths, node: dict, kind: str) -> Path:
    """节点报告路径：优先用节点里登记的相对路径，否则用约定路径。

    约定路径让执行 Agent 完全不需要碰 backlog.json：
      .devflow/nodes/DF-001/execution-report.md
      .devflow/nodes/DF-001/test-report.json
    """
    ref = node.get(kind)
    if ref:
        return paths.root / ref
    name = "test-report.json" if kind == "test_report" else "execution-report.md"
    return paths.node_dir(node["node_id"]) / name


def load_test_report(paths: Paths, node: dict) -> dict | None:
    path = report_path(paths, node, "test_report")
    if not path.exists():
        return None
    return load_json(path)


def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _binding_problems(problems: list[str], label: str, data: dict, node: dict, plan: dict | None) -> None:
    """版本与执行尝试绑定。

    只有节点带 current_attempt_id（新一次执行）时才强制要求，
    避免历史报告因为新增字段集体失效。
    """
    strict = bool(node.get("current_attempt_id"))
    if not strict:
        return
    if plan is not None:
        if data.get("plan_version") is None:
            problems.append(f"{label} 缺少 plan_version，无法确认属于当前计划 v{plan.get('plan_version')}")
        elif data.get("plan_version") != plan.get("plan_version"):
            problems.append(
                f"{label} 的 plan_version={data.get('plan_version')}，"
                f"当前计划是 v{plan.get('plan_version')}"
            )
        if data.get("requirement_version") is None:
            problems.append(f"{label} 缺少 requirement_version")
        elif data.get("requirement_version") != plan.get("requirement_version"):
            problems.append(
                f"{label} 的 requirement_version={data.get('requirement_version')}，"
                f"当前需求是 v{plan.get('requirement_version')}"
            )
    if data.get("attempt_id") is None:
        problems.append(
            f"{label} 缺少 attempt_id，无法证明是本轮执行产出的证据"
            f"（当前 {node['current_attempt_id']}）"
        )
    elif data.get("attempt_id") != node["current_attempt_id"]:
        problems.append(
            f"{label} 的 attempt_id={data.get('attempt_id')} 与当前执行 "
            f"{node['current_attempt_id']} 不一致（可能是上一轮的旧报告）"
        )


def _acceptance_problems(problems: list[str], node: dict, evidence: dict) -> None:
    declared = [str(c).strip() for c in node.get("acceptance_criteria", [])]
    reported = evidence.get("acceptance")
    index = {}
    if isinstance(reported, list):
        for item in reported:
            if isinstance(item, dict) and isinstance(item.get("criterion"), str):
                index[item["criterion"].strip()] = item
    for criterion in declared:
        item = index.get(criterion)
        if item is None:
            problems.append(f"验收标准未在报告中逐条对应：{criterion}")
            continue
        if item.get("met") is not True:
            problems.append(f"验收标准未满足（met 不为 true）：{criterion}")
        if not str(item.get("evidence") or "").strip():
            problems.append(f"验收标准缺少证据：{criterion}")


def normalize_criterion(text: str) -> str:
    """验收标准归一化：去空白、全角转半角标点、大小写不敏感。"""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", "", text).strip().lower()


def acceptance_items(req: dict) -> list[dict]:
    """把需求里的 acceptance 统一成 [{id, text}]，兼容旧的纯字符串格式。"""
    items: list[dict] = []
    for entry in req.get("acceptance") or []:
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                items.append({"id": None, "text": text})
        elif isinstance(entry, dict):
            text = str(entry.get("criterion") or "").strip()
            if text:
                items.append({"id": entry.get("id"), "text": text})
    return items


ACCEPTANCE_ID_RE = re.compile(r"^AC-[0-9A-Za-z._-]+$")


def acceptance_spec_problems(req: dict) -> list[str]:
    """active 需求的验收标准本身是否合格。

    非 active（deferred / out_of_scope / removed / changed）不要求验收标准，
    它们由 reason / confirmed_by / superseded_by 规则约束。
    """
    if req.get("status") != "active":
        return []
    req_id = req.get("req_id") or "?"
    items = req.get("acceptance")
    if items is None:
        return [f"{req_id}: active 需求必须至少有一条验收标准（acceptance）"]
    if not isinstance(items, list):
        return [f"{req_id}: acceptance 必须是数组"]
    if not items:
        return [f"{req_id}: acceptance 为空；active 需求至少要有一条可判断真假的验收标准"]
    problems: list[str] = []
    for idx, item in enumerate(items):
        if isinstance(item, str):
            if not item.strip():
                problems.append(f"{req_id}: acceptance[{idx}] 是空字符串")
            continue
        if isinstance(item, dict):
            criterion = item.get("criterion")
            if not isinstance(criterion, str) or not criterion.strip():
                problems.append(f"{req_id}: acceptance[{idx}] 的 criterion 为空")
            cid = item.get("id")
            if cid is not None and not ACCEPTANCE_ID_RE.match(str(cid)):
                problems.append(
                    f"{req_id}: acceptance[{idx}] 的 id={cid!r} 不匹配 AC-xxx 格式"
                )
            continue
        problems.append(f"{req_id}: acceptance[{idx}] 只能是字符串或 {{criterion, id}} 对象")
    return problems


def traceability_problems(trace: dict) -> list[str]:
    """需求追踪表整体校验（导入/确认/回归都要用同一套）。"""
    problems: list[str] = []
    reqs = trace.get("requirements", []) or []
    by_req = {
        req.get("req_id"): req
        for req in reqs
        if isinstance(req, dict) and req.get("req_id")
    }
    for req in reqs:
        problems.extend(acceptance_spec_problems(req))
        problems.extend(requirement_retirement_problems(req, by_req))
    return problems


def requirement_acceptance_problems(
    paths: Paths,
    node: dict,
    evidence: dict,
    plan: dict | None = None,
) -> list[str]:
    """逐条核对：节点覆盖的每条需求的每条验收标准，都要有可核查的证据。

    - 证据条目必须来自本节点、且 req_id 对得上（不能借别的需求的证据）
    - met 必须为 True
    - evidence 必须写清测试名/命令/产物
    """
    node_id = node["node_id"]
    req_ids = [r for r in (node.get("requirement_ids") or []) if isinstance(r, str)]
    if not req_ids:
        return []
    trace = load_traceability(paths) or {}
    by_req = {r.get("req_id"): r for r in trace.get("requirements", []) if r.get("req_id")}

    reported = evidence.get("requirement_acceptance")
    if not isinstance(reported, list):
        reported = []
    index: dict[tuple[str, str], dict] = {}
    for entry in reported:
        if not isinstance(entry, dict):
            continue
        req_id = entry.get("req_id")
        criterion = entry.get("criterion")
        if not isinstance(req_id, str) or not isinstance(criterion, str):
            continue
        index[(req_id, normalize_criterion(criterion))] = entry

    problems: list[str] = []
    for req_id in req_ids:
        req = by_req.get(req_id)
        if req is None:
            continue  # 追踪表缺失由 validate 单独报
        items = acceptance_items(req)
        if not items:
            problems.append(f"{node_id} 覆盖的 {req_id} 没有验收标准，无法证明需求被验证")
            continue
        for item in items:
            key = (req_id, normalize_criterion(item["text"]))
            entry = index.get(key)
            if entry is None and item["id"]:
                # 允许用稳定 id 对应（criterion 文本易改，id 更可靠）
                entry = next(
                    (
                        e for e in reported
                        if isinstance(e, dict)
                        and e.get("req_id") == req_id
                        and e.get("criterion_id") == item["id"]
                    ),
                    None,
                )
            if entry is None:
                problems.append(
                    f"{node_id}: 缺少 {req_id} 验收项的证据 —— {item['text']}"
                )
                continue
            if entry.get("met") is not True:
                problems.append(f"{node_id}: {req_id} 验收项未满足 —— {item['text']}")
            if not str(entry.get("evidence") or "").strip():
                problems.append(f"{node_id}: {req_id} 验收项没有写证据 —— {item['text']}")
    return problems


def _command_problems(problems: list[str], node_id: str, report: dict) -> None:
    commands = report.get("commands")
    if not isinstance(commands, list) or not commands:
        problems.append(f"{node_id} 测试报告没有记录任何实际执行的命令")
        return
    for idx, item in enumerate(commands, start=1):
        if not isinstance(item, dict):
            problems.append(f"{node_id} 第 {idx} 条命令格式不对")
            continue
        if not str(item.get("cmd") or "").strip():
            problems.append(f"{node_id} 第 {idx} 条命令缺少 cmd")
        code = _int_or_none(item.get("exit_code"))
        if code is None:
            problems.append(f"{node_id} 第 {idx} 条命令没有明确的整数退出码（不允许默认成功）")
        elif code != 0:
            problems.append(f"{node_id} 第 {idx} 条命令退出码 {code}：{str(item.get('cmd'))[:60]}")


def _summary_problems(problems: list[str], node_id: str, report: dict, gates: dict, strict: bool) -> None:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        problems.append(f"{node_id} 测试报告缺少 summary")
        return
    total = _int_or_none(summary.get("total"))
    passed = _int_or_none(summary.get("passed"))
    failed = _int_or_none(summary.get("failed"))
    skipped = _int_or_none(summary.get("skipped", 0))
    if None in (total, passed, failed, skipped):
        problems.append(f"{node_id} summary 的 total/passed/failed/skipped 必须是整数")
        return
    if failed != 0:
        problems.append(f"{node_id} 测试报告里 failed={failed}")
    if total == 0:
        problems.append(f"{node_id} 测试总数为 0，不能宣称测试通过")
    if total != passed + failed + skipped:
        problems.append(
            f"{node_id} summary 统计不一致：total={total} ≠ "
            f"passed({passed}) + failed({failed}) + skipped({skipped})"
        )
    if skipped and strict and gates.get("require_no_skipped", True):
        problems.append(f"{node_id} 有 {skipped} 条用例被跳过，质量门槛要求全部执行")

    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        if strict and gates.get("require_case_details", True):
            problems.append(
                f"{node_id} 测试报告没有 cases 用例明细；新执行必须逐条列出"
                "（确实没有测试就用 no_test 流程）"
            )
        return
    counts = {"pass": 0, "fail": 0, "skip": 0}
    for case in cases:
        if not isinstance(case, dict):
            problems.append(f"{node_id} cases 里存在非对象项")
            continue
        result = case.get("result")
        if result not in counts:
            problems.append(f"{node_id} 用例 {case.get('name')!r} 的 result={result!r} 不合法")
            continue
        counts[result] += 1
    if counts["fail"]:
        problems.append(f"{node_id} cases 里有 {counts['fail']} 条失败用例")
    if report.get("status") == "passed" and counts["fail"]:
        problems.append(f"{node_id} status=passed 但 cases 里有失败用例")
    if not strict:
        # 旧格式报告：cases 可能只列本节点新增用例，与 summary.total（全量）不可比
        return
    if len(cases) != total:
        problems.append(f"{node_id} cases 数量 {len(cases)} 与 summary.total={total} 不一致")
    if counts["pass"] != passed or counts["fail"] != failed or counts["skip"] != skipped:
        problems.append(
            f"{node_id} cases 统计（pass={counts['pass']}, fail={counts['fail']}, "
            f"skip={counts['skip']}）与 summary 不一致"
        )


def evidence_problems(
    paths: Paths,
    node: dict,
    gates: dict | None = None,
    plan: dict | None = None,
) -> list[str]:
    """验收门槛：返回阻止 completed 的问题列表（空列表=可验收）。

    原则：**没有证据就是没做**。缺字段不补默认值，一律判失败。
    """
    gates = (gates or {}).get("gates", {}).get("node_accept", {})
    problems: list[str] = []
    node_id = node["node_id"]
    # 新一次执行（有 attempt_id）走全量校验；历史节点只做基本校验，
    # 避免升级后旧报告集体失效（兼容性要求）。
    strict = bool(node.get("current_attempt_id"))

    # ---- 执行报告 ----
    if gates.get("require_execution_report", True):
        path = report_path(paths, node, "execution_report")
        if not path.exists():
            problems.append(f"{node_id} 缺少执行报告 {paths.rel(path)}")
        else:
            evidence = load_execution_evidence(paths, node)
            if evidence is None:
                problems.append(
                    f"{node_id} 的执行报告里没有 ```devflow-execution 证据块，无法核实改动与测试"
                )
            else:
                if evidence.get("node_id") != node_id:
                    problems.append(
                        f"{node_id} 执行报告证据块的 node_id 是 {evidence.get('node_id')!r}，对不上"
                    )
                result = evidence.get("result")
                if result != "success":
                    problems.append(
                        f"{node_id} 执行报告 result={result!r}，只有 success 才能进入验收"
                    )
                _binding_problems(problems, "执行报告", evidence, node, plan)
                if gates.get("require_acceptance_evidence", True) and strict:
                    _acceptance_problems(problems, node, evidence)
                if gates.get("require_requirement_evidence", True) and strict:
                    problems.extend(
                        requirement_acceptance_problems(paths, node, evidence, plan)
                    )
                modified = evidence.get("modified_files")
                if modified is None:
                    # 不允许"没写 = 没改"：这会直接绕过 files_scope
                    if strict:
                        problems.append(
                            f"{node_id} 执行报告缺少 modified_files；新执行必须显式声明"
                            "（没有改动写空数组 []）"
                        )
                elif not isinstance(modified, list):
                    problems.append(f"{node_id} 执行报告的 modified_files 必须是数组")

    # ---- 无测试节点 ----
    if node.get("no_test"):
        if not node.get("no_test_reason"):
            problems.append(f"{node_id} 标记了 no_test 但没有写 no_test_reason")
        return problems

    # ---- 测试报告 ----
    report = load_test_report(paths, node)
    if report is None:
        if gates.get("require_test_report", True):
            problems.append(f"{node_id} 缺少测试证据 test-report.json")
        return problems

    if report.get("node_id") != node_id:
        problems.append(
            f"{node_id} 的 test-report.json 里 node_id 是 {report.get('node_id')!r}，对不上"
        )

    status = report.get("status")
    if gates.get("require_tests_passed", True) and status != "passed":
        problems.append(f"{node_id} 测试状态是 {status}，只有 passed 才能验收")

    _binding_problems(problems, "测试报告", report, node, plan)

    if gates.get("require_commands_executed", True):
        _command_problems(problems, node_id, report)

    _summary_problems(problems, node_id, report, gates, strict)
    return problems


EXEC_BLOCK_PATTERNS = [
    re.compile(r"```devflow-execution\s*\n(.*?)\n```", re.S),
    re.compile(r"```json\s+devflow-execution\s*\n(.*?)\n```", re.S),
]


def parse_execution_evidence(text: str) -> dict | None:
    """从 execution-report.md 里取出机器可读证据块。

    执行报告是给人看的 Markdown，但里面必须带一个围栏代码块：

        ```devflow-execution
        {"node_id": "DF-001", "result": "success", "modified_files": [...]}
        ```

    没有这个块，主控就无法核实"改了哪些文件、测试是不是真的跑了"。
    """
    for pattern in EXEC_BLOCK_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                raise DevFlowError(f"执行报告里的 devflow-execution 块不是合法 JSON：{exc}")
    return None


def load_execution_evidence(paths: Paths, node: dict) -> dict | None:
    path = report_path(paths, node, "execution_report")
    if not path.exists():
        return None
    return parse_execution_evidence(read_text(path))


def out_of_scope_files(node: dict, modified: list) -> list[str]:
    """兼容旧接口：返回越界文件（只做展示，验收请用 scope_violations）。"""
    scope = []
    for entry in node.get("files_scope") or []:
        try:
            scope.append(normalize_rel(entry))
        except ValueError:
            continue
    if not scope:
        return []
    outs = []
    for item in modified:
        try:
            rel = normalize_rel(item)
        except ValueError:
            outs.append(str(item))
            continue
        if not any(scope_match(rel, entry) for entry in scope):
            outs.append(rel)
    return outs
