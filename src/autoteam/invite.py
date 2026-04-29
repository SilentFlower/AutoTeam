#!/usr/bin/env python3
import autoteam.display  # noqa: F401 — 自动设置虚拟显示器

"""
ChatGPT Team 自动邀请 + 注册工具

完整流程:
1. CloudMail 创建临时邮箱
2. ChatGPT API 发送 Team 邀请
3. CloudMail 收取邀请邮件，提取邀请链接
4. Playwright 打开邀请链接，注册 ChatGPT 账号
5. CloudMail 收取验证码邮件，自动填入
6. 完成注册并加入 workspace

用法:
    python invite.py
"""

import datetime
import logging
import os
import random
import sys
import time

from playwright.sync_api import sync_playwright

from autoteam.chatgpt_api import ChatGPTTeamAPI
from autoteam.config import get_playwright_launch_options
from autoteam.mail_provider import get_mail_client as CloudMailClient

logger = logging.getLogger(__name__)

MAIL_TIMEOUT = int(os.environ.get("MAIL_TIMEOUT", "180"))
SCREENSHOT_DIR = "screenshots"

# about-you 页随机姓名候选(英文常见名,长度短便于通过 OpenAI 校验)
_ABOUT_YOU_FIRST_NAMES = (
    "Alex", "Jamie", "Sam", "Taylor", "Jordan", "Morgan", "Casey",
    "Riley", "Chris", "Pat", "Robin", "Avery", "Drew", "Quinn",
)
_ABOUT_YOU_LAST_NAMES = (
    "Smith", "Johnson", "Lee", "Brown", "Garcia", "Miller", "Davis",
    "Wilson", "Moore", "Taylor", "Anderson", "Clark", "Walker",
)


def _random_about_you_profile():
    """随机生成 about-you 页所需的姓名 + 生日 + 年龄。

    生日年份固定在 1985-2000 之间(对应当前 2026 年的 26-41 岁),
    避开 18 岁红线;day 限制在 1-28 避免月末日期对各月份的差异。

    :return: (name, year, month, day, age) 五元组,字符串/整数混合
    """
    first = random.choice(_ABOUT_YOU_FIRST_NAMES)
    last = random.choice(_ABOUT_YOU_LAST_NAMES)
    year = random.randint(1985, 2000)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    age = 2026 - year
    return f"{first} {last}", year, month, day, age


def screenshot(page, name):
    """保存当前页面截图到 ``screenshots/`` 目录,文件名前自动拼时间戳。

    时间戳精度到毫秒(``YYYYMMDD-HHMMSS-fff``),保证同一秒内多次截图也不会
    互相覆盖,便于事后回溯失败现场。截图本身失败时降级为 warning,绝不
    影响主流程(失败现场更重要的是日志,截图是 best-effort)。
    """
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + f"{datetime.datetime.now().microsecond // 1000:03d}"
    path = f"{SCREENSHOT_DIR}/{ts}_{name}"
    try:
        page.screenshot(path=path, full_page=True)
        logger.debug("[截图] %s", path)
    except Exception as exc:
        logger.warning("[截图] 保存失败 %s: %s", path, exc)


def find_and_click(page, selectors, label="元素", timeout=3000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout):
                logger.debug("[注册] 找到%s: %s", label, sel)
                loc.click()
                return True
        except Exception:
            continue
    return False


def find_visible(page, selectors, label="元素", timeout=3000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout):
                logger.debug("[注册] 找到%s: %s", label, sel)
                return loc
        except Exception:
            continue
    return None


