"""配置文件 - 从 .env 文件或环境变量加载"""

import os
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from autoteam.textio import parse_env_line, parse_env_value, read_text

# 项目根目录（pyproject.toml 所在位置）
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 加载 .env 文件（从项目根目录）
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    for line in read_text(_env_file).splitlines():
        parsed = parse_env_line(line)
        if parsed:
            key, value = parsed
            os.environ.setdefault(key, value)


def _get_int_env(name: str, default: int) -> int:
    return int(parse_env_value(os.environ.get(name, str(default))))


def _get_float_env(name: str, default: float) -> float:
    return float(parse_env_value(os.environ.get(name, str(default))))


def _get_bool_env(name: str, default: bool) -> bool:
    raw = parse_env_value(os.environ.get(name, ""))
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _get_str_env(name: str, default: str = "") -> str:
    value = parse_env_value(os.environ.get(name, default))
    return str(value).strip()


def _normalize_sub2api_ws_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"off", "ctx_pool", "passthrough"}:
        return mode
    return "off"


def _normalize_chatgpt_api_transport(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"auto", "playwright", "curl_cffi"}:
        return mode
    return "auto"


# CloudMail 配置
CLOUDMAIL_BASE_URL = os.environ.get("CLOUDMAIL_BASE_URL", "")
CLOUDMAIL_EMAIL = os.environ.get("CLOUDMAIL_EMAIL", "")
CLOUDMAIL_PASSWORD = os.environ.get("CLOUDMAIL_PASSWORD", "")
CLOUDMAIL_DOMAIN = os.environ.get("CLOUDMAIL_DOMAIN", "")

# 邮箱提供者配置
MAIL_PROVIDER = os.environ.get("MAIL_PROVIDER", "cloudmail").strip().lower() or "cloudmail"

# Cloudflare Temp Email 配置
CF_TEMP_EMAIL_BASE_URL = os.environ.get("CF_TEMP_EMAIL_BASE_URL", "")
CF_TEMP_EMAIL_ADMIN_PASSWORD = os.environ.get("CF_TEMP_EMAIL_ADMIN_PASSWORD", "")
CF_TEMP_EMAIL_DOMAIN = os.environ.get("CF_TEMP_EMAIL_DOMAIN", "")

# ChatGPT Team 配置
CHATGPT_ACCOUNT_ID = os.environ.get("CHATGPT_ACCOUNT_ID", "")

# Sub2API 配置
SUB2API_URL = os.environ.get("SUB2API_URL", "")
SUB2API_EMAIL = os.environ.get("SUB2API_EMAIL", "")
SUB2API_PASSWORD = os.environ.get("SUB2API_PASSWORD", "")
SUB2API_GROUP = os.environ.get("SUB2API_GROUP", "")
SUB2API_PROXY = _get_str_env("SUB2API_PROXY", "")
SUB2API_CONCURRENCY = _get_int_env("SUB2API_CONCURRENCY", 10)
SUB2API_PRIORITY = _get_int_env("SUB2API_PRIORITY", 1)
SUB2API_RATE_MULTIPLIER = _get_float_env("SUB2API_RATE_MULTIPLIER", 1)
SUB2API_AUTO_PAUSE_ON_EXPIRED = _get_bool_env("SUB2API_AUTO_PAUSE_ON_EXPIRED", True)
SUB2API_MODEL_WHITELIST = _get_str_env("SUB2API_MODEL_WHITELIST", "")
SUB2API_OPENAI_WS_MODE = _normalize_sub2api_ws_mode(_get_str_env("SUB2API_OPENAI_WS_MODE", "off"))
SUB2API_OPENAI_PASSTHROUGH = _get_bool_env("SUB2API_OPENAI_PASSTHROUGH", False)
SUB2API_OVERWRITE_ACCOUNT_SETTINGS = _get_bool_env("SUB2API_OVERWRITE_ACCOUNT_SETTINGS", False)

# 轮询邮件间隔/超时（秒）
EMAIL_POLL_INTERVAL = _get_int_env("EMAIL_POLL_INTERVAL", 3)
EMAIL_POLL_TIMEOUT = _get_int_env("EMAIL_POLL_TIMEOUT", 300)

# API 鉴权（不设置则不启用）
API_KEY = os.environ.get("API_KEY", "")

# 自动巡检配置
AUTO_CHECK_INTERVAL = _get_int_env("AUTO_CHECK_INTERVAL", 300)  # 巡检间隔（秒），默认 5 分钟
AUTO_CHECK_THRESHOLD = _get_int_env("AUTO_CHECK_THRESHOLD", 10)  # 额度低于此百分比触发轮转，默认 10%
AUTO_CHECK_MIN_LOW = _get_int_env("AUTO_CHECK_MIN_LOW", 2)  # 至少几个账号低于阈值才触发，默认 2

