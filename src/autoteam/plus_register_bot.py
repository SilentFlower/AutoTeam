"""ChatGPT Plus 自动注册机器人(浏览器自动化 + 库入口)。

库模式入口:``register_one_plus(config, otp_callback, *, create_mailbox,
fetch_email_code, cleanup_mailbox, ...)``。临时邮箱通过注入的 callback
提供,本模块对邮箱实现完全无知 —— 调用方(``plus_auto_register``)负责
wrap ``autoteam.mail_provider`` 的同步客户端。

依赖:playwright(已在 pyproject 依赖)。

注:本模块原是根目录独立 CLI ``chatgpt_registration_bot_public.py``,
05-08 任务 plus-bot-mail-provider-httpx 整体迁入项目,删除独立 CLI
专属代码(TempEmailClient / VerificationCodeExtractor / AccountManager /
WhatsAppOTPHandler / main / aiohttp 依赖)。GoPay 是单实体账号,并发
会撞同一账号 OTP/PIN,因此调用方必须保证全局串行(见 plus_auto_register)。
"""

import asyncio
import logging
import os
import random
import re
import secrets
import string
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

# ===================== 日志 =====================
logger = logging.getLogger(__name__)

# 全局调试标志(由调用方按需 set;不再支持 --debug CLI 参数)
DEBUG_MODE = False

SCREENSHOTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "screenshots"))
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

# 静态默认值(原 CLI USER CONFIG 区,迁入项目后保留为模块常量)。
# DEFAULT_NAME 是 ChatGPTBot 内部 billing 字段 fallback;register_one_plus
# 库入口默认调 ``generate_real_name()`` 生成真名,不走 fallback。
# EMAIL_PREFIX_TAG 是 ``generate_email_prefix()`` 在未传 tag 时的默认前缀。
DEFAULT_NAME = "John Doe"
EMAIL_PREFIX_TAG = "oai"


# ===================== 库化配置(BotConfig)=====================
# GoPay 是单实体账号(单手机号 + 单 PIN),并发会撞同一账号 OTP/PIN,
# 因此调用方必须保证全局串行,详见 plus_auto_register 模块。


@dataclass(frozen=True)
class BotConfig:
    """注册机运行所需的全部可配置项,集中校验并禁止后续修改(frozen)。

    :param gopay_phone: GoPay 注册手机号,纯数字,不带 + 与国家码,
        例 ``"13800138000"``。
    :param gopay_country_code: GoPay 手机国家码,纯数字字符串,
        例 ``"86"``(中国)、``"62"``(印尼)。
    :param gopay_pin: GoPay 6 位支付 PIN。
    :param email_prefix_tag: 临时邮箱前缀的固定 tag,默认 ``"oai"``,
        最终生成形如 ``oai20260508a``。
    :param subscription_plan_name: ChatGPT 订阅计划名称,通常无需改动。
    :param subscription_billing_country: 订阅账单国家代码,默认印尼 ``"ID"``。
    :param subscription_currency: 订阅币种,默认 ``"IDR"``。
    :param subscription_promo_campaign_id: 1 个月免费试用活动 ID,
        如活动失效需要抓包替换。
    :param subscription_cancel_url: 订阅取消跳转 URL,通常无需改动。
    """

    gopay_phone: str
    gopay_country_code: str
    gopay_pin: str
    email_prefix_tag: str = "oai"
    subscription_plan_name: str = "chatgptplusplan"
    subscription_billing_country: str = "ID"
    subscription_currency: str = "IDR"
    subscription_promo_campaign_id: str = "plus-1-month-free"
    subscription_cancel_url: str = "https://chatgpt.com/#pricing"

    def __post_init__(self) -> None:
        """构造时校验所有必填字段,禁止 ``YOUR_xxx`` 占位符与格式错误。"""
        required = {
            "gopay_phone": self.gopay_phone,
            "gopay_country_code": self.gopay_country_code,
            "gopay_pin": self.gopay_pin,
        }
        missing: list[str] = []
        for name, value in required.items():
            if not value or "YOUR_" in str(value).upper():
                missing.append(name)
        if missing:
            raise ValueError("BotConfig 字段未配置或仍为占位符: " + ", ".join(missing))

        if not self.gopay_phone.isdigit():
            raise ValueError("BotConfig.gopay_phone 必须是纯数字字符串(不带 + 与国家码)")
        if not self.gopay_country_code.isdigit():
            raise ValueError("BotConfig.gopay_country_code 必须是纯数字字符串")
        if len(self.gopay_pin) != 6 or not self.gopay_pin.isdigit():
            raise ValueError("BotConfig.gopay_pin 必须是 6 位数字字符串")


# ===================== 工具函数 =====================
def generate_strong_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_birthdate() -> str:
    """生成随机出生日期（18-35岁）"""
    import datetime as dt

    today = dt.date.today()
    years_ago = random.randint(18, 35)
    days_ago = random.randint(0, 365)
    bd = today - dt.timedelta(days=365 * years_ago + days_ago)
    return bd.strftime("%Y-%m-%d")


# 真人姓名库
REAL_FIRST_NAMES = [
    "James",
    "John",
    "Robert",
    "Michael",
    "William",
    "David",
    "Richard",
    "Joseph",
    "Thomas",
    "Charles",
    "Mary",
    "Patricia",
    "Jennifer",
    "Linda",
    "Barbara",
    "Elizabeth",
    "Susan",
    "Jessica",
    "Sarah",
    "Karen",
    "Lisa",
    "Nancy",
    "Betty",
    "Margaret",
    "Sandra",
    "Ashley",
    "Dorothy",
    "Kimberly",
    "Emily",
    "Donna",
    "Michelle",
    "Carol",
    "Amanda",
    "Melissa",
    "Deborah",
    "Stephanie",
    "Rebecca",
    "Sharon",
    "Laura",
    "Cynthia",
    "Kathleen",
    "Amy",
    "Angela",
    "Shirley",
    "Anna",
    "Brenda",
    "Pamela",
    "Emma",
    "Nicole",
    "Helen",
    "Samantha",
]
REAL_LAST_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Hernandez",
    "Lopez",
    "Gonzalez",
    "Wilson",
    "Anderson",
    "Thomas",
    "Taylor",
    "Moore",
    "Jackson",
    "Martin",
    "Lee",
    "Perez",
    "Thompson",
    "White",
    "Harris",
    "Sanchez",
    "Clark",
    "Ramirez",
    "Lewis",
    "Robinson",
    "Walker",
    "Young",
    "Allen",
    "King",
    "Wright",
    "Scott",
    "Torres",
    "Nguyen",
    "Hill",
    "Flores",
    "Green",
    "Adams",
    "Nelson",
    "Baker",
    "Hall",
    "Rivera",
    "Campbell",
    "Mitchell",
    "Carter",
    "Roberts",
]


def generate_real_name() -> str:
    return f"{random.choice(REAL_FIRST_NAMES)} {random.choice(REAL_LAST_NAMES)}"


def generate_email_prefix(tag: str | None = None) -> str:
    """生成 <TAG><YYYYMMDD><随机字符> 格式的前缀。

    :param tag: 邮箱前缀 tag,缺省回退到模块顶层 ``EMAIL_PREFIX_TAG``。
        库模式下应传 ``BotConfig.email_prefix_tag``。
    """
    today = datetime.now().strftime("%Y%m%d")
    suffix = random.choice("abcdefghjkmnpqrstuvwxyz")
    effective_tag = tag if tag is not None else EMAIL_PREFIX_TAG
    return f"{effective_tag}{today}{suffix}"


async def retry_with_backoff(coro_fn, max_retries=3, base_delay=2.0, description="operation"):
    for attempt in range(max_retries):
        try:
            return await coro_fn()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(f"{description} 失败 (重试 {attempt + 1}/{max_retries}): {e}")
            await asyncio.sleep(delay)


async def screenshot(page: Page, name: str):
    path = os.path.join(SCREENSHOTS_DIR, f"{time.strftime('%H%M%S')}_{name}.png")
    try:
        await page.screenshot(path=path, full_page=True)
        logger.info(f"截图: {path}")
    except Exception as e:
        logger.warning(f"截图失败: {e}")