def wait_for_cloudflare(page, max_wait=60):
    for i in range(max_wait // 5):
        html = page.content()[:2000].lower()
        if "verify you are human" not in html and "challenge" not in page.url:
            return True
        logger.info("[注册] 等待 Cloudflare... (%ds)", i * 5)
        time.sleep(5)
    return False


def register_with_invite(page, invite_link, email, mail_client, password=None):
    """用邀请链接注册 ChatGPT 账号并加入 workspace，返回 (success, password)"""

    logger.info("[注册] 打开邀请链接...")
    page.goto(invite_link, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    wait_for_cloudflare(page)
    screenshot(page, "reg_01_invite_page.png")
    logger.info("[注册] 当前 URL: %s", page.url)

    # 可能需要点击 Sign up
    find_and_click(
        page,
        [
            'button:has-text("Sign up")',
            'a:has-text("Sign up")',
            'button:has-text("Create account")',
            'a:has-text("Create account")',
            'button:has-text("注册")',
        ],
        "注册按钮",
        timeout=5000,
    )
    time.sleep(3)
    screenshot(page, "reg_02_signup.png")

    # 输入邮箱
    logger.info("[注册] 输入邮箱: %s", email)
    email_input = find_visible(
        page,
        [
            'input[name="email"]',
            'input[type="email"]',
            'input[placeholder*="email" i]',
            'input[id="email"]',
            "#email-input",
            'input[autocomplete="email"]',
        ],
        "邮箱输入框",
    )

    if email_input:
        email_input.fill(email)
        time.sleep(1)

        # 点击 Continue
        find_and_click(
            page,
            [
                'button:has-text("Continue")',
                'button:has-text("继续")',
                'button[type="submit"]',
            ],
            "继续按钮",
        )
        time.sleep(5)
        screenshot(page, "reg_03_after_email.png")
    else:
        logger.info("[注册] 未找到邮箱输入框，可能页面已自动填入")
        screenshot(page, "reg_03_no_email_input.png")

    # 可能需要输入密码（注册流程）
    pwd_input = find_visible(
        page,
        [
            'input[name="password"]',
            'input[type="password"]',
            'input[id="password"]',
        ],
        "密码输入框",
        timeout=5000,
    )

    if pwd_input:
        if not password:
            import uuid

            password = f"Tmp_{uuid.uuid4().hex[:12]}!"
        logger.info("[注册] 设置密码: %s", password)
        pwd_input.fill(password)
        time.sleep(1)

        find_and_click(
            page,
            [
                'button:has-text("Continue")',
                'button:has-text("继续")',
                'button[type="submit"]',
            ],
            "继续按钮",
        )
        time.sleep(5)
        screenshot(page, "reg_04_after_password.png")

    # 等待验证码邮件
    logger.info("[注册] 等待 ChatGPT 发送验证码到 %s...", email)
    verification_code = None
    try:
        # 搜索来自 OpenAI 的验证码邮件（不是邀请邮件）
        start = time.time()
        while time.time() - start < MAIL_TIMEOUT:
            emails = mail_client.search_emails_by_recipient(email, size=10)
            for em in emails:
                subject = em.get("subject", "").lower()
                sender = em.get("sendEmail", "").lower()
                # 跳过邀请邮件，只要验证码邮件
                if "invited" in subject or "invitation" in subject:
                    continue
                if "openai" in sender or "chatgpt" in sender:
                    verification_code = mail_client.extract_verification_code(em)
                    if verification_code:
                        logger.info("[CloudMail] 收到验证码: %s", verification_code)
                        break
            if verification_code:
                break
            elapsed = int(time.time() - start)
            print(f"\r[CloudMail] 等待验证码... ({elapsed}s)", end="", flush=True)
            time.sleep(3)
    except Exception as e:
        logger.error("[注册] 等待验证码异常: %s", e)

    if not verification_code:
        logger.warning("[注册] 未自动获取到验证码")
        screenshot(page, "reg_05_no_code.png")
        return False, password

    # 输入验证码
    logger.info("[注册] 输入验证码: %s", verification_code)
    screenshot(page, "reg_05_before_code.png")

    # 检查是否是多个单字符输入框
    single_inputs = page.locator('input[maxlength="1"]').all()
    if len(single_inputs) >= 4:
        logger.debug("[注册] 检测到 %d 个单字符输入框", len(single_inputs))
        for i, char in enumerate(verification_code):
            if i < len(single_inputs):
                single_inputs[i].fill(char)
                time.sleep(0.2)
    else:
        code_input = find_visible(
            page,
            [
                'input[name="code"]',
                'input[placeholder*="code" i]',
                'input[placeholder*="验证" i]',
                'input[type="text"]',
                'input[inputmode="numeric"]',
            ],
            "验证码输入框",
        )
        if code_input:
            code_input.fill(verification_code)
        else:
            logger.warning("[注册] 未找到验证码输入框")
            screenshot(page, "reg_05_no_code_input.png")
            return False, password

    time.sleep(1)

    # 点击确认
    find_and_click(
        page,
        [
            'button:has-text("Continue")',
            'button:has-text("Verify")',
            'button:has-text("Submit")',
            'button[type="submit"]',
        ],
        "确认按钮",
    )

    time.sleep(8)
    screenshot(page, "reg_06_after_code.png")
    logger.info("[注册] 当前 URL: %s", page.url)

    # 填写个人信息（全名 + 生日/年龄）
    name_input = find_visible(
        page,
        [
            'input[name="name"]',
            'input[placeholder*="name" i]',
            'input[id="name"]',
            'input[placeholder*="全名" i]',
        ],
        "名字输入框",
        timeout=5000,
    )

    if name_input:
        name_input.fill("User")
        time.sleep(0.5)

    # 自适应：生日日期（spinbutton）或年龄（普通 input）
    filled_age = False
    spinbuttons = page.locator('[role="spinbutton"]').all()
    if len(spinbuttons) >= 3:
        # 类型 A：React Aria DateField（年/月/日 spinbutton）
        try:
            page.locator("text=生日日期").click()
            time.sleep(0.5)
        except Exception:
            pass
        for sb, val in zip(spinbuttons[:3], ["1995", "06", "15"]):
            sb.click(force=True)
            time.sleep(0.2)
            page.keyboard.type(val, delay=80)
            time.sleep(0.3)
        logger.info("[注册] 填入生日: 1995/06/15 (spinbutton)")
        filled_age = True
    else:
        # 类型 B：普通年龄数字输入框
        age_input = find_visible(
            page,
            [
                'input[name="age"]',
                'input[id="age"]',
                'input[placeholder*="age" i]',
                'input[placeholder*="年龄" i]',
                'input[type="number"]',
            ],
            "年龄输入框",
            timeout=3000,
        )
        if age_input:
            age_input.fill("25")
            logger.info("[注册] 填入年龄: 25")
            filled_age = True

    if name_input or filled_age:
        find_and_click(
            page,
            [
                'button:has-text("完成帐户创建")',
                'button:has-text("Complete")',
                'button:has-text("Continue")',
                'button:has-text("Agree")',
                'button[type="submit"]',
            ],
            "完成按钮",
        )
        time.sleep(8)
        screenshot(page, "reg_07_after_profile.png")

    # 可能需要接受条款 / 加入 workspace
    find_and_click(
        page,
        [
            'button:has-text("Accept")',
            'button:has-text("Agree")',
            'button:has-text("Join")',
            'button:has-text("Join workspace")',
            'button:has-text("加入")',
            'button:has-text("Accept invite")',
        ],
        "加入/接受按钮",
        timeout=5000,
    )
    time.sleep(5)
    screenshot(page, "reg_08_final.png")

    # 检查结果
    current_url = page.url
    page_text = page.inner_text("body")[:500].lower()

    if "chatgpt.com" in current_url and "auth" not in current_url:
        logger.info("[注册] 注册成功并已加入 workspace!")
        return True, password
    elif "workspace" in page_text or "welcome" in page_text:
        logger.info("[注册] 已加入 workspace!")
        return True, password
    else:
        logger.warning("[注册] 注册流程可能未完成，请查看截图")
        return False, password


def login_with_invite(page, invite_link, email, mail_client, password):
    """通过邀请链接以"邮箱 + 密码 + OTP"方式接受邀请并加入 workspace。

    OpenAI 邀请加号实测流程:
    1. 邀请链接落地 ``chatgpt.com/auth/login``,先点击"Log in/登录"按钮跳转到
       ``auth.openai.com/log-in-or-create-account``;
    2. 在 OpenAI 登录页填邮箱、点击底部黑色"Continue"主按钮(必须避开顶部
       "Continue with Google/Apple/Microsoft/phone"四个第三方按钮);
    3. OpenAI 检测到邮箱未注册,弹出"设置密码"输入框,填入预先生成的密码;
    4. 等邮件 OTP 一次性验证码,填入并提交;
    5. 跳转到 chatgpt.com 内的 workspace,判定成功。

    密码由调用方预先生成并通过参数传入,目的是与 manager.py 持久化层共享同一密码,
    后续 Codex OAuth 直接复用,避免再走 OTP 路径。

    :param page: Playwright Page 对象(外部负责开/关 browser/context)
    :param invite_link: 邀请邮件中提取出的邀请链接
    :param email: 临时邮箱地址(与邀请链接对应)
    :param mail_client: CloudMailClient 实例,用于自动收取 OTP 邮件
    :param password: 预生成的密码,用于在"设置密码"步骤填入
    :return: True 表示成功加入 workspace;False 表示流程中断
    """
    # 复用 codex_auth 已经验证过的"主按钮精确点击 + Google 跳转检测"
    from autoteam.codex_auth import _click_primary_auth_button, _is_google_redirect

    logger.info("[邀请登录] 打开邀请链接...")
    page.goto(invite_link, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    wait_for_cloudflare(page)
    screenshot(page, "inv_login_01_invite_page.png")
    logger.info("[邀请登录] 当前 URL: %s", page.url)

    # Step 0: 邀请链接落地 chatgpt.com/auth/login(ChatGPT 自家入口)时,先点"Log in/登录"按钮
    # 跳到 auth.openai.com。这页只有 ChatGPT 自家主登录按钮、没有第三方,has-text 是安全的。
    if "chatgpt.com" in (page.url or ""):
        clicked_chatgpt_login = find_and_click(
            page,
            [
                'button:has-text("Log in")',
                'button:has-text("登录")',
                'a:has-text("Log in")',
                'a:has-text("登录")',
            ],
            "ChatGPT 登录按钮",
            timeout=4000,
        )
        if clicked_chatgpt_login:
            logger.info("[邀请登录] 已点击 ChatGPT 登录按钮,等待跳转到 auth.openai.com...")
            try:
                page.wait_for_url("**auth.openai.com/**", timeout=15000)
            except Exception:
                # 没有真正跳 URL 也可能是 SPA 内部状态切换,继续往下走、靠选择器探测
                pass
            time.sleep(3)
            screenshot(page, "inv_login_01b_after_chatgpt_login.png")
            logger.info("[邀请登录] 跳转后 URL: %s", page.url)

    # Step 1: 在 auth.openai.com 上填邮箱
    logger.info("[邀请登录] 输入邮箱: %s", email)
    email_input = find_visible(
        page,
        [
            'input[name="email"]',
            'input[type="email"]',
            'input[id="email"]',
            'input[autocomplete="email"]',
            'input[autocomplete="username"]',
            'input[placeholder*="Email" i]',
            'input[placeholder*="邮箱" i]',
        ],
        "邮箱输入框",
        timeout=10000,
    )
    if not email_input:
        logger.error("[邀请登录] 未找到邮箱输入框(可能仍卡在 chatgpt.com 入口)")
        screenshot(page, "inv_login_02_no_email.png")
        return False

    email_input.fill(email)
    time.sleep(0.5)

    # 精确正则匹配 ^Continue$/^继续$ 主按钮,避开 "Continue with Google/Apple/Microsoft/phone"
    if not _click_primary_auth_button(page, email_input, ["Continue", "继续"]):
        logger.error("[邀请登录] 未能点击 Continue 主按钮")
        screenshot(page, "inv_login_02_no_continue.png")
        return False
    time.sleep(5)
    screenshot(page, "inv_login_03_after_email.png")

    # 检测是否被误带到 Google/第三方登录(理论上前面已避免,这里兜底)
    if _is_google_redirect(page):
        logger.error("[邀请登录] 邮箱步骤后被带到 Google 登录: %s", page.url)
        screenshot(page, "inv_login_03b_google_redirect.png")
        return False

    # Step 2: 出现密码框,填入预生成的密码(OpenAI 邀请加号在此设置初始密码)
    pwd_input = find_visible(
        page,
        [
            'input[name="password"]',
            'input[type="password"]',
        ],
        "密码输入框",
        timeout=8000,
    )
    if not pwd_input:
        logger.error("[邀请登录] 提交邮箱后未出现密码框,放弃: url=%s", page.url)
        screenshot(page, "inv_login_03c_no_password.png")
        return False

    logger.info("[邀请登录] 填入密码")
    pwd_input.fill(password)
    time.sleep(0.5)
    if not _click_primary_auth_button(page, pwd_input, ["Continue", "继续", "Log in"]):
        logger.error("[邀请登录] 未能点击密码后的 Continue")
        screenshot(page, "inv_login_03d_no_continue_after_pwd.png")
        return False
    time.sleep(5)
    screenshot(page, "inv_login_03e_after_password.png")

    if _is_google_redirect(page):
        logger.error("[邀请登录] 密码步骤后被带到 Google 登录: %s", page.url)
        return False

    # Step 3: 等待 OTP 邮件(跳过邀请邮件本身)
    logger.info("[邀请登录] 等待 OTP 邮件到 %s ...", email)
    otp_code = None
    try:
        start = time.time()
        while time.time() - start < MAIL_TIMEOUT:
            for em in mail_client.search_emails_by_recipient(email, size=10):
                subject = em.get("subject", "").lower()
                sender = em.get("sendEmail", "").lower()
                if "invited" in subject or "invitation" in subject:
                    continue
                if "openai" in sender or "chatgpt" in sender:
                    otp_code = mail_client.extract_verification_code(em)
                    if otp_code:
                        logger.info("[邀请登录] 收到 OTP: %s", otp_code)
                        break
            if otp_code:
                break
            elapsed = int(time.time() - start)
            print(f"\r[邀请登录] 等待 OTP... ({elapsed}s)", end="", flush=True)
            time.sleep(3)
    except Exception as e:
        logger.error("[邀请登录] 等待 OTP 异常: %s", e)

    if not otp_code:
        logger.warning("[邀请登录] 未自动获取到 OTP")
        screenshot(page, "inv_login_04_no_otp.png")
        return False

    # Step 4: 填写 OTP(兼容多个单字符输入框 / 单一输入框)
    single_inputs = page.locator('input[maxlength="1"]').all()
    code_field = None
    if len(single_inputs) >= 4:
        for i, char in enumerate(otp_code):
            if i < len(single_inputs):
                single_inputs[i].fill(char)
                time.sleep(0.2)
        code_field = single_inputs[0]
    else:
        code_field = find_visible(
            page,
            [
                'input[name="code"]',
                'input[autocomplete="one-time-code"]',
                'input[inputmode="numeric"]',
                'input[placeholder*="code" i]',
                'input[placeholder*="验证" i]',
            ],
            "验证码输入框",
        )
        if code_field:
            code_field.fill(otp_code)
        else:
            logger.warning("[邀请登录] 未找到验证码输入框")
            screenshot(page, "inv_login_04b_no_code_input.png")
            return False
    time.sleep(1)

    # 用精确正则点确认按钮,避开 "Continue with Google" 等
    if not _click_primary_auth_button(page, code_field, ["Continue", "继续", "Verify", "Submit"]):
        logger.warning("[邀请登录] 未能点击验证码确认按钮")
    time.sleep(8)
    screenshot(page, "inv_login_05_after_otp.png")
    logger.info("[邀请登录] OTP 验证后 URL: %s", page.url)

    # Step 4.5: OpenAI 在 OTP 之后会跳到 about-you 要求填姓名 + 生日/年龄,与正常注册一致;
    # 不填这一步会卡在 auth.openai.com/about-you,无法 redirect 进 chatgpt.com 主域。
    if "about-you" in (page.url or ""):
        about_name, about_year, about_month, about_day, about_age = _random_about_you_profile()
        logger.info(
            "[邀请登录] 检测到 about-you 页,随机资料: name=%s birth=%04d-%02d-%02d age=%d",
            about_name, about_year, about_month, about_day, about_age,
        )
        name_input = find_visible(
            page,
            [
                'input[name="name"]',
                'input[id="name"]',
                'input[placeholder*="name" i]',
                'input[placeholder*="全名" i]',
            ],
            "名字输入框",
            timeout=5000,
        )
        if name_input:
            name_input.fill(about_name)
            time.sleep(0.3)

        # 自适应:生日日期(spinbutton 三栏 年/月/日) 或 普通年龄数字输入
        filled_birth = False
        spinbuttons = page.locator('[role="spinbutton"]').all()
        if len(spinbuttons) >= 3:
            try:
                page.locator("text=生日日期").click()
                time.sleep(0.3)
            except Exception:
                pass
            spin_values = [str(about_year), f"{about_month:02d}", f"{about_day:02d}"]
            for sb, val in zip(spinbuttons[:3], spin_values):
                sb.click(force=True)
                time.sleep(0.2)
                page.keyboard.type(val, delay=80)
                time.sleep(0.3)
            filled_birth = True
        else:
            age_input = find_visible(
                page,
                [
                    'input[name="age"]',
                    'input[id="age"]',
                    'input[type="number"]',
                    'input[placeholder*="age" i]',
                    'input[placeholder*="年龄" i]',
                ],
                "年龄输入框",
                timeout=3000,
            )
            if age_input:
                age_input.fill(str(about_age))
                filled_birth = True

        if name_input or filled_birth:
            # 主按钮文案可能是"完成帐户创建 / Complete / Continue / Agree"
            anchor = name_input
            if anchor is None:
                # 用任意可见的输入控件作为表单 anchor
                try:
                    anchor = page.locator('input[name="age"], [role="spinbutton"]').first
                except Exception:
                    anchor = None
            if anchor is not None:
                _click_primary_auth_button(
                    page,
                    anchor,
                    ["完成帐户创建", "Complete", "Continue", "继续", "Agree"],
                )
            time.sleep(8)
            screenshot(page, "inv_login_05b_after_about_you.png")
            logger.info("[邀请登录] about-you 提交后 URL: %s", page.url)

    # Step 5: 接受邀请 / 加入 workspace(可能已自动加入)
    find_and_click(
        page,
        [
            'button:has-text("Accept invite")',
            'button:has-text("Accept")',
            'button:has-text("Agree")',
            'button:has-text("Join workspace")',
            'button:has-text("Join")',
            'button:has-text("加入")',
            'button:has-text("接受")',
        ],
        "加入/接受按钮",
        timeout=5000,
    )
    time.sleep(5)
    screenshot(page, "inv_login_06_final.png")

    # Step 6: 判定成功
    current_url = page.url
    try:
        page_text = page.inner_text("body")[:500].lower()
    except Exception:
        page_text = ""

    if "chatgpt.com" in current_url and "auth" not in current_url:
        logger.info("[邀请登录] 已加入 workspace!")
        return True
    if "workspace" in page_text or "welcome" in page_text:
        logger.info("[邀请登录] 已加入 workspace!")
        return True

    logger.warning("[邀请登录] 流程可能未完成,请查看截图")
    return False


def run():
    mail_client = None
    account_id = None
    chatgpt = None

    try:
        # Step 1: 创建临时邮箱
        mail_client = CloudMailClient()
        mail_client.login()
        account_id, email = mail_client.create_temp_email()
        logger.info("[邀请] 临时邮箱: %s", email)

        # Step 2: 发送 Team 邀请
        chatgpt = ChatGPTTeamAPI()
        chatgpt.start()
        status, data = chatgpt.invite_member(email)

        if status != 200:
            logger.error("[邀请] 邀请失败 (HTTP %d)", status)
            return False
        logger.info("[邀请] 邀请已发送")

        # Step 3: 等待邀请邮件
        logger.info("[邀请] 等待邀请邮件...")
        invite_link = None
        try:
            email_data = mail_client.wait_for_email(
                to_email=email,
                timeout=MAIL_TIMEOUT,
                sender_keyword="openai",
            )
            invite_link = mail_client.extract_invite_link(email_data)
        except TimeoutError:
            logger.error("[邀请] 等待邀请邮件超时")
        except Exception as e:
            logger.error("[邀请] 获取邀请邮件失败: %s", e)

        if not invite_link:
            logger.error("[邀请] 未获取到邀请链接")
            return False

        logger.info("[邀请] 邀请链接: %s", invite_link)

        # Step 4: 关闭 ChatGPT API 浏览器，开新浏览器做注册
        chatgpt.stop()
        chatgpt = None

        logger.info("[邀请] 开始注册 ChatGPT 账号")

        with sync_playwright() as p:
            browser = p.chromium.launch(**get_playwright_launch_options())
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            result, pwd = register_with_invite(page, invite_link, email, mail_client)

            screenshot(page, "final.png")
            browser.close()

        if result:
            logger.info("[邀请] %s 已注册并加入 ChatGPT Team", email)
        else:
            logger.error("[邀请] 流程未完成，请查看 screenshots/ 目录")

        return result

    finally:
        if chatgpt:
            chatgpt.stop()
        # 不删除临时邮箱，保留账号
        if mail_client and account_id:
            logger.info("[邀请] 临时邮箱保留: %s (accountId=%s)", email, account_id)


def main():
    logger.info("ChatGPT Team 自动邀请 + 注册工具")
    result = run()
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
