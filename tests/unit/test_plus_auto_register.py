"""``src.autoteam.plus_auto_register`` 单元测试。

覆盖:
    - ``load_bot_config_from_env`` 缺项抛 ``ValueError``
    - ``submit_auto_register_job`` 参数 / 配置 / 锁竞争三类分支
    - happy path / register 失败 / import 失败 / OTP timeout / cancel 等场景
    - ``feed_otp`` 与 ``cancel_job`` 的状态机与异常分支
    - 串行锁:同时启两个 job,第二个被 _playwright_lock 探测拦截
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from autoteam import plus_auto_register
from autoteam.plus_register_bot import BotConfig

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_state():
    """每条用例前后清理 in-memory job 状态。"""
    plus_auto_register._reset_for_tests()
    yield
    plus_auto_register._reset_for_tests()


@pytest.fixture
def fake_config(monkeypatch):
    """让 ``load_bot_config_from_env`` 直接返回合法 ``BotConfig``。

    同时把 ``mail_provider.get_mail_client`` 替成无网络副作用的默认替身,
    防止 `_run_job_async` 在大多数测试里提前真实登录 CloudMail。
    """
    cfg = BotConfig(
        gopay_phone="13800138000",
        gopay_country_code="86",
        gopay_pin="123456",
    )

    class _NoopMailClient:
        def login(self):
            return None

        def create_temp_email(self, prefix=None):
            return "acc-noop", "noop@example.com"

        def wait_for_email(self, to_email, timeout=None):
            return {"subject": "Your code is 654321", "text": "654321"}

        def extract_verification_code(self, email_data):
            return "654321"

        def delete_account(self, account_id):
            return None

    monkeypatch.setattr(plus_auto_register, "load_bot_config_from_env", lambda: cfg)
    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda provider=None: _NoopMailClient())
    return cfg


@pytest.fixture
def fake_import(monkeypatch):
    """默认 ``import_plus_account`` 返回 ok=True;测试可改写 ``calls.result``。"""

    class _Box:
        result: dict[str, Any] = {"ok": True, "email": "fake", "status": "active"}
        calls: list[tuple[str, str, str | None]] = []

    def _fake(email, password, admin_id):
        _Box.calls.append((email, password, admin_id))
        return _Box.result

    monkeypatch.setattr(plus_auto_register, "_safe_import_plus_account", _fake)
    return _Box


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.02):
    """polling 等条件满足,超时抛 AssertionError。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _wait_status(job_id: str, statuses: set[str], timeout: float = 5.0):
    _wait_until(
        lambda: (plus_auto_register.get_job(job_id) or {}).get("status") in statuses,
        timeout=timeout,
    )
    return plus_auto_register.get_job(job_id)


def _wait_step(job_id: str, target_step: str, timeout: float = 5.0):
    _wait_until(
        lambda: (plus_auto_register.get_job(job_id) or {}).get("step") == target_step,
        timeout=timeout,
    )
    return plus_auto_register.get_job(job_id)


# =============================================================================
# load_bot_config_from_env
# =============================================================================
def test_load_bot_config_missing_env_raises(monkeypatch):
    """3 项 GoPay 必填环境变量任意缺失都应抛 ``ValueError``。"""
    monkeypatch.delenv("GOPAY_PHONE", raising=False)
    monkeypatch.delenv("GOPAY_COUNTRY_CODE", raising=False)
    monkeypatch.delenv("GOPAY_PIN", raising=False)

    with pytest.raises(ValueError, match="GOPAY_PHONE"):
        plus_auto_register.load_bot_config_from_env()


def test_load_bot_config_partial_missing(monkeypatch):
    """只缺一项也要在异常消息里命名出来。"""
    monkeypatch.setenv("GOPAY_PHONE", "13800138000")
    monkeypatch.setenv("GOPAY_COUNTRY_CODE", "86")
    monkeypatch.delenv("GOPAY_PIN", raising=False)

    with pytest.raises(ValueError, match="GOPAY_PIN"):
        plus_auto_register.load_bot_config_from_env()


