"""管理员登录态持久化。

每个 Team 管理员（即"主号"）的登录态分别保存在
``data/admins/{admin_id}/state.json`` 中，单个文件结构如下：
- session_token
- email
- password
- account_id
- workspace_name
- updated_at

兼容性:

- 当调用方不传 ``admin_id`` 时（旧调用约定），自动 fallback 到
  ``admin_registry.get_active_admin_id()`` 取出当前激活 admin 并读写其文件。
- 项目根目录下的旧 ``state.json`` 文件由 ``admin_registry.bootstrap_admin_registry``
  自动迁移到新位置；旧版纯文本 ``session`` 文件兼容仍然保留。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from autoteam.textio import read_text, write_text

PROJECT_ROOT = Path(__file__).parent.parent.parent

# 旧版部署的单实例文件路径（仍保留为模块默认 fallback，便于 monkeypatch 测试）。
# `admin_registry.bootstrap_admin_registry` 会把它迁到 data/admins/{id}/。
STATE_FILE = PROJECT_ROOT / "state.json"
LEGACY_SESSION_FILE = PROJECT_ROOT / "session"
STATE_FILE_MODE = 0o666


def _state_file_for(admin_id: str | None) -> Path:
    """根据 admin_id 计算 ``state.json`` 路径。

    - ``admin_id`` 非空 → ``data/admins/{admin_id}/state.json``。
    - ``admin_id`` 为空 → 兜底到模块级 ``STATE_FILE``,保持向后兼容
      （未传 admin_id 的旧调用 + monkeypatch 常量的旧测试都不受影响）。

    旧测试常用 ``monkeypatch.setattr(admin_state, "STATE_FILE", tmp_path)``
    重定向写入。但若机器上已有 active admin,按 admin 维度解析会 **绕过
    monkeypatch 直接污染 production state.json**(踩过坑:
    test_admin_state.test_clear_admin_state_keeps_state_file_for_symlink_safety
    把真实 admin 的 state.json 写成 ``{}``,把用户登出了)。这里只要
    ``STATE_FILE`` 被显式覆盖到非默认值就尊重它,优先级高于 admin 维度。
    """
    default_file = PROJECT_ROOT / "state.json"
    if STATE_FILE != default_file:
        return STATE_FILE
    if admin_id:
        # 延迟 import 避免循环依赖
        from autoteam.admin_registry import admin_data_dir

        return admin_data_dir(admin_id) / "state.json"
    return STATE_FILE


def _resolve_admin_id(admin_id: str | None) -> str | None:
    """admin_id 缺省时回退到当前激活 admin。仍可能返回 None（无任何 admin）。"""
    if admin_id:
        return admin_id
    try:
        from autoteam.admin_registry import get_active_admin_id

        return get_active_admin_id()
    except Exception:
        # 模块尚未初始化或索引文件缺失时静默回 None，让上层走 STATE_FILE。
        return None


def _normalize_state(data):
    if not isinstance(data, dict):
        return {}
    return {
        "email": data.get("email", "") or "",
        "session_token": data.get("session_token", "") or "",
        "password": data.get("password", "") or "",
        "account_id": data.get("account_id", "") or "",
        "workspace_name": data.get("workspace_name", "") or "",
        "updated_at": data.get("updated_at"),
    }


def _load_state_from_file(path: Path):
    if not path.exists():
        return {}

    try:
        raw = read_text(path).strip()
    except Exception:
        return {}

    if not raw:
        return {}

    try:
        return _normalize_state(json.loads(raw))
    except Exception:
        # 兼容旧版纯文本 session 文件
        return {
            "email": "",
            "session_token": raw,
            "account_id": "",
            "workspace_name": "",
            "updated_at": path.stat().st_mtime,
        }


def _save_state(state, *, path: Path):
    """保存 state 到指定路径，确保父目录存在并修正权限。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.resolve()
    write_text(target, json.dumps(_normalize_state(state), indent=2, ensure_ascii=False))
    # Docker bind mount 下文件常由容器用户写入；给宿主机用户保留可访问权限
    try:
        os.chmod(target, STATE_FILE_MODE)
    except Exception:
        pass


