"""免费号池 - 与 active 池完全隔离的"已注册可登录、不占 Team 席位"账号资产。

设计目标(PRD ``04-29-free-account-generator``):

- **存储隔离**: 免费号写到 ``data/admins/{admin_id}/free_accounts.json``(或
  无 admin 时落到 ``data/free_accounts.json``),与 ``accounts.json`` 完全分开。
- **流程隔离**: 现有 ``cmd_check / cmd_rotate / cmd_fill / cmd_cleanup``
  一律不读 ``free_accounts.json``;反向也不写 ``accounts.json``。
- **能力复用**: 通过直接 import 复用 ``manager.invite_to_team`` /
  ``manager.remove_from_team`` / ``manager._login_codex_with_result`` /
  ``codex_auth.save_auth_file`` / ``codex_auth.check_codex_quota`` /
  ``mail_provider.get_mail_client``,但**不**调用 ``manager._run_invite_login_flow``
  ——后者会写 ``accounts.json``,违反隔离铁律。

数据 schema(PRD R2 已锁定)::

    {
      "email": "...",
      "password": "...",
      "auth_file": "data/admins/{aid}/auths/codex-{email}-{ts}.json | null",
      "status": "active | auth_failed | exhausted",
      "mail_provider": "cloudmail",
      "mail_account_id": 123,
      "team_residue": false,  # 与 status 正交的布尔字段:移出 Team 失败时打 True
      "created_at": 1234567890,
      "last_quota": null,
      "last_quota_at": null,
      "last_sub2api_synced_at": null
    }

PRD R2 原文把 ``team_residue`` 同时列在 ``status`` 候选值和单独字段两处。
落地时选择"布尔字段 + status 保持业务语义"的方案:status 表达"账号本身是否可用",
team_residue 表达"是否需要用户手动清理 Team 残留"——两者正交,允许出现
``status=active, team_residue=true`` 这种组合。

公开 API:

- 数据层: ``load_free / save_free / find_free / add_free / update_free / delete_free``
- 业务命令: ``cmd_generate_free_account / check_free_quota / delete_free_account``
- 内部辅助: ``_verify_team_removal``(PRD F2 二次确认)
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from autoteam.textio import read_text, write_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent

# 旧式 / 单实例部署兜底:无激活 admin 时使用顶层 ``data/free_accounts.json``。
# 与 ``accounts.ACCOUNTS_FILE`` 同样保留为模块常量,便于旧测试 monkeypatch。
DEFAULT_FREE_ACCOUNTS_FILE = PROJECT_ROOT / "data" / "free_accounts.json"

# ---------------------------------------------------------------------------
# 状态枚举
# ---------------------------------------------------------------------------
# 命名前缀使用 ``FREE_STATUS_`` 与 accounts 模块的 ``STATUS_`` 区分,
# 防止跨模块误用。值与 accounts 的 active/exhausted 字符串可重合(语义一致),
# 但两个模块各自独立维护,避免一改全改。
# Team 残留(remove_from_team 后二次确认仍在 Team 内)用单独的 ``team_residue``
# 布尔字段表达,而不是枚举到 status 里——见模块 docstring 解释。
FREE_STATUS_ACTIVE = "active"  # 已加入 Team -> 立即移出 -> auth_file 落库,可用
FREE_STATUS_AUTH_FAILED = "auth_failed"  # Step A 成功但 Step B 失败,auth_file=None 半成品
FREE_STATUS_EXHAUSTED = "exhausted"  # 额度用尽(用户手动刷新额度时检测到)

# 可被 ``check_free_quota`` 默认刷新的状态白名单。
_QUOTA_REFRESHABLE_STATUSES = (FREE_STATUS_ACTIVE, FREE_STATUS_EXHAUSTED)


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def _resolve_admin_id(admin_id: str | None) -> str | None:
    """admin_id 缺省时回退到当前激活 admin。"""
    if admin_id:
        return admin_id
    try:
        from autoteam.admin_registry import get_active_admin_id

        return get_active_admin_id()
    except Exception:
        # admin_registry 模块在极早期启动 / 旧测试中可能不可用,降级到默认路径
        return None


def _free_accounts_file(admin_id: str | None = None) -> Path:
    """根据 admin_id 计算 ``free_accounts.json`` 路径。

    与 ``accounts._accounts_file`` 同源逻辑:

    - 显式 admin_id → ``data/admins/{admin_id}/free_accounts.json``;
    - 缺省且有 active admin → 该 admin 的目录;
    - 缺省且无 active admin → 模块级 ``DEFAULT_FREE_ACCOUNTS_FILE``。
    """
    resolved = _resolve_admin_id(admin_id)
    if resolved:
        from autoteam.admin_registry import admin_data_dir

        return admin_data_dir(resolved) / "free_accounts.json"
    return DEFAULT_FREE_ACCOUNTS_FILE


def _normalized_email(value: object | None) -> str:
    """统一邮箱比较的小写归一化。"""
    return str(value or "").strip().lower()


# ---------------------------------------------------------------------------
# 数据层 CRUD
# ---------------------------------------------------------------------------


def load_free(admin_id: str | None = None) -> list[dict[str, Any]]:
    """加载某 admin 的免费号列表(缺省 = 当前激活 admin)。

    :param admin_id: 目标 admin_id;缺省回退激活 admin,无激活则用顶层文件。
    :return: 免费号 dict 列表;文件不存在或为空时返回 ``[]``。
    """
    path = _free_accounts_file(admin_id)
    if path.exists():
        text = read_text(path).strip()
        if text:
            return json.loads(text)
    return []


def save_free(records: list[dict[str, Any]], admin_id: str | None = None) -> None:
    """原子写入免费号列表(缺省 = 当前激活 admin)。

    采用"先写 ``.tmp``,再 ``os.replace``"两步原子替换,避免中途崩溃留下半成品。
    """
    path = _free_accounts_file(admin_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    write_text(tmp_path, json.dumps(records, indent=2, ensure_ascii=False))
    # os.replace 在同一文件系统上是原子的;跨设备会抛 OSError 由调用方感知
    os.replace(tmp_path, path)


def find_free(records: list[dict[str, Any]], email: str) -> dict[str, Any] | None:
    """大小写不敏感地按邮箱查找免费号记录。"""
    target = _normalized_email(email)
    if not target:
        return None
    for rec in records:
        if _normalized_email(rec.get("email")) == target:
            return rec
    return None


def add_free(record: dict[str, Any], admin_id: str | None = None) -> dict[str, Any]:
    """追加一条免费号记录。

    :param record: 待追加记录;必须包含 ``email`` 字段。
    :raises ValueError: 缺 email 或同邮箱已存在时抛出。
    :return: 入库后的归一化记录(``_normalize_record`` 新建的 dict,与传入参数不共享引用)。
    """
    email = _normalized_email(record.get("email"))
    if not email:
        raise ValueError("免费号记录缺少 email 字段")

    records = load_free(admin_id)
    if find_free(records, email):
        raise ValueError(f"免费号已存在: {email}")

    # 字段补默认值,避免后续读取者命中 KeyError
    normalized = _normalize_record(record)
    records.append(normalized)
    save_free(records, admin_id=admin_id)
    return normalized


def update_free(email: str, admin_id: str | None = None, **fields: Any) -> dict[str, Any] | None:
    """部分字段更新,未列出字段保留原值。

    :return: 更新后的记录;email 不存在时返回 ``None``。
    """
    records = load_free(admin_id)
    rec = find_free(records, email)
    if rec is None:
        return None
    rec.update(fields)
    save_free(records, admin_id=admin_id)
    return rec


def delete_free(email: str, admin_id: str | None = None) -> dict[str, Any] | None:
    """**仅**删除本地 ``free_accounts.json`` 中的条目,不做任何远端清理。

    业务侧的级联删除请走 ``delete_free_account``。

    :return: 被删除的记录;不存在时返回 ``None``。
    """
    records = load_free(admin_id)
    rec = find_free(records, email)
    if rec is None:
        return None
    target = _normalized_email(email)
    remaining = [r for r in records if _normalized_email(r.get("email")) != target]
    save_free(remaining, admin_id=admin_id)
    return rec


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """按 PRD R2 schema 给字段补默认值,确保入库记录字段齐全。"""
    return {
        "email": record.get("email", ""),
        "password": record.get("password", ""),
        "auth_file": record.get("auth_file"),
        "status": record.get("status", FREE_STATUS_ACTIVE),
        "mail_provider": record.get("mail_provider", ""),
        "mail_account_id": record.get("mail_account_id"),
        "team_residue": bool(record.get("team_residue", False)),
        "created_at": record.get("created_at", time.time()),
        "last_quota": record.get("last_quota"),
        "last_quota_at": record.get("last_quota_at"),
        "last_sub2api_synced_at": record.get("last_sub2api_synced_at"),
    }


# ---------------------------------------------------------------------------
# 内部辅助: F2 二次确认 remove
# ---------------------------------------------------------------------------


def _verify_team_removal(chatgpt_api, email: str, *, retries: int = 2, sleep_seconds: float = 2.0) -> bool:
    """PRD F2 末尾二次确认:拉一次 Team 成员列表确认账号确实不在了。

    流程(参考 PRD Technical Approach 伪代码):

    1. 拉成员列表;不含目标邮箱 → 返回 ``True``;
    2. 含目标邮箱且仍可重试 → ``time.sleep`` + 再调一次 ``remove_from_team`` 后重试;
    3. 用尽重试仍命中 → 返回 ``False``,调用方应在记录上打 ``team_residue=true``。

    :param chatgpt_api: 已 start 的 ChatGPTTeamAPI 实例。
    :param email: 待确认离开 Team 的邮箱。
    :param retries: 最大重试次数(含首次确认)。<=1 时只确认一次,不重试。
    :param sleep_seconds: 每次重试前的等待秒数,主要为了让 OpenAI 后端状态收敛。
    :return: True 表示已确认离开 Team;False 表示用尽重试仍残留。
    """
    # 局部 import 避免顶部循环依赖(manager.py 在 import 阶段会触发 display 等副作用)
    from autoteam.account_ops import fetch_team_state
    from autoteam.manager import remove_from_team

    target = _normalized_email(email)
    attempts = max(1, int(retries))

    for attempt in range(attempts):
        try:
            members, _invites = fetch_team_state(chatgpt_api)
        except Exception as exc:
            logger.warning("[免费号] 拉取 Team 成员列表失败,无法确认 %s 是否已移出: %s", email, exc)
            return False

        member_emails = {_normalized_email(m.get("email")) for m in members}
        if target not in member_emails:
            return True

        # 命中残留:还有重试机会则补刀一次 remove
        if attempt < attempts - 1:
            logger.warning("[免费号] %s 仍在 Team 成员列表,等待 %ss 后重试 remove", email, sleep_seconds)
            time.sleep(sleep_seconds)
            try:
                remove_from_team(chatgpt_api, email)
            except Exception as exc:
                logger.warning("[免费号] 二次 remove 调用异常: %s", exc)

    return False


# ---------------------------------------------------------------------------
# 业务命令
# ---------------------------------------------------------------------------


def _generate_free_password() -> str:
    """生成符合 OpenAI 复杂度要求的密码(大小写字母 + 数字 + 符号)。

    与 ``manager.create_account_via_invite`` 内部生成的密码格式保持一致。
    """
    return f"Tmp_{uuid.uuid4().hex[:12]}!"


def cmd_generate_free_account(count: int = 1, admin_id: str | None = None) -> list[dict[str, Any]]:
    """生成 ``count`` 个免费号(PRD R3 / D5 流程)。

    串行循环,每轮:

    1. 申请临时邮箱(``mail_provider.get_mail_client``);
    2. 母号发邀请(``manager.invite_to_team``);
    3. Playwright 跑邀请链接登录(Step A: ``invite.login_with_invite``);
    4. 跑 Codex OAuth(Step B: ``manager._login_codex_with_result``);
    5. **任意成功路径**都立即 ``manager.remove_from_team`` + ``_verify_team_removal``;
    6. 落库到 ``free_accounts.json``。

    分支(PRD D5 锁定):

    - **Step A 失败** → 母号邀请未被接受,账号没进 Team,**不落库**;尝试删除临时邮箱;
    - **Step A 成功 + Step B 失败** → 半成品落库 ``status=auth_failed, auth_file=null``,
      用户可在 FreePage 里手动重试或删除;
    - **Step A 成功 + Step B 成功** → ``status=active`` 落库,带 auth_file。

    每条独立 try,单条失败不阻塞其他;所有写盘前都做 F2 二次确认 remove。

    :param count: 本次生成的目标数量;<=0 时直接返回空列表。
    :param admin_id: 目标 admin;缺省回退激活 admin。
    :return: 本次生成成功落库的记录列表(不含跳过/中途失败的条目)。
    """
    if count <= 0:
        return []

    from autoteam.chatgpt_api import ChatGPTTeamAPI

    generated: list[dict[str, Any]] = []
    for index in range(count):
        logger.info("[免费号] 开始生成第 %d/%d 个免费号", index + 1, count)
        try:
            record = _generate_one_free_account(ChatGPTTeamAPI, admin_id=admin_id)
            if record:
                generated.append(record)
        except Exception as exc:
            # 单条失败:写错误日志后继续下一条,不影响其他;不抛
            logger.error("[免费号] 第 %d/%d 个免费号生成异常: %s", index + 1, count, exc)

    logger.info("[免费号] 本次共落库 %d/%d 个免费号", len(generated), count)

    # PRD R3 第 8 步:落库后触发一次 FREE → sub2api 同步,
    # 与 ``cmd_add`` 末尾 ``sync_to_configured_targets()`` 的惯例对齐。
    # 同步异常不应让生成命令本身失败——日志记一笔即可,用户可在 FreePage 手动重试。
    if generated:
        try:
            from autoteam.sub2api_sync import sync_free_to_sub2api

            sync_free_to_sub2api()
        except Exception as exc:
            logger.warning("[免费号] 落库后同步 sub2api 失败,可在 FreePage 手动重试: %s", exc)

    return generated


def _generate_one_free_account(chatgpt_api_factory, *, admin_id: str | None = None) -> dict[str, Any] | None:
    """单次生成一个免费号并落库,返回入库记录或 ``None``。

    :param chatgpt_api_factory: 一个无参可调用对象,调用后返回 ``ChatGPTTeamAPI`` 实例;
        以工厂模式注入是为了让单元测试可以 mock 母号会话。
    :param admin_id: 目标 admin。
    """
    from autoteam.codex_auth import save_auth_file
    from autoteam.mail_provider import get_mail_client
    from autoteam.manager import (
        _login_codex_with_result,
        invite_to_team,
    )

    mail_client = get_mail_client()
    mail_client.login()

    chatgpt = chatgpt_api_factory()
    chatgpt.start()

    try:
        # ---- Step 1: 临时邮箱
        mail_account_id, email = mail_client.create_temp_email()
        logger.info("[免费号] 临时邮箱已申请: %s", email)
        password = _generate_free_password()

        # ---- Step 2: 母号邀请
        invited = invite_to_team(chatgpt, email)
        if not invited:
            logger.error("[免费号] 母号邀请失败,放弃: %s", email)
            _safe_delete_temp_email(mail_client, mail_account_id)
            return None

        # ---- Step 3: 等待邀请邮件 & 提取邀请链接
        invite_link = _fetch_invite_link(mail_client, email)
        if not invite_link:
            logger.error("[免费号] 未获取到邀请链接,放弃: %s", email)
            _safe_delete_temp_email(mail_client, mail_account_id)
            return None

        # 邀请发出后母号浏览器需要释放,后续 Playwright 才能用
        try:
            chatgpt.stop()
        except Exception as exc:
            logger.warning("[免费号] 母号会话 stop 异常: %s", exc)

        # ---- Step A: 邀请链接登录(加入 Team)
        joined = _run_invite_login_step_a(email, password, mail_client, invite_link)
        if not joined:
            # PRD D5: Step A 失败 → 不落库
            logger.error("[免费号] Step A 邀请链接登录失败,不落库: %s", email)
            _safe_delete_temp_email(mail_client, mail_account_id)
            return None

        # ---- Step B: Codex OAuth(无论成功失败都要 remove + 落库)
        login_result = _login_codex_with_result(email, password, mail_client=mail_client)
        bundle = login_result.get("bundle") if isinstance(login_result, dict) else None
        oauth_ok = bool(login_result and login_result.get("ok") and bundle)

        # ---- 立即 remove + 二次确认(无论 Step B 是否成功)
        chatgpt = chatgpt_api_factory()
        chatgpt.start()
        team_residue = False
        try:
            remove_status = _safe_remove(chatgpt, email)
            verified = _verify_team_removal(chatgpt, email)
            if not verified:
                team_residue = True
                logger.error(
                    "[免费号] 移出 Team 后二次确认仍残留,标记 team_residue=true: %s (remove=%s)",
                    email,
                    remove_status,
                )
        finally:
            try:
                chatgpt.stop()
            except Exception as exc:
                logger.warning("[免费号] remove 阶段 chatgpt stop 异常: %s", exc)

        # ---- 落库
        auth_file: str | None = None
        if oauth_ok:
            try:
                auth_file = save_auth_file(bundle, admin_id=admin_id)
            except Exception as exc:
                # OAuth 拿到 bundle 但写文件失败:降级为半成品,不让流程崩
                logger.error("[免费号] 保存 auth_file 失败,降级为 auth_failed: %s (%s)", email, exc)
                auth_file = None
                oauth_ok = False

        status = FREE_STATUS_ACTIVE if oauth_ok else FREE_STATUS_AUTH_FAILED
        record = {
            "email": email,
            "password": password,
            "auth_file": auth_file,
            "status": status,
            "mail_provider": getattr(mail_client, "provider_name", ""),
            "mail_account_id": mail_account_id,
            "team_residue": team_residue,
            "created_at": time.time(),
            "last_quota": None,
            "last_quota_at": None,
            "last_sub2api_synced_at": None,
        }
        saved = add_free(record, admin_id=admin_id)
        logger.info(
            "[免费号] 落库成功: %s (status=%s, team_residue=%s)",
            email,
            status,
            team_residue,
        )
        return saved
    finally:
        # 兜底关闭 chatgpt 会话(若仍处于 ready 状态)
        try:
            chatgpt.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 内部辅助:子步骤
# ---------------------------------------------------------------------------


def _fetch_invite_link(mail_client, email: str, timeout: int = 180) -> str | None:
    """等邀请邮件并提取邀请链接。失败返回 ``None`` 并写 warn 日志。"""
    try:
        email_data = mail_client.wait_for_email(
            to_email=email,
            timeout=timeout,
            sender_keyword="openai",
        )
        return mail_client.extract_invite_link(email_data)
    except TimeoutError:
        logger.error("[免费号] 等待邀请邮件超时: %s", email)
        return None
    except Exception as exc:
        logger.error("[免费号] 获取邀请邮件失败: %s (%s)", email, exc)
        return None


def _run_invite_login_step_a(email: str, password: str, mail_client, invite_link: str) -> bool:
    """跑 invite Step A:打开邀请链接 → 邮箱+密码+OTP → 加入 workspace。

    与 ``manager._run_invite_login_flow`` 的 Step A 等价,但**不**写 accounts.json。
    成功返回 True,失败返回 False(写 error 日志,不抛)。
    """
    try:
        from playwright.sync_api import sync_playwright

        from autoteam.config import get_playwright_launch_options
        from autoteam.invite import login_with_invite

        with sync_playwright() as p:
            browser = p.chromium.launch(**get_playwright_launch_options())
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            joined = login_with_invite(page, invite_link, email, mail_client, password)
            browser.close()
        return bool(joined)
    except Exception as exc:
        logger.error("[免费号] Step A 邀请链接登录异常: %s (%s)", email, exc)
        return False


def _safe_remove(chatgpt_api, email: str) -> str:
    """调 ``manager.remove_from_team`` 并捕获异常,返回状态字符串供日志使用。"""
    from autoteam.manager import remove_from_team

    try:
        result = remove_from_team(chatgpt_api, email, return_status=True)
        return str(result or "unknown")
    except Exception as exc:
        logger.error("[免费号] remove_from_team 异常: %s (%s)", email, exc)
        return "exception"


def _safe_delete_temp_email(mail_client, mail_account_id) -> None:
    """删除临时邮箱时吞掉异常,只写 warn 日志(主流程已经失败,清理失败不应再阻塞)。"""
    if mail_account_id is None:
        return
    try:
        mail_client.delete_account(mail_account_id)
    except Exception as exc:
        logger.warning("[免费号] 删除失败临时邮箱异常: %s", exc)


# ---------------------------------------------------------------------------
# 额度刷新
# ---------------------------------------------------------------------------


def check_free_quota(emails: list[str] | None = None, admin_id: str | None = None) -> dict[str, str]:
    """串行刷新免费号额度,把结果写回 ``last_quota`` / ``last_quota_at``。

    :param emails: 指定要刷新的邮箱列表;``None`` 表示全部 active/exhausted 免费号。
    :param admin_id: 目标 admin。
    :return: 邮箱 → 状态字符串(``ok / exhausted / auth_error / no_auth / not_found``)的映射。
    """
    from autoteam.codex_auth import (
        check_codex_quota,
        get_quota_exhausted_info,
        quota_result_quota_info,
        refresh_access_token,
    )

    records = load_free(admin_id)
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
        else:
            if rec.get("status") not in _QUOTA_REFRESHABLE_STATUSES:
                continue

        auth_file = rec.get("auth_file")
        if not auth_file:
            summary[email] = "no_auth"
            logger.info("[免费号] %s 无 auth_file,跳过额度刷新", email)
            continue

        auth_path = Path(auth_file)
        if not auth_path.exists():
            summary[email] = "no_auth"
            logger.warning("[免费号] %s auth_file 不存在: %s", email, auth_file)
            continue

        try:
            auth_data = json.loads(read_text(auth_path))
        except Exception as exc:
            summary[email] = "auth_error"
            logger.error("[免费号] %s 解析 auth_file 失败: %s", email, exc)
            continue

        access_token = auth_data.get("access_token")
        refresh_token_value = auth_data.get("refresh_token")
        if not access_token:
            summary[email] = "no_auth"
            continue

        status, info = check_codex_quota(access_token)

        # 401 自动刷新 token,与 manager._check_and_refresh 同一行为
        if status == "auth_error" and refresh_token_value:
            logger.info("[免费号] %s token 过期,尝试刷新...", email)
            new_tokens = refresh_access_token(refresh_token_value)
            if new_tokens:
                auth_data["access_token"] = new_tokens.get("access_token", access_token)
                auth_data["refresh_token"] = new_tokens.get("refresh_token", refresh_token_value)
                auth_data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                try:
                    write_text(auth_path, json.dumps(auth_data, indent=2))
                except Exception as exc:
                    logger.warning("[免费号] %s 写回 auth_file 失败: %s", email, exc)
                status, info = check_codex_quota(auth_data["access_token"])
            else:
                logger.error("[免费号] %s token 刷新失败", email)

        # 写回快照与状态
        quota_snapshot: dict[str, Any] | None = None
        new_status = rec.get("status")
        if status == "ok" and isinstance(info, dict):
            quota_snapshot = info
            new_status = FREE_STATUS_ACTIVE
        elif status == "exhausted":
            quota_snapshot = quota_result_quota_info(info) or get_quota_exhausted_info(
                info if isinstance(info, dict) else {}
            )
            new_status = FREE_STATUS_EXHAUSTED
        # auth_error / 其他保留原状态;只更新 last_quota_at 让 UI 看到尝试过

        update_free(
            email,
            admin_id=admin_id,
            last_quota=quota_snapshot,
            last_quota_at=time.time(),
            status=new_status,
        )
        summary[email] = status
        logger.info("[免费号] %s 额度刷新结果: %s", email, status)

    # 处理"指定 emails 但本地不存在"的情形,显式回报 not_found 给调用方
    if target_set is not None:
        present = {_normalized_email(r.get("email")) for r in records}
        for raw in emails or []:
            norm = _normalized_email(raw)
            if norm and norm not in present:
                summary[raw] = "not_found"

    return summary


# ---------------------------------------------------------------------------
# 删除(PR1 仅本地;远端清理留 PR3 完整级联)
# ---------------------------------------------------------------------------


def delete_free_account(
    email: str,
    *,
    admin_id: str | None = None,
    cleanup_remote: bool = False,
) -> dict[str, Any]:
    """删除一条免费号(PR1 范围:仅本地 auth_file + json 条目)。

    PRD F3 完整级联清理(sub2api 远端 + cloudmail 邮箱)依赖 PR2 的 sub2api 参数化,
    在 PR3 实现 ``/api/free/{email}`` 端点时一起接入。这里先把签名预留好,
    并返回与未来一致的 cleanup 摘要结构,避免 PR3 改函数签名造成回归。

    :param email: 目标邮箱(大小写不敏感)。
    :param cleanup_remote: 占位参数;PR1 内固定按 False 处理(传 True 也只会做本地清理,
        并在日志中提示功能尚未上线)。
    :return: cleanup 摘要 dict,字段固定,缺失能力的字段保持 False/空列表。
    """
    cleanup: dict[str, Any] = {
        "local_record": False,
        "local_auth_files": [],
        "sub2api_accounts": [],
        "cloudmail_deleted": False,
    }

    if cleanup_remote:
        # PR1 边界:不实现远端清理,但允许调用方传 True 不报错(PR3 接入后转为真实级联)
        logger.info("[免费号] cleanup_remote=True 暂不生效(PR3 级联清理未上线): %s", email)

    records = load_free(admin_id)
    rec = find_free(records, email)
    if rec is None:
        logger.info("[免费号] 删除时未找到本地记录: %s", email)
        return cleanup

    # ---- 删 auth_file
    auth_file = rec.get("auth_file")
    if auth_file:
        path = Path(auth_file)
        try:
            if path.exists() and path.is_file():
                path.unlink()
                cleanup["local_auth_files"].append(path.name)
                logger.info("[免费号] 已删除 auth_file: %s", path.name)
        except Exception as exc:
            # 单步失败不阻塞:写日志后继续删 JSON 条目
            logger.warning("[免费号] 删除 auth_file 失败: %s (%s)", auth_file, exc)

    # ---- 删 JSON 条目
    deleted = delete_free(email, admin_id=admin_id)
    if deleted is not None:
        cleanup["local_record"] = True
        logger.info("[免费号] 已删除本地记录: %s", email)

    return cleanup