def test_load_bot_config_returns_botconfig(monkeypatch):
    """全部齐备时返回合法 BotConfig 实例。"""
    monkeypatch.setenv("GOPAY_PHONE", "13800138000")
    monkeypatch.setenv("GOPAY_COUNTRY_CODE", "86")
    monkeypatch.setenv("GOPAY_PIN", "123456")

    cfg = plus_auto_register.load_bot_config_from_env()
    assert isinstance(cfg, BotConfig)
    assert cfg.gopay_phone == "13800138000"
    assert cfg.gopay_pin == "123456"


def test_load_bot_config_error_message_includes_format_hint(monkeypatch):
    """缺项异常消息应附每个字段的格式提示(R3.5 要求"具体到 6 位数字"等)。"""
    monkeypatch.setenv("GOPAY_PHONE", "13800138000")
    monkeypatch.delenv("GOPAY_COUNTRY_CODE", raising=False)
    monkeypatch.delenv("GOPAY_PIN", raising=False)

    with pytest.raises(ValueError) as excinfo:
        plus_auto_register.load_bot_config_from_env()
    msg = str(excinfo.value)
    assert "GOPAY_PIN" in msg
    assert "6 位" in msg
    assert "GOPAY_COUNTRY_CODE" in msg
    assert "国家码" in msg


# =============================================================================
# submit_auto_register_job 参数与锁
# =============================================================================
def test_submit_rejects_non_positive_count(fake_config):
    with pytest.raises(ValueError, match="count"):
        plus_auto_register.submit_auto_register_job(0, None)
    with pytest.raises(ValueError, match="count"):
        plus_auto_register.submit_auto_register_job(-1, None)


def test_submit_propagates_config_error(monkeypatch):
    """配置缺失时 ``submit`` 应直接把 ``load_bot_config_from_env`` 的 ValueError 透传。"""

    def _raise():
        raise ValueError("以下环境变量缺失,请在 .env 中填写后重启服务:GOPAY_PIN")

    monkeypatch.setattr(plus_auto_register, "load_bot_config_from_env", _raise)
    with pytest.raises(ValueError, match="GOPAY_PIN"):
        plus_auto_register.submit_auto_register_job(1, None)


def test_submit_runtime_error_when_playwright_lock_busy(monkeypatch, fake_config):
    """已有 Playwright 任务在跑时,``submit`` 应抛 RuntimeError(api 转 409)。"""

    class _BusyLock:
        def acquire(self, blocking=False):
            return False  # 永远拿不到

        def release(self):
            pass

    monkeypatch.setattr("autoteam.api._playwright_lock", _BusyLock())
    with pytest.raises(RuntimeError, match="任务正在执行"):
        plus_auto_register.submit_auto_register_job(1, None)


# =============================================================================
# happy / failure / OTP timeout 场景
# =============================================================================


def _make_register_stub(
    *,
    use_otp: bool = True,
    result: dict[str, Any] | None = None,
):
    """生成假的 ``register_one_plus`` 协程。

    :param use_otp: 是否在 register 内部触发 OTP callback(模拟 D7 的
        ``awaiting_whatsapp_otp`` 节点)。
    :param result: 最终返回值;缺省 ok=True。
    """
    default = {
        "ok": True,
        "email": "stub@example.com",
        "password": "p@ssw0rd",
        "last_step": "done",
        "error_type": None,
        "error_detail": None,
    }
    final = result if result is not None else default

    async def _stub(*, config, otp_callback, step_callback=None, **kwargs):
        if step_callback:
            step_callback(plus_auto_register.STEP_CREATING_EMAIL)
            step_callback(plus_auto_register.STEP_SIGNING_UP)
        if use_otp:
            if step_callback:
                step_callback(plus_auto_register.STEP_AWAITING_WHATSAPP_OTP)
            otp = await otp_callback()
            if not otp:
                return {
                    "ok": False,
                    "email": default["email"],
                    "password": default["password"],
                    "last_step": "awaiting_whatsapp_otp",
                    "error_type": "whatsapp_otp_timeout",
                    "error_detail": "OTP 未在期望时间内返回",
                }
        if step_callback:
            step_callback(plus_auto_register.STEP_DONE)
        return final

    return _stub