def _migrate_legacy_session_file():
    """把项目根的旧版纯文本 ``session`` 文件转成 ``state.json``（仅在缺省路径下生效）。"""
    if STATE_FILE.exists():
        return
    state = _load_state_from_file(LEGACY_SESSION_FILE)
    if state:
        _save_state(state, path=STATE_FILE)
        try:
            LEGACY_SESSION_FILE.unlink()
        except Exception:
            pass


def load_admin_state(admin_id: str | None = None):
    """加载某 admin 的登录态。

    :param admin_id: 目标 admin_id；缺省则回退到当前激活 admin，
        若无激活 admin 则继续兜底到模块级 ``STATE_FILE``（旧调用兼容路径）。
    """
    resolved = _resolve_admin_id(admin_id)
    if resolved:
        return _load_state_from_file(_state_file_for(resolved))

    # 旧调用路径：先做 legacy session 迁移，再读 STATE_FILE
    _migrate_legacy_session_file()
    return _load_state_from_file(STATE_FILE)


def save_admin_state(state, admin_id: str | None = None):
    """覆写指定 admin 的 state.json。"""
    resolved = _resolve_admin_id(admin_id)
    target = _state_file_for(resolved) if resolved else STATE_FILE
    _save_state(state, path=target)


def update_admin_state(admin_id: str | None = None, **kwargs):
    """局部更新某 admin 的 state.json，自动写入 ``updated_at``。"""
    state = load_admin_state(admin_id)
    state.update(kwargs)
    state["updated_at"] = time.time()
    save_admin_state(state, admin_id=admin_id)
    return state


def clear_admin_state(admin_id: str | None = None):
    """清空某 admin 的登录态，但保留文件本身（兼容 Docker 软链）。"""
    resolved = _resolve_admin_id(admin_id)
    target = _state_file_for(resolved) if resolved else STATE_FILE
    if target.exists():
        # 写空内容而不是删除（保护 Docker 软链）
        real = target.resolve()
        write_text(real, "{}")
        try:
            os.chmod(real, STATE_FILE_MODE)
        except Exception:
            pass
    if resolved is None and LEGACY_SESSION_FILE.exists():
        # 清旧路径时顺带清理纯文本 session 兼容文件
        try:
            LEGACY_SESSION_FILE.unlink()
        except Exception:
            pass


def get_admin_email(admin_id: str | None = None):
    return load_admin_state(admin_id).get("email", "")


def get_admin_session_token(admin_id: str | None = None):
    return load_admin_state(admin_id).get("session_token", "")


def _is_valid_uuid(value: str) -> bool:
    """检查是否为有效的 UUID 格式"""
    import re

    return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", value, re.I))


def get_chatgpt_account_id(admin_id: str | None = None):
    state = load_admin_state(admin_id)
    state_id = state.get("account_id", "")
    # state.json 里的值必须是 UUID 格式才有效（user-xxx 是 user ID 不是 account ID）
    if state_id and _is_valid_uuid(state_id):
        return state_id
    return os.environ.get("CHATGPT_ACCOUNT_ID", "")


def get_admin_password(admin_id: str | None = None):
    return load_admin_state(admin_id).get("password", "")


def get_chatgpt_workspace_name(admin_id: str | None = None):
    state = load_admin_state(admin_id)
    return state.get("workspace_name", "")


def get_admin_state_summary(admin_id: str | None = None):
    state = load_admin_state(admin_id)
    return {
        "configured": bool(state.get("session_token") and state.get("account_id")),
        "email": state.get("email", ""),
        "account_id": state.get("account_id", ""),
        "workspace_name": state.get("workspace_name", ""),
        "session_present": bool(state.get("session_token")),
        "password_saved": bool(state.get("password")),
        "updated_at": state.get("updated_at"),
    }