# HeroSMS 接码服务配置（兼容 SMS-Activate 协议）
# 仅在需要自动通过 OpenAI 手机号验证页时使用；未填 API_KEY 则功能关闭
HERO_SMS_BASE_URL = os.environ.get("HERO_SMS_BASE_URL", "https://hero-sms.com/stubs/handler_api.php").strip()
HERO_SMS_API_KEY = os.environ.get("HERO_SMS_API_KEY", "").strip()
# hero-sms 上 OpenAI 的服务代码是 ``dr``(不是 SMS-Activate 行业惯例的 ``oai``)
HERO_SMS_SERVICE = os.environ.get("HERO_SMS_SERVICE", "dr").strip() or "dr"
# hero-sms 自家国家 ID 与 SMS-Activate 标准不同; 187=USA、0=Russia、3=China
HERO_SMS_COUNTRY = os.environ.get("HERO_SMS_COUNTRY", "187").strip() or "187"
HERO_SMS_OPERATOR = os.environ.get("HERO_SMS_OPERATOR", "").strip()
HERO_SMS_MAX_PRICE = _get_float_env("HERO_SMS_MAX_PRICE", 0)
HERO_SMS_HTTP_TIMEOUT = _get_int_env("HERO_SMS_HTTP_TIMEOUT", 30)
HERO_SMS_WAIT_SECONDS = _get_int_env("HERO_SMS_WAIT_SECONDS", 180)
# 一个号码最多被复用多少次成功验证后停止复用(0 表示不限)
HERO_SMS_PHONE_REUSE_MAX = _get_int_env("HERO_SMS_PHONE_REUSE_MAX", 3)
# 强制每次都申请新号码(关闭号码复用), 用于已知缓存号码已废、想立刻换号的情况
HERO_SMS_FORCE_NEW_PHONE = _get_bool_env("HERO_SMS_FORCE_NEW_PHONE", False)

# Playwright 代理配置
PLAYWRIGHT_PROXY_URL = os.environ.get("PLAYWRIGHT_PROXY_URL", "").strip()
PLAYWRIGHT_PROXY_SERVER = os.environ.get("PLAYWRIGHT_PROXY_SERVER", "").strip()
PLAYWRIGHT_PROXY_USERNAME = os.environ.get("PLAYWRIGHT_PROXY_USERNAME", "").strip()
PLAYWRIGHT_PROXY_PASSWORD = os.environ.get("PLAYWRIGHT_PROXY_PASSWORD", "").strip()
PLAYWRIGHT_PROXY_BYPASS = os.environ.get("PLAYWRIGHT_PROXY_BYPASS", "").strip()


def _format_proxy_host(hostname: str) -> str:
    if ":" in hostname and not hostname.startswith("["):
        return f"[{hostname}]"
    return hostname


def _parse_proxy_url(proxy_url: str):
    if "://" not in proxy_url:
        return {"server": proxy_url}

    parsed = urlsplit(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return {"server": proxy_url}

    host = _format_proxy_host(parsed.hostname)
    server = f"{parsed.scheme}://{host}"
    if parsed.port:
        server = f"{server}:{parsed.port}"

    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def get_chatgpt_api_transport() -> str:
    return _normalize_chatgpt_api_transport(_get_str_env("CHATGPT_API_TRANSPORT", "auto"))


def get_chatgpt_api_http_timeout() -> int:
    return max(5, _get_int_env("CHATGPT_API_HTTP_TIMEOUT", 60))


def get_chatgpt_api_impersonate() -> str:
    return _get_str_env("CHATGPT_API_IMPERSONATE", "chrome136") or "chrome136"


def get_chatgpt_http_proxy_url() -> str:
    proxy_url = _get_str_env("PLAYWRIGHT_PROXY_URL", "")
    if proxy_url:
        return proxy_url

    proxy_server = _get_str_env("PLAYWRIGHT_PROXY_SERVER", "")
    if not proxy_server:
        return ""

    username = _get_str_env("PLAYWRIGHT_PROXY_USERNAME", "")
    password = _get_str_env("PLAYWRIGHT_PROXY_PASSWORD", "")
    if not (username or password):
        return proxy_server

    parsed = urlsplit(proxy_server)
    if not parsed.scheme or not parsed.hostname:
        return proxy_server

    host = _format_proxy_host(parsed.hostname)
    auth = quote(username, safe="")
    if password:
        auth = f"{auth}:{quote(password, safe='')}"

    proxy = f"{parsed.scheme}://{auth}@{host}"
    if parsed.port:
        proxy = f"{proxy}:{parsed.port}"
    return proxy


def get_playwright_launch_options():
    """统一的 Playwright Chromium 启动参数。"""
    options = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    }

    proxy = None
    if PLAYWRIGHT_PROXY_URL:
        proxy = _parse_proxy_url(PLAYWRIGHT_PROXY_URL)
    elif PLAYWRIGHT_PROXY_SERVER:
        proxy = {"server": PLAYWRIGHT_PROXY_SERVER}
        if PLAYWRIGHT_PROXY_USERNAME:
            proxy["username"] = PLAYWRIGHT_PROXY_USERNAME
        if PLAYWRIGHT_PROXY_PASSWORD:
            proxy["password"] = PLAYWRIGHT_PROXY_PASSWORD

    if proxy:
        if PLAYWRIGHT_PROXY_BYPASS:
            proxy["bypass"] = PLAYWRIGHT_PROXY_BYPASS
        options["proxy"] = proxy

    return options