def test_happy_path_single(monkeypatch, fake_config, fake_import):
    """单条:OTP 喂入 → register ok → import ok → status=done, ok=1。"""
    monkeypatch.setattr(plus_auto_register, "register_one_plus", _make_register_stub())

    job_id = plus_auto_register.submit_auto_register_job(1, None)

    # 等到 awaiting_otp 再喂
    _wait_step(job_id, plus_auto_register.STEP_AWAITING_WHATSAPP_OTP)
    plus_auto_register.feed_otp(job_id, "654321")

    job = _wait_status(job_id, {plus_auto_register.JOB_STATUS_DONE})
    assert job["ok"] == 1
    assert job["errors"] == []
    assert job["summary"] == {
        "total": 1,
        "ok": 1,
        "auth_failed": 0,
        "payment_failed": 0,
        "cancelled": 0,
        "other_failed": 0,
    }
    # import_plus_account 实际被调
    assert fake_import.calls == [("stub@example.com", "p@ssw0rd", None)]


def test_register_receives_mailbox_callbacks(monkeypatch, fake_config, fake_import):
    """_run_job_async 必须把 create/fetch/cleanup 三个 mailbox callback 注给 register_one_plus。"""
    seen: dict[str, Any] = {}

    class _FakeMailClient:
        def login(self):
            seen["login_called"] = True

        def create_temp_email(self, prefix=None):
            seen["prefix"] = prefix
            return "acc-1", "mailbox@example.com"

        def wait_for_email(self, to_email, timeout=None):
            seen["wait_for_email"] = (to_email, timeout)
            return {"subject": "Your code is 654321", "text": "654321"}

        def extract_verification_code(self, email_data):
            seen["extract_verification_code"] = email_data
            return "654321"

        def delete_account(self, account_id):
            seen["delete_account"] = account_id

    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda provider=None: _FakeMailClient())

    async def _stub(
        *, config, otp_callback, create_mailbox, fetch_email_code, cleanup_mailbox, step_callback=None, **kwargs
    ):
        mailbox = await create_mailbox()
        seen["mailbox"] = mailbox
        code = await fetch_email_code(mailbox, 180)
        seen["code"] = code
        await cleanup_mailbox(mailbox)
        return {
            "ok": True,
            "email": mailbox["email"],
            "password": "p@ssw0rd",
            "last_step": "done",
            "error_type": None,
            "error_detail": None,
        }

    monkeypatch.setattr(plus_auto_register, "register_one_plus", _stub)

    job_id = plus_auto_register.submit_auto_register_job(1, None)
    _wait_status(job_id, {plus_auto_register.JOB_STATUS_DONE})

    assert seen["login_called"] is True
    assert seen["mailbox"] == {"account_id": "acc-1", "email": "mailbox@example.com"}
    assert seen["wait_for_email"] == ("mailbox@example.com", 180)
    assert seen["code"] == "654321"
    assert seen["delete_account"] == "acc-1"


def test_payment_failed_recorded(monkeypatch, fake_config, fake_import):
    """register 返回 payment_failed:summary 计数 + errors 登记;不调 import。"""
    monkeypatch.setattr(
        plus_auto_register,
        "register_one_plus",
        _make_register_stub(
            use_otp=False,
            result={
                "ok": False,
                "email": "fail@example.com",
                "password": "x",
                "last_step": "paying_gopay",
                "error_type": "payment_failed",
                "error_detail": "Stripe 异常",
            },
        ),
    )

    job_id = plus_auto_register.submit_auto_register_job(1, "admin-x")
    job = _wait_status(job_id, {plus_auto_register.JOB_STATUS_DONE})
    assert job["ok"] == 0
    assert job["summary"]["payment_failed"] == 1
    assert len(job["errors"]) == 1
    err = job["errors"][0]
    assert err["error_type"] == "payment_failed"
    assert err["email"] == "fail@example.com"
    # 失败号不应触发 import_plus_account
    assert fake_import.calls == []


