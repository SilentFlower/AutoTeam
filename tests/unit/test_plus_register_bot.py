"""``src.autoteam.plus_register_bot`` 单元测试。

覆盖范围:
    - BotConfig 校验红线:占位符、PIN 位数、phone/cc 数字格式
    - generate_email_prefix 接受 tag 参数
    - register_one_plus 库入口的成功路径与各 error_type 分类
    - mailbox callback 注入(create / fetch_code / cleanup)三路径
    - ChatGPTBot 必须从 BotConfig 注入配置(不再回退模块全局常量)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from autoteam import plus_register_bot as bot_module


# =====================================================================
# BotConfig 校验
# =====================================================================
def _valid_kwargs(**overrides: Any) -> dict[str, Any]:
    """构造一组合法的 BotConfig 关键字参数,便于按需 override。"""
    base = {
        "gopay_phone": "13800138000",
        "gopay_country_code": "86",
        "gopay_pin": "123456",
    }
    base.update(overrides)
    return base


def test_bot_config_valid_baseline():
    cfg = bot_module.BotConfig(**_valid_kwargs())
    assert cfg.gopay_phone == "13800138000"
    assert cfg.gopay_pin == "123456"
    assert cfg.subscription_currency == "IDR"


def test_bot_config_rejects_placeholder_gopay_pin():
    with pytest.raises(ValueError, match="gopay_pin"):
        bot_module.BotConfig(**_valid_kwargs(gopay_pin="YOUR_6_DIGIT_PIN"))


def test_bot_config_rejects_short_gopay_pin():
    with pytest.raises(ValueError, match="6 位数字"):
        bot_module.BotConfig(**_valid_kwargs(gopay_pin="12345"))


def test_bot_config_rejects_non_digit_gopay_pin():
    with pytest.raises(ValueError, match="6 位数字"):
        bot_module.BotConfig(**_valid_kwargs(gopay_pin="12345a"))


def test_bot_config_rejects_non_digit_phone():
    with pytest.raises(ValueError, match="gopay_phone"):
        bot_module.BotConfig(**_valid_kwargs(gopay_phone="+8613800138000"))


def test_bot_config_rejects_non_digit_country_code():
    with pytest.raises(ValueError, match="gopay_country_code"):
        bot_module.BotConfig(**_valid_kwargs(gopay_country_code="CN"))


def test_bot_config_rejects_empty_field():
    with pytest.raises(ValueError, match="gopay_phone"):
        bot_module.BotConfig(**_valid_kwargs(gopay_phone=""))


# =====================================================================
# generate_email_prefix
# =====================================================================
def test_generate_email_prefix_uses_explicit_tag():
    prefix = bot_module.generate_email_prefix(tag="custom")
    assert prefix.startswith("custom")
    assert len(prefix) == len("custom") + 8 + 1


def test_generate_email_prefix_falls_back_to_module_default():
    prefix = bot_module.generate_email_prefix()
    assert prefix.startswith(bot_module.EMAIL_PREFIX_TAG)


# =====================================================================
# register_one_plus —— mock ChatGPTBot + mailbox callback
# =====================================================================
class _FakeChatGPTBot:
    """替身 ChatGPTBot,记录每个方法是否被调用。"""

    instances: list[_FakeChatGPTBot] = []

    def __init__(self, headless=False, slow_mo=100, *, config):
        self.config = config
        self.headless = headless
        self.calls: list[str] = []
        self.payment_result = "success"
        self.otp_callback_invocations = 0
        _FakeChatGPTBot.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def navigate_to_signup(self, email):
        self.calls.append(f"navigate_to_signup:{email}")

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
    _FakeChatGPTBot.instances.clear()
    yield
    _FakeChatGPTBot.instances.clear()


def _patch_bot_module(monkeypatch, *, payment_result: str = "success"):
    class _BotFactory(_FakeChatGPTBot):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.payment_result = payment_result

    monkeypatch.setattr(bot_module, "ChatGPTBot", _BotFactory)


def _run(coro):
    return asyncio.run(coro)


def test_register_one_plus_happy_path(monkeypatch):
    _patch_bot_module(monkeypatch)
    cfg = bot_module.BotConfig(**_valid_kwargs())

    steps: list[str] = []
    cleaned: list[dict[str, Any]] = []

    async def otp_cb():
        return "111111"

    async def create_mailbox():
        return {"account_id": "acc-1", "email": "fake@mail.example.com"}

    async def fetch_email_code(mailbox, timeout):
        assert mailbox["email"] == "fake@mail.example.com"
        assert timeout == 180
        return "654321"

    async def cleanup_mailbox(mailbox):
        cleaned.append(mailbox)

    result = _run(
        bot_module.register_one_plus(
            cfg,
            otp_cb,
            create_mailbox=create_mailbox,
            fetch_email_code=fetch_email_code,
            cleanup_mailbox=cleanup_mailbox,
            headless=True,
            step_callback=steps.append,
        )
    )

    assert result["ok"] is True
    assert result["email"] == "fake@mail.example.com"
    assert result["password"]
    assert result["last_step"] == "done"
    assert result["error_type"] is None
    assert "creating_email" in steps and "done" in steps
    assert "awaiting_whatsapp_otp" in steps
    assert _FakeChatGPTBot.instances[0].otp_callback_invocations == 1
    assert cleaned == [{"account_id": "acc-1", "email": "fake@mail.example.com"}]


def test_register_one_plus_otp_timeout(monkeypatch):
    _patch_bot_module(monkeypatch)
    cfg = bot_module.BotConfig(**_valid_kwargs())

    async def otp_cb():
        return None

    async def create_mailbox():
        return {"account_id": "acc-1", "email": "fake@mail.example.com"}

    async def fetch_email_code(mailbox, timeout):
        return "654321"

    result = _run(
        bot_module.register_one_plus(
            cfg,
            otp_cb,
            create_mailbox=create_mailbox,
            fetch_email_code=fetch_email_code,
        )
    )
    assert result["ok"] is False
    assert result["error_type"] == "whatsapp_otp_timeout"
    assert result["last_step"] == "awaiting_whatsapp_otp"


def test_register_one_plus_payment_failed(monkeypatch):
    _patch_bot_module(monkeypatch, payment_result="bypass_failed")
    cfg = bot_module.BotConfig(**_valid_kwargs())

    async def otp_cb():
        return "222222"

    async def create_mailbox():
        return {"account_id": "acc-1", "email": "fake@mail.example.com"}

    async def fetch_email_code(mailbox, timeout):
        return "654321"

    result = _run(
        bot_module.register_one_plus(
            cfg,
            otp_cb,
            create_mailbox=create_mailbox,
            fetch_email_code=fetch_email_code,
        )
    )
    assert result["ok"] is False
    assert result["error_type"] == "payment_failed"
    assert "bypass_failed" in (result["error_detail"] or "")


def test_register_one_plus_email_extract_failed(monkeypatch):
    _patch_bot_module(monkeypatch)
    cfg = bot_module.BotConfig(**_valid_kwargs())

    async def otp_cb():
        return "333333"

    async def create_mailbox():
        return {"account_id": "acc-1", "email": "fake@mail.example.com"}

    async def fetch_email_code(mailbox, timeout):
        return None

    result = _run(
        bot_module.register_one_plus(
            cfg,
            otp_cb,
            create_mailbox=create_mailbox,
            fetch_email_code=fetch_email_code,
        )
    )
    assert result["ok"] is False
    assert result["error_type"] == "email_otp_extract_failed"


def test_register_one_plus_mailbox_create_failed(monkeypatch):
    _patch_bot_module(monkeypatch)
    cfg = bot_module.BotConfig(**_valid_kwargs())

    async def otp_cb():
        return "444444"

    async def create_mailbox():
        raise bot_module.MailboxError("创建邮箱失败")

    async def fetch_email_code(mailbox, timeout):
        return "654321"

    result = _run(
        bot_module.register_one_plus(
            cfg,
            otp_cb,
            create_mailbox=create_mailbox,
            fetch_email_code=fetch_email_code,
        )
    )
    assert result["ok"] is False
    assert result["error_type"] == "email_create_failed"


def test_register_one_plus_always_cleans_mailbox(monkeypatch):
    _patch_bot_module(monkeypatch)
    cfg = bot_module.BotConfig(**_valid_kwargs())
    cleaned: list[str] = []

    async def otp_cb():
        return "555555"

    async def create_mailbox():
        return {"account_id": "acc-1", "email": "fake@mail.example.com"}

    async def fetch_email_code(mailbox, timeout):
        return None  # 走失败路径,也要 cleanup

    async def cleanup_mailbox(mailbox):
        cleaned.append(mailbox["account_id"])

    _run(
        bot_module.register_one_plus(
            cfg,
            otp_cb,
            create_mailbox=create_mailbox,
            fetch_email_code=fetch_email_code,
            cleanup_mailbox=cleanup_mailbox,
        )
    )
    assert cleaned == ["acc-1"]


def test_register_one_plus_propagates_config_to_bot(monkeypatch):
    _patch_bot_module(monkeypatch)
    cfg = bot_module.BotConfig(**_valid_kwargs(gopay_phone="62123456789", gopay_country_code="62"))

    async def otp_cb():
        return "555555"

    async def create_mailbox():
        return {"account_id": "acc-1", "email": "fake@mail.example.com"}

    async def fetch_email_code(mailbox, timeout):
        return "654321"

    _run(
        bot_module.register_one_plus(
            cfg,
            otp_cb,
            create_mailbox=create_mailbox,
            fetch_email_code=fetch_email_code,
        )
    )

    assert _FakeChatGPTBot.instances
    last = _FakeChatGPTBot.instances[-1]
    assert last.config is cfg
    assert last.config.gopay_phone == "62123456789"


# =====================================================================
# ChatGPTBot 配置传递(直接构造,不跑 Playwright)
# =====================================================================
def test_chatgpt_bot_uses_injected_config():
    cfg = bot_module.BotConfig(**_valid_kwargs(gopay_phone="62987654321", gopay_country_code="62"))
    inst = bot_module.ChatGPTBot(config=cfg)
    assert inst._gopay_phone == "62987654321"
    assert inst._gopay_country_code == "62"
    assert inst._sub_billing_country == "ID"


# =====================================================================
# ChatGPTBot 注册入口 UI 变体
# =====================================================================
class _FakeLocator:
    """模拟 Playwright async Locator,用于注册入口 UI 变体回归测试。"""

    def __init__(self, page, name: str, *, visible: bool):
        self.page = page
        self.name = name
        self._visible = visible
        self.first = self
        self.last = self

    async def is_visible(self, timeout=0):
        return self._visible

    async def click(self, timeout=0, force=False):
        self.page.events.append(("click", self.name))
        if self.name == "signup":
            self.page.email_option_visible = True
        elif self.name == "email-option":
            self.page.email_input_visible = True
        elif self.name == "continue":
            self.page.continue_clicked = True

    async def press(self, ch, delay=0):
        self.page.events.append(("press", ch))


class _FakeSignupPage:
    """模拟 ChatGPT 新 UI:注册后先出现邮箱方式按钮,再出现邮箱输入框。"""

    url = "https://chatgpt.com/"

    def __init__(self):
        self.email_option_visible = False
        self.email_input_visible = False
        self.continue_clicked = False
        self.events: list[tuple[str, str]] = []

    async def goto(self, url, wait_until=None):
        self.events.append(("goto", url))

    def get_by_test_id(self, test_id):
        return _FakeLocator(self, "signup", visible=test_id == "signup-button")

    def get_by_role(self, role, name=None):
        if role == "button" and name and name.search("使用电子邮箱继续"):
            return _FakeLocator(self, "email-option", visible=self.email_option_visible)
        if role == "button" and name and name.search("Continue"):
            return _FakeLocator(self, "continue", visible=self.email_input_visible)
        return _FakeLocator(self, f"{role}-missing", visible=False)

    def get_by_text(self, pattern):
        return _FakeLocator(self, "text-missing", visible=False)

    def get_by_placeholder(self, pattern):
        return _FakeLocator(self, "placeholder-email", visible=self.email_input_visible)

    def locator(self, selector):
        if selector in ('input[type="email"]', 'input[name="email"]', 'input[autocomplete="email"]'):
            return _FakeLocator(self, f"email:{selector}", visible=self.email_input_visible)
        return _FakeLocator(self, f"missing:{selector}", visible=False)

    async def wait_for_url(self, pattern, timeout=0):
        self.events.append(("wait_for_url", str(pattern)))

    async def evaluate(self, script):
        return ""


def test_navigate_to_signup_clicks_email_auth_option(monkeypatch):
    async def _noop_screenshot(page, name):
        page.events.append(("screenshot", name))

    async def _noop_delay(*args, **kwargs):
        return None

    async def _record_typing(self, locator, text):
        self.page.events.append(("type", text))

    monkeypatch.setattr(bot_module, "screenshot", _noop_screenshot)
    monkeypatch.setattr(bot_module.ChatGPTBot, "_random_delay", _noop_delay)
    monkeypatch.setattr(bot_module.ChatGPTBot, "_type_human_like", _record_typing)

    cfg = bot_module.BotConfig(**_valid_kwargs())
    bot = bot_module.ChatGPTBot(config=cfg)
    bot.page = _FakeSignupPage()

    result = _run(bot.navigate_to_signup("new-ui@example.com"))

    assert result is True
    assert ("click", "signup") in bot.page.events
    assert ("click", "email-option") in bot.page.events
    assert ("type", "new-ui@example.com") in bot.page.events
    assert bot.page.continue_clicked is True
