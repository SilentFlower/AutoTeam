"""Plus 号池 - 独立管理用户导入的 ChatGPT Plus 账号。

Plus 池服务于 PRD ``04-30-plus-account-pool-oauth``：

- 用户导入已有 Plus 邮箱 / 密码，AutoTeam 不负责注册或购买 Plus；
- 通过现有 Codex OAuth 流程登录，遇到手机号验证时由 ``codex_auth`` 内部
  调用 HeroSMS 自动接码；
- OAuth 成功后必须校验 ``plan_type`` 是 Plus，才写入 active 状态并推送 sub2api；
- 数据写入 ``plus_accounts.json``，与 active 池 ``accounts.json`` 和 FREE 池
  ``free_accounts.json`` 完全隔离。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from autoteam.textio import read_text, write_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_PLUS_ACCOUNTS_FILE = PROJECT_ROOT / "data" / "plus_accounts.json"

PLUS_STATUS_ACTIVE = "active"
PLUS_STATUS_AUTH_FAILED = "auth_failed"
PLUS_STATUS_PLAN_MISMATCH = "plan_mismatch"
PLUS_STATUS_EXHAUSTED = "exhausted"

_QUOTA_REFRESHABLE_STATUSES = (PLUS_STATUS_ACTIVE, PLUS_STATUS_EXHAUSTED)
_login_in_progress: set[str] = set()
_login_lock = threading.Lock()


def _resolve_admin_id(admin_id: str | None) -> str | None:
    """admin_id 缺省时回退到当前激活 admin。

    :param admin_id: 显式传入的管理员标识。
    :return: 可用 admin_id；无激活管理员时返回 ``None``。
    """
    if admin_id:
        return admin_id
    try:
        from autoteam.admin_registry import get_active_admin_id

        return get_active_admin_id()
    except Exception:
        return None


def _plus_accounts_file(admin_id: str | None = None) -> Path:
    """根据 admin_id 计算 ``plus_accounts.json`` 路径。

    :param admin_id: 目标 admin_id；缺省回退当前激活 admin。
    :return: 当前 admin 的 Plus 池 JSON 路径，或旧式单实例兜底路径。
    """
    resolved = _resolve_admin_id(admin_id)
    if resolved:
        from autoteam.admin_registry import admin_data_dir

        return admin_data_dir(resolved) / "plus_accounts.json"
    return DEFAULT_PLUS_ACCOUNTS_FILE


def _normalized_email(value: object | None) -> str:
    """统一邮箱比较的小写归一化。

    :param value: 原始邮箱值。
    :return: 去空格后的小写邮箱。
    """
    return str(value or "").strip().lower()


def load_plus(admin_id: str | None = None) -> list[dict[str, Any]]:
    """加载某 admin 的 Plus 号列表。

    :param admin_id: 目标 admin_id；缺省回退激活 admin。
    :return: Plus 号记录列表；文件不存在或为空时返回 ``[]``。
    """
    path = _plus_accounts_file(admin_id)
    if path.exists():
        text = read_text(path).strip()
        if text:
            return json.loads(text)
    return []


def save_plus(records: list[dict[str, Any]], admin_id: str | None = None) -> None:
    """原子写入 Plus 号列表。

    :param records: 待写入的 Plus 号记录列表。
    :param admin_id: 目标 admin_id；缺省回退激活 admin。
    :return: 无返回值。
    """
    path = _plus_accounts_file(admin_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    write_text(tmp_path, json.dumps(records, indent=2, ensure_ascii=False))
    os.replace(tmp_path, path)


def find_plus(records: list[dict[str, Any]], email: str) -> dict[str, Any] | None:
    """大小写不敏感地按邮箱查找 Plus 号记录。

    :param records: Plus 号记录列表。
    :param email: 目标邮箱。
    :return: 命中的记录；不存在时返回 ``None``。
    """
    target = _normalized_email(email)
    if not target:
        return None
    for rec in records:
        if _normalized_email(rec.get("email")) == target:
            return rec
    return None


def add_plus(record: dict[str, Any], admin_id: str | None = None) -> dict[str, Any]:
    """追加一条 Plus 号记录。

    :param record: 待追加记录；必须包含 ``email`` 字段。
    :param admin_id: 目标 admin_id；缺省回退激活 admin。
    :raises ValueError: 缺 email 或同邮箱已存在时抛出。
    :return: 入库后的归一化记录。
    """
    email = _normalized_email(record.get("email"))
    if not email:
        raise ValueError("Plus 号记录缺少 email 字段")

    records = load_plus(admin_id)
    if find_plus(records, email):
        raise ValueError(f"Plus 号已存在: {email}")

    normalized = _normalize_record({**record, "email": email})
    records.append(normalized)
    save_plus(records, admin_id=admin_id)
    return normalized


def update_plus(email: str, admin_id: str | None = None, **fields: Any) -> dict[str, Any] | None:
    """按邮箱局部更新 Plus 号记录。

    :param email: 目标邮箱。
    :param admin_id: 目标 admin_id；缺省回退激活 admin。
    :param fields: 要覆盖的字段集合。
    :return: 更新后的记录；邮箱不存在时返回 ``None``。
    """
    records = load_plus(admin_id)
    rec = find_plus(records, email)
    if rec is None:
        return None
    rec.update(fields)
    save_plus(records, admin_id=admin_id)
    return rec


def delete_plus(email: str, admin_id: str | None = None) -> dict[str, Any] | None:
    """仅删除本地 ``plus_accounts.json`` 中的一条记录。

    级联清理请调用 :func:`delete_plus_account`。

    :param email: 目标邮箱。
    :param admin_id: 目标 admin_id；缺省回退激活 admin。
    :return: 被删除的记录；不存在时返回 ``None``。
    """
    records = load_plus(admin_id)
    rec = find_plus(records, email)
    if rec is None:
        return None
    target = _normalized_email(email)
    save_plus([r for r in records if _normalized_email(r.get("email")) != target], admin_id=admin_id)
    return rec


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """给 Plus 号记录补齐默认字段，避免旧数据读取时 KeyError。"""
    return {
        "email": _normalized_email(record.get("email")),
        "password": record.get("password", ""),
        "auth_file": record.get("auth_file"),
        "status": record.get("status", PLUS_STATUS_AUTH_FAILED),
        "plan_type": record.get("plan_type", ""),
        "last_error": record.get("last_error"),
        "created_at": record.get("created_at", time.time()),
        "last_login_at": record.get("last_login_at"),
        "last_quota": record.get("last_quota"),
        "last_quota_at": record.get("last_quota_at"),
        "last_sub2api_synced_at": record.get("last_sub2api_synced_at"),
    }


def _is_plus_plan(plan_type: object) -> bool:
    """判断 OAuth claim 中的 plan_type 是否表示 Plus。"""
    normalized = str(plan_type or "").strip().lower().replace("-", "_")
    return normalized in {"plus", "chatgpt_plus"}


def _upsert_plus_record(email: str, admin_id: str | None, **fields: Any) -> dict[str, Any]:
    """新增或更新 Plus 号记录，供导入/重授权共用。"""
    records = load_plus(admin_id)
    existing = find_plus(records, email)
    if existing:
        existing.update(fields)
        existing["email"] = _normalized_email(email)
        save_plus(records, admin_id=admin_id)
        return existing
    return add_plus({"email": email, **fields}, admin_id=admin_id)


def _sync_plus_to_sub2api_best_effort(admin_id: str | None = None) -> None:
    """Plus 授权成功后尝试推送 sub2api，失败只记录日志。"""
    try:
        from autoteam.sub2api_sync import sync_plus_to_sub2api

        sync_plus_to_sub2api(admin_id=admin_id)
    except Exception as exc:
        logger.warning("[Plus池] 同步 sub2api 失败，可稍后手动重试: %s", exc)


def import_plus_account(email: str, password: str, admin_id: str | None = None) -> dict[str, Any]:
    """导入已有 Plus 邮箱/密码并执行 Codex OAuth 登录。

    :param email: 用户已有 Plus 账号邮箱。
    :param password: 用户已有 Plus 账号密码。
    :param admin_id: 目标 admin_id；缺省回退激活 admin。
    :raises ValueError: 邮箱或密码为空时抛出。
    :raises RuntimeError: 同邮箱已有导入/重授权任务在执行时抛出。
    :return: ``{ok, email, status, auth_file, plan_type, record, error_detail}``。
    """
    norm = _normalized_email(email)
    if not norm:
        raise ValueError("Plus 号邮箱不能为空")
    if not password:
        raise ValueError("Plus 号密码不能为空")
    return _login_and_store_plus(norm, password, admin_id=admin_id)


def reauth_plus_account(email: str, admin_id: str | None = None) -> dict[str, Any]:
    """使用已保存密码对单条 Plus 号重新执行 Codex OAuth 授权。

    :param email: 目标 Plus 号邮箱。
    :param admin_id: 目标 admin_id；缺省回退激活 admin。
    :raises RuntimeError: 记录不存在或缺少密码时抛出。
    :return: ``{ok, email, status, auth_file, plan_type, record, error_detail}``。
    """
    records = load_plus(admin_id)
    rec = find_plus(records, email)
    if not rec:
        raise RuntimeError(f"找不到 Plus 号记录: {email}")
    password = rec.get("password") or ""
    if not password:
        raise RuntimeError(f"Plus 号缺少密码字段，无法重新授权: {email}")
    return _login_and_store_plus(_normalized_email(email), password, admin_id=admin_id)


def _login_and_store_plus(email: str, password: str, *, admin_id: str | None = None) -> dict[str, Any]:
    """导入和重授权共用的 OAuth 主体。"""
    from autoteam.codex_auth import save_auth_file
    from autoteam.mail_provider import get_mail_client
    from autoteam.manager import _login_codex_with_result

    with _login_lock:
        if email in _login_in_progress:
            raise RuntimeError(f"该 Plus 号正在授权中，请稍候: {email}")
        _login_in_progress.add(email)

    try:
        logger.info("[Plus池] 开始 Codex OAuth: %s", email)
        mail_client = get_mail_client()
        mail_client.login()
        login_result = _login_codex_with_result(
            email,
            password,
            mail_client=mail_client,
            allow_non_team=True,
        )
        bundle = login_result.get("bundle") if isinstance(login_result, dict) else None
        oauth_ok = bool(login_result and login_result.get("ok") and bundle)
        plan_type = str((bundle or {}).get("plan_type") or "").lower()
        auth_file: str | None = None
        error_detail = None
        status = PLUS_STATUS_AUTH_FAILED

        if oauth_ok and _is_plus_plan(plan_type):
            try:
                auth_file = save_auth_file(bundle, admin_id=admin_id)
                status = PLUS_STATUS_ACTIVE
            except Exception as exc:
                logger.error("[Plus池] 保存 auth_file 失败: %s (%s)", email, exc)
                error_detail = f"保存 auth_file 失败: {exc}"
                oauth_ok = False
        elif oauth_ok:
            status = PLUS_STATUS_PLAN_MISMATCH
            error_detail = f"登录后 plan={plan_type or 'unknown'}，不是 Plus"
            oauth_ok = False
        else:
            error_detail = (
                login_result.get("error_detail") if isinstance(login_result, dict) else "Codex OAuth 登录失败"
            )

        record = _upsert_plus_record(
            email,
            admin_id,
            password=password,
            auth_file=auth_file,
            status=status,
            plan_type=plan_type,
            last_error=error_detail,
            last_login_at=time.time(),
            last_quota=None,
            last_quota_at=None,
            last_sub2api_synced_at=None,
        )
        logger.info("[Plus池] OAuth 完成: %s (status=%s, plan=%s)", email, status, plan_type or "unknown")

        if status == PLUS_STATUS_ACTIVE:
            _sync_plus_to_sub2api_best_effort(admin_id)

        return {
            "ok": status == PLUS_STATUS_ACTIVE,
            "email": email,
            "status": status,
            "auth_file": auth_file,
            "plan_type": plan_type,
            "record": record,
            "error_detail": error_detail,
        }
    finally:
        with _login_lock:
            _login_in_progress.discard(email)


def check_plus_quota(emails: list[str] | None = None, admin_id: str | None = None) -> dict[str, str]:
    """串行刷新 Plus 号额度并写回 ``last_quota``。

    :param emails: 指定邮箱列表；``None`` 表示刷新全部 active/exhausted Plus 号。
    :param admin_id: 目标 admin_id；缺省回退激活 admin。
    :return: 邮箱到状态字符串的映射。
    """
    from autoteam.codex_auth import (
        check_codex_quota,
        get_quota_exhausted_info,
        quota_result_quota_info,
        refresh_access_token,
    )

    records = load_plus(admin_id)
    target_set = {_normalized_email(e) for e in emails} if emails is not None else None
    summary: dict[str, str] = {}

    for rec in records:
        email = rec.get("email") or ""
        norm_email = _normalized_email(email)
        if not norm_email:
            continue
        if target_set is not None:
            if norm_email not in target_set:
                continue
        elif rec.get("status") not in _QUOTA_REFRESHABLE_STATUSES:
            continue

        auth_file = rec.get("auth_file")
        if not auth_file:
            summary[email] = "no_auth"
            continue
        auth_path = Path(auth_file)
        if not auth_path.exists():
            summary[email] = "no_auth"
            logger.warning("[Plus池] %s auth_file 不存在: %s", email, auth_file)
            continue

        try:
            auth_data = json.loads(read_text(auth_path))
        except Exception as exc:
            summary[email] = "auth_error"
            logger.error("[Plus池] %s 解析 auth_file 失败: %s", email, exc)
            continue

        access_token = auth_data.get("access_token")
        refresh_token_value = auth_data.get("refresh_token")
        if not access_token:
            summary[email] = "no_auth"
            continue

        status, info = check_codex_quota(access_token)
        if status == "auth_error" and refresh_token_value:
            logger.info("[Plus池] %s token 过期，尝试刷新", email)
            new_tokens = refresh_access_token(refresh_token_value)
            if new_tokens:
                auth_data["access_token"] = new_tokens.get("access_token", access_token)
                auth_data["refresh_token"] = new_tokens.get("refresh_token", refresh_token_value)
                auth_data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                try:
                    write_text(auth_path, json.dumps(auth_data, indent=2))
                except Exception as exc:
                    logger.warning("[Plus池] %s 写回 auth_file 失败: %s", email, exc)
                status, info = check_codex_quota(auth_data["access_token"])

        quota_snapshot: dict[str, Any] | None = None
        new_status = rec.get("status")
        if status == "ok" and isinstance(info, dict):
            quota_snapshot = info
            new_status = PLUS_STATUS_ACTIVE
        elif status == "exhausted":
            quota_snapshot = quota_result_quota_info(info) or get_quota_exhausted_info(
                info if isinstance(info, dict) else {}
            )
            new_status = PLUS_STATUS_EXHAUSTED

        update_plus(
            email,
            admin_id=admin_id,
            last_quota=quota_snapshot,
            last_quota_at=time.time(),
            status=new_status,
        )
        summary[email] = status

    if target_set is not None:
        present = {_normalized_email(r.get("email")) for r in records}
        for raw in emails or []:
            norm = _normalized_email(raw)
            if norm and norm not in present:
                summary[raw] = "not_found"

    return summary


def delete_plus_account(
    email: str,
    *,
    admin_id: str | None = None,
    cleanup_remote: bool = True,
) -> dict[str, Any]:
    """删除 Plus 号并清理本地 auth_file 与 sub2api 远端账号。

    :param email: 目标邮箱。
    :param admin_id: 目标 admin_id；缺省回退激活 admin。
    :param cleanup_remote: 是否同步删除 sub2api 远端账号。
    :return: cleanup 摘要 ``{local_record, local_auth_files, sub2api_accounts}``。
    """
    cleanup: dict[str, Any] = {
        "local_record": False,
        "local_auth_files": [],
        "sub2api_accounts": [],
    }
    records = load_plus(admin_id)
    rec = find_plus(records, email)
    if rec is None:
        logger.info("[Plus池] 删除时未找到本地记录: %s", email)
        return cleanup

    auth_file = rec.get("auth_file")
    if auth_file:
        path = Path(auth_file)
        try:
            if path.exists() and path.is_file():
                path.unlink()
                cleanup["local_auth_files"].append(path.name)
                logger.info("[Plus池] 已删除 auth_file: %s", path.name)
        except Exception as exc:
            logger.warning("[Plus池] 删除 auth_file 失败: %s (%s)", auth_file, exc)

    if cleanup_remote:
        try:
            from autoteam.sub2api_sync import delete_plus_account_from_sub2api

            result = delete_plus_account_from_sub2api(email, auth_names=list(cleanup["local_auth_files"]))
            cleanup["sub2api_accounts"] = list((result or {}).get("deleted", []))
        except Exception as exc:
            logger.warning("[Plus池] 删除 sub2api 远端账号失败，跳过: %s (%s)", email, exc)

    deleted = delete_plus(email, admin_id=admin_id)
    if deleted is not None:
        cleanup["local_record"] = True
        logger.info("[Plus池] 已删除本地记录: %s", email)

    return cleanup
