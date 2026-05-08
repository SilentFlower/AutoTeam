"""注册机库化(BotConfig + register_one_plus)单元测试。

覆盖范围:
    - BotConfig 校验红线:占位符、PIN 位数、phone/cc 数字格式
    - generate_email_prefix 接受 tag 参数
    - register_one_plus 库入口的成功路径与各 error_type 分类
    - 库模式下不写 accounts.json(AccountManager 未被调用)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

# 注册机文件位于仓库根而非 ``src/autoteam`` 包内,需要把仓库根加进 sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import chatgpt_registration_bot_public as bot_module  # noqa: E402


# =====================================================================
# BotConfig 校验
# =====================================================================
def _valid_kwargs(**overrides: Any) -> dict:
    """构造一组合法的 BotConfig 关键字参数,便于按需 override。"""
    base = {
        "temp_email_api": "https://mail.example.com",
        "temp_email_login_base": "https://mail.example.com",
        "gopay_phone": "13800138000",
        "gopay_country_code": "86",
        "gopay_pin": "123456",
    }
    base.update(overrides)
    return base


def test_bot_config_valid_baseline():
    """合法配置应无异常构造,且字段保持原值。"""
    cfg = bot_module.BotConfig(**_valid_kwargs())
    assert cfg.gopay_phone == "13800138000"
    assert cfg.gopay_pin == "123456"
    assert cfg.subscription_currency == "IDR"  # 默认值


def test_bot_config_rejects_placeholder_temp_email_api():
    """temp_email_api 仍为 ``YOUR_xxx`` 占位符时必须 raise ValueError。"""
    with pytest.raises(ValueError, match="temp_email_api"):
        bot_module.BotConfig(**_valid_kwargs(temp_email_api="https://YOUR_TEMP_EMAIL_API_DOMAIN"))


def test_bot_config_rejects_placeholder_gopay_pin():
    """gopay_pin 仍为占位符时应被 placeholder 校验先拦截。"""
    with pytest.raises(ValueError, match="gopay_pin"):
        bot_module.BotConfig(**_valid_kwargs(gopay_pin="YOUR_6_DIGIT_PIN"))


def test_bot_config_rejects_short_gopay_pin():
    """gopay_pin 不是 6 位数字时必须 raise(GoPay 后台规定)。"""
    with pytest.raises(ValueError, match="6 位数字"):
        bot_module.BotConfig(**_valid_kwargs(gopay_pin="12345"))


def test_bot_config_rejects_non_digit_gopay_pin():
    """gopay_pin 含非数字字符必须 raise。"""
    with pytest.raises(ValueError, match="6 位数字"):
        bot_module.BotConfig(**_valid_kwargs(gopay_pin="12345a"))


def test_bot_config_rejects_non_digit_phone():
    """gopay_phone 必须是纯数字(不带 + 与国家码)。"""
    with pytest.raises(ValueError, match="gopay_phone"):
        bot_module.BotConfig(**_valid_kwargs(gopay_phone="+8613800138000"))


def test_bot_config_rejects_non_digit_country_code():
    """gopay_country_code 必须是纯数字。"""
    with pytest.raises(ValueError, match="gopay_country_code"):
        bot_module.BotConfig(**_valid_kwargs(gopay_country_code="CN"))


def test_bot_config_rejects_empty_field():
    """必填字段为空字符串视作未配置。"""
    with pytest.raises(ValueError, match="gopay_phone"):
        bot_module.BotConfig(**_valid_kwargs(gopay_phone=""))


# =====================================================================
# generate_email_prefix
# =====================================================================
def test_generate_email_prefix_uses_explicit_tag():
    """显式传 tag 时应优先使用,而非模块顶层 EMAIL_PREFIX_TAG。"""
    prefix = bot_module.generate_email_prefix(tag="custom")
    assert prefix.startswith("custom")
    # 形如 customYYYYMMDDx
    assert len(prefix) == len("custom") + 8 + 1


def test_generate_email_prefix_falls_back_to_module_default():
    """未传 tag 时回退到模块顶层 EMAIL_PREFIX_TAG(默认 'oai')。"""
    prefix = bot_module.generate_email_prefix()
    assert prefix.startswith(bot_module.EMAIL_PREFIX_TAG)


# =====================================================================
# register_one_plus —— mock 全套外部依赖
# =====================================================================
class _FakeTempEmailClient:
    """替身 TempEmailClient,返回固定 email/jwt,记录调用。"""

    instances: list = []

    def __init__(self, config=None):
        self.config = config
        self.created = False
        self.poll_called = False
        _FakeTempEmailClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def create_address(self, name: str = ""):
        self.created = True
        return {"address": "fake@mail.example.com", "jwt": "fake-jwt", "password": "fake-pw"}

    async def poll_for_emails(self, jwt, timeout=120, interval=5):
        self.poll_called = True
        # 返回一封含 6 位数字验证码的假邮件
        return [
            {
                "id": 1,
                "subject": "Your code is 654321",
                "text": "Your code is 654321",
                "html": "",
                "created_at": "2026-05-08T00:00:00Z",
            }
        ]


class _FakeChatGPTBot:
    """替身 ChatGPTBot,记录每个方法是否被调用。"""

    instances: list = []

    def __init__(self, headless=False, slow_mo=100, *, config=None):
        self.config = config
        self.headless = headless
        self.calls: list[str] = []
        self.payment_result = "success"  # 测试可在 instance 上覆写
        # 预留 hook,让测试可以注入 OTP callback 行为
        self.otp_callback_invocations = 0
        _FakeChatGPTBot.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def navigate_to_signup(self, email):
        self.calls.append("navigate_to_signup")

    async def wait_for_verification_page(self):
        self.calls.append("wait_for_verification_page")

    async def enter_verification_code(self, code):
        self.calls.append(f"enter_verification_code:{code}")

    async def fill_about_you(self, name=None, birthdate=None):
        self.calls.append("fill_about_you")

    async def wait_for_login_complete(self):
        self.calls.append("wait_for_login_complete")
        return True

    async def execute_gopay_payment(self):
        self.calls.append("execute_gopay_payment")
        return "https://stripe.example/checkout"

    async def handle_stripe_checkout(self, whatsapp_callback):
        self.calls.append("handle_stripe_checkout")
        # 模拟付款流程会触发一次 OTP 取值
        self.otp_callback_invocations += 1
        await whatsapp_callback()
        return self.payment_result

    async def add_password_login(self, password):
        self.calls.append(f"add_password_login:{password}")
        return True

    async def cancel_subscription(self):
        self.calls.append("cancel_subscription")
        return True


@pytest.fixture(autouse=True)
def _reset_fakes():
    """每个测试前清空替身的实例列表。"""
    _FakeTempEmailClient.instances.clear()
    _FakeChatGPTBot.instances.clear()
    yield


def _patch_bot_module(monkeypatch, *, payment_result: str = "success"):
    """把 bot_module 内的 TempEmailClient / ChatGPTBot 换成替身。"""
    monkeypatch.setattr(bot_module, "TempEmailClient", _FakeTempEmailClient)

    class _BotFactory(_FakeChatGPTBot):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.payment_result = payment_result

    monkeypatch.setattr(bot_module, "ChatGPTBot", _BotFactory)


def _run(coro):
    return asyncio.run(coro)


def test_register_one_plus_happy_path(monkeypatch):
    """成功路径:返回 ok=True、关键字段齐全、step 推进到 done。"""
    _patch_bot_module(monkeypatch)
    cfg = bot_module.BotConfig(**_valid_kwargs())

    steps: list[str] = []

    async def otp_cb():
        return "111111"

    result = _run(
        bot_module.register_one_plus(
            cfg,
            otp_cb,
            headless=True,
            step_callback=steps.append,
        )
    )

    assert result["ok"] is True
    assert result["email"] == "fake@mail.example.com"
    assert result["password"]  # 默认随机 16 位
    assert result["last_step"] == "done"
    assert result["error_type"] is None
    assert "creating_email" in steps and "done" in steps
    # 至少一次 awaiting_whatsapp_otp(由 _wrapped_otp_callback 触发)
    assert "awaiting_whatsapp_otp" in steps
    assert _FakeChatGPTBot.instances[0].otp_callback_invocations == 1


def test_register_one_plus_otp_timeout(monkeypatch):
    """OTP callback 返回 None → error_type=whatsapp_otp_timeout。"""
    _patch_bot_module(monkeypatch)
    cfg = bot_module.BotConfig(**_valid_kwargs())

    async def otp_cb():
        return None

    result = _run(bot_module.register_one_plus(cfg, otp_cb))
    assert result["ok"] is False
    assert result["error_type"] == "whatsapp_otp_timeout"
    assert result["last_step"] == "awaiting_whatsapp_otp"


def test_register_one_plus_payment_failed(monkeypatch):
    """handle_stripe_checkout 返回非 success → error_type=payment_failed。"""
    _patch_bot_module(monkeypatch, payment_result="bypass_failed")
    cfg = bot_module.BotConfig(**_valid_kwargs())

    async def otp_cb():
        return "222222"

    result = _run(bot_module.register_one_plus(cfg, otp_cb))
    assert result["ok"] is False
    assert result["error_type"] == "payment_failed"
    assert "bypass_failed" in (result["error_detail"] or "")


def test_register_one_plus_email_extract_failed(monkeypatch):
    """邮件已收但抽不到验证码 → error_type=email_otp_extract_failed。"""

    class _NoCodeTempEmailClient(_FakeTempEmailClient):
        async def poll_for_emails(self, jwt, timeout=120, interval=5):
            return [{"id": 1, "subject": "hello", "text": "no digits here", "html": "", "created_at": "x"}]

    monkeypatch.setattr(bot_module, "TempEmailClient", _NoCodeTempEmailClient)
    monkeypatch.setattr(bot_module, "ChatGPTBot", _FakeChatGPTBot)

    cfg = bot_module.BotConfig(**_valid_kwargs())

    async def otp_cb():
        return "333333"

    result = _run(bot_module.register_one_plus(cfg, otp_cb))
    assert result["ok"] is False
    assert result["error_type"] == "email_otp_extract_failed"


def test_register_one_plus_does_not_call_account_manager(monkeypatch):
    """库模式禁止调用 AccountManager.create_account / save_account / write_txt_dump。"""
    _patch_bot_module(monkeypatch)
    cfg = bot_module.BotConfig(**_valid_kwargs())

    called = {"count": 0}

    def _spy(*a, **kw):
        called["count"] += 1
        return None

    monkeypatch.setattr(bot_module.AccountManager, "create_account", classmethod(lambda cls, *a, **kw: _spy()))
    monkeypatch.setattr(bot_module.AccountManager, "save_account", classmethod(lambda cls, *a, **kw: _spy()))

    async def otp_cb():
        return "444444"

    _run(bot_module.register_one_plus(cfg, otp_cb))

    assert called["count"] == 0, "register_one_plus 不应调用 AccountManager 任何写入方法"


def test_register_one_plus_propagates_config_to_bot(monkeypatch):
    """ChatGPTBot 实例应拿到注入的 BotConfig 而非模块全局。"""
    _patch_bot_module(monkeypatch)
    cfg = bot_module.BotConfig(**_valid_kwargs(gopay_phone="62123456789", gopay_country_code="62"))

    async def otp_cb():
        return "555555"

    _run(bot_module.register_one_plus(cfg, otp_cb))

    assert _FakeChatGPTBot.instances, "ChatGPTBot 替身未被实例化"
    last = _FakeChatGPTBot.instances[-1]
    assert last.config is cfg
    assert last.config.gopay_phone == "62123456789"


# =====================================================================
# ChatGPTBot 配置传递(直接构造,不跑 Playwright)
# =====================================================================
def test_chatgpt_bot_falls_back_to_module_globals_when_no_config():
    """ChatGPTBot 不传 config 时应回退到模块顶层 GOPAY_* 常量。"""
    inst = bot_module.ChatGPTBot()
    assert inst._gopay_phone == bot_module.GOPAY_PHONE
    assert inst._gopay_pin == bot_module.GOPAY_PIN
    assert inst._sub_currency == bot_module.SUBSCRIPTION_CURRENCY


def test_chatgpt_bot_uses_injected_config():
    """ChatGPTBot 接受 config 时应优先使用 BotConfig 字段。"""
    cfg = bot_module.BotConfig(**_valid_kwargs(gopay_phone="62987654321", gopay_country_code="62"))
    inst = bot_module.ChatGPTBot(config=cfg)
    assert inst._gopay_phone == "62987654321"
    assert inst._gopay_country_code == "62"
    assert inst._sub_billing_country == "ID"  # 默认值未被 override