async def debug_pause(page: Page, reason: str):
    """调试模式：打印当前状态并保持浏览器打开，等待手动检查。"""
    global _bot_instance
    logger.error("=" * 60)
    logger.error(f"调试暂停: {reason}")
    logger.error(f"当前 URL: {page.url[:150]}")
    try:
        text = await page.evaluate("() => document.body?.innerText?.substring(0, 800) || '(no body text)'")
        logger.error(f"页面文本:\n{text}")
    except Exception:
        pass
    await screenshot(page, f"DEBUG_{reason.replace(' ', '_')[:40]}")
    logger.error("截图已保存到 screenshots/ 目录")
    logger.error("浏览器保持打开。调试完成后按 Ctrl+C 退出。")
    logger.error("=" * 60)
    try:
        while True:
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        pass


async def log_page_state(page: Page, step_name: str):
    """输出当前页面状态用于调试"""
    url = page.url
    title = await page.title()
    try:
        body = await page.evaluate("() => document.body?.innerText?.substring(0, 300) || '(empty)'")
    except Exception:
        body = "(无法读取)"
    logger.info(f"[{step_name}] URL={url[:120]}")
    logger.info(f"[{step_name}] Title={title}")
    logger.info(f"[{step_name}] Body={body[:200]}")


# ===================== 自定义异常 =====================
class MailboxError(Exception):
    """邮箱创建 / 取信 / 提取验证码 / 清理流程错误。"""


class SignupFlowError(Exception):
    pass


class PaymentError(Exception):
    pass


class VerificationTimeout(Exception):
    pass


