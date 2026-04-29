"""账号池管理 - 持久化存储所有账号状态。

每个 Team 管理员（"主号"）持有独立的账号池，文件位于
``data/admins/{admin_id}/accounts.json``。

兼容性:

- 当调用方不传 ``admin_id`` 时，自动 fallback 到
  ``admin_registry.get_active_admin_id()`` 拿当前激活 admin；
  若仍无激活 admin（旧部署 / 未迁移）则使用模块级 ``ACCOUNTS_FILE``
  作为兜底路径，便于旧测试 monkeypatch 该常量直接生效。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from autoteam.admin_state import get_admin_email
from autoteam.mail_provider import build_account_mail_fields, get_mail_provider_name
from autoteam.textio import read_text, write_text

PROJECT_ROOT = Path(__file__).parent.parent.parent

# 旧版部署的单实例文件路径；新结构下由 admin_registry.bootstrap_admin_registry 迁出。
ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"

# 账号状态
STATUS_ACTIVE = "active"  # 在 team 中，额度可用
STATUS_EXHAUSTED = "exhausted"  # 在 team 中，额度用完
STATUS_STANDBY = "standby"  # 已移出 team，等待额度恢复
STATUS_PENDING = "pending"  # 已邀请，等待注册完成
STATUS_AUTH_PENDING = "auth_pending"  # 已在 team 中，但 Codex 认证未就绪


def _resolve_admin_id(admin_id: str | None) -> str | None:
    """admin_id 缺省时回退到当前激活 admin。"""
    if admin_id:
        return admin_id
    try:
        from autoteam.admin_registry import get_active_admin_id

        return get_active_admin_id()
    except Exception:
        return None


def _accounts_file(admin_id: str | None = None) -> Path:
    """根据 admin_id 计算 ``accounts.json`` 路径。

    - admin_id 显式传入 → ``data/admins/{admin_id}/accounts.json``。
    - admin_id 为空且当前有 active admin → 使用 active admin 的目录。
    - admin_id 为空且无 active admin → 兜底到模块级 ``ACCOUNTS_FILE``
      （兼容旧测试与未迁移部署）。

    旧测试常用 ``monkeypatch.setattr(accounts, "ACCOUNTS_FILE", tmp_path)``
    重定向写入。但若机器上已经有 active admin (data/admins.json),按 admin
    维度解析会 **绕过 monkeypatch 直接污染 production 数据**(踩过坑:
    test_accounts.py 把 owner/ready/later/always@example.com 写进了
    真实 admin 的 accounts.json)。这里参考 account_ops._resolve_auth_dir
    的做法,只要 ``ACCOUNTS_FILE`` 被显式覆盖到非默认值就尊重它,优先级
    高于 admin 维度解析。
    """
    default_file = PROJECT_ROOT / "accounts.json"
    if ACCOUNTS_FILE != default_file:
        return ACCOUNTS_FILE
    resolved = _resolve_admin_id(admin_id)
    if resolved:
        from autoteam.admin_registry import admin_data_dir

        return admin_data_dir(resolved) / "accounts.json"
    return ACCOUNTS_FILE


def _normalized_email(value):
    return (value or "").strip().lower()


def _is_main_account_email(email, admin_id: str | None = None):
    if not _normalized_email(email):
        return False
    # 兼容旧测试：monkeypatch 把 get_admin_email 替换成无参 lambda，
    # 此时调用带 admin_id 的新签名会 TypeError。先按新签名调用，失败时降级。
    try:
        admin_email = get_admin_email(admin_id)
    except TypeError:
        admin_email = get_admin_email()
    return _normalized_email(email) == _normalized_email(admin_email)


def load_accounts(admin_id: str | None = None):
    """加载某 admin 的账号列表（缺省 = 当前激活 admin）。"""
    path = _accounts_file(admin_id)
    if path.exists():
        text = read_text(path).strip()
        if text:
            return json.loads(text)
    return []


def save_accounts(accounts, admin_id: str | None = None):
    """保存账号列表（缺省 = 当前激活 admin）。"""
    path = _accounts_file(admin_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, json.dumps(accounts, indent=2, ensure_ascii=False))


def find_account(accounts, email):
    """按邮箱查找账号"""
    for acc in accounts:
        if acc["email"] == email:
            return acc
    return None


def add_account(
    email,
    password,
    cloudmail_account_id=None,
    *,
    mail_provider=None,
    mail_account_id=None,
    admin_id: str | None = None,
):
    """添加新账号到指定 admin 的账号池（缺省 = 当前激活 admin）。"""
    accounts = load_accounts(admin_id)
    if find_account(accounts, email):
        return  # 已存在

    if mail_account_id is None:
        mail_account_id = cloudmail_account_id
    resolved_mail_provider = mail_provider or (get_mail_provider_name() if mail_account_id is not None else "")
    mail_fields = (
        build_account_mail_fields(mail_account_id, provider=resolved_mail_provider)
        if mail_account_id is not None
        else {
            "mail_provider": resolved_mail_provider,
            "mail_account_id": None,
            "cloudmail_account_id": cloudmail_account_id,
        }
    )

    accounts.append(
        {
            "email": email,
            "password": password,
            **mail_fields,
            "status": STATUS_PENDING,
            "auth_file": None,  # CPA 认证文件路径
            "quota_exhausted_at": None,  # 额度用完的时间
            "quota_resets_at": None,  # 额度恢复时间
            "created_at": time.time(),
            "last_active_at": None,
            "auth_retry_count": 0,
            "auth_last_error": None,
            "auth_last_error_detail": None,
            "auth_last_failed_at": None,
            "auth_retry_after": None,
            "auth_retry_paused": False,
        }
    )
    save_accounts(accounts, admin_id=admin_id)


def update_account(email, admin_id: str | None = None, **kwargs):
    """更新账号字段（缺省 = 当前激活 admin）。"""
    accounts = load_accounts(admin_id)
    acc = find_account(accounts, email)
    if acc:
        acc.update(kwargs)
        save_accounts(accounts, admin_id=admin_id)
    return acc


def get_active_accounts(admin_id: str | None = None):
    """获取所有活跃账号（不含主号自身）。"""
    return [
        a
        for a in load_accounts(admin_id)
        if a["status"] == STATUS_ACTIVE and not _is_main_account_email(a.get("email"), admin_id)
    ]


def get_standby_accounts(admin_id: str | None = None):
    """获取所有待命账号（已移出 team，可能额度已恢复）"""
    accounts = load_accounts(admin_id)
    now = time.time()
    standby = []
    for a in accounts:
        if _is_main_account_email(a.get("email"), admin_id):
            continue
        if a["status"] == STATUS_STANDBY:
            resets_at = a.get("quota_resets_at")
            if resets_at is None:
                # 没有恢复时间 = 不是因为额度用完被移出的，随时可复用
                a["_quota_recovered"] = True
            else:
                # 有恢复时间，看是否已过
                a["_quota_recovered"] = now >= resets_at
            standby.append(a)
    # 已恢复的排前面
    standby.sort(key=lambda x: (not x.get("_quota_recovered", False), x.get("quota_exhausted_at") or 0))
    return standby


def get_next_reusable_account(admin_id: str | None = None):
    """获取下一个可重用的 standby 账号（优先额度已恢复的）"""
    standby = get_standby_accounts(admin_id)
    if standby:
        return standby[0]
    return None