def test_otp_timeout_path(monkeypatch, fake_config, fake_import):
    """otp_callback 真等到超时:OTP_WAIT_TIMEOUT_SECONDS 被改小后,
    没人喂 OTP → register 返回 whatsapp_otp_timeout → errors 登记。"""
    monkeypatch.setattr(plus_auto_register, "OTP_WAIT_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(plus_auto_register, "register_one_plus", _make_register_stub(use_otp=True))

    job_id = plus_auto_register.submit_auto_register_job(1, None)
    job = _wait_status(job_id, {plus_auto_register.JOB_STATUS_DONE}, timeout=5.0)
    assert job["ok"] == 0
    assert job["summary"]["other_failed"] == 1  # whatsapp_otp_timeout 归入 other_failed
    assert len(job["errors"]) == 1
    assert job["errors"][0]["error_type"] == "whatsapp_otp_timeout"
    assert fake_import.calls == []


def test_import_failure_marks_oauth_failed(monkeypatch, fake_config, fake_import):
    """register 成功但 import_plus_account 返回 ok=False → oauth_failed。"""
    monkeypatch.setattr(plus_auto_register, "register_one_plus", _make_register_stub(use_otp=False))
    fake_import.result = {"ok": False, "error_detail": "Codex OAuth 拒绝登录"}

    job_id = plus_auto_register.submit_auto_register_job(1, None)
    job = _wait_status(job_id, {plus_auto_register.JOB_STATUS_DONE})
    assert job["ok"] == 0
    assert job["summary"]["auth_failed"] == 1
    assert job["errors"][0]["error_type"] == "oauth_failed"
    assert "Codex OAuth 拒绝登录" in job["errors"][0]["error_detail"]


def test_batch_partial_failure(monkeypatch, fake_config, fake_import):
    """count=3:第二个失败不影响第一与第三的入池。"""
    seq = iter(
        [
            {  # 第 1 个 ok
                "ok": True,
                "email": "a@x",
                "password": "1",
                "last_step": "done",
                "error_type": None,
                "error_detail": None,
            },
            {  # 第 2 个 payment_failed
                "ok": False,
                "email": "b@x",
                "password": "2",
                "last_step": "paying_gopay",
                "error_type": "payment_failed",
                "error_detail": "Stripe down",
            },
            {  # 第 3 个 ok
                "ok": True,
                "email": "c@x",
                "password": "3",
                "last_step": "done",
                "error_type": None,
                "error_detail": None,
            },
        ]
    )

    async def _stub(*, config, otp_callback, step_callback=None, **kwargs):
        if step_callback:
            step_callback(plus_auto_register.STEP_DONE)
        return next(seq)

    monkeypatch.setattr(plus_auto_register, "register_one_plus", _stub)

    job_id = plus_auto_register.submit_auto_register_job(3, None)
    job = _wait_status(job_id, {plus_auto_register.JOB_STATUS_DONE}, timeout=10.0)
    assert job["ok"] == 2
    assert job["summary"] == {
        "total": 3,
        "ok": 2,
        "auth_failed": 0,
        "payment_failed": 1,
        "cancelled": 0,
        "other_failed": 0,
    }
    # 1 与 3 都进入 import
    assert [c[0] for c in fake_import.calls] == ["a@x", "c@x"]


# =============================================================================
# feed_otp 异常分支
# =============================================================================
def test_feed_otp_unknown_job_raises_keyerror():
    with pytest.raises(KeyError):
        plus_auto_register.feed_otp("not-exists", "123456")


def test_feed_otp_when_not_awaiting_raises_runtime(monkeypatch, fake_config, fake_import):
    """job 不在 awaiting_whatsapp_otp 步时喂 OTP 应抛 RuntimeError。"""
    # register stub 不进入 OTP 步,直接走完
    monkeypatch.setattr(plus_auto_register, "register_one_plus", _make_register_stub(use_otp=False))
    job_id = plus_auto_register.submit_auto_register_job(1, None)
    _wait_status(job_id, {plus_auto_register.JOB_STATUS_DONE})

    with pytest.raises(RuntimeError, match="不在等待 WhatsApp OTP"):
        plus_auto_register.feed_otp(job_id, "111111")


# =============================================================================
# cancel_job
# =============================================================================
def test_cancel_during_otp_wait(monkeypatch, fake_config, fake_import):
    """awaiting_whatsapp_otp 时 cancel:OTP queue 收到 sentinel,
    register 返回 whatsapp_otp_timeout,job 标 cancelled(因 cancel_requested=True)。"""
    monkeypatch.setattr(plus_auto_register, "register_one_plus", _make_register_stub(use_otp=True))

    job_id = plus_auto_register.submit_auto_register_job(1, None)
    _wait_step(job_id, plus_auto_register.STEP_AWAITING_WHATSAPP_OTP)
    plus_auto_register.cancel_job(job_id)

    job = _wait_status(job_id, {plus_auto_register.JOB_STATUS_CANCELLED}, timeout=5.0)
    assert job["status"] == plus_auto_register.JOB_STATUS_CANCELLED
    # cancel 后未走 import
    assert fake_import.calls == []


def test_cancel_unknown_job_raises_keyerror():
    with pytest.raises(KeyError):
        plus_auto_register.cancel_job("not-exists")


def test_cancel_terminal_job_is_noop(monkeypatch, fake_config, fake_import):
    """已完成的 job 再 cancel 应当 no-op,不抛异常,状态不退化。"""
    monkeypatch.setattr(plus_auto_register, "register_one_plus", _make_register_stub(use_otp=False))
    job_id = plus_auto_register.submit_auto_register_job(1, None)
    _wait_status(job_id, {plus_auto_register.JOB_STATUS_DONE})

    plus_auto_register.cancel_job(job_id)  # 应当不抛
    job_after = plus_auto_register.get_job(job_id)
    assert job_after["status"] == plus_auto_register.JOB_STATUS_DONE


# =============================================================================
# 串行锁:第二个 submit 在第一个未结束时被拒
# =============================================================================
def test_second_submit_rejected_while_first_running(monkeypatch, fake_config, fake_import):
    """第一个 job 持锁未释放,第二个 submit 拿不到 _playwright_lock 抛 RuntimeError。"""
    started = threading.Event()
    proceed = threading.Event()

    async def _slow_stub(*, config, otp_callback, step_callback=None, **kwargs):
        started.set()
        # 阻塞协程,模拟"长时间持锁"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, proceed.wait)
        return {
            "ok": True,
            "email": "first@x",
            "password": "1",
            "last_step": "done",
            "error_type": None,
            "error_detail": None,
        }

    monkeypatch.setattr(plus_auto_register, "register_one_plus", _slow_stub)

    first_id = plus_auto_register.submit_auto_register_job(1, None)
    assert started.wait(timeout=5.0), "第一个 job 没启动"

    # 第二个 submit 在第一个仍持锁时应被拒
    with pytest.raises(RuntimeError, match="任务正在执行"):
        plus_auto_register.submit_auto_register_job(1, None)

    # 放第一个跑完,清理状态
    proceed.set()
    _wait_status(first_id, {plus_auto_register.JOB_STATUS_DONE})


# =============================================================================
# get_job 不存在
# =============================================================================
def test_get_job_returns_none_for_unknown():
    assert plus_auto_register.get_job("not-exists") is None