# ===================== 3. ChatGPT 浏览器自动化 =====================
class ChatGPTBot:
    CHATGPT_URL = "https://chatgpt.com"

    def __init__(
        self,
        headless: bool = False,
        slow_mo: int = 100,
        *,
        config: "BotConfig",
    ):
        """初始化浏览器自动化机器人。

        :param headless: 是否启用无头浏览器。
        :param slow_mo: Playwright slow_mo 参数(ms),便于观察。
        :param config: 必填,所有 GoPay / 订阅参数通过 BotConfig 注入。
        """
        self.headless = headless
        self.slow_mo = slow_mo
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.playwright = None
        # 注册时填的姓名,支付时用于 Stripe 账单字段
        self.registration_name: str = ""
        # 配置项全部从 BotConfig 注入(本模块迁入项目后不再保留 CLI 模块全局回退)
        self._gopay_phone = config.gopay_phone
        self._gopay_country_code = config.gopay_country_code
        self._gopay_pin = config.gopay_pin
        self._sub_plan_name = config.subscription_plan_name
        self._sub_billing_country = config.subscription_billing_country
        self._sub_currency = config.subscription_currency
        self._sub_promo_id = config.subscription_promo_campaign_id
        self._sub_cancel_url = config.subscription_cancel_url

    async def __aenter__(self):
        await self.launch()
        return self

    async def __aexit__(self, *args):
        if not DEBUG_MODE:
            await self.close()
        else:
            logger.info("调试模式 — 浏览器保持打开")

    async def launch(self):
        global _bot_instance
        _bot_instance = self
        logger.info("启动浏览器...")
        self.playwright = await async_playwright().__aenter__()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=ChromeWhatsNewUI",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            ignore_default_args=["--enable-automation"],
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            permissions=["geolocation"],
        )
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = window.chrome || { runtime: {} };
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (p) =>
                p.name === 'notifications'
                    ? Promise.resolve({ state: 'prompt', onchange: null })
                    : originalQuery(p);
        """)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(30000)
        logger.info("✅ 浏览器启动完成")

    async def close(self):
        try:
            if self.page:
                await self.page.close()
        except Exception:
            pass
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        logger.info("浏览器已关闭")

    async def _random_delay(self, ms_min=500, ms_max=2000):
        await asyncio.sleep(random.randint(ms_min, ms_max) / 1000)

    async def _type_human_like(self, locator, text: str):
        for ch in text:
            await locator.press(ch, delay=random.randint(50, 150))
            await asyncio.sleep(random.randint(10, 30) / 1000)

    async def _fill_controlled_input(self, locator, value: str) -> str:
        await locator.scroll_into_view_if_needed(timeout=3000)
        # 先尝试 force click（绕过 label 遮挡），失败则用 JS focus
        try:
            await locator.click(timeout=3000, force=True)
        except Exception:
            try:
                await locator.evaluate("(el) => { el.focus(); el.dispatchEvent(new Event('focus', {bubbles:true})); }")
            except Exception:
                pass
        try:
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Backspace")
        except Exception:
            pass
        try:
            await locator.fill("")
        except Exception:
            pass
        await self.page.keyboard.type(value, delay=random.randint(45, 95))
        await locator.evaluate("""
            (el) => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
        """)
        try:
            actual = await locator.input_value(timeout=1000)
        except Exception:
            actual = ""
        if actual != value:
            await locator.evaluate(
                """
                (el, value) => {
                    const proto = el instanceof HTMLTextAreaElement
                        ? window.HTMLTextAreaElement.prototype
                        : window.HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                    if (setter) setter.call(el, value);
                    else el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
            """,
                value,
            )
            try:
                actual = await locator.input_value(timeout=1000)
            except Exception:
                actual = ""
        return actual

    async def _fill_age_only_input(self, age_value: str) -> bool:
        candidates = [
            self.page.locator('input[name="age"]').first,
            self.page.get_by_placeholder(re.compile(r"^(年龄|age)$", re.IGNORECASE)).first,
            self.page.get_by_label(re.compile(r"^(年龄|age)$", re.IGNORECASE)).first,
            self.page.get_by_role("spinbutton", name=re.compile(r"(年龄|age)", re.IGNORECASE)).first,
            self.page.locator('input[type="number"]').first,
        ]
        for loc in candidates:
            try:
                if not await loc.is_visible(timeout=1500):
                    continue
                meta = await loc.evaluate("""
                    (el) => ({
                        type: el.getAttribute('type') || '',
                        name: el.getAttribute('name') || '',
                        id: el.getAttribute('id') || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        ariaLabel: el.getAttribute('aria-label') || '',
                        role: el.getAttribute('role') || ''
                    })
                """)
                combined = "".join(str(v) for v in meta.values())
                if re.search(r"(full.?name|姓名|全名)", combined, re.IGNORECASE):
                    continue
                actual = await self._fill_controlled_input(loc, age_value)
                if actual == age_value:
                    logger.info(f"已填写年龄: {actual}")
                    return True
                logger.warning(f"年龄输入校验失败: expected={age_value}, actual={actual}, meta={meta}")
            except Exception as e:
                logger.warning(f"年龄候选输入失败: {e}")
        return False

    async def _find_signup_email_input(self):
        """查找注册流程中的邮箱输入框,兼容 dialog 与 auth.openai.com 整页表单。"""
        for sel in [
            self.page.get_by_role("textbox", name=re.compile(r"(电子邮件|email)", re.IGNORECASE)).first,
            self.page.locator('input[type="email"]').first,
            self.page.locator('input[name="email"]').first,
            self.page.locator('input[autocomplete="email"]').first,
            self.page.locator('input[id*="email" i]').first,
            self.page.get_by_placeholder(re.compile(r"(电子邮件|邮箱|email)", re.IGNORECASE)).first,
        ]:
            try:
                if await sel.is_visible(timeout=1200):
                    return sel
            except Exception:
                continue
        return None

    async def _click_email_auth_option_if_visible(self) -> bool:
        """点击 ChatGPT 新登录弹窗中的邮箱方式入口,避免误点第三方登录。"""
        email_option_re = re.compile(
            r"^(使用(?:电子邮箱|电子邮件|邮箱)继续|继续使用(?:电子邮箱|电子邮件|邮箱)|"
            r"Continue with (?:email|e-mail)(?: address)?|Email|E-mail)$",
            re.IGNORECASE,
        )
        for sel in [
            self.page.get_by_role("button", name=email_option_re).first,
            self.page.get_by_role("link", name=email_option_re).first,
            self.page.locator('button:has-text("使用电子邮箱继续")').first,
            self.page.locator('button:has-text("Continue with email")').first,
            self.page.locator('a:has-text("使用电子邮箱继续")').first,
            self.page.locator('a:has-text("Continue with email")').first,
        ]:
            try:
                if await sel.is_visible(timeout=800):
                    await sel.click(timeout=3000)
                    logger.info("[Plus注册] 已点击邮箱方式入口")
                    return True
            except Exception:
                continue
        return False

    async def navigate_to_signup(self, email: str) -> bool:
        logger.info(f"导航到 ChatGPT 注册页, 邮箱={email}")

        # 1. 打开 ChatGPT
        await self.page.goto(self.CHATGPT_URL, wait_until="domcontentloaded")
        await screenshot(self.page, "step_01_homepage")
        await self._random_delay(2000, 3000)

        # 2. 点击 "免费注册" / "Sign up"
        # 兼容两种 UI 变体：
        # (a) chatgpt.com 内嵌 chat 视图 → 顶部 banner 有 [data-testid=signup-button] 按钮，
        #     点击后弹出 dialog 含邮箱输入框；
        # (b) "开始使用" 独立 splash 页（部分地区/设备） → 中间有大块按钮/链接，
        #     点击后会跳转到 auth.openai.com 上的整页表单。
        signup_clicked = False
        for sel_factory in [
            lambda: self.page.get_by_test_id("signup-button").first,
            lambda: (
                self.page.get_by_role(
                    "button", name=re.compile(r"(免费注册|Sign up|Create account|Get started)", re.IGNORECASE)
                ).first
            ),
            lambda: (
                self.page.get_by_role(
                    "link", name=re.compile(r"(免费注册|Sign up|Create account|Get started)", re.IGNORECASE)
                ).first
            ),
            lambda: self.page.get_by_text(re.compile(r"^(免费注册|Sign up)$", re.IGNORECASE)).first,
        ]:
            try:
                el = sel_factory()
                if await el.is_visible(timeout=4000):
                    await el.click(timeout=5000)
                    signup_clicked = True
                    logger.info("已点击注册按钮")
                    break
            except Exception:
                continue

        if not signup_clicked:
            page_text = await self.page.evaluate("() => document.body.innerText.substring(0, 1000)")
            logger.error(f"页面 URL: {self.page.url}")
            logger.error(f"页面文本: {page_text}")
            await screenshot(self.page, "step_01_signup_btn_missing")
            raise SignupFlowError("找不到注册按钮")

        # 等到 dialog 出现 或者 已跳到 auth.openai.com
        await self._random_delay(2000, 3500)
        await screenshot(self.page, "step_01b_after_signup_click")

        # 3. 找邮箱输入框（dialog 内 OR auth.openai.com 整页）
        email_input = None
        deadline = asyncio.get_event_loop().time() + 30  # 最多等 30 秒
        attempt = 0
        while asyncio.get_event_loop().time() < deadline:
            attempt += 1
            email_input = await self._find_signup_email_input()
            if email_input:
                break
            if await self._click_email_auth_option_if_visible():
                await self._random_delay(800, 1500)
                continue
            # 偶尔可能页面跳转到了 auth.openai.com 但 DOM 还在加载，多等一下
            await asyncio.sleep(1)

        if not email_input:
            page_text = await self.page.evaluate("() => document.body.innerText.substring(0, 1500)")
            logger.error(f"找不到邮箱输入框，当前 URL: {self.page.url}")
            logger.error(f"页面文本（前 1500 字）: {page_text}")
            await screenshot(self.page, "step_02_email_input_missing")
            raise SignupFlowError(f"找不到邮箱输入框（attempts={attempt}, url={self.page.url[:100]}）")

        await email_input.click()
        await self._type_human_like(email_input, email)
        await self._random_delay()
        await screenshot(self.page, "step_02_email_in_dialog")

        # 4. 点击 "继续"
        continue_btn = None
        for sel in [
            self.page.get_by_role("button", name=re.compile(r"^(继续|Continue)$", re.IGNORECASE)),
        ]:
            try:
                if await sel.is_visible(timeout=5000):
                    continue_btn = sel
                    break
            except Exception:
                continue

        if not continue_btn:
            raise SignupFlowError("找不到继续按钮")

        await continue_btn.click()

        # 5. 等待跳转到 auth.openai.com/create-account/password
        logger.info("等待跳转到 create-account/password...")
        try:
            await self.page.wait_for_url("**/create-account/password**", timeout=30000)
        except Exception:
            logger.warning("未检测到 create-account/password URL")

        await self._random_delay(1000, 2000)
        await screenshot(self.page, "step_03_password_page")

        # 6. 点击 "使用一次性验证码注册"（passwordless）
        otp_btn = None
        current_url = self.page.url
        if "create-account/password" in current_url or "auth.openai.com" in current_url:
            for sel in [
                self.page.get_by_role("button", name=re.compile(r"(一次性验证码|one.time|use a code)", re.IGNORECASE)),
                self.page.get_by_text(re.compile(r"(一次性验证码|use a one.time code)", re.IGNORECASE)),
            ]:
                try:
                    if await sel.first.is_visible(timeout=3000):
                        otp_btn = sel.first
                        break
                except Exception:
                    continue

            if otp_btn:
                await otp_btn.click()
                await self._random_delay()
            else:
                logger.info("未找到OTP按钮 — 可能已在验证页面")

        # 7. 等待 email-verification 页面
        try:
            await self.page.wait_for_url("**/email-verification**", timeout=30000)
            logger.info("✅ 已到达 email-verification 页面")
        except Exception:
            logger.warning("等待 email-verification 超时")
            current_url = self.page.url
            logger.info(f"当前 URL: {current_url[:120]}")

        await screenshot(self.page, "step_04_verification_page")
        return True

    async def wait_for_verification_page(self) -> bool:
        try:
            await self.page.wait_for_url("**/email-verification**", timeout=60000)
            logger.info("到达邮箱验证页面")
            await screenshot(self.page, "step_04_verification_page")
            return True
        except Exception:
            logger.warning("未检测到邮箱验证 URL，检查页面内容...")
            url = self.page.url
            if "email-verification" in url or "auth.openai.com" in url:
                return True
            return False

    async def enter_verification_code(self, code: str) -> bool:
        logger.info(f"输入验证码: {code[:2]}...{code[-1]}")
        await screenshot(self.page, "step_05_before_code")

        # email-verification 页面: textbox "验证码"
        code_input = None
        for sel in [
            self.page.get_by_role("textbox", name=re.compile(r"(验证码|verification code)", re.IGNORECASE)),
            self.page.get_by_placeholder(re.compile(r"(code|验证码|verification)", re.IGNORECASE)),
            self.page.locator("form input").first,
        ]:
            try:
                if await sel.is_visible(timeout=5000):
                    code_input = sel
                    break
            except Exception:
                continue

        if not code_input:
            # 尝试 6 个独立数字输入框
            single_inputs = self.page.locator('input[maxlength="1"]')
            count = await single_inputs.count()
            if count >= 4:
                logger.info(f"检测到 {count} 个独立数字输入框")
                for i in range(min(len(code), count)):
                    await single_inputs.nth(i).fill(code[i])
                    await asyncio.sleep(0.1)
            else:
                raise SignupFlowError("找不到验证码输入框")
        else:
            await code_input.click()
            await code_input.fill(code)

        await screenshot(self.page, "step_06_code_entered")

        # 点击 "继续" 按钮
        for sel in [
            self.page.get_by_role("button", name=re.compile(r"^(继续|Continue)$", re.IGNORECASE)),
            self.page.locator("button").last,
        ]:
            try:
                if await sel.is_visible(timeout=2000):
                    await sel.click()
                    break
            except Exception:
                continue

        logger.info("验证码已提交，等待跳转...")
        return True

    async def fill_about_you(self, name=None, birthdate=None):
        if name is None:
            name = DEFAULT_NAME
        if birthdate is None:
            birthdate = generate_birthdate()
        # 记下注册姓名，用于 Stripe 账单
        self.registration_name = name
        parts = birthdate.split("-")
        year, month, day = parts[0], parts[1], parts[2]

        for _ in range(30):
            url = self.page.url
            if "about-you" in url or "about_you" in url:
                break
            await asyncio.sleep(2)

        logger.info(f"到达 about-you, name={name}, birthdate={birthdate}")
        await screenshot(self.page, "step_07_about_you")
        await self._random_delay()

        name_filled = False
        for name_input in [
            self.page.locator('input[name="name"]').first,
            self.page.get_by_placeholder(re.compile(r"(全名|full name|name|姓名)", re.IGNORECASE)).first,
            self.page.get_by_label(re.compile(r"(全名|full name|name|姓名)", re.IGNORECASE)).first,
            self.page.get_by_role("textbox", name=re.compile(r"(全名|full name|name|姓名)", re.IGNORECASE)).first,
        ]:
            try:
                if await name_input.is_visible(timeout=1500):
                    actual_name = await self._fill_controlled_input(name_input, name)
                    if actual_name == name:
                        name_filled = True
                        logger.info(f"已填写姓名: {name}")
                        break
            except Exception:
                continue
        if not name_filled:
            logger.warning("未找到姓名输入框")

        await self._random_delay(300, 800)

        # 检测页面是要求"年龄"还是"生日日期"
        page_body = await self.page.evaluate("() => document.body?.innerText || ''")
        logger.info(f"about-you 页面关键词检测: body前150字={page_body[:150]}")

        # 先处理 "成年人" 复选框 (常见于年龄验证)
        if any(k in page_body for k in ("成年人", "adult", "18+")):
            try:
                adult_cb = self.page.locator('input[type="checkbox"]').first
                if await adult_cb.is_visible(timeout=2000):
                    if not await adult_cb.is_checked():
                        await adult_cb.click()
                        logger.info("已勾选「成年人」复选框")
            except Exception:
                pass

        has_birthdate = any(k in page_body for k in ("生日", "出生", "birth", "YYYY", "MM", "DD"))
        has_age_only = any(k in page_body for k in ("年龄", "age")) and not has_birthdate

        # 计算一个合理的年龄
        age_value = str(random.randint(22, 30))

        if has_age_only:
            logger.info(f"检测到年龄输入（非日期），填入: {age_value}")
            if not await self._fill_age_only_input(age_value):
                raise SignupFlowError("找不到可填写的年龄输入框")
            try:
                current_name = await self.page.locator('input[name="name"]').first.input_value(timeout=1000)
                if current_name != name:
                    logger.warning(f"姓名字段被改写，重新填写: {current_name!r} -> {name!r}")
                    await self._fill_controlled_input(self.page.locator('input[name="name"]').first, name)
            except Exception:
                pass
        else:
            # year/month/day 三字段 - 排除 name 字段
            filled = await self.page.evaluate(f"""
                (() => {{
                    const year = '{year}', month = '{month}', day = '{day}';
                    const allInputs = document.querySelectorAll(
                        'input[type="number"], [role="spinbutton"]'
                    );
                    const visible = Array.from(allInputs).filter(el => {{
                        const r = el.getBoundingClientRect();
                        const combined = (el.name || '') + (el.id || '') + (el.getAttribute('aria-label') || '');
                        return r.width > 0 && r.height > 0 && !/name|姓名|full/i.test(combined);
                    }});
                    visible.sort((a, b) => {{
                        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                        return (ra.top - rb.top) || (ra.left - rb.left);
                    }});

                    const s = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    const set = (el, val) => {{
                        s.call(el, val);
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }};

                    const log = [];
                    const values = [year, month, day];
                    for (let i = 0; i < visible.length && i < 3; i++) {{
                        set(visible[i], values[i]);
                        log.push('[' + i + ']=' + values[i]);
                    }}
                    return JSON.stringify({{ count: visible.length, log }});
                }})()
            """)
            logger.info(f"年龄填写 (y/m/d): {filled[:300]}")

        await self._random_delay(300, 500)

        await screenshot(self.page, "step_08_about_filled")
        await log_page_state(self.page, "about-you 填完表单后")

        # 提交 — 按钮文本是 "完成帐户创建"
        clicked = False
        for btn_text in [
            re.compile(r"(完成|Complete|create account|agree)", re.IGNORECASE),
            re.compile(r"(Continue|继续|Save|保存|Next|下一步)", re.IGNORECASE),
        ]:
            try:
                btn = self.page.get_by_role("button", name=btn_text)
                if await btn.is_visible(timeout=2000):
                    text = (await btn.text_content() or "").strip()
                    logger.info(f"点击提交按钮: {text[:40]}")
                    await btn.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            buttons = self.page.locator("button")
            cnt = await buttons.count()
            logger.warning(f"未找到标准提交按钮，页面有 {cnt} 个按钮")
            if cnt > 0:
                await buttons.last.click()
                clicked = True
                logger.info("已点击最后一个按钮作为保底")

        logger.info(f"about-you 提交 {'成功' if clicked else '未点击'}")

        # 主动检测是否有跳转，卡住则立即截断
        age_retry_done = False
        for i in range(15):  # 最多等 30 秒
            await asyncio.sleep(2)
            current = self.page.url
            if "about-you" not in current and "about_you" not in current:
                logger.info(f"about-you 已跳转: {current[:100]}")
                return  # 已离开 about-you
            if has_age_only and not age_retry_done:
                try:
                    body_text = await self.page.evaluate("() => document.body?.innerText || ''")
                except Exception:
                    body_text = ""
                if "请输入有效年龄" in body_text or "valid age" in body_text.lower():
                    age_retry_done = True
                    logger.warning("检测到年龄校验失败，重新填写年龄并再次提交")
                    if await self._fill_age_only_input(age_value):
                        try:
                            retry_btn = self.page.get_by_role(
                                "button",
                                name=re.compile(
                                    r"(完成|Complete|create account|agree|Continue|继续|Save|保存|Next|下一步)",
                                    re.IGNORECASE,
                                ),
                            ).first
                            if await retry_btn.is_visible(timeout=2000):
                                await retry_btn.click()
                                logger.info("年龄重填后已再次提交")
                                continue
                        except Exception as e:
                            logger.warning(f"年龄重填后再次提交失败: {e}")
            if i == 5:
                await log_page_state(self.page, f"about-you 等待跳转中 ({i * 2}s)")
            if i == 14:
                # 卡住了，立刻截断留给 MCP 处理
                logger.error(f"about-you 提交后 30s 无跳转，当前仍在: {current[:100]}")
                await log_page_state(self.page, "about-you 卡住")
                if DEBUG_MODE:
                    await debug_pause(self.page, "about-you 提交后卡住，无跳转")
                return

    async def wait_for_login_complete(self) -> bool:
        logger.info("等待 OAuth 回调返回 chatgpt.com...")
        # 先检查是否已经回到了 chatgpt.com
        url = self.page.url
        if self.CHATGPT_URL in url and "callback" not in url and "auth" not in url:
            logger.info(f"已在 chatgpt.com: {url[:100]}")
            await self._random_delay(2000, 3000)
            return await self._check_session()

        # 等待 60s 跳转，超时就截断
        try:
            await self.page.wait_for_url(re.compile(r"https://chatgpt\.com(?!.*callback).*"), timeout=60000)
            logger.info(f"已回到: {self.page.url[:100]}")
        except Exception:
            url = self.page.url
            logger.error(f"60s 内未跳转回 chatgpt.com，当前: {url[:120]}")
            await log_page_state(self.page, "wait_for_login 超时")
            if DEBUG_MODE:
                await debug_pause(self.page, "wait_for_login 超时 60s")
            return False

        await self._random_delay(2000, 3000)
        return await self._check_session()

    async def _check_session(self) -> bool:
        """检查 /api/auth/session 确认登录状态"""
        try:
            session_data = await self.page.evaluate("""
                async () => {
                    const resp = await fetch('/api/auth/session', { credentials: 'include' });
                    const text = await resp.text();
                    try { return JSON.parse(text); } catch(e) { return null; }
                }
            """)
            if session_data and session_data.get("accessToken"):
                user_info = session_data.get("user", {})
                plan = session_data.get("account", {}).get("planType", "?")
                logger.info(f"✅ 登录成功: {user_info.get('email', '?')} plan={plan}")
                return True
            else:
                logger.warning(f"Session 无 accessToken: {str(session_data)[:300]}")
        except Exception as e:
            logger.warning(f"Session 检查失败: {e}")
        return False

        try:
            session_data = await self.page.evaluate("""
                async () => {
                    const resp = await fetch('/api/auth/session', { credentials: 'include' });
                    const text = await resp.text();
                    try { return JSON.parse(text); } catch(e) { return null; }
                }
            """)
            if session_data and session_data.get("accessToken"):
                user_info = session_data.get("user", {})
                plan = session_data.get("account", {}).get("planType", "?")
                logger.info(f"✅ 登录成功: {user_info.get('email', '?')} plan={plan}")
                return True
            else:
                logger.warning(f"Session 内容: {str(session_data)[:300]}")
        except Exception as e:
            logger.warning(f"Session 检查失败: {e}")

        # 保底：URL 确认
        if self.CHATGPT_URL in self.page.url:
            logger.info("保底确认：已在 chatgpt.com")
            return True
        return False

    async def get_access_token(self) -> str:
        token = await self.page.evaluate("""
            async () => {
                const resp = await fetch('/api/auth/session', { credentials: 'include' });
                const data = await resp.json();
                return data?.accessToken || null;
            }
        """)
        if not token:
            raise SignupFlowError("获取 accessToken 失败")
        return token

    async def execute_gopay_payment(self) -> str:
        """调用 ChatGPT payments/checkout API 创建 ID/IDR Stripe Checkout，返回 Stripe hosted URL"""
        logger.info("执行 GoPay 支付流程...")

        token = await self.get_access_token()

        payload = {
            "plan_name": self._sub_plan_name,
            "billing_details": {
                "country": self._sub_billing_country,
                "currency": self._sub_currency,
            },
            "cancel_url": self._sub_cancel_url,
            "promo_campaign": {
                "promo_campaign_id": self._sub_promo_id,
                "is_coupon_from_query_param": False,
            },
            "checkout_ui_mode": "hosted",
        }

        checkout_url = await self.page.evaluate(
            """
            async (args) => {
                const resp = await fetch('https://chatgpt.com/backend-api/payments/checkout', {
                    method: 'POST', credentials: 'include',
                    headers: { Authorization: 'Bearer ' + args.token, 'Content-Type': 'application/json' },
                    body: JSON.stringify(args.payload),
                });
                const data = await resp.json().catch(() => null);
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return data?.url || data?.checkout_url || null;
            }
        """,
            {"token": token, "payload": payload},
        )

        if not checkout_url:
            raise PaymentError("未获取到 Stripe Checkout URL")

        logger.info(f"Stripe URL: {checkout_url}")
        await screenshot(self.page, "step_09_before_checkout")
        await self.page.goto(checkout_url)
        await self._random_delay(2000, 4000)
        return checkout_url

    async def handle_stripe_checkout(self, whatsapp_callback) -> str:
        """Stripe hosted checkout → Midtrans SNAP → GoPay 完整支付流程"""
        logger.info("处理 Stripe Checkout → GoPay...")
        await screenshot(self.page, "step_10_stripe_checkout")

        # 1. 选择 GoPay 并填账单地址（用注册时的姓名）
        await self._fill_gopay_stripe_form(self.registration_name or DEFAULT_NAME)
        await screenshot(self.page, "step_11_gopay_selected")

        # 2. 点击订阅 → 跳转到 Midtrans
        # 关键：单一 force click 在某些场景下不会触发 Stripe React 处理函数
        # 实测需要"模拟真实鼠标"（pointerdown/mousedown/mouseup/click）才能让 Stripe
        # 的 hosted checkout 真正提交。下面用多策略：
        #   A. page.mouse.click(x, y) 在按钮中心点击（最像真实用户）
        #   B. locator.click(force=True) 兜底
        #   C. JS dispatch click 兜底
        # 每个策略后等几秒看 URL 是否跳转，跳了就停。
        submit_url_before = self.page.url

        async def _click_and_wait(strategy: str, action_coro) -> bool:
            try:
                await action_coro
                logger.info(f"已点击订阅按钮（{strategy}）")
            except Exception as e:
                logger.warning(f"订阅按钮 {strategy} 失败: {e}")
                return False
            # 等最多 12 秒看是否开始跳转（pay.openai.com → midtrans 中间会经过 stripe redirect）
            try:
                await self.page.wait_for_url(
                    lambda u: "pay.openai.com" not in u or "midtrans" in u,
                    timeout=12000,
                )
                return True
            except Exception:
                return self.page.url != submit_url_before

        submit = self.page.locator('[data-testid="hosted-payment-submit-button"]').first
        try:
            await submit.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass

        navigated = False
        # 策略 A: 真实鼠标坐标点击
        try:
            box = await submit.bounding_box()
            if box:
                navigated = await _click_and_wait(
                    "mouse-coord",
                    self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2),
                )
        except Exception as e:
            logger.warning(f"获取订阅按钮 bounding_box 失败: {e}")

        # 策略 B: force click
        if not navigated:
            navigated = await _click_and_wait("force-click", submit.click(force=True, timeout=10000))

        # 策略 C: JS dispatch click + form.requestSubmit
        if not navigated:
            navigated = await _click_and_wait(
                "js-click",
                self.page.evaluate(
                    """
                    () => {
                        const btn = document.querySelector('[data-testid="hosted-payment-submit-button"]');
                        if (btn) btn.click();
                        const form = btn && btn.closest('form');
                        if (form && form.requestSubmit) form.requestSubmit();
                    }
                    """
                ),
            )

        if not navigated:
            logger.warning("3 种策略后仍未跳转，继续等待 60s...")

        logger.info("等待跳转到 Midtrans...")
        try:
            await self.page.wait_for_url("**/midtrans.com/**", timeout=60000)
        except Exception:
            pass

        await screenshot(self.page, "step_12_midtrans")

        # 3. 处理 Midtrans GoPay 页面
        current_url = self.page.url
        if "midtrans.com" in current_url:
            return await self._handle_midtrans_gopay(whatsapp_callback)

        return "unknown_state"

    async def _fill_gopay_stripe_form(self, billing_name: str = None):
        """在 Stripe hosted checkout 选择 GoPay 并填账单地址。
        关键：GoPay 的 radio 被 accordion 按钮的 expandedClickArea 子层遮挡，
        Playwright 标准 click 会失败，必须用 page.mouse.click(x, y) 在 radio
        的几何坐标上点击；表单字段则用 JS setter 触发 React 状态更新。
        billing_name: 填到 Stripe 账单姓名字段，应该用 about-you 时填的注册姓名。
        """
        if billing_name is None:
            billing_name = DEFAULT_NAME
        # 等表单完全渲染
        await self._random_delay(1500, 2500)

        # 1. 用真实鼠标坐标点击 GoPay radio（绕过遮挡）
        gopay_selected = False
        try:
            radio = self.page.locator('input[type="radio"][value="gopay"]').first
            await radio.scroll_into_view_if_needed(timeout=5000)
            await self._random_delay(300, 600)
            box = await radio.bounding_box()
            if box:
                await self.page.mouse.click(
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                )
                await self._random_delay(400, 800)
                gopay_selected = await radio.is_checked()
                logger.info(f"GoPay radio 已选中: {gopay_selected}")
        except Exception as e:
            logger.warning(f"鼠标坐标点击 GoPay 失败: {e}")

        # 兜底：直接 JS 触发 click
        if not gopay_selected:
            try:
                gopay_selected = await self.page.evaluate("""
                    () => {
                        const r = document.querySelector('input[type="radio"][value="gopay"]');
                        if (!r) return false;
                        r.click();
                        return r.checked;
                    }
                """)
                logger.info(f"JS 兜底点击 GoPay: {gopay_selected}")
            except Exception as e:
                logger.warning(f"JS 兜底失败: {e}")

        if not gopay_selected:
            raise PaymentError("无法选中 GoPay 支付方式")

        await self._random_delay(800, 1500)

        # 2. 点 "手动输入地址"，让 Stripe 显示完整 line1/city/zip 输入框
        try:
            manual_btn = self.page.get_by_role("button", name=re.compile(r"(手动输入|manual)", re.IGNORECASE))
            if await manual_btn.is_visible(timeout=3000):
                await manual_btn.click()
                await self._random_delay(500, 1000)
                logger.info("已切换到手动输入地址")
        except Exception:
            logger.info("手动输入地址按钮不可见（可能默认就是手动模式）")

        # 3. 用 JS setter 填字段，确保触发 React onChange
        fill_result = await self.page.evaluate(
            """
            (billingName) => {
                const setReact = (el, value) => {
                    const proto = el instanceof HTMLSelectElement
                        ? HTMLSelectElement.prototype
                        : HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    setter.call(el, value);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                };
                const fields = {
                    billingName: billingName,
                    billingAddressLine1: '574 East Avenue 28',
                    billingLocality: 'Los Angeles',
                    billingPostalCode: '90031',
                };
                const result = {};
                for (const [name, val] of Object.entries(fields)) {
                    const el = document.querySelector(`input[name="${name}"]`);
                    if (el) { setReact(el, val); result[name] = el.value; }
                    else { result[name] = '<missing>'; }
                }
                const stateSel = document.querySelector('select[name="billingAdministrativeArea"]');
                if (stateSel) {
                    const opt = Array.from(stateSel.options).find(
                        o => o.text === 'California' || o.value === 'CA'
                    );
                    if (opt) { setReact(stateSel, opt.value); result.state = stateSel.value; }
                }
                const cb = document.querySelector('input[name="termsOfServiceConsentCheckbox"]');
                if (cb && !cb.checked) cb.click();
                result.termsChecked = !!(cb && cb.checked);
                return result;
            }
            """,
            billing_name,
        )
        logger.info(f"账单字段填写结果: {fill_result}")

        # 4. 校验 name 字段已填（最关键的必填项之一）
        if fill_result.get("billingName") != billing_name:
            raise PaymentError(f"账单姓名未能正确填写: {fill_result}")

    async def _handle_midtrans_gopay(self, whatsapp_callback) -> str:
        """Midtrans SNAP → GoPay account linking → 真实付款.

        流程（MCP 抓包验证过）：
            1. 在 linking 页：点 .phone-code-wrapper → 选 China(+86) → 填手机号 → Link and pay
            2. 一定遇到 "There's a technical error" 风控（429）
            3. 走绕过：POST /snap/v3/accounts/{txn}/linking 不带 Authorization → 201 activation_link_url
            4. 打开 activation_link → 点 Hubungkan → WhatsApp OTP → PIN
            5. 自动跳回 Midtrans pay 页 → 点 Pay now → iframe 内 Bayar → iframe 内 PIN
            6. 成功后 Stripe 跳回 chatgpt.com
        """
        logger.info("Midtrans GoPay 流程...")
        midtrans_url = self.page.url  # 保存原 linking URL，bypass 后回到这里继续付款
        await screenshot(self.page, "step_13_midtrans_linking")

        # Step A: 切国家 +86 + 填手机号
        try:
            await self.page.wait_for_selector(".phone-code-wrapper", timeout=15000)
            await self._random_delay(500, 1000)
            country_trigger = self.page.locator(".phone-code-wrapper").first
            await country_trigger.click(timeout=5000)
            await self._random_delay(400, 800)
            china = self.page.get_by_text("China (+86)", exact=True).first
            await china.scroll_into_view_if_needed(timeout=3000)
            await china.click(timeout=5000)
            await self._random_delay(300, 500)

            phone_input = self.page.get_by_role("textbox").first
            await phone_input.fill(self._gopay_phone)
            logger.info(f"已填写 GoPay 手机号: +{self._gopay_country_code} {self._gopay_phone}")
        except Exception as e:
            logger.warning(f"国家切换/手机号填写异常: {e}")

        # 点 Link and pay
        try:
            link_btn = self.page.get_by_role("button", name=re.compile(r"link\s*and\s*pay", re.IGNORECASE)).first
            await link_btn.click(timeout=5000)
            logger.info("已点击 Link and pay")
        except Exception as e:
            logger.warning(f"点击 Link and pay 异常: {e}")

        # 等到风控错误出现（必现）
        await self._random_delay(2500, 3500)

        # Step B: 检测风控并走 bypass
        page_text = await self.page.evaluate("() => document.body.innerText")
        if any(k in page_text for k in ("technical error", "Technical error", "too many", "Too many")):
            logger.info("检测到 GoPay 风控（429），走 bypass...")
            ok = await self._bypass_gopay_ratelimit()
            if not ok:
                logger.error("GoPay bypass 失败")
                return "bypass_failed"
            await screenshot(self.page, "step_14_after_bypass")

        # Step C: 等待 OTP 输入页（pin-web-client.gopayapi.com / linking/otp）并要求用户输入 OTP
        otp_inputted = False
        pin_inputted = False
        navigated_back = False  # 是否已从 callback 页跳回原 Midtrans linking URL
        for _poll in range(180):  # 最多等 6 分钟
            await asyncio.sleep(2)
            current = self.page.url

            # 成功条件
            if (
                "payments/success" in current
                or "chatgpt.com/payments/success" in current
                or current.rstrip("/").endswith("chatgpt.com")
            ):
                logger.info("✅ GoPay 支付成功，已跳回 chatgpt.com")
                return "success"

            # 关键：PIN 通过后会跳到 midtrans 的 linking callback URL（页面空白），
            # 需要主动导航回原 linking URL 才会自动进入 pay 阶段
            if "/snap/v3/callback/gopay/linking" in current and not navigated_back:
                logger.info("检测到 GoPay 绑定 callback，导航回原 Midtrans 链接继续付款...")
                try:
                    await self.page.goto(midtrans_url)
                    navigated_back = True
                    await self._random_delay(2000, 3000)
                except Exception as e:
                    logger.warning(f"导航回原链接失败: {e}")
                continue

            try:
                page_text = await self.page.evaluate("() => document.body.innerText")
            except Exception:
                continue

            # OTP 页（WhatsApp）
            if not otp_inputted and (
                "Masukkin OTP" in page_text
                or "linking/otp" in current
                or ("OTP" in page_text and "WhatsApp" in page_text)
            ):
                logger.info("检测到 OTP 输入页面，等待用户从 WhatsApp 复制验证码")
                await screenshot(self.page, "step_15_otp_page")
                otp = await whatsapp_callback() if whatsapp_callback else None
                if otp:
                    if await self._fill_pin_or_otp(otp):
                        otp_inputted = True
                        logger.info(f"已填入 OTP: {otp[:2]}***")
                        await self._random_delay(2500, 3500)
                continue

            # 第一次 PIN 页（绑定 GoPay）—— 写死 GoPay PIN，无需用户输入
            if not pin_inputted and "ketik 6 digit PIN" in page_text and "pin-web-client" in current:
                logger.info("检测到 GoPay PIN 输入页面（绑定阶段），自动填入预设 PIN")
                await screenshot(self.page, "step_16_pin_page")
                if await self._fill_pin_or_otp(self._gopay_pin):
                    pin_inputted = True
                    logger.info(f"已填入 PIN: {self._gopay_pin[:2]}***")
                    await self._random_delay(3000, 4000)
                continue

            # 回到 Midtrans pay 页（GoPay 已绑定，需要点 Pay now → iframe Bayar → iframe PIN）
            if "gopay-tokenization/pay" in current or ("midtrans" in current and "Pay now" in page_text):
                if await self._handle_midtrans_pay_step(whatsapp_callback):
                    return "success"

        logger.warning("GoPay 流程超时")
        return "timeout"

    async def _fill_pin_or_otp(self, code: str) -> bool:
        """OTP/PIN 输入：找第一个 input，点击聚焦，逐字符 keyboard.type 让它自动跳格。
        支持顶层 page 和 iframe 内场景。"""
        code = (code or "").strip()
        if not code:
            return False

        # 先尝试顶层 page 的 pin-input-field（GoPay OTP/绑定 PIN 页用）
        for locator in [
            self.page.get_by_test_id("pin-input-field").first,
            self.page.locator('input[type="tel"], input[type="text"], input[type="password"]').first,
        ]:
            try:
                if await locator.is_visible(timeout=2000):
                    await locator.click(timeout=3000)
                    await self.page.keyboard.type(code, delay=80)
                    return True
            except Exception:
                continue

        # iframe 内（Pay 阶段第二次 PIN 在 iframe 里）
        try:
            frames = self.page.frames
            for fr in frames:
                if fr is self.page.main_frame:
                    continue
                try:
                    inp = fr.get_by_test_id("pin-input-field").first
                    if await inp.is_visible(timeout=1500):
                        await inp.click(timeout=3000)
                        await self.page.keyboard.type(code, delay=80)
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    async def _handle_midtrans_pay_step(self, whatsapp_callback) -> bool:
        """Pay now → iframe Bayar → iframe PIN → 成功跳回 chatgpt.com.
        返回 True 表示已完成（已跳回 chatgpt.com 或检测到 success）。"""
        # 点 Pay now
        try:
            pay_now = self.page.get_by_role("button", name=re.compile(r"pay\s*now", re.IGNORECASE)).first
            if await pay_now.is_visible(timeout=2000):
                await pay_now.click()
                logger.info("已点击 Pay now")
                await self._random_delay(2000, 3000)
        except Exception:
            pass

        # 等 3DS iframe 出现
        try:
            await self.page.wait_for_selector("iframe", timeout=10000)
        except Exception:
            return False

        # 找 iframe 里的 Bayar 按钮 (data-testid=pay-button)
        clicked_bayar = False
        for fr in self.page.frames:
            if fr is self.page.main_frame:
                continue
            try:
                btn = fr.get_by_test_id("pay-button").first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    clicked_bayar = True
                    logger.info("已点击 iframe 内的 Bayar 按钮")
                    break
            except Exception:
                continue

        if not clicked_bayar:
            logger.warning("未找到 iframe 内的 Bayar 按钮")
            return False

        await self._random_delay(2500, 4000)

        # iframe 内会出现 PIN 输入框（Masukkin PIN GoPay）
        for _ in range(15):
            await asyncio.sleep(2)
            current = self.page.url
            if "payments/success" in current or current.rstrip("/").endswith("chatgpt.com"):
                logger.info("✅ 已跳回 chatgpt.com，付款完成")
                return True

            # 检测 iframe 内 PIN 输入框
            for fr in self.page.frames:
                if fr is self.page.main_frame:
                    continue
                try:
                    pin_input = fr.get_by_test_id("pin-input-field").first
                    if await pin_input.is_visible(timeout=1500):
                        logger.info("检测到 iframe 内的 PIN 页面（付款确认），自动填入预设 PIN")
                        await screenshot(self.page, "step_17_iframe_pin")
                        await pin_input.click()
                        await self.page.keyboard.type(self._gopay_pin, delay=80)
                        logger.info(f"已填入付款 PIN: {self._gopay_pin[:2]}***")
                        await self._random_delay(5000, 8000)
                        break
                except Exception:
                    continue

        # 最后再确认一次
        if "payments/success" in self.page.url or self.page.url.rstrip("/").endswith("chatgpt.com"):
            return True
        return False

    async def _bypass_gopay_ratelimit(self) -> bool:
        """GoPay 风控绕过：在浏览器上下文中 fetch /snap/v3/accounts/{txn}/linking 不带 Authorization，
        拿 201 + activation_link_url，导航到该链接并点 Hubungkan。返回 True/False 表示是否成功进入 OTP 页。"""
        match = re.search(r"redirection/([a-f0-9-]+)", self.page.url)
        if not match:
            logger.warning(f"无法从 URL 提取 Midtrans txn_id: {self.page.url}")
            return False
        txn_id = match.group(1)

        # 在浏览器上下文里 fetch（无 Authorization）
        try:
            data = await self.page.evaluate(
                """
                async (args) => {
                    const r = await fetch(`https://app.midtrans.com/snap/v3/accounts/${args.txn}/linking`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                        body: JSON.stringify({ type: 'gopay', country_code: args.cc, phone_number: args.phone }),
                    });
                    const t = await r.text();
                    let d; try { d = JSON.parse(t); } catch { d = t; }
                    return { status: r.status, data: d };
                }
                """,
                {"txn": txn_id, "cc": self._gopay_country_code, "phone": self._gopay_phone},
            )
        except Exception as e:
            logger.warning(f"bypass fetch 失败: {e}")
            return False

        if data.get("status") != 201:
            logger.warning(f"bypass 非 201: status={data.get('status')} data={data.get('data')}")
            return False
        link_url = (data.get("data") or {}).get("activation_link_url")
        if not link_url:
            logger.warning(f"bypass 返回缺失 activation_link_url: {data}")
            return False

        logger.info(f"GoPay 激活链接: {link_url}")
        try:
            await self.page.goto(link_url)
        except Exception as e:
            logger.warning(f"打开激活链接失败: {e}")
            return False
        await self._random_delay(2000, 3000)

        # 点 Hubungkan（consent-button）
        try:
            connect_btn = self.page.get_by_test_id("consent-button").first
            await connect_btn.click(timeout=10000)
            logger.info("已点击 Hubungkan（同意绑定）")
        except Exception as e:
            logger.warning(f"点击 Hubungkan 失败: {e}")
            return False

        await self._random_delay(2000, 3500)
        return True

    async def _click_confirm_button(self):
        for sel in [
            self.page.get_by_role("button", name=re.compile(r"(Confirm|确认|Submit|Pay|Verify|验证)", re.IGNORECASE)),
            self.page.get_by_text(re.compile(r"(Confirm|确认|Pay|付款)", re.IGNORECASE)),
        ]:
            try:
                btn = sel.last
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    return
            except Exception:
                continue

    async def add_password_login(self, password: str) -> bool:
        logger.info("设置账号密码...")
        try:
            await self.page.goto(f"{self.CHATGPT_URL}/#settings/Account")
            await self._random_delay(1000, 2000)
            await screenshot(self.page, "step_14_settings")

            # 查找设置密码的入口
            set_pw_btn = None
            for sel in [
                self.page.get_by_text(re.compile(r"(set password|设置密码|add password)", re.IGNORECASE)),
                self.page.get_by_role("button", name=re.compile(r"(password|密码)", re.IGNORECASE)),
            ]:
                try:
                    if await sel.first.is_visible(timeout=3000):
                        set_pw_btn = sel.first
                        break
                except Exception:
                    continue

            if set_pw_btn:
                await set_pw_btn.click()
                await self._random_delay()

                # 填新密码
                pw_inputs = self.page.locator('input[type="password"]')
                count = await pw_inputs.count()
                if count >= 2:
                    await pw_inputs.nth(0).fill(password)
                    await pw_inputs.nth(1).fill(password)
                elif count >= 1:
                    await pw_inputs.first.fill(password)

                await screenshot(self.page, "step_15_password_set")
                await self._click_confirm_button()
                logger.info("✅ 密码已设置")
                return True

            logger.warning("没找到设置密码的入口")
            return False

        except Exception as e:
            logger.error(f"设置密码失败: {e}")
            return False

    async def cancel_subscription(self) -> bool:
        """通过 ChatGPT 后端 API 直接取消续订（MCP 抓包验证）：
            POST https://chatgpt.com/backend-api/subscriptions/cancel
            Headers: Authorization: Bearer <accessToken>
            Body: {}
            Response: 200 {} （成功后 GET subscriptions 会显示 will_renew=false）

        在浏览器上下文 fetch 可自动带上 cookies + access token。比 UI 流程稳定得多。
        """
        logger.info("取消订阅（API 调用）...")
        try:
            # 必须先在 chatgpt.com 同源页面下，才能正确读到 NextAuth session 与 cookies
            if "chatgpt.com" not in (self.page.url or ""):
                await self.page.goto(self.CHATGPT_URL)
                await self._random_delay(2000, 3000)

            await screenshot(self.page, "step_16_before_cancel")

            result = await self.page.evaluate(
                """
                async () => {
                    // 1. 取 accessToken
                    let token = null;
                    try {
                        const sess = await fetch('/api/auth/session', { credentials: 'include' }).then(r => r.json());
                        token = sess && sess.accessToken;
                    } catch (e) {}
                    if (!token) return { ok: false, stage: 'session', error: 'no accessToken' };

                    // 2. 解析 JWT 拿到 chatgpt_account_id（cancel API 必填字段）
                    let acctId = null;
                    try {
                        const parts = token.split('.');
                        if (parts.length === 3) {
                            const payload = JSON.parse(atob(parts[1].replace(/-/g,'+').replace(/_/g,'/')));
                            const cgpt = payload && payload['https://api.openai.com/auth'];
                            if (cgpt && cgpt.chatgpt_account_id) acctId = cgpt.chatgpt_account_id;
                        }
                    } catch (e) {}
                    // JWT 解析失败时退回到 /backend-api/me
                    if (!acctId) {
                        try {
                            const me = await fetch('/backend-api/me', {
                                credentials: 'include',
                                headers: { Authorization: 'Bearer ' + token },
                            }).then(r => r.json());
                            acctId = me && me.chatgpt_account_id;
                            if (!acctId && me && me.orgs && me.orgs.data) {
                                // org id 仅作最后兜底
                                acctId = me.orgs.data[0] && me.orgs.data[0].id;
                            }
                        } catch (e) {}
                    }
                    if (!acctId) return { ok: false, stage: 'acct_id', error: 'no chatgpt_account_id' };

                    // 3. POST 取消（必须带 account_id）
                    let cancelStatus = 0, cancelBody = '';
                    try {
                        const r = await fetch('/backend-api/subscriptions/cancel', {
                            method: 'POST',
                            credentials: 'include',
                            headers: {
                                'Content-Type': 'application/json',
                                'Authorization': 'Bearer ' + token,
                            },
                            body: JSON.stringify({ account_id: acctId }),
                        });
                        cancelStatus = r.status;
                        cancelBody = await r.text();
                    } catch (e) {
                        return { ok: false, stage: 'cancel', error: String(e), acctId };
                    }
                    if (cancelStatus !== 200) {
                        return { ok: false, stage: 'cancel', status: cancelStatus, body: cancelBody, acctId };
                    }

                    // 4. 验证 will_renew=false
                    let willRenew = null, plan = null;
                    try {
                        const sub = await fetch('/backend-api/subscriptions?account_id=' + acctId, {
                            credentials: 'include',
                            headers: { Authorization: 'Bearer ' + token },
                        }).then(r => r.json());
                        willRenew = sub && sub.will_renew;
                        plan = sub && sub.plan_type;
                    } catch (e) {}

                    return { ok: true, willRenew, plan, acctId };
                }
                """
            )

            logger.info(f"取消订阅结果: {result}")
            if not result.get("ok"):
                logger.error(f"取消订阅失败: {result}")
                return False

            # 校验 will_renew
            wr = result.get("willRenew")
            if wr is False:
                logger.info(f"✅ 订阅已取消续订（plan={result.get('plan')}）")
                await screenshot(self.page, "step_17_cancelled")
                return True
            if wr is None:
                # 校验信息缺失但 cancel 接口返回 200，认为成功
                logger.info("✅ 取消请求已被接受（will_renew 未能校验，但 200 OK）")
                return True
            logger.warning(f"取消接口 200 但 will_renew 仍为 {wr}")
            return False

        except Exception as e:
            logger.error(f"取消订阅失败: {e}")
            return False


# ===================== 6. 库模式入口(register_one_plus) =====================
async def register_one_plus(
    config: BotConfig,
    otp_callback: Callable[[], Awaitable[str | None]],
    *,
    create_mailbox: Callable[[], Awaitable[dict[str, Any]]],
    fetch_email_code: Callable[[dict[str, Any], int], Awaitable[str | None]],
    cleanup_mailbox: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    headless: bool = True,
    slow_mo: int = 100,
    step_callback: Callable[[str], None] | None = None,
    chatgpt_password: str | None = None,
    name: str | None = None,
    birthdate: str | None = None,
    email_timeout: int = 180,
) -> dict[str, Any]:
    """库模式入口:跑一次完整 ChatGPT 注册 + GoPay 付款 + 设密码 + 取消续订。

    临时邮箱完全通过 callback 注入,本模块不关心具体 provider 是 cloudmail
    还是 cloudflare_temp_email;调用方负责 wrap ``mail_provider`` 同步 client。

    :param config: 已校验的 BotConfig,所有 GoPay / 订阅参数必须显式传入。
    :param otp_callback: 异步 OTP 提供者;返回 ``None`` 视作超时,会触发付款失败。
    :param create_mailbox: 创建临时邮箱,返回至少含 ``email`` 字段的 dict。
    :param fetch_email_code: 等待并提取验证码;返回 ``None`` 视作未能提取。
    :param cleanup_mailbox: best-effort 清理邮箱 callback;可为 ``None``。
    :param headless: 是否无头模式,默认 ``True``(服务端建议保持)。
    :param slow_mo: Playwright slow_mo(ms)。
    :param step_callback: 进度回调(同步函数),参数为当前 step 名,
        枚举值见 PRD D7。可为 ``None``。
    :param chatgpt_password: 自定义 ChatGPT 密码;默认随机 16 位强密码。
    :param name: 注册时填的姓名;默认随机生成英文真名。
    :param birthdate: ``YYYY-MM-DD`` 生日;默认随机生成。
    :param email_timeout: 邮件验证码等待超时(秒)。
    :return: 字典 ``{ok, email, password, last_step, error_type, error_detail}``,
        ``ok=True`` 时 email/password 必填;失败时 email 可能为空。
    """
    chatgpt_password = chatgpt_password or generate_strong_password(16)
    birthdate = birthdate or generate_birthdate()
    real_name = name or generate_real_name()

    address: str = ""
    last_step = "creating_email"
    mailbox: dict[str, Any] | None = None

    def _step(name: str) -> None:
        """安全地推进 step,异常不影响主流程。"""
        nonlocal last_step
        last_step = name
        if step_callback is not None:
            try:
                step_callback(name)
            except Exception as exc:  # pragma: no cover - 防御性
                logger.warning("step_callback 异常被忽略: %s", exc)

    def _result(ok: bool, *, error_type: str | None = None, error_detail: str | None = None) -> dict[str, Any]:
        return {
            "ok": ok,
            "email": address,
            "password": chatgpt_password,
            "last_step": last_step,
            "error_type": error_type,
            "error_detail": error_detail,
        }

    async def _wrapped_otp_callback() -> str:
        _step("awaiting_whatsapp_otp")
        otp = await otp_callback()
        if not otp:
            raise PaymentError("WhatsApp OTP 未在期望时间内返回(otp_callback 返回空值)")
        return otp

    try:
        _step("creating_email")
        mailbox = await retry_with_backoff(
            lambda: create_mailbox(),
            max_retries=3,
            description="创建临时邮箱",
        )
        address = str((mailbox or {}).get("email") or "")
        if not address:
            raise MailboxError("邮箱创建成功但返回结果缺少 email 字段")

        async with ChatGPTBot(headless=headless, slow_mo=slow_mo, config=config) as bot:
            _step("signing_up")
            await bot.navigate_to_signup(address)
            await bot.wait_for_verification_page()

            _step("awaiting_email_otp")
            code = await fetch_email_code(mailbox, email_timeout)
            if not code:
                return _result(
                    False,
                    error_type="email_otp_extract_failed",
                    error_detail="邮件已收到但未能自动提取验证码",
                )

            _step("filling_about_you")
            await bot.enter_verification_code(code)
            await bot.fill_about_you(name=real_name, birthdate=birthdate)

            logged_in = await bot.wait_for_login_complete()
            if not logged_in:
                return _result(
                    False,
                    error_type="signup_failed",
                    error_detail="ChatGPT 登录态校验失败(session 无 accessToken)",
                )

            _step("paying_gopay")
            await bot.execute_gopay_payment()
            payment_result = await bot.handle_stripe_checkout(whatsapp_callback=_wrapped_otp_callback)
            if payment_result != "success":
                return _result(
                    False,
                    error_type="payment_failed",
                    error_detail=f"Stripe/Midtrans 流程未成功: {payment_result}",
                )

            _step("setting_password")
            await bot.add_password_login(chatgpt_password)

            _step("cancelling_subscription")
            await bot.cancel_subscription()

        _step("done")
        return _result(True)

    except VerificationTimeout as exc:
        return _result(False, error_type="email_otp_timeout", error_detail=str(exc))
    except MailboxError as exc:
        et = "email_create_failed" if last_step == "creating_email" else "email_poll_failed"
        return _result(False, error_type=et, error_detail=str(exc))
    except SignupFlowError as exc:
        return _result(False, error_type="signup_failed", error_detail=str(exc))
    except PaymentError as exc:
        if last_step == "awaiting_whatsapp_otp":
            return _result(False, error_type="whatsapp_otp_timeout", error_detail=str(exc))
        return _result(False, error_type="payment_failed", error_detail=str(exc))
    except asyncio.CancelledError:
        return _result(False, error_type="cancelled", error_detail="任务被调用方取消")
    finally:
        if mailbox and cleanup_mailbox is not None:
            try:
                await cleanup_mailbox(mailbox)
            except Exception as exc:  # pragma: no cover - best-effort 清理
                logger.warning("清理临时邮箱失败,可忽略: %s", exc)
