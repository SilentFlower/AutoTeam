"""Plus 号自动注册任务编排(PRD task 05-08-plus-oauth-sub2api / R2)。

把 ``autoteam.plus_register_bot.register_one_plus`` 库入口与现有
``plus_accounts.import_plus_account`` 衔接,封装为 Web UI 可轮询的后台 job:

* job 状态机:``pending → running → done | cancelled | failed``,中间通过
  ``step`` 字段细化(D7 步骤枚举);
* WhatsApp OTP 通过 ``feed_otp`` 端点喂入 ``queue.Queue``,与
  ``register_one_plus`` 内部的 ``otp_callback`` 双向通信;
* ``register_lock``(threading.Lock)叠加 api 模块的 ``_playwright_lock``,
  保证 GoPay 单实体账号 + Playwright 单浏览器双重串行;
* ``cancel_job`` 通过 ``call_soon_threadsafe(task.cancel)`` 中断 asyncio
  task,并给 OTP queue 塞 sentinel(None)兜底唤醒等待中的 OTP callback;
* 临时邮箱完全复用项目既有 ``mail_provider`` 抽象(CloudMail / Cloudflare
  Temp Email),库模式不再依赖专用临时邮箱客户端。

模块只维护内存 ``_jobs`` 字典,服务重启即丢——job 生命周期 < 1 小时,
重启时仍在跑的极小概率任务由运营手动 reauth 处置(失败号自动 fall back
到 ``status=auth_failed`` 的现有 reauth 流程,见 ``plus_accounts.reauth_plus_account``)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from autoteam.plus_register_bot import BotConfig, MailboxError, generate_email_prefix, register_one_plus

logger = logging.getLogger(__name__)

# ===================== Step 枚举(PRD D7)=====================
STEP_CREATING_EMAIL = "creating_email"
STEP_SIGNING_UP = "signing_up"
STEP_AWAITING_EMAIL_OTP = "awaiting_email_otp"
STEP_FILLING_ABOUT_YOU = "filling_about_you"
STEP_PAYING_GOPAY = "paying_gopay"
STEP_AWAITING_WHATSAPP_OTP = "awaiting_whatsapp_otp"
STEP_SETTING_PASSWORD = "setting_password"
STEP_CANCELLING_SUBSCRIPTION = "cancelling_subscription"
STEP_OAUTH = "oauth"
STEP_SYNCING_SUB2API = "syncing_sub2api"
STEP_DONE = "done"

# ===================== Job 状态枚举 =====================
JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_DONE = "done"
JOB_STATUS_CANCELLED = "cancelled"
JOB_STATUS_FAILED = "failed"

# ===================== OTP 等待超时(PRD D5)=====================
OTP_WAIT_TIMEOUT_SECONDS = 300  # 5 分钟,与 PRD D5 锁定值一致

# ===================== 内存状态 =====================
# job_id → job dict;受 _jobs_lock 保护
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()

# job_id → OTP 通道(maxsize=1,sentinel=None 表示 cancel/timeout)
_otp_queues: dict[str, queue.Queue[str | None]] = {}

# job_id → asyncio loop / task 引用,供 cancel_job 跨线程触发 task.cancel
_job_loops: dict[str, asyncio.AbstractEventLoop] = {}
_job_tasks: dict[str, asyncio.Task[Any]] = {}

# 整个 Plus 自动注册子系统的全局串行锁(PRD R2)。
# api.py 的 _playwright_lock 已是全局 Playwright 互斥,这里独立留一把锁
# 用于"等其他 Plus 注册任务"与"等其他 Playwright 任务"的语义区分,
# 同时防御 _playwright_lock 被未来重构后的细粒度替代。
_register_lock = threading.Lock()


# ===================== BotConfig 装载 =====================
# 各字段的"如何填"提示,缺项错误消息会逐项附上,前端原样展示给用户看
# (与 spec/backend/error-handling.md 的"告诉用户下一步"约定对齐)。
_ENV_FIELD_HINT = {
    "GOPAY_PHONE": "GoPay 手机号纯数字(不带 + 与国家码,例 13800138000)",
    "GOPAY_COUNTRY_CODE": "GoPay 国家码纯数字(例 86 或 62)",
    "GOPAY_PIN": "GoPay 6 位数字支付 PIN",
}


def load_bot_config_from_env() -> BotConfig:
    """从进程环境变量构造 ``BotConfig``,缺一项直接抛 ``ValueError``。

    GoPay 是单实体账号,自动注册配置走全局 ``.env``。临时邮箱配置不再由
    本函数读取——统一交给 ``mail_provider.get_mail_client()`` 根据
    ``MAIL_PROVIDER`` + 对应 provider 的 env 自己解析。

    :raises ValueError: 任意必填字段缺失或仍为占位符;消息含每项格式提示。
    :return: 已校验的 ``BotConfig`` 实例(``__post_init__`` 通过)。
    """
    gopay_phone = os.environ.get("GOPAY_PHONE", "").strip()
    gopay_country_code = os.environ.get("GOPAY_COUNTRY_CODE", "").strip()
    gopay_pin = os.environ.get("GOPAY_PIN", "").strip()

    missing: list[str] = []
    if not gopay_phone:
        missing.append("GOPAY_PHONE")
    if not gopay_country_code:
        missing.append("GOPAY_COUNTRY_CODE")
    if not gopay_pin:
        missing.append("GOPAY_PIN")
    if missing:
        details = ", ".join(f"{name}({_ENV_FIELD_HINT.get(name, '必填')})" for name in missing)
        raise ValueError("以下环境变量缺失,请在 .env 中填写后重启服务:" + details)

    return BotConfig(
        gopay_phone=gopay_phone,
        gopay_country_code=gopay_country_code,
        gopay_pin=gopay_pin,
    )


# ===================== Job 生命周期 =====================
def submit_auto_register_job(count: int, admin_id: str | None) -> str:
    """提交一个新的批量自动注册任务,返回 ``job_id``。

    :param count: 本次批量注册的目标数量,必须 >= 1。
    :param admin_id: 目标 admin;缺省由 ``import_plus_account`` 内部回退激活 admin。
    :raises ValueError: ``count`` 不合法,或 ``.env`` 配置缺失。
    :raises RuntimeError: 已有 Playwright 任务在跑(由 api.py 转 409)。
    :return: 12 位 hex 的 ``job_id``。
    """
    if count <= 0:
        raise ValueError("count 必须是 >= 1 的正整数")

    # 配置缺失抛 ValueError,api.py 转 400(给前端"请去 .env 填 X"的指引)
    config = load_bot_config_from_env()

    # 跨任务互斥探测(快速失败);真锁在 _run_job 的 thread 里再拿,避免主线程长锁
    from autoteam.api import _playwright_lock

    if not _playwright_lock.acquire(blocking=False):
        raise RuntimeError("有 Playwright 任务正在执行,请等待完成后再触发自动注册")
    _playwright_lock.release()

    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {
        "job_id": job_id,
        "status": JOB_STATUS_PENDING,
        "step": None,
        "current_index": 0,
        "total": count,
        "ok": 0,
        "errors": [],
        "summary": None,
        "otp_request_at": None,
        "started_at": None,
        "finished_at": None,
        "admin_id": admin_id,
        "cancel_requested": False,
    }
    with _jobs_lock:
        _jobs[job_id] = job
    _otp_queues[job_id] = queue.Queue(maxsize=1)

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, count, admin_id, config),
        name=f"plus-auto-register-{job_id}",
        daemon=True,
    )
    thread.start()

    logger.info("[Plus自注册] 创建 job %s (count=%d, admin=%s)", job_id, count, admin_id or "-")
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    """返回 ``job_id`` 对应的状态快照(只读 copy);不存在返回 ``None``。"""
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def feed_otp(job_id: str, otp: str) -> None:
    """喂 WhatsApp OTP 给等待中的 job。

    :raises KeyError: job 不存在。
    :raises RuntimeError: job 当前不在 ``awaiting_whatsapp_otp`` 步,或队列已满。
    """
    job = get_job(job_id)
    if job is None:
        raise KeyError(f"未知 job_id: {job_id}")
    if job["step"] != STEP_AWAITING_WHATSAPP_OTP:
        raise RuntimeError(f"任务 {job_id} 当前不在等待 WhatsApp OTP(step={job['step']}),无法喂入验证码")

    q = _otp_queues.get(job_id)
    if q is None:
        # job 已经走到清理阶段,OTP 通道已回收
        raise RuntimeError(f"任务 {job_id} 的 OTP 通道已关闭,可能已超时或被取消")

    try:
        q.put_nowait(otp)
    except queue.Full as exc:
        raise RuntimeError("已有 OTP 在排队等消费,请勿重复提交") from exc


def cancel_job(job_id: str) -> None:
    """请求取消 job;终态(done/cancelled/failed)直接返回。

    通过两路并行兜底唤醒:
    1. ``task.cancel()`` 跨线程让 asyncio 当前 await 点抛 ``CancelledError``;
    2. 给 OTP queue 塞 sentinel,确保正在等 OTP 的 ``otp_callback`` 立即解锁。

    :raises KeyError: job 不存在。
    """
    job = get_job(job_id)
    if job is None:
        raise KeyError(f"未知 job_id: {job_id}")
    if job["status"] in (JOB_STATUS_DONE, JOB_STATUS_CANCELLED, JOB_STATUS_FAILED):
        return

    with _jobs_lock:
        # 二次防御:取锁后再判一遍状态,避免与 _finalize_job 竞态把已结束 job 标 cancel
        current = _jobs.get(job_id)
        if current is None or current["status"] in (JOB_STATUS_DONE, JOB_STATUS_CANCELLED, JOB_STATUS_FAILED):
            return
        _jobs[job_id]["cancel_requested"] = True

    # 路径 A:塞 OTP sentinel,优先唤醒 awaiting_whatsapp_otp
    q = _otp_queues.get(job_id)
    if q is not None:
        try:
            q.put_nowait(None)
        except queue.Full:
            pass  # 已有 OTP 排队,cancel 信号让 task.cancel 兜底

    # 路径 B:跨线程 task.cancel(),让任意 await 点抛 CancelledError
    loop = _job_loops.get(job_id)
    task = _job_tasks.get(job_id)
    if loop is not None and task is not None and not task.done():
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError as exc:
            # loop 已关闭(极少数竞态),靠 sentinel 兜底
            logger.warning("[Plus自注册] task.cancel 跨线程提交失败 job=%s: %s", job_id, exc)

    logger.info("[Plus自注册] 收到取消请求 job=%s", job_id)


# ===================== 后台 worker =====================
def _run_job(
    job_id: str,
    count: int,
    admin_id: str | None,
    config: BotConfig,
) -> None:
    """后台 thread 入口:启动新的 asyncio loop 跑 ``_run_job_async``。

    锁顺序:
        ``_playwright_lock``(跨任务互斥) → ``_register_lock``(Plus 子系统专属串行)。
    """
    from autoteam.api import _playwright_lock

    _playwright_lock.acquire()
    try:
        _register_lock.acquire()
        try:
            with _jobs_lock:
                _jobs[job_id]["status"] = JOB_STATUS_RUNNING
                _jobs[job_id]["started_at"] = time.time()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _job_loops[job_id] = loop
            try:
                task = loop.create_task(_run_job_async(job_id, count, admin_id, config))
                _job_tasks[job_id] = task
                loop.run_until_complete(task)
            finally:
                _job_loops.pop(job_id, None)
                _job_tasks.pop(job_id, None)
                try:
                    loop.close()
                except Exception as exc:  # pragma: no cover
                    logger.warning("[Plus自注册] loop 关闭异常 job=%s: %s", job_id, exc)
        finally:
            _register_lock.release()
    finally:
        _playwright_lock.release()
        _otp_queues.pop(job_id, None)


async def _run_job_async(
    job_id: str,
    count: int,
    admin_id: str | None,
    config: BotConfig,
) -> None:
    """协程主体:批量跑 ``register_one_plus`` + ``import_plus_account``。

    每条独立 try,单条异常不阻塞下一条;cancel 在批次边界与 OTP 等待点都生效。
    """
    summary = {
        "total": count,
        "ok": 0,
        "auth_failed": 0,
        "payment_failed": 0,
        "cancelled": 0,
        "other_failed": 0,
    }

    try:
        for index in range(count):
            with _jobs_lock:
                if _jobs[job_id]["cancel_requested"]:
                    summary["cancelled"] += count - index
                    break
                _jobs[job_id]["current_index"] = index
                _jobs[job_id]["step"] = None
                _jobs[job_id]["otp_request_at"] = None

            otp_callback = _make_otp_callback(job_id)
            step_callback = _make_step_callback(job_id)
            create_mailbox, fetch_email_code, cleanup_mailbox = _build_mailbox_callbacks(config)

            logger.info("[Plus自注册] job=%s 开始第 %d/%d 个号", job_id, index + 1, count)

            try:
                result = await register_one_plus(
                    config=config,
                    otp_callback=otp_callback,
                    create_mailbox=create_mailbox,
                    fetch_email_code=fetch_email_code,
                    cleanup_mailbox=cleanup_mailbox,
                    step_callback=step_callback,
                    headless=False,
                )
            except asyncio.CancelledError:
                # cancel_job 跨线程触发的 task.cancel:登记 cancelled 后跳出循环,
                # 让 finally 走 _finalize_job;不向外 raise,避免 thread 顶层把
                # CancelledError 传给 loop.run_until_complete 触发 pytest 警告。
                _append_error(job_id, index, "", "cancelled", "任务被用户取消")
                summary["cancelled"] += count - index
                break
            except Exception as exc:
                logger.exception("[Plus自注册] register_one_plus 抛出未预期异常 job=%s", job_id)
                _append_error(job_id, index, "", "unexpected", f"register_one_plus 异常: {exc}")
                summary["other_failed"] += 1
                continue

            email = result.get("email") or ""
            error_type = result.get("error_type")

            if not result.get("ok"):
                if error_type == "cancelled":
                    summary["cancelled"] += 1
                elif error_type == "payment_failed":
                    summary["payment_failed"] += 1
                else:
                    summary["other_failed"] += 1
                _append_error(
                    job_id,
                    index,
                    email,
                    error_type or "unknown",
                    result.get("error_detail") or "",
                )
                continue

            password = result.get("password") or ""
            try:
                step_callback(STEP_OAUTH)
                import_result = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda e=email, p=password, a=admin_id: _safe_import_plus_account(e, p, a),
                )
            except asyncio.CancelledError:
                _append_error(job_id, index, email, "cancelled", "OAuth 阶段被用户取消")
                summary["cancelled"] += 1
                break
            except Exception as exc:
                logger.exception("[Plus自注册] import_plus_account 异常 job=%s email=%s", job_id, email)
                _append_error(job_id, index, email, "oauth_failed", f"import_plus_account 异常: {exc}")
                summary["auth_failed"] += 1
                continue

            if not import_result.get("ok"):
                _append_error(
                    job_id,
                    index,
                    email,
                    "oauth_failed",
                    import_result.get("error_detail") or "OAuth 失败,详见 plus_accounts.json",
                )
                summary["auth_failed"] += 1
                continue

            step_callback(STEP_SYNCING_SUB2API)
            step_callback(STEP_DONE)
            summary["ok"] += 1
            with _jobs_lock:
                _jobs[job_id]["ok"] += 1
            logger.info("[Plus自注册] job=%s 第 %d/%d 个号入池: %s", job_id, index + 1, count, email)
    finally:
        _finalize_job(job_id, summary)


def _build_mailbox_callbacks(config: BotConfig):
    """构造 mailbox callback 三件套,把同步 mail_provider client 包成 async。

    注意:mail_client 不是线程安全对象,每个号都新建一份并在当前 worker thread 内
    使用,避免跨 job 共享 session/token 状态。
    """
    from autoteam.mail_provider import get_mail_client

    mail_client = get_mail_client()
    mail_client.login()
    loop = asyncio.get_running_loop()

    async def create_mailbox() -> dict[str, Any]:
        prefix = generate_email_prefix(tag=config.email_prefix_tag)

        def _create():
            account_id, email = mail_client.create_temp_email(prefix=prefix)
            return {"account_id": account_id, "email": email}

        return await loop.run_in_executor(None, _create)

    async def fetch_email_code(mailbox: dict[str, Any], timeout: int) -> str | None:
        email = str(mailbox.get("email") or "")
        if not email:
            raise MailboxError("邮箱对象缺少 email 字段,无法等待验证码")

        def _fetch():
            email_data = mail_client.wait_for_email(email, timeout=timeout)
            if not email_data:
                return None
            return mail_client.extract_verification_code(email_data)

        return await loop.run_in_executor(None, _fetch)

    async def cleanup_mailbox(mailbox: dict[str, Any]) -> None:
        account_id = mailbox.get("account_id")
        if not account_id:
            return

        def _cleanup():
            mail_client.delete_account(account_id)

        await loop.run_in_executor(None, _cleanup)

    return create_mailbox, fetch_email_code, cleanup_mailbox


def _safe_import_plus_account(
    email: str,
    password: str,
    admin_id: str | None,
) -> dict[str, Any]:
    """``import_plus_account`` 的同步包装,异常转 ok=False(避免抛进 executor)。"""
    from autoteam.plus_accounts import import_plus_account

    try:
        return import_plus_account(email, password, admin_id)
    except Exception as exc:
        return {"ok": False, "error_detail": f"{type(exc).__name__}: {exc}"}


# ===================== 内部辅助 =====================
def _make_otp_callback(job_id: str) -> Callable[[], Awaitable[str | None]]:
    """构造与 job 绑定的 OTP callback:从 ``_otp_queues[job_id]`` 取值,
    超时或拿到 sentinel(``None``)时返回 ``None``。
    """

    async def otp_callback() -> str | None:
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["otp_request_at"] = time.time()

        q = _otp_queues.get(job_id)
        if q is None:
            return None

        loop = asyncio.get_running_loop()
        try:
            otp = await loop.run_in_executor(None, lambda: q.get(timeout=OTP_WAIT_TIMEOUT_SECONDS))
        except queue.Empty:
            logger.warning("[Plus自注册] WhatsApp OTP 等待 %ds 超时 job=%s", OTP_WAIT_TIMEOUT_SECONDS, job_id)
            return None

        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["otp_request_at"] = None
        return otp  # None = sentinel(cancel)

    return otp_callback


def _make_step_callback(job_id: str) -> Callable[[str], None]:
    """构造与 job 绑定的 step_callback:同步更新 ``_jobs[job_id]['step']``。"""

    def step_callback(step: str) -> None:
        with _jobs_lock:
            if job_id not in _jobs:
                return
            _jobs[job_id]["step"] = step
            if step != STEP_AWAITING_WHATSAPP_OTP:
                _jobs[job_id]["otp_request_at"] = None

    return step_callback


def _append_error(
    job_id: str,
    index: int,
    email: str,
    error_type: str,
    error_detail: str,
) -> None:
    """统一登记单条错误到 ``_jobs[job_id]['errors']``。"""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["errors"].append(
                {
                    "index": index,
                    "email": email,
                    "error_type": error_type,
                    "error_detail": error_detail,
                }
            )


def _finalize_job(job_id: str, summary: dict[str, int]) -> None:
    """job 退出时统一标终态,在 ``cancel_requested`` 与正常完成间分流。"""
    with _jobs_lock:
        if job_id not in _jobs:
            return
        if _jobs[job_id]["cancel_requested"]:
            _jobs[job_id]["status"] = JOB_STATUS_CANCELLED
        else:
            _jobs[job_id]["status"] = JOB_STATUS_DONE
        _jobs[job_id]["finished_at"] = time.time()
        _jobs[job_id]["summary"] = dict(summary)


# ===================== 测试用清理钩子 =====================
def _reset_for_tests() -> None:
    """仅供单元测试在 ``autouse`` fixture 里清理状态;生产代码勿调。"""
    with _jobs_lock:
        _jobs.clear()
    _otp_queues.clear()
    _job_loops.clear()
    _job_tasks.clear()
