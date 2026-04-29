"""AutoTeam HTTP API - 将 CLI 功能暴露为 HTTP 接口"""

import inspect
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from autoteam import admin_registry
from autoteam.config import API_KEY
from autoteam.textio import parse_env_line, read_text, write_text

logger = logging.getLogger(__name__)

# admin_id 格式：8 位小写十六进制（PRD 决策 #9）。
# 用于 header / body 入参白名单校验，防止路径穿越（如 "../../etc"）。
_ADMIN_ID_PATTERN = re.compile(r"^[0-9a-f]{8}$")


def _validate_admin_id(value: str) -> str:
    """白名单校验 admin_id，不通过抛 HTTPException 400。

    :param value: 待校验 admin_id 字符串。
    :return: 通过校验后的 admin_id。
    :raises HTTPException: 格式不合法时返回 400 中文 detail。
    """
    if not _ADMIN_ID_PATTERN.match(value or ""):
        raise HTTPException(status_code=400, detail="管理员标识格式不正确，请联系管理员重新登录")
    return value


def get_current_admin_id(
    x_autoteam_admin_id: str | None = Header(None, alias="X-Autoteam-Admin-Id"),
) -> str | None:
    """FastAPI 依赖：解析当前请求的 admin_id 上下文。

    优先级：``X-Autoteam-Admin-Id`` header → ``admin_registry`` 的激活 admin →
    ``None``（让数据层走兜底路径，向后兼容旧客户端）。

    :param x_autoteam_admin_id: HTTP header 中携带的 admin_id；无则忽略。
    :return: 通过白名单校验的 admin_id 字符串；无任何上下文时返回 ``None``。
    :raises HTTPException: header 提供了非法格式的 admin_id 时 400。
    """
    if x_autoteam_admin_id:
        return _validate_admin_id(x_autoteam_admin_id.strip())
    try:
        return admin_registry.get_active_admin_id()
    except Exception:
        return None


app = FastAPI(
    title="AutoTeam API",
    description="ChatGPT Team 账号自动轮转管理 API",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# API Key 鉴权中间件
# ---------------------------------------------------------------------------

_AUTH_SKIP_PATHS = {"/api/auth/check", "/api/setup/status", "/api/setup/save"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    try:
        _maybe_reload_runtime_config_from_env_file()
    except Exception as exc:
        logger.warning("[配置] 自动热加载失败: %s", exc)

    path = request.url.path
    # 不鉴权的路径：非 /api 路径、auth/check 端点
    if not path.startswith("/api/") or path in _AUTH_SKIP_PATHS:
        return await call_next(request)
    # 未配置 API_KEY 则跳过鉴权
    if not API_KEY:
        return await call_next(request)
    # 从 header 或 query param 获取 key
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.query_params.get("key", "")
    if token != API_KEY:
        return JSONResponse(status_code=401, content={"detail": "未授权，请提供有效的 API Key"})
    return await call_next(request)


@app.get("/api/auth/check")
def check_auth(request: Request):
    """验证 API Key 是否有效。未配置 API_KEY 时始终返回成功。"""
    if not API_KEY:
        return {"authenticated": True, "auth_required": False}
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == API_KEY:
        return {"authenticated": True, "auth_required": True}
    return JSONResponse(status_code=401, content={"authenticated": False, "auth_required": True})


# ---------------------------------------------------------------------------
# 初始配置 API（无需鉴权）
# ---------------------------------------------------------------------------


class SetupConfig(BaseModel):
    MAIL_PROVIDER: str = "cloudmail"
    CLOUDMAIL_BASE_URL: str = ""
    CLOUDMAIL_EMAIL: str = ""
    CLOUDMAIL_PASSWORD: str = ""
    CLOUDMAIL_DOMAIN: str = ""
    CF_TEMP_EMAIL_BASE_URL: str = ""
    CF_TEMP_EMAIL_ADMIN_PASSWORD: str = ""
    CF_TEMP_EMAIL_DOMAIN: str = ""
    SYNC_TARGET_SUB2API: str | bool = ""
    SUB2API_URL: str = ""
    SUB2API_EMAIL: str = ""
    SUB2API_PASSWORD: str = ""
    SUB2API_GROUP: str = ""
    SUB2API_PROXY: str | int = ""
    SUB2API_CONCURRENCY: str | int = "10"
    SUB2API_PRIORITY: str | int = "1"
    SUB2API_RATE_MULTIPLIER: str | int | float = "1"
    SUB2API_AUTO_PAUSE_ON_EXPIRED: str | bool = "true"
    SUB2API_MODEL_WHITELIST: str = ""
    SUB2API_OPENAI_WS_MODE: str = "off"
    SUB2API_OPENAI_PASSTHROUGH: str | bool = "false"
    SUB2API_OVERWRITE_ACCOUNT_SETTINGS: str | bool = "false"
    PLAYWRIGHT_PROXY_URL: str = ""
    PLAYWRIGHT_PROXY_BYPASS: str = ""
    HERO_SMS_BASE_URL: str = "https://hero-sms.com/stubs/handler_api.php"
    HERO_SMS_API_KEY: str = ""
    HERO_SMS_SERVICE: str = "dr"
    HERO_SMS_COUNTRY: str = "187"
    HERO_SMS_OPERATOR: str = ""
    HERO_SMS_MAX_PRICE: str | int | float = "0"
    HERO_SMS_HTTP_TIMEOUT: str | int = "30"
    HERO_SMS_WAIT_SECONDS: str | int = "180"
    HERO_SMS_PHONE_REUSE_MAX: str | int = "3"
    HERO_SMS_FORCE_NEW_PHONE: str | bool = "false"
    API_KEY: str = ""


class SourceConfig(BaseModel):
    content: str = ""


_RUNTIME_CONFIG_CLEARABLE_FIELDS = {
    "SUB2API_GROUP",
    "SUB2API_PROXY",
    "SUB2API_MODEL_WHITELIST",
    "PLAYWRIGHT_PROXY_URL",
    "PLAYWRIGHT_PROXY_BYPASS",
    "HERO_SMS_API_KEY",
    "HERO_SMS_OPERATOR",
}

_CLOUDMAIL_REQUIRED_KEYS = ("CLOUDMAIL_BASE_URL", "CLOUDMAIL_EMAIL", "CLOUDMAIL_PASSWORD", "CLOUDMAIL_DOMAIN")
_CF_TEMP_EMAIL_REQUIRED_KEYS = (
    "CF_TEMP_EMAIL_BASE_URL",
    "CF_TEMP_EMAIL_ADMIN_PASSWORD",
    "CF_TEMP_EMAIL_DOMAIN",
)
_SUB2API_REQUIRED_KEYS = ("SUB2API_URL", "SUB2API_EMAIL", "SUB2API_PASSWORD")
_SYNC_TARGET_TOGGLE_KEYS = ("SYNC_TARGET_SUB2API",)

_ALL_RUNTIME_ENV_KEYS = [
    "MAIL_PROVIDER",
    "CLOUDMAIL_BASE_URL",
    "CLOUDMAIL_EMAIL",
    "CLOUDMAIL_PASSWORD",
    "CLOUDMAIL_DOMAIN",
    "CF_TEMP_EMAIL_BASE_URL",
    "CF_TEMP_EMAIL_ADMIN_PASSWORD",
    "CF_TEMP_EMAIL_DOMAIN",
    "CHATGPT_ACCOUNT_ID",
    "SYNC_TARGET_SUB2API",
    "SUB2API_URL",
    "SUB2API_EMAIL",
    "SUB2API_PASSWORD",
    "SUB2API_GROUP",
    "SUB2API_PROXY",
    "SUB2API_CONCURRENCY",
    "SUB2API_PRIORITY",
    "SUB2API_RATE_MULTIPLIER",
    "SUB2API_AUTO_PAUSE_ON_EXPIRED",
    "SUB2API_MODEL_WHITELIST",
    "SUB2API_OPENAI_WS_MODE",
    "SUB2API_OPENAI_PASSTHROUGH",
    "SUB2API_OVERWRITE_ACCOUNT_SETTINGS",
    "EMAIL_POLL_INTERVAL",
    "EMAIL_POLL_TIMEOUT",
    "API_KEY",
    "AUTO_CHECK_INTERVAL",
    "AUTO_CHECK_THRESHOLD",
    "AUTO_CHECK_MIN_LOW",
    "PLAYWRIGHT_PROXY_URL",
    "PLAYWRIGHT_PROXY_SERVER",
    "PLAYWRIGHT_PROXY_USERNAME",
    "PLAYWRIGHT_PROXY_PASSWORD",
    "PLAYWRIGHT_PROXY_BYPASS",
    "HERO_SMS_BASE_URL",
    "HERO_SMS_API_KEY",
    "HERO_SMS_SERVICE",
    "HERO_SMS_COUNTRY",
    "HERO_SMS_OPERATOR",
    "HERO_SMS_MAX_PRICE",
    "HERO_SMS_HTTP_TIMEOUT",
    "HERO_SMS_WAIT_SECONDS",
    "HERO_SMS_PHONE_REUSE_MAX",
    "HERO_SMS_FORCE_NEW_PHONE",
]
_RUNTIME_ENV_BASE = {key: os.environ.get(key) for key in _ALL_RUNTIME_ENV_KEYS}
_runtime_env_reload_lock = threading.Lock()
_runtime_env_reload_state = {"signature": None}


def _runtime_config_prompt_map():
    from autoteam.setup_wizard import REQUIRED_CONFIGS

    return {key: prompt for key, prompt, _default, _optional in REQUIRED_CONFIGS}


def _current_runtime_env():
    from autoteam.setup_wizard import _read_env

    env = _read_env()
    merged = {key: value for key, value in os.environ.items()}
    merged.update({key: value for key, value in env.items() if value is not None})
    return merged


def _missing_runtime_configs(keys: tuple[str, ...] | list[str], *, env: dict[str, str] | None = None):
    env_values = env or _current_runtime_env()
    prompt_map = _runtime_config_prompt_map()
    missing = []
    for key in keys:
        value = (env_values.get(key, "") or "").strip()
        if not value:
            missing.append((key, prompt_map.get(key, key)))
    return missing


def _format_missing_runtime_configs(missing: list[tuple[str, str]]) -> str:
    return "、".join(f"{key}（{prompt}）" for key, prompt in missing)


def _effective_sync_target_states(env: dict[str, str] | None = None):
    from autoteam.sync_targets import get_sync_target_states

    return get_sync_target_states(env or _current_runtime_env())


def _runtime_required_keys(env: dict[str, str] | None = None) -> set[str]:
    from autoteam.mail_provider import get_mail_provider_name, get_mail_provider_required_keys

    states = _effective_sync_target_states(env)
    provider = get_mail_provider_name(env)
    required = set(get_mail_provider_required_keys(provider))
    required.add("API_KEY")
    if states.get("sub2api"):
        required.update(_SUB2API_REQUIRED_KEYS)
    return required


def _require_runtime_configs(
    keys: tuple[str, ...] | list[str], action_label: str, *, env: dict[str, str] | None = None
):
    missing = _missing_runtime_configs(keys, env=env)
    if not missing:
        return

    detail = _format_missing_runtime_configs(missing)
    raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板填写：{detail}")


def _require_mail_provider_configs(
    action_label: str, *, provider: str | None = None, env: dict[str, str] | None = None
):
    from autoteam.mail_provider import get_mail_provider_name, get_mail_provider_prompt, get_mail_provider_required_keys

    env_values = env or _current_runtime_env()
    resolved_provider = provider or get_mail_provider_name(env_values)
    missing = _missing_runtime_configs(get_mail_provider_required_keys(resolved_provider), env=env_values)
    if not missing:
        return

    detail = _format_missing_runtime_configs(missing)
    provider_label = get_mail_provider_prompt(resolved_provider)
    raise HTTPException(
        status_code=400,
        detail=f"{action_label} 前请先在配置面板填写当前邮箱服务（{provider_label}）配置：{detail}",
    )


def _require_pool_operation_configs(action_label: str):
    from autoteam.sync_targets import get_enabled_sync_targets

    env = _current_runtime_env()
    _require_mail_provider_configs(action_label, env=env)

    enabled_targets = get_enabled_sync_targets(env)
    if not enabled_targets:
        raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板启用 Sub2API 同步目标")

    missing = _missing_runtime_configs(
        [key for target in enabled_targets for key in (_SUB2API_REQUIRED_KEYS if target == "sub2api" else ())],
        env=env,
    )
    if missing:
        detail = _format_missing_runtime_configs(missing)
        raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板填写：{detail}")


def _require_account_mail_configs(account: dict, action_label: str):
    from autoteam.mail_provider import get_account_mail_provider

    provider = get_account_mail_provider(account)
    _require_mail_provider_configs(action_label, provider=provider, env=_current_runtime_env())


def _require_sync_target_configs(action_label: str):
    from autoteam.sync_targets import get_enabled_sync_targets

    env = _current_runtime_env()
    enabled_targets = get_enabled_sync_targets(env)
    if not enabled_targets:
        raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板启用 Sub2API 同步目标")

    missing = _missing_runtime_configs(
        [key for target in enabled_targets for key in (_SUB2API_REQUIRED_KEYS if target == "sub2api" else ())],
        env=env,
    )
    if missing:
        detail = _format_missing_runtime_configs(missing)
        raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板填写：{detail}")


def _collect_config_fields(*, include_values: bool = False, configs=None):
    from autoteam.mail_provider import get_mail_provider_name
    from autoteam.setup_wizard import REQUIRED_CONFIGS, _read_env

    env = _read_env()
    merged_env = dict(os.environ)
    merged_env.update(env)
    target_states = _effective_sync_target_states(merged_env)
    runtime_required_keys = _runtime_required_keys(merged_env)
    mail_provider = get_mail_provider_name(merged_env)
    config_items = configs or REQUIRED_CONFIGS
    fields = []
    all_ok = True
    for key, prompt, default, optional in config_items:
        raw_value = env.get(key, "") or os.environ.get(key, "")
        if key == "SYNC_TARGET_SUB2API":
            raw_value = "true" if target_states.get("sub2api") else "false"
            configured = True
        elif key == "MAIL_PROVIDER":
            raw_value = mail_provider
            configured = True
        else:
            configured = bool(raw_value)
        if not configured and (key in runtime_required_keys or not optional):
            all_ok = False

        field = {
            "key": key,
            "prompt": prompt,
            "default": default,
            "optional": optional,
            "configured": configured,
        }
        if include_values:
            field["value"] = raw_value if raw_value != "" else default
            field["runtime_required"] = key in runtime_required_keys
        fields.append(field)
    return {"configured": all_ok, "fields": fields}


def _reload_runtime_config_modules():
    import importlib

    import autoteam.config

    modules = [autoteam.config]
    for module_name in (
        "autoteam.cloudmail",
        "autoteam.cloudflare_temp_email",
        "autoteam.mail_provider",
        "autoteam.sub2api_sync",
        "autoteam.hero_sms",
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        modules.append(module)

    for module in modules:
        importlib.reload(module)


def _restore_runtime_env(previous_env: dict[str, str | None]):
    for key, previous in previous_env.items():
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def _runtime_env_file_signature():
    from autoteam.setup_wizard import ENV_FILE

    if not ENV_FILE.exists():
        return None
    stat = ENV_FILE.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _read_runtime_env_file_text():
    from autoteam.setup_wizard import ENV_FILE

    if not ENV_FILE.exists():
        return ""
    return read_text(ENV_FILE)


def _read_runtime_source_text():
    from autoteam.setup_wizard import ENV_EXAMPLE, ENV_FILE

    if ENV_FILE.exists():
        return read_text(ENV_FILE), str(ENV_FILE)
    if ENV_EXAMPLE.exists():
        return read_text(ENV_EXAMPLE), str(ENV_FILE)
    return "", str(ENV_FILE)


def _write_runtime_source_text(content: str):
    from autoteam.setup_wizard import ENV_FILE

    write_text(ENV_FILE, content)


def _restore_runtime_source_text(previous_exists: bool, previous_content: str):
    from autoteam.setup_wizard import ENV_FILE

    if previous_exists:
        write_text(ENV_FILE, previous_content)
        return
    if ENV_FILE.exists():
        ENV_FILE.unlink()


def _load_env_values_from_source(content: str, env_keys: list[str]):
    values = {key: "" for key in env_keys}
    for line in content.splitlines():
        parsed = parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        if key in values:
            values[key] = value
    return values


def _load_present_env_values_from_source(content: str, env_keys: list[str]):
    allowed = set(env_keys)
    values = {}
    for line in content.splitlines():
        parsed = parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        if key in allowed:
            values[key] = value
    return values


def _validate_runtime_required_values(values: dict[str, str]):
    from autoteam.setup_wizard import STARTUP_REQUIRED_CONFIGS

    return [
        f"{key} ({prompt})"
        for key, prompt, _default, optional in STARTUP_REQUIRED_CONFIGS
        if not optional and not values.get(key)
    ]


def _parse_bool_text(value: object, *, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    lowered = text.lower()
    if lowered in {"1", "true", "yes", "on", "enabled"}:
        return True
    if lowered in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError(f"无效布尔值: {value}")


def _validate_runtime_optional_values(values: dict[str, str]):
    normalized = dict(values)

    def _normalize_positive_int(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            return
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是正整数") from exc
        if value <= 0:
            raise ValueError(f"{key} 必须是正整数")
        normalized[key] = str(value)

    def _normalize_int(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            return
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是整数") from exc
        normalized[key] = str(value)

    def _normalize_positive_float(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            return
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是大于 0 的数字") from exc
        if value <= 0:
            raise ValueError(f"{key} 必须是大于 0 的数字")
        normalized[key] = format(value, "g")

    def _normalize_bool(key: str):
        try:
            value = _parse_bool_text(normalized.get(key, ""), default=None)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是 true 或 false") from exc
        if value is None:
            return
        normalized[key] = "true" if value else "false"

    def _normalize_sub2api_proxy(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            normalized[key] = ""
            return
        if raw.lstrip("+-").isdigit():
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{key} 必须是 Sub2API 代理 ID（正整数）或代理名称") from exc
            if value <= 0:
                raise ValueError(f"{key} 必须是 Sub2API 代理 ID（正整数）或代理名称")
            normalized[key] = str(value)
            return
        normalized[key] = raw

    _normalize_sub2api_proxy("SUB2API_PROXY")
    _normalize_positive_int("SUB2API_CONCURRENCY")
    _normalize_int("SUB2API_PRIORITY")
    _normalize_positive_float("SUB2API_RATE_MULTIPLIER")
    _normalize_bool("SUB2API_AUTO_PAUSE_ON_EXPIRED")
    _normalize_bool("SUB2API_OPENAI_PASSTHROUGH")
    _normalize_bool("SUB2API_OVERWRITE_ACCOUNT_SETTINGS")

    ws_mode = str(normalized.get("SUB2API_OPENAI_WS_MODE", "") or "").strip().lower()
    if ws_mode:
        if ws_mode not in {"off", "ctx_pool", "passthrough"}:
            raise ValueError("SUB2API_OPENAI_WS_MODE 必须是 off、ctx_pool 或 passthrough")
        normalized["SUB2API_OPENAI_WS_MODE"] = ws_mode

    whitelist = str(normalized.get("SUB2API_MODEL_WHITELIST", "") or "").strip()
    if whitelist:
        normalized["SUB2API_MODEL_WHITELIST"] = ",".join(part.strip() for part in whitelist.split(",") if part.strip())
    else:
        normalized["SUB2API_MODEL_WHITELIST"] = ""

    return normalized


def _sync_runtime_globals():
    global API_KEY

    API_KEY = os.environ.get("API_KEY", "")

    auto_check_config = globals().get("_auto_check_config")
    auto_check_restart = globals().get("_auto_check_restart")
    if auto_check_config is None:
        return

    try:
        from autoteam.config import AUTO_CHECK_INTERVAL, AUTO_CHECK_MIN_LOW, AUTO_CHECK_THRESHOLD

        auto_check_config["interval"] = AUTO_CHECK_INTERVAL
        auto_check_config["threshold"] = AUTO_CHECK_THRESHOLD
        auto_check_config["min_low"] = AUTO_CHECK_MIN_LOW
        if auto_check_restart is not None:
            auto_check_restart.set()
    except Exception:
        pass


def _apply_runtime_env_file_values(values: dict[str, str]):
    for key in _ALL_RUNTIME_ENV_KEYS:
        if key in values:
            value = values[key]
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
            continue

        base_value = _RUNTIME_ENV_BASE.get(key)
        if base_value:
            os.environ[key] = base_value
        else:
            os.environ.pop(key, None)


def _sync_runtime_env_reload_state():
    with _runtime_env_reload_lock:
        _runtime_env_reload_state["signature"] = _runtime_env_file_signature()


def _maybe_reload_runtime_config_from_env_file(*, force: bool = False):
    signature = _runtime_env_file_signature()

    with _runtime_env_reload_lock:
        previous_signature = _runtime_env_reload_state.get("signature")
        if not force and signature == previous_signature:
            return False

        previous_env = {key: os.environ.get(key) for key in _ALL_RUNTIME_ENV_KEYS}
        try:
            content = _read_runtime_env_file_text()
            values = _load_present_env_values_from_source(content, _ALL_RUNTIME_ENV_KEYS)
            _apply_runtime_env_file_values(values)
            _reload_runtime_config_modules()
            _sync_runtime_globals()
            _runtime_env_reload_state["signature"] = signature
        except Exception:
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            _sync_runtime_globals()
            raise

    if previous_signature is not None and signature != previous_signature:
        logger.info("[配置] 检测到 .env 变更，已自动热加载")
    return True


def _verify_runtime_integrations(previous_env: dict[str, str | None] | None = None):
    from autoteam.mail_provider import get_mail_provider_name, get_mail_provider_prompt, get_mail_provider_required_keys
    from autoteam.setup_wizard import _verify_mail_provider, _verify_sub2api

    errors = []
    mail_provider = get_mail_provider_name()
    mail_keys = tuple(get_mail_provider_required_keys(mail_provider))
    sub2api_keys = ("SUB2API_URL", "SUB2API_EMAIL", "SUB2API_PASSWORD")

    mail_values = [os.environ.get(key, "") for key in mail_keys]
    sub2api_values = [os.environ.get(key, "") for key in sub2api_keys]
    sync_states = _effective_sync_target_states()

    if mail_keys and all(mail_values) and not _verify_mail_provider(mail_provider):
        errors.append(f"{get_mail_provider_prompt(mail_provider)} 连接失败")
    if sync_states.get("sub2api") and all(sub2api_values) and not _verify_sub2api():
        errors.append("Sub2API 连接失败")
    if errors:
        api_key = ""
        if previous_env:
            api_key = previous_env.get("API_KEY", "") or ""
        return JSONResponse(status_code=400, content={"message": "、".join(errors), "api_key": api_key})
    return None


def _save_runtime_config(data: dict[str, str]):
    """保存运行时配置到 .env，并在当前进程立即生效。"""
    import secrets as _secrets

    from autoteam.mail_provider import normalize_mail_provider
    from autoteam.setup_wizard import REQUIRED_CONFIGS, _write_env

    env_keys = [key for key, _prompt, _default, _optional in REQUIRED_CONFIGS]
    existing = {key: os.environ.get(key, "") for key in env_keys}
    merged = {key: data.get(key, existing.get(key, "")) for key in env_keys}
    merged["MAIL_PROVIDER"] = normalize_mail_provider(merged.get("MAIL_PROVIDER") or existing.get("MAIL_PROVIDER"))

    if not merged.get("API_KEY"):
        merged["API_KEY"] = _secrets.token_urlsafe(24)

    missing = _validate_runtime_required_values(merged)
    if missing:
        return JSONResponse(
            status_code=400,
            content={"message": "缺少必填项: " + "、".join(missing)},
        )

    try:
        merged = _validate_runtime_optional_values(merged)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"message": str(exc)})

    previous_env = {key: os.environ.get(key) for key in env_keys}
    try:
        for key, value in merged.items():
            os.environ[key] = value
        _reload_runtime_config_modules()

        verify_result = _verify_runtime_integrations(previous_env)
        if verify_result:
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return verify_result

        for key, value in merged.items():
            if value or key in _RUNTIME_CONFIG_CLEARABLE_FIELDS:
                _write_env(key, value)

        _sync_runtime_env_reload_state()
        _sync_runtime_globals()
        return {"message": "配置保存成功", "api_key": API_KEY, "configured": True}
    except Exception:
        _restore_runtime_env(previous_env)
        _reload_runtime_config_modules()
        raise


@app.get("/api/setup/status")
def get_setup_status():
    """检查配置是否完整"""
    from autoteam.setup_wizard import STARTUP_REQUIRED_CONFIGS

    return _collect_config_fields(configs=STARTUP_REQUIRED_CONFIGS)


@app.post("/api/setup/save")
def post_setup_save(config: SetupConfig):
    """保存配置到 .env 并验证连通性"""
    return _save_runtime_config(config.model_dump())


@app.get("/api/config/runtime")
def get_runtime_config():
    """获取当前运行时配置，供登录后的设置面板编辑。"""
    return _collect_config_fields(include_values=True)


@app.get("/api/config/source")
def get_runtime_config_source():
    """获取 .env 源文件内容。"""
    content, path = _read_runtime_source_text()
    return {"path": path, "content": content}


# ---------------------------------------------------------------------------
# HeroSMS 元数据(供配置面板的"可搜索下拉"使用)
# ---------------------------------------------------------------------------


def _build_hero_sms_client_for_lookup(api_key_override: str | None, base_url_override: str | None):
    """根据请求参数或当前配置构造一个临时 HeroSmsClient。

    用户在面板里可能还没保存 API Key,所以允许通过 query 参数透传。
    base_url 同理。其他参数(country/service 等)对元数据查询无影响。
    """
    from autoteam import config as runtime_config
    from autoteam.hero_sms import DEFAULT_BASE_URL, HeroSmsClient

    api_key = (api_key_override or "").strip() or (getattr(runtime_config, "HERO_SMS_API_KEY", "") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="HeroSMS API Key 未配置")

    base_url = (
        (base_url_override or "").strip()
        or (getattr(runtime_config, "HERO_SMS_BASE_URL", "") or DEFAULT_BASE_URL).strip()
        or DEFAULT_BASE_URL
    )

    # 元数据查询用较短超时,避免阻塞配置面板
    return HeroSmsClient(api_key=api_key, base_url=base_url, timeout=20)


@app.get("/api/hero-sms/countries")
def get_hero_sms_countries(api_key: str = "", base_url: str = ""):
    """返回 HeroSMS 国家列表,供前端搜索下拉使用。"""
    from autoteam.hero_sms import HeroSmsError

    client = _build_hero_sms_client_for_lookup(api_key, base_url)
    try:
        countries = client.get_countries()
    except HeroSmsError as exc:
        raise HTTPException(status_code=400, detail=f"获取国家列表失败: {exc} ({exc.code})") from exc
    return {"data": countries}


@app.get("/api/hero-sms/services")
def get_hero_sms_services(country: str = "", lang: str = "cn", api_key: str = "", base_url: str = ""):
    """返回 HeroSMS 服务列表,可按国家筛选。"""
    from autoteam.hero_sms import HeroSmsError

    client = _build_hero_sms_client_for_lookup(api_key, base_url)
    try:
        services = client.get_services_list(country=country or None, lang=lang or "cn")
    except HeroSmsError as exc:
        raise HTTPException(status_code=400, detail=f"获取服务列表失败: {exc} ({exc.code})") from exc
    return {"data": services}


@app.get("/api/hero-sms/availability")
def get_hero_sms_availability(service: str = "dr", api_key: str = "", base_url: str = "", limit: int = 50):
    """按服务返回实时可用国家库存,排除 count=0 的国家。

    数据源是 SMS-Activate 协议的 ``getPrices?service=X``,平台会返回每个 country
    下该 service 的当前库存与单价,这是判断"哪些 country 此刻能买到号"的唯一
    可靠依据(网站上看到的国家列表只是平台支持的国家,与库存无关)。
    """
    from autoteam.hero_sms import HeroSmsError

    client = _build_hero_sms_client_for_lookup(api_key, base_url)
    try:
        prices = client.get_prices(service=service)
    except HeroSmsError as exc:
        raise HTTPException(status_code=400, detail=f"获取价格失败: {exc} ({exc.code})") from exc

    if not isinstance(prices, dict):
        raise HTTPException(status_code=502, detail=f"非预期 getPrices 响应: {type(prices).__name__}")

    # 平台返回 {country_id: {service_code: {cost, count}}}
    rows = []
    for country_id, services_map in prices.items():
        if not isinstance(services_map, dict):
            continue
        info = services_map.get(service)
        if not isinstance(info, dict):
            continue
        try:
            count = int(info.get("count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        try:
            cost = float(info.get("cost", 0) or 0)
        except (TypeError, ValueError):
            cost = 0.0
        if count <= 0:
            continue
        try:
            cid = int(country_id)
        except (TypeError, ValueError):
            cid = country_id
        rows.append({"country": cid, "count": count, "cost": cost})

    rows.sort(key=lambda row: (-row["count"], row["cost"]))
    if limit and limit > 0:
        rows = rows[: int(limit)]
    return {"data": rows, "service": service, "available_count": len(rows)}


@app.put("/api/config/runtime")
def put_runtime_config(config: SetupConfig):
    """登录后修改 CloudMail / Sub2API / 代理等运行时配置。"""
    return _save_runtime_config(config.model_dump())


@app.put("/api/config/source")
def put_runtime_config_source(config: SourceConfig):
    """保存 .env 源文件内容，并立即应用到运行时。"""
    env_keys = list(_ALL_RUNTIME_ENV_KEYS)
    previous_env = {key: os.environ.get(key) for key in env_keys}
    source_path = None
    previous_exists = False
    previous_content = ""

    try:
        current_content, source_path = _read_runtime_source_text()
        previous_content = current_content
        from autoteam.setup_wizard import ENV_FILE

        previous_exists = ENV_FILE.exists()

        _write_runtime_source_text(config.content)

        loaded_values = _load_env_values_from_source(config.content, env_keys)
        missing = _validate_runtime_required_values(loaded_values)
        if missing:
            _restore_runtime_source_text(previous_exists, previous_content)
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return JSONResponse(status_code=400, content={"message": "缺少必填项: " + "、".join(missing)})

        try:
            loaded_values = _validate_runtime_optional_values(loaded_values)
        except ValueError as exc:
            _restore_runtime_source_text(previous_exists, previous_content)
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return JSONResponse(status_code=400, content={"message": str(exc)})

        for key in env_keys:
            if loaded_values.get(key):
                os.environ[key] = loaded_values[key]
            else:
                os.environ.pop(key, None)

        _reload_runtime_config_modules()
        verify_result = _verify_runtime_integrations(previous_env)
        if verify_result:
            _restore_runtime_source_text(previous_exists, previous_content)
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return verify_result

        _sync_runtime_env_reload_state()
        _sync_runtime_globals()
        return {
            "message": "源文件保存成功",
            "api_key": API_KEY,
            "configured": True,
            "path": source_path,
        }
    except Exception:
        _restore_runtime_source_text(previous_exists, previous_content)
        _restore_runtime_env(previous_env)
        _reload_runtime_config_modules()
        raise


# ---------------------------------------------------------------------------
# 后台任务管理
# ---------------------------------------------------------------------------

_tasks: dict[str, dict] = {}
_playwright_lock = threading.Lock()
_current_task_id: str | None = None
# 当前在飞的管理员登录会话（受全局 Playwright lock 串行保护，只可能有一个）。
# 旧测试通过 monkeypatch 直接覆盖这两个标量，保留兼容。
_admin_login_api = None
_admin_login_step: str | None = None
# 当前在飞会话的目标 admin_id（PR2 多 admin 化）。
# - 已存在 admin 重新登录：admin_id 是该 admin 的 8 位 hex；
# - 创建新 admin 流程：填占位 sentinel ``__pending__``，登录完成后再插入索引；
# - 兼容旧 ``/api/admin/login/*`` 路由（无 target）：写当前激活 admin_id 或 ``__pending__``。
_admin_login_target: str | None = None
# 占位 key：标记一个尚未生成 admin_id 的"创建新管理员"流程。
_PENDING_ADMIN_KEY = "__pending__"
_main_codex_flow = None
_main_codex_step: str | None = None
_main_codex_action: str | None = None
MAX_TASK_HISTORY = 50


# ---------------------------------------------------------------------------
# Playwright 专用线程执行器（解决跨线程调用问题）
# ---------------------------------------------------------------------------

import queue as _queue


class _PlaywrightExecutor:
    """将 Playwright 操作派发到专用线程执行，避免跨线程错误"""

    def __init__(self):
        self._queue: _queue.Queue = _queue.Queue()
        self._thread: threading.Thread | None = None
        self._broken_reason: str | None = None

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            func, args, kwargs, result_event, result_holder = item
            try:
                result_holder["result"] = func(*args, **kwargs)
            except Exception as e:
                result_holder["error"] = e
            finally:
                result_event.set()

    def ensure_started(self):
        if self._broken_reason:
            raise RuntimeError(self._broken_reason)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def run(self, func, *args, timeout_seconds=300, **kwargs):
        """在专用线程中执行函数，阻塞等待结果"""
        self.ensure_started()
        result_event = threading.Event()
        result_holder: dict = {}
        self._queue.put((func, args, kwargs, result_event, result_holder))
        if not result_event.wait(timeout=max(1, timeout_seconds)):
            func_name = getattr(func, "__name__", repr(func))
            self._broken_reason = (
                f"Playwright 专用线程执行超时（>{timeout_seconds}s）: {func_name}；"
                "为避免浏览器进程继续堆积，已拒绝后续专用线程任务，请重启服务"
            )
            logger.error("[API] %s", self._broken_reason)
            raise TimeoutError(self._broken_reason)
        if "error" in result_holder:
            raise result_holder["error"]
        return result_holder.get("result")

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("[API] Playwright 专用线程在停止时仍未退出")
            else:
                self._thread = None
                self._queue = _queue.Queue()
                self._broken_reason = None


_pw_executor = _PlaywrightExecutor()


def _stop_playwright_resource(resource):
    if not resource:
        return

    stop = getattr(resource, "stop", None)
    if not callable(stop):
        return

    try:
        stop()
    except Exception:
        pass


def _run_playwright_start(factory, starter, *args, **kwargs):
    resource = factory()
    try:
        result = starter(resource, *args, **kwargs)
        return resource, result
    except Exception:
        _stop_playwright_resource(resource)
        raise


def _run_with_chatgpt_session(callback):
    from autoteam.chatgpt_api import ChatGPTTeamAPI

    chatgpt = ChatGPTTeamAPI()
    try:
        chatgpt.start()
        return callback(chatgpt)
    finally:
        chatgpt.stop()


def _current_busy_detail(default_message: str):
    if _admin_login_api:
        return {
            "message": default_message,
            "running_task": {
                "task_id": "admin-login",
                "command": "admin-login",
                "started_at": None,
            },
        }

    if _main_codex_flow:
        return {
            "message": default_message,
            "running_task": {
                "task_id": "main-codex-sync",
                "command": "main-codex-sync",
                "started_at": None,
            },
        }

    running = _tasks.get(_current_task_id, {})
    return {
        "message": default_message,
        "running_task": {
            "task_id": _current_task_id,
            "command": running.get("command", "unknown"),
            "started_at": running.get("started_at"),
        },
    }


def _prune_tasks():
    """保留最近 MAX_TASK_HISTORY 个任务"""
    if len(_tasks) <= MAX_TASK_HISTORY:
        return
    sorted_ids = sorted(_tasks, key=lambda k: _tasks[k]["created_at"])
    for tid in sorted_ids[: len(_tasks) - MAX_TASK_HISTORY]:
        if _tasks[tid]["status"] in ("completed", "failed"):
            del _tasks[tid]


def _run_task(task_id: str, func, *args, **kwargs):
    """在后台线程中执行任务"""
    global _current_task_id
    task = _tasks[task_id]

    _playwright_lock.acquire()
    _current_task_id = task_id
    task["status"] = "running"
    task["started_at"] = time.time()

    try:
        result = func(*args, **kwargs)
        task["status"] = "completed"
        task["result"] = result
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        logger.error("[API] 任务 %s 失败: %s", task_id[:8], e)
    finally:
        task["finished_at"] = time.time()
        _current_task_id = None
        _playwright_lock.release()


def _start_task(command: str, func, params: dict, *args, **kwargs) -> dict:
    """创建并启动后台任务，返回任务信息

    任务自动关联当前激活 admin_id（从 admin_registry 读),供 GET /api/tasks
    按 admin 过滤、TaskHistory / LogViewer 区分多 admin 来源。激活 admin 缺失
    时记 None,前端展示为"-"。
    """
    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再试"))
    _playwright_lock.release()

    task_id = uuid.uuid4().hex[:12]
    try:
        owner_admin_id = admin_registry.get_active_admin_id()
    except Exception:
        # admin_registry 自身异常不应阻塞任务创建,退化为 None
        owner_admin_id = None
    task = {
        "task_id": task_id,
        "command": command,
        "params": params,
        "status": "pending",
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
        "admin_id": owner_admin_id,
    }
    _tasks[task_id] = task
    _prune_tasks()

    thread = threading.Thread(target=_run_task, args=(task_id, func, *args), kwargs=kwargs, daemon=True)
    thread.start()

    return task


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------


class TaskParams(BaseModel):
    target: int = 5


class CleanupParams(BaseModel):
    max_seats: int | None = None


class AdminEmailParams(BaseModel):
    email: str


class AdminSessionParams(BaseModel):
    email: str
    session_token: str


class AdminPasswordParams(BaseModel):
    password: str


class AdminCodeParams(BaseModel):
    code: str


class AdminWorkspaceParams(BaseModel):
    option_id: str


class TeamMemberRemoveParams(BaseModel):
    email: str
    user_id: str
    type: str


class SwitchAdminParams(BaseModel):
    """切换激活管理员请求体。"""

    admin_id: str


class AdminLoginStartParams(BaseModel):
    """新增/重新登录管理员的 ``/api/admins/login/start`` 请求体。

    :param email: 管理员邮箱。
    :param target_admin_id: 为已存在的 admin 重新登录时填写其 admin_id；
        为空表示走"创建新管理员"流程。
    """

    email: str
    target_admin_id: str | None = None


class AdminLoginPasswordParams(BaseModel):
    """``/api/admins/login/password`` 请求体。"""

    password: str
    target_admin_id: str | None = None


class AdminLoginCodeParams(BaseModel):
    """``/api/admins/login/code`` 请求体。"""

    code: str
    target_admin_id: str | None = None


class AdminLoginWorkspaceParams(BaseModel):
    """``/api/admins/login/workspace`` 请求体。"""

    option_id: str
    target_admin_id: str | None = None


def _normalized_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _is_main_account_email(email: str | None, admin_id: str | None = None) -> bool:
    """判断 email 是否属于指定 admin 的主号。

    :param email: 待判断邮箱。
    :param admin_id: 目标 admin；缺省回退到当前激活 admin（兼容旧调用）。
    """
    from autoteam.admin_state import get_admin_email

    normalized = _normalized_email(email)
    if not normalized:
        return False
    # 旧测试 monkeypatch 把 get_admin_email 替换为无参 lambda；新签名调用会 TypeError。
    # 这里做 fallback 保兼容（PR1 在 accounts.py 也有同款处理）。
    try:
        admin_email = get_admin_email(admin_id)
    except TypeError:
        admin_email = get_admin_email()
    return normalized == _normalized_email(admin_email)


def _func_accepts_admin_id(func) -> bool:
    """检测 ``func`` 的签名中是否声明了 ``admin_id`` 关键字参数。

    用于替代以前"try/except TypeError"的兜底兼容写法——后者会把生产代码
    内部抛出的真实 TypeError 静默吞掉，导致悄悄退化成不带 admin_id 调用，
    可能写错 admin 目录。

    :param func: 待检测的 callable。
    :return: ``True`` 表示签名里能接受 ``admin_id``（直接 kwarg 或经 ``**kwargs``
        透传）；``False`` 表示不接受（多半是旧测试 monkeypatch 出的 1-arg
        lambda）。无法 introspect（如 C 实现的内置函数）时保守返回 ``False``。
    """
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        # 内置或 C 函数等无法 introspect：按"不接受"处理，等价旧逻辑里 fallback 的分支。
        return False
    params = sig.parameters
    if "admin_id" in params:
        return True
    # 接受 **kwargs 的也认为能透传 admin_id。
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _email_is_main(email: str | None, admin_id: str | None = None) -> bool:
    """``_is_main_account_email`` 的间接调用，兼容把后者 monkeypatch 成 1-arg lambda 的旧测试。

    路由层统一通过本函数调用，避免直接以 ``(email, admin_id)`` 签名调用 monkeypatch
    替换出的 1 参 lambda 引发 TypeError。

    用 ``inspect.signature`` 探测 ``_is_main_account_email`` 是否接受 ``admin_id``，
    避免 try/except 吞掉生产代码内部 TypeError。

    :param email: 待判断邮箱。
    :param admin_id: 目标 admin_id；可为 ``None``。
    """
    if _func_accepts_admin_id(_is_main_account_email):
        return _is_main_account_email(email, admin_id)
    return _is_main_account_email(email)


def _admin_state_call(func, admin_id: str | None, *args, **kwargs):
    """带 ``admin_id`` 透传的兼容调用器：兼容旧测试 monkeypatch 用的"无 admin_id 参数"形态。

    用于调用 ``admin_state`` / ``accounts`` / ``codex_auth`` 等数据层函数（PR1
    已为它们补了 ``admin_id`` 参数），优先按新签名调用；如果调用方已经被
    monkeypatch 替换为旧签名（无 admin_id），自动 fallback。

    采用 ``inspect.signature`` 检测目标函数是否接受 ``admin_id`` 关键字参数，
    避免旧实现"try/except TypeError 围绕 func 调用"造成的副作用——后者会把
    生产代码内部抛的真实 TypeError 也吞掉，悄悄退化成不带 admin_id 调用，
    可能写错 admin 目录。

    :param func: 目标函数。
    :param admin_id: 目标 admin_id；可为 ``None``。
    :param args: 透传给 ``func`` 的位置参数（admin_id 之外）。
    :param kwargs: 透传给 ``func`` 的关键字参数（admin_id 之外）。
    :return: ``func`` 返回值。
    """
    if _func_accepts_admin_id(func):
        return func(*args, admin_id=admin_id, **kwargs)
    return func(*args, **kwargs)


def _quota_snapshot_status(quota_info: dict | None) -> str:
    if not isinstance(quota_info, dict):
        return ""

    values = []
    for key in ("primary_pct", "weekly_pct"):
        value = quota_info.get(key)
        if isinstance(value, (int, float)):
            values.append(value)

    if not values:
        return ""
    return "exhausted" if any(value >= 100 for value in values) else "active"


def _resolve_status_auth_file(acc: dict, admin_id: str | None = None) -> str:
    auth_file = (acc.get("auth_file") or "").strip()
    if auth_file and Path(auth_file).exists():
        return auth_file

    if _email_is_main(acc.get("email"), admin_id):
        from autoteam.codex_auth import get_saved_main_auth_file

        saved_auth_file = _admin_state_call(get_saved_main_auth_file, admin_id)
        if saved_auth_file and Path(saved_auth_file).exists():
            return saved_auth_file

    return ""


def _display_account_status(acc: dict, quota_snapshot: dict | None = None, admin_id: str | None = None) -> str:
    status = acc.get("status", "")
    if not _email_is_main(acc.get("email"), admin_id):
        return status

    quota_status = _quota_snapshot_status(quota_snapshot) or _quota_snapshot_status(acc.get("last_quota"))
    if quota_status:
        return quota_status

    return "active" if _resolve_status_auth_file(acc, admin_id) else status


def _sanitize_account(acc: dict, quota_snapshot: dict | None = None, admin_id: str | None = None) -> dict:
    """脱敏账号信息（去掉 password 等敏感字段）"""
    sanitized = {k: v for k, v in acc.items() if k not in ("password", "cloudmail_account_id", "mail_account_id")}
    sanitized["is_main_account"] = _email_is_main(acc.get("email"), admin_id)
    sanitized["status"] = _display_account_status(acc, quota_snapshot, admin_id)
    return sanitized


def _admin_status(admin_id: str | None = None):
    """返回某 admin 的登录态摘要 + 当前在飞登录步骤。

    :param admin_id: 目标 admin；缺省回退到当前激活 admin。
    """
    from autoteam.admin_state import get_admin_state_summary

    # get_admin_state_summary 接受 admin_id（PR1 已改造）；旧测试仍用无参 monkeypatch，
    # 这里捕获 TypeError 降级，确保兼容。
    try:
        status = get_admin_state_summary(admin_id)
    except TypeError:
        status = get_admin_state_summary()
    status["login_step"] = _admin_login_step
    status["login_in_progress"] = _admin_login_api is not None
    if _admin_login_api and _admin_login_step == "workspace_required":
        status["workspace_options"] = getattr(_admin_login_api, "workspace_options_cache", []) or []
    else:
        status["workspace_options"] = []
    return status


def _main_codex_status():
    return {
        "in_progress": _main_codex_flow is not None,
        "step": _main_codex_step,
        "action": _main_codex_action,
    }


def _finish_admin_login(completed: dict):
    global _admin_login_api, _admin_login_step, _admin_login_target
    api = _admin_login_api
    info = None
    try:
        info = _pw_executor.run(api.complete_admin_login)
    finally:
        if api:
            try:
                _pw_executor.run(api.stop)
            except Exception:
                pass
        _admin_login_api = None
        _admin_login_step = None
        _admin_login_target = None
        if _playwright_lock.locked():
            _playwright_lock.release()
    return {"status": "completed", "admin": _admin_status(), "codex": _main_codex_status(), "info": info}


def _set_pending_admin_login(api, step, target_admin_id: str | None = None):
    """登记一个进行中的管理员登录会话。

    :param target_admin_id: 该会话的目标 admin_id；为 ``None`` 表示沿用旧路由
        约定（兼容老客户端，记为 ``_PENDING_ADMIN_KEY``）。
    """
    global _admin_login_api, _admin_login_step, _admin_login_target
    _admin_login_api = api
    _admin_login_step = step
    _admin_login_target = target_admin_id or _PENDING_ADMIN_KEY
    return {"status": step, "admin": _admin_status()}


def _clear_admin_login_session(*, release_lock: bool = True) -> None:
    """统一清理在飞管理员登录会话的全局变量。

    :param release_lock: 是否在持锁时释放 Playwright lock。``False`` 用于
        登录已完成的场景（锁已由 ``_finish_admin_login`` 处理）。
    """
    global _admin_login_api, _admin_login_step, _admin_login_target
    _admin_login_api = None
    _admin_login_step = None
    _admin_login_target = None
    if release_lock and _playwright_lock.locked():
        _playwright_lock.release()


def _finish_main_codex_flow():
    global _main_codex_flow, _main_codex_step, _main_codex_action
    flow = _main_codex_flow
    action = _main_codex_action or "sync"
    try:
        info = _pw_executor.run(flow.complete)
    finally:
        if flow:
            try:
                _pw_executor.run(flow.stop)
            except Exception:
                pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    message = "主号 Codex 已同步到已启用远端" if action == "sync" else "主号 Codex 已登录"
    return {
        "status": "completed",
        "message": message,
        "codex": _main_codex_status(),
        "info": info,
    }


def _set_pending_main_codex_flow(flow, step, action):
    global _main_codex_flow, _main_codex_step, _main_codex_action
    _main_codex_flow = flow
    _main_codex_step = step
    _main_codex_action = action
    return {"status": step, "codex": _main_codex_status()}


def _start_main_codex_flow(action="sync"):
    from autoteam.codex_auth import MainCodexLoginFlow, MainCodexSyncFlow

    flow_cls = MainCodexSyncFlow if action == "sync" else MainCodexLoginFlow

    def _do_start():
        return _run_playwright_start(flow_cls, lambda flow: flow.start())

    flow, result = _pw_executor.run(_do_start)
    step = result["step"]
    if step == "completed":
        _set_pending_main_codex_flow(flow, step, action)
        return step, _finish_main_codex_flow()
    if step in ("password_required", "code_required"):
        return step, _set_pending_main_codex_flow(flow, step, action)

    _pw_executor.run(flow.stop)
    raise RuntimeError(result.get("detail") or "无法识别主号 Codex 登录步骤")


# ---------------------------------------------------------------------------
# 同步端点
# ---------------------------------------------------------------------------


@app.get("/api/admin/status")
def get_admin_status(admin_id: str | None = Depends(get_current_admin_id)):
    """获取管理员登录状态。"""
    return _admin_status(admin_id)


@app.get("/api/main-codex/status")
def get_main_codex_status():
    """获取主号 Codex 同步状态。"""
    return _main_codex_status()


@app.post("/api/admin/login/start")
def post_admin_login_start(
    params: AdminEmailParams,
    admin_id: str | None = Depends(get_current_admin_id),
):
    """开始管理员登录流程。"""
    global _admin_login_api, _admin_login_target

    if _admin_login_api:
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _clear_admin_login_session()

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再进行管理员登录")
        )

    try:
        from autoteam.chatgpt_api import ChatGPTTeamAPI

        logger.info("[API] 开始管理员登录: %s", params.email.strip())

        def _do_start(email):
            return _run_playwright_start(
                ChatGPTTeamAPI, lambda api, login_email: api.begin_admin_login(login_email), email
            )

        api, result = _pw_executor.run(_do_start, params.email.strip())
        step = result["step"]
        logger.info("[API] 管理员登录 start 返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            _admin_login_api = api
            _admin_login_target = admin_id or _PENDING_ADMIN_KEY
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            return _set_pending_admin_login(api, step, target_admin_id=admin_id)
        _pw_executor.run(api.stop)
        _playwright_lock.release()
        raise HTTPException(status_code=400, detail=result.get("detail") or "无法识别管理员登录步骤")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员登录 start 失败")
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/session")
def post_admin_login_session(
    params: AdminSessionParams,
    admin_id: str | None = Depends(get_current_admin_id),
):
    """手动导入管理员 session_token。"""
    if _admin_login_api:
        post_admin_login_cancel()

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail=_current_busy_detail("有任务正在执行，请等待完成后再导入管理员 session_token"),
        )

    try:
        from autoteam.chatgpt_api import ChatGPTTeamAPI

        logger.info("[API] 导入管理员 session_token: %s", params.email.strip())

        def _do_import(email, session_token):
            api = ChatGPTTeamAPI()
            try:
                return api.import_admin_session(email, session_token)
            finally:
                api.stop()

        info = _pw_executor.run(_do_import, params.email.strip(), params.session_token.strip())
        _clear_admin_login_session(release_lock=False)
        return {"status": "completed", "admin": _admin_status(admin_id), "codex": _main_codex_status(), "info": info}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 导入管理员 session_token 失败")
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if _playwright_lock.locked():
            _playwright_lock.release()


@app.post("/api/admin/login/password")
def post_admin_login_password(params: AdminPasswordParams):
    """提交管理员密码。"""
    global _admin_login_step
    if not _admin_login_api or _admin_login_step != "password_required":
        raise HTTPException(status_code=409, detail="当前没有等待密码的管理员登录流程")

    try:
        logger.info("[API] 提交管理员密码 | current_step=%s", _admin_login_step)
        result = _pw_executor.run(_admin_login_api.submit_admin_password, params.password)
        step = result["step"]
        logger.info("[API] 管理员密码提交返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员密码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员密码提交失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _clear_admin_login_session()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/code")
def post_admin_login_code(params: AdminCodeParams):
    """提交管理员验证码。"""
    global _admin_login_step
    if not _admin_login_api or _admin_login_step != "code_required":
        raise HTTPException(status_code=409, detail="当前没有等待验证码的管理员登录流程")

    try:
        logger.info("[API] 提交管理员验证码 | current_step=%s code_len=%d", _admin_login_step, len(params.code.strip()))
        result = _pw_executor.run(_admin_login_api.submit_admin_code, params.code.strip())
        step = result["step"]
        logger.info("[API] 管理员验证码提交返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员验证码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员验证码提交失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _clear_admin_login_session()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/workspace")
def post_admin_login_workspace(params: AdminWorkspaceParams):
    """提交管理员 workspace 选择。"""
    global _admin_login_step
    if not _admin_login_api or _admin_login_step != "workspace_required":
        raise HTTPException(status_code=409, detail="当前没有等待组织选择的管理员登录流程")

    try:
        logger.info("[API] 提交管理员 workspace 选择 | option_id=%s", params.option_id)
        result = _pw_executor.run(_admin_login_api.select_workspace_option, params.option_id)
        step = result["step"]
        logger.info("[API] 管理员 workspace 选择返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员组织选择失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员 workspace 选择失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _clear_admin_login_session()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/cancel")
def post_admin_login_cancel():
    """取消管理员登录流程。"""
    if _admin_login_api:
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _clear_admin_login_session()
    return {"message": "管理员登录已取消", "admin": _admin_status()}


@app.post("/api/admin/logout")
def post_admin_logout(admin_id: str | None = Depends(get_current_admin_id)):
    """清除已保存的管理员登录态。"""
    from autoteam.admin_state import clear_admin_state

    if _admin_login_api:
        post_admin_login_cancel()
    clear_admin_state(admin_id)
    return {"message": "管理员登录态已清除", "admin": _admin_status(admin_id)}


# ---------------------------------------------------------------------------
# 多管理员管理 API（PR2）
# ---------------------------------------------------------------------------


def _serialize_admin(admin: admin_registry.Admin, *, active_id: str | None = None) -> dict:
    """把 ``Admin`` dataclass 序列化为前端可消费的 dict（带 ``is_active`` 标记）。"""
    payload = admin.to_dict()
    payload["is_active"] = bool(active_id) and admin.admin_id == active_id
    return payload


def _resolve_target_admin_id(target: str | None) -> str | None:
    """把 body 里的 ``target_admin_id`` 转成校验过的 admin_id。

    - 为空 → 返回 ``None``（创建新 admin 流程）。
    - 校验通过且存在 → 返回该 admin_id。
    - 校验通过但不存在 → 抛 404；格式非法 → 由 ``_validate_admin_id`` 抛 400。
    """
    if not target:
        return None
    target = target.strip()
    if not target:
        return None
    _validate_admin_id(target)
    if not admin_registry.get_admin(target):
        raise HTTPException(status_code=404, detail=f"管理员不存在: {target}")
    return target


@app.get("/api/admins")
def get_admins():
    """列出所有已注册的 admin（含别名、是否激活）。"""
    active_id = admin_registry.get_active_admin_id()
    return {
        "admins": [_serialize_admin(a, active_id=active_id) for a in admin_registry.list_admins()],
        "active_admin_id": active_id,
    }


@app.get("/api/admins/active")
def get_active_admin():
    """获取当前激活 admin（无任何 admin 时返回 ``{"admin": null}``）。"""
    admin = admin_registry.get_active_admin()
    if not admin:
        return {"admin": None, "active_admin_id": None}
    return {
        "admin": _serialize_admin(admin, active_id=admin.admin_id),
        "active_admin_id": admin.admin_id,
    }


@app.post("/api/admins/active")
def post_switch_active_admin(params: SwitchAdminParams):
    """切换激活 admin。

    body: ``{"admin_id": "<8 hex>"}``。404 表示该 id 未注册；400 表示 id 格式非法。
    """
    target = (params.admin_id or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="admin_id 不能为空")
    _validate_admin_id(target)
    try:
        admin = admin_registry.set_active_admin(target)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "admin": _serialize_admin(admin, active_id=admin.admin_id),
        "active_admin_id": admin.admin_id,
    }


@app.delete("/api/admins/{admin_id}")
def delete_admin(admin_id: str):
    """删除一个 admin：移除索引项 + 删除其数据目录 + 清理远端 codex-main 主号文件。

    禁止删除当前唯一的 admin（避免清空所有上下文）。返回 ``{deleted_admin_id, ...}``。
    """
    _validate_admin_id(admin_id)
    target = admin_registry.get_admin(admin_id)
    if not target:
        raise HTTPException(status_code=404, detail=f"管理员不存在: {admin_id}")

    all_admins = admin_registry.list_admins()
    if len(all_admins) <= 1:
        raise HTTPException(status_code=400, detail="禁止删除当前唯一的管理员，请先添加另一个管理员")

    # 记录主号 codex-main 文件名（用于远端清理）
    from autoteam.auth_storage import get_auth_dir

    auth_files: list[str] = []
    auth_dir = get_auth_dir(admin_id)
    if auth_dir.exists():
        auth_files = [p.name for p in auth_dir.glob("codex-main-*.json") if p.is_file()]

    # 同步：取消远端 codex-main 关联（best-effort，失败不阻塞索引清理）
    remote_cleanup: dict = {}
    if auth_files:
        try:
            from autoteam.sync_targets import delete_account_from_configured_targets

            remote_cleanup = delete_account_from_configured_targets(
                target.email or admin_id,
                auth_names=auth_files,
                include_disabled=True,
            )
        except Exception as exc:
            logger.warning("[Admin] 删除 admin %s 时清理远端失败（已忽略，继续删除本地）: %s", admin_id, exc)

    # 移除索引（自动调整 active_admin_id）
    admin_registry.remove_admin(admin_id)

    # 删除本地数据目录
    try:
        from autoteam.admin_registry import admin_data_dir

        data_dir = admin_data_dir(admin_id)
        if data_dir.exists():
            import shutil as _shutil

            _shutil.rmtree(data_dir, ignore_errors=False)
    except Exception as exc:
        logger.error("[Admin] 删除 admin %s 数据目录失败: %s", admin_id, exc)

    new_active = admin_registry.get_active_admin_id()
    logger.info("[Admin] 已删除 admin: %s，新的激活 admin: %s", admin_id, new_active)
    return {
        "deleted_admin_id": admin_id,
        "active_admin_id": new_active,
        "remote_cleanup": remote_cleanup,
    }


def _prepare_admin_login_target(target_admin_id: str | None, email: str | None) -> str | None:
    """根据请求体决定本次登录会话的目标 admin_id。

    - 已存在的 admin → 返回其 id 并切到激活，让随后 ``update_admin_state`` 写到正确目录。
    - 创建新 admin → 调用 ``add_admin`` 生成新 id，切为激活。
    - 缺省（旧路由 fallback）→ 返回 ``None``。

    :param target_admin_id: 客户端指定的 ``target_admin_id``，已 strip。
    :param email: ``params.email``，仅在创建新 admin 时用作初始 ``email`` 字段。
    """
    if target_admin_id:
        # 已存在的 admin 重新登录：切激活，登录流程的 update_admin_state 自然写入其目录。
        admin = admin_registry.get_admin(target_admin_id)
        if not admin:
            raise HTTPException(status_code=404, detail=f"管理员不存在: {target_admin_id}")
        admin_registry.set_active_admin(target_admin_id)
        return target_admin_id

    if email is None or not email.strip():
        # 没有 target 也没有 email → 兼容老路由（active fallback）。
        return None

    # 创建新 admin：先在索引里登记一条空壳，让登录流程把 state 写到它的目录。
    new_admin = admin_registry.add_admin(admin_registry.Admin(admin_id="", email=email.strip()))
    admin_registry.set_active_admin(new_admin.admin_id)
    logger.info("[Admin] 为新管理员预创建索引: %s (%s)", new_admin.admin_id, new_admin.email)
    return new_admin.admin_id


def _rollback_pending_admin(admin_id: str | None) -> None:
    """登录失败时回滚预创建的空壳 admin，避免锁死用户。

    场景：``_prepare_admin_login_target`` 在登录开始前就 ``add_admin`` +
    ``set_active_admin`` 写了一条空壳记录；若后续 ``begin_admin_login`` /
    密码 / 验证码 / workspace 任意一步抛异常，必须把空壳清掉，否则当系统中
    仅剩这一条空壳时，DELETE 路由会拒绝（"唯一 admin"），用户死锁。

    判定空壳的依据：``workspace_name`` 与 ``account_id`` 都为空——只有走完
    ``complete_admin_login`` 才会回填这两个字段。

    :param admin_id: 待回滚的 admin_id；为占位符（``_PENDING_ADMIN_KEY``）或
        ``None`` 时不动作。
    """
    if not admin_id or admin_id == _PENDING_ADMIN_KEY:
        return
    try:
        admin = admin_registry.get_admin(admin_id)
    except Exception:
        return
    if admin is None:
        return
    # 已经走完登录的 admin 有 workspace_name 或 account_id；不能误删。
    if admin.workspace_name or admin.account_id:
        return
    try:
        admin_registry.remove_admin(admin_id)
        logger.info("[Admin] 登录失败回滚空壳 admin: %s", admin_id)
    except Exception as exc:
        logger.warning("[Admin] 回滚空壳 admin 失败: %s (%s)", admin_id, exc)


def _ensure_pending_target_matches(target_admin_id: str | None) -> None:
    """检查当前在飞登录会话的目标 admin_id 是否与请求体一致。

    若 body 指定了 ``target_admin_id`` 但与正在跑的会话不匹配，返回 409，避免
    跨 admin 误用（例如同时两个浏览器抢登录）。
    """
    if not target_admin_id:
        return
    if not _admin_login_api:
        raise HTTPException(status_code=409, detail="当前没有等待中的管理员登录流程")
    if _admin_login_target and _admin_login_target != target_admin_id:
        raise HTTPException(
            status_code=409,
            detail="当前在跑的管理员登录流程不属于该 admin，请先取消后再发起",
        )


@app.post("/api/admins/login/start")
def post_admins_login_start(params: AdminLoginStartParams):
    """启动登录流程：可为现有 admin 重新登录，或创建新 admin。

    body: ``{"email": "...", "target_admin_id": "..."}``。``target_admin_id``
    省略时表示创建新 admin。完成后激活即指向该 admin。
    """
    global _admin_login_api, _admin_login_target

    target = _resolve_target_admin_id(params.target_admin_id)

    if _admin_login_api:
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _clear_admin_login_session()

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再进行管理员登录")
        )

    try:
        # 必须在 begin_admin_login 之前切到目标 admin，否则登录中途写 state 会写到错误目录。
        target = _prepare_admin_login_target(target, params.email)

        from autoteam.chatgpt_api import ChatGPTTeamAPI

        logger.info("[API] 启动多 admin 登录: target=%s email=%s", target or "<active>", params.email.strip())

        def _do_start(email):
            return _run_playwright_start(
                ChatGPTTeamAPI, lambda api, login_email: api.begin_admin_login(login_email), email
            )

        api, result = _pw_executor.run(_do_start, params.email.strip())
        step = result["step"]
        if step == "completed":
            _admin_login_api = api
            _admin_login_target = target or _PENDING_ADMIN_KEY
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            return _set_pending_admin_login(api, step, target_admin_id=target)
        _pw_executor.run(api.stop)
        _playwright_lock.release()
        # 步骤无法识别也算登录失败：回滚预创建的空壳 admin。
        _rollback_pending_admin(target)
        raise HTTPException(status_code=400, detail=result.get("detail") or "无法识别管理员登录步骤")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 多 admin 登录 start 失败")
        if _playwright_lock.locked():
            _playwright_lock.release()
        # begin_admin_login 抛异常时清理预创建的空壳，避免用户被锁死。
        _rollback_pending_admin(target)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admins/login/password")
def post_admins_login_password(params: AdminLoginPasswordParams):
    """提交密码（``target_admin_id`` 用于校验当前在飞会话与请求一致）。"""
    global _admin_login_step
    target = _resolve_target_admin_id(params.target_admin_id)
    _ensure_pending_target_matches(target)

    if not _admin_login_api or _admin_login_step != "password_required":
        raise HTTPException(status_code=409, detail="当前没有等待密码的管理员登录流程")

    try:
        result = _pw_executor.run(_admin_login_api.submit_admin_password, params.password)
        step = result["step"]
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        # 无法识别的 step：登录已失败，回滚空壳后再抛 400。
        _rollback_pending_admin(_admin_login_target)
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员密码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 多 admin 登录 password 失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        # 先回滚空壳 admin（依赖 _admin_login_target），再清会话变量。
        _rollback_pending_admin(_admin_login_target)
        _clear_admin_login_session()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admins/login/code")
def post_admins_login_code(params: AdminLoginCodeParams):
    """提交验证码。"""
    global _admin_login_step
    target = _resolve_target_admin_id(params.target_admin_id)
    _ensure_pending_target_matches(target)

    if not _admin_login_api or _admin_login_step != "code_required":
        raise HTTPException(status_code=409, detail="当前没有等待验证码的管理员登录流程")

    try:
        result = _pw_executor.run(_admin_login_api.submit_admin_code, params.code.strip())
        step = result["step"]
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        # 无法识别的 step：登录已失败，回滚空壳后再抛 400。
        _rollback_pending_admin(_admin_login_target)
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员验证码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 多 admin 登录 code 失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        # 先回滚空壳 admin（依赖 _admin_login_target），再清会话变量。
        _rollback_pending_admin(_admin_login_target)
        _clear_admin_login_session()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admins/login/workspace")
def post_admins_login_workspace(params: AdminLoginWorkspaceParams):
    """提交 workspace 选择（最后一步）。"""
    global _admin_login_step
    target = _resolve_target_admin_id(params.target_admin_id)
    _ensure_pending_target_matches(target)

    if not _admin_login_api or _admin_login_step != "workspace_required":
        raise HTTPException(status_code=409, detail="当前没有等待组织选择的管理员登录流程")

    try:
        result = _pw_executor.run(_admin_login_api.select_workspace_option, params.option_id)
        step = result["step"]
        if step == "completed":
            response = _finish_admin_login(result)
            # 登录成功：把 info 里的最新 workspace_name/account_id 回写到 admins.json
            info = response.get("info") or {}
            target_id = target or admin_registry.get_active_admin_id()
            if target_id and isinstance(info, dict):
                admin_registry.update_admin(
                    target_id,
                    email=info.get("email") or None,
                    workspace_name=info.get("workspace_name") or None,
                    account_id=info.get("account_id") or None,
                )
            return response
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        # 无法识别的 step：登录已失败，回滚空壳后再抛 400。
        _rollback_pending_admin(_admin_login_target)
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员组织选择失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 多 admin 登录 workspace 失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        # 先回滚空壳 admin（依赖 _admin_login_target），再清会话变量。
        _rollback_pending_admin(_admin_login_target)
        _clear_admin_login_session()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/start")
def post_main_codex_start(admin_id: str | None = Depends(get_current_admin_id)):
    """开始主号 Codex 登录并同步到已启用远端。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action

    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    _require_sync_target_configs("同步主号 Codex")

    from autoteam.codex_auth import get_saved_main_auth_file
    from autoteam.sync_targets import sync_main_codex_to_configured_targets

    saved_auth_file = _admin_state_call(get_saved_main_auth_file, admin_id)
    if saved_auth_file:
        sync_main_codex_to_configured_targets(saved_auth_file)
        return {
            "status": "completed",
            "message": "主号 Codex 已同步到已启用远端",
            "codex": _main_codex_status(),
            "info": {"auth_file": saved_auth_file},
        }

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再同步主号 Codex")
        )

    try:
        _step, result = _start_main_codex_flow(action="sync")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/login")
def post_main_codex_login():
    """开始主号 Codex 登录，仅保存本地认证文件。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action

    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再登录主号 Codex")
        )

    try:
        _step, result = _start_main_codex_flow(action="login")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/password")
def post_main_codex_password(params: AdminPasswordParams):
    """提交主号 Codex 登录密码。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action
    if not _main_codex_flow or _main_codex_step != "password_required":
        raise HTTPException(status_code=409, detail="当前没有等待密码的主号 Codex 登录流程")

    try:
        result = _pw_executor.run(_main_codex_flow.submit_password, params.password)
        step = result["step"]
        if step == "completed":
            return _finish_main_codex_flow()
        if step in ("password_required", "code_required"):
            _main_codex_step = step
            return {"status": step, "codex": _main_codex_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "主号 Codex 密码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/code")
def post_main_codex_code(params: AdminCodeParams):
    """提交主号 Codex 登录验证码。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action
    if not _main_codex_flow or _main_codex_step != "code_required":
        raise HTTPException(status_code=409, detail="当前没有等待验证码的主号 Codex 登录流程")

    try:
        result = _pw_executor.run(_main_codex_flow.submit_code, params.code.strip())
        step = result["step"]
        if step == "completed":
            return _finish_main_codex_flow()
        if step in ("password_required", "code_required"):
            _main_codex_step = step
            return {"status": step, "codex": _main_codex_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "主号 Codex 验证码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/cancel")
def post_main_codex_cancel():
    """取消主号 Codex 登录流程。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action
    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()
    return {"message": "主号 Codex 登录已取消", "codex": _main_codex_status()}


@app.get("/api/accounts")
def get_accounts(admin_id: str | None = Depends(get_current_admin_id)):
    """获取所有账号列表"""
    from autoteam.accounts import load_accounts

    accounts = _admin_state_call(load_accounts, admin_id)
    return [_sanitize_account(a, admin_id=admin_id) for a in accounts]


@app.get("/api/accounts/{email}/codex-auth")
def get_codex_auth(email: str, admin_id: str | None = Depends(get_current_admin_id)):
    """导出账号的 Codex CLI 格式认证文件（~/.codex/auth.json）"""
    from autoteam.accounts import find_account, load_accounts
    from autoteam.codex_auth import get_saved_main_auth_file

    email = email.strip().lower()
    auth_file = ""

    if _email_is_main(email, admin_id):
        auth_file = _admin_state_call(get_saved_main_auth_file, admin_id)
        if not auth_file or not Path(auth_file).exists():
            raise HTTPException(status_code=404, detail="主号没有可导出的认证文件")
    else:
        acc = find_account(_admin_state_call(load_accounts, admin_id), email)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        auth_file = acc.get("auth_file") or ""
        if not auth_file or not Path(auth_file).exists():
            raise HTTPException(status_code=404, detail="该账号没有认证文件")

    auth_data = json.loads(Path(auth_file).read_text())

    # 转换为 Codex CLI 的 auth.json 格式
    codex_auth = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": auth_data.get("id_token", ""),
            "access_token": auth_data.get("access_token", ""),
            "refresh_token": auth_data.get("refresh_token", ""),
            "account_id": auth_data.get("account_id", ""),
        },
        "last_refresh": auth_data.get("last_refresh", ""),
    }

    return {
        "email": email,
        "codex_auth": codex_auth,
        "hint": "将内容保存到 ~/.codex/auth.json（Linux/macOS）或 %APPDATA%\\codex\\auth.json（Windows）",
    }


@app.get("/api/accounts/active")
def get_active(admin_id: str | None = Depends(get_current_admin_id)):
    """获取活跃账号"""
    from autoteam.accounts import get_active_accounts

    return [_sanitize_account(a, admin_id=admin_id) for a in get_active_accounts(admin_id)]


@app.get("/api/accounts/standby")
def get_standby(admin_id: str | None = Depends(get_current_admin_id)):
    """获取待命账号"""
    from autoteam.accounts import get_standby_accounts

    accounts = get_standby_accounts(admin_id)
    return [_sanitize_account(a, admin_id=admin_id) for a in accounts]


@app.delete("/api/accounts/{email}")
def delete_account(email: str, admin_id: str | None = Depends(get_current_admin_id)):
    """删除本地管理账号及其关联资源。"""
    if not _playwright_lock.acquire(blocking=False):
        running = _tasks.get(_current_task_id, {})
        raise HTTPException(
            status_code=409,
            detail={
                "message": "有任务正在执行，请等待完成后再删除账号",
                "running_task": {
                    "task_id": _current_task_id,
                    "command": running.get("command", "unknown"),
                    "started_at": running.get("started_at"),
                },
            },
        )

    try:
        from autoteam.account_ops import delete_managed_account
        from autoteam.accounts import load_accounts

        if _email_is_main(email, admin_id):
            raise HTTPException(status_code=400, detail="主号不允许删除")

        accounts = _admin_state_call(load_accounts, admin_id)
        if not any(a["email"].lower() == email.lower() for a in accounts):
            raise HTTPException(status_code=404, detail="账号不存在")

        cleanup = _pw_executor.run(delete_managed_account, email)
        return {
            "message": "账号删除完成",
            "deleted_email": email,
            "cleanup": cleanup,
        }
    finally:
        _playwright_lock.release()


@app.post("/api/accounts/{email}/kick")
def post_kick_account(email: str, admin_id: str | None = Depends(get_current_admin_id)):
    """将账号从 Team 中移出，状态变为 standby"""
    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再操作"))

    try:
        from autoteam.accounts import find_account, load_accounts, update_account
        from autoteam.manager import remove_from_team

        email = email.strip().lower()
        if _email_is_main(email, admin_id):
            raise HTTPException(status_code=400, detail="主号不允许移出 Team")
        accounts = _admin_state_call(load_accounts, admin_id)
        acc = find_account(accounts, email)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        if acc["status"] != "active":
            raise HTTPException(status_code=400, detail=f"账号状态为 {acc['status']}，不是 active")

        def _do_kick():
            return _run_with_chatgpt_session(lambda chatgpt: remove_from_team(chatgpt, email))

        ok = _pw_executor.run(_do_kick)
        if ok:
            _admin_state_call(update_account, admin_id, email, status="standby")
            return {"message": f"已将 {email} 移出 Team", "email": email, "status": "standby"}
        raise HTTPException(status_code=500, detail=f"移出 {email} 失败")
    finally:
        _playwright_lock.release()


class LoginAccountParams(BaseModel):
    email: str


@app.post("/api/accounts/login", status_code=202)
def post_account_login(params: LoginAccountParams, admin_id: str | None = Depends(get_current_admin_id)):
    """触发单个账号的 Codex 登录（后台执行）"""
    from autoteam.accounts import find_account, load_accounts

    email = params.email.strip().lower()
    if _email_is_main(email, admin_id):
        raise HTTPException(status_code=400, detail="主号不属于账号池登录对象")
    accounts = _admin_state_call(load_accounts, admin_id)
    acc = find_account(accounts, email)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    _require_account_mail_configs(acc, "登录账号")
    _require_sync_target_configs("登录账号")

    def _run():
        from autoteam.accounts import STATUS_ACTIVE, update_account
        from autoteam.codex_auth import (
            check_codex_quota,
            login_codex_via_browser,
            quota_result_quota_info,
            quota_result_resets_at,
            save_auth_file,
        )
        from autoteam.mail_provider import get_mail_client_for_account

        mail_client = get_mail_client_for_account(acc)
        mail_client.login()
        bundle = login_codex_via_browser(email, acc.get("password", ""), mail_client=mail_client)
        if bundle:
            plan_type = str(bundle.get("plan_type") or "").lower()
            if plan_type != "team":
                raise RuntimeError(f"登录后 plan={plan_type or 'unknown'}，未进入 Team workspace")
            # 显式透传 admin_id：后台线程内激活 admin 可能被巡检循环切换
            auth_file = save_auth_file(bundle, admin_id=admin_id)
            _admin_state_call(update_account, admin_id, email, auth_file=auth_file)
            # 登录成功且是 team plan，自动标记为 active
            if plan_type == "team":
                _admin_state_call(update_account, admin_id, email, status=STATUS_ACTIVE, last_active_at=time.time())
                # 查一下额度并保存快照
                token = bundle.get("access_token")
                if token:
                    st, info = check_codex_quota(token)
                    if st == "ok" and isinstance(info, dict):
                        _admin_state_call(update_account, admin_id, email, last_quota=info)
                    elif st == "exhausted":
                        quota_info = quota_result_quota_info(info)
                        if quota_info:
                            _admin_state_call(update_account, admin_id, email, last_quota=quota_info)
                        update_account(
                            email,
                            admin_id=admin_id,
                            status="exhausted",
                            quota_exhausted_at=time.time(),
                            quota_resets_at=quota_result_resets_at(info) or int(time.time() + 18000),
                        )
            # 同步到已启用远端
            from autoteam.sync_targets import sync_to_configured_targets

            sync_to_configured_targets()
            return {"email": email, "plan": bundle.get("plan_type"), "auth_file": auth_file}
        raise RuntimeError(f"Codex 登录失败: {email}")

    task = _start_task(f"login:{email}", _run, {"email": email})
    return task


@app.get("/api/status")
def get_status(admin_id: str | None = Depends(get_current_admin_id)):
    """获取所有账号状态 + active 账号实时额度"""
    from autoteam.accounts import (
        STATUS_ACTIVE,
        STATUS_AUTH_PENDING,
        STATUS_EXHAUSTED,
        STATUS_PENDING,
        STATUS_STANDBY,
        load_accounts,
    )
    from autoteam.codex_auth import check_codex_quota, quota_result_quota_info

    accounts = _admin_state_call(load_accounts, admin_id)
    quota_cache = {}

    for acc in accounts:
        if acc["status"] not in (STATUS_ACTIVE, STATUS_AUTH_PENDING) and not _email_is_main(acc.get("email"), admin_id):
            continue

        auth_file = _resolve_status_auth_file(acc, admin_id)
        if not auth_file:
            continue

        try:
            auth_data = json.loads(read_text(Path(auth_file)))
            access_token = auth_data.get("access_token")
            if access_token:
                status, info = check_codex_quota(access_token)
                if status == "ok" and isinstance(info, dict):
                    quota_cache[acc["email"]] = info
                elif status == "exhausted":
                    quota_info = quota_result_quota_info(info)
                    if quota_info:
                        quota_cache[acc["email"]] = quota_info
        except Exception:
            pass

    sanitized_accounts = [_sanitize_account(a, quota_cache.get(a.get("email")), admin_id) for a in accounts]

    summary = {
        "active": sum(1 for a in sanitized_accounts if a["status"] == STATUS_ACTIVE),
        "auth_pending": sum(1 for a in sanitized_accounts if a["status"] == STATUS_AUTH_PENDING),
        "standby": sum(1 for a in sanitized_accounts if a["status"] == STATUS_STANDBY),
        "exhausted": sum(1 for a in sanitized_accounts if a["status"] == STATUS_EXHAUSTED),
        "pending": sum(1 for a in sanitized_accounts if a["status"] == STATUS_PENDING),
        "total": len(sanitized_accounts),
    }

    return {
        "accounts": sanitized_accounts,
        "summary": summary,
        "quota_cache": quota_cache,
    }


@app.post("/api/sync")
def post_sync():
    """同步认证文件到已启用远端。"""
    from autoteam.sync_targets import describe_sync_targets, get_enabled_sync_targets, sync_to_configured_targets

    _require_sync_target_configs("同步远端")
    targets = get_enabled_sync_targets()
    result = sync_to_configured_targets()
    return {"message": f"已同步到 {describe_sync_targets(targets)}", "result": result}


@app.post("/api/sync/accounts")
def post_sync_accounts(admin_id: str | None = Depends(get_current_admin_id)):
    """从 auths 目录和 Team 成员同步账号到 accounts.json"""
    from autoteam.manager import sync_account_states

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再同步"))

    try:
        _pw_executor.run(sync_account_states)
    finally:
        _playwright_lock.release()

    from autoteam.accounts import load_accounts

    accounts = _admin_state_call(load_accounts, admin_id)
    return {"message": f"同步完成，共 {len(accounts)} 个账号", "total": len(accounts)}


@app.get("/api/team/members")
def get_team_members(admin_id: str | None = Depends(get_current_admin_id)):
    """获取 Team 全部成员（包括手动添加的外部成员）"""
    from autoteam.admin_state import get_admin_session_token, get_chatgpt_account_id

    if not _admin_state_call(get_admin_session_token, admin_id) or not _admin_state_call(
        get_chatgpt_account_id, admin_id
    ):
        raise HTTPException(status_code=400, detail="请先完成管理员登录")

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再查询"))

    try:

        def _fetch_team_members():
            from autoteam.account_ops import fetch_team_state
            from autoteam.accounts import load_accounts

            def _collect(chatgpt):
                members, invites = fetch_team_state(chatgpt)
                local_emails = {a["email"].lower() for a in _admin_state_call(load_accounts, admin_id)}

                result = []
                for m in members:
                    email = (m.get("email") or "").lower()
                    result.append(
                        {
                            "email": m.get("email", ""),
                            "role": m.get("role", ""),
                            "user_id": m.get("user_id") or m.get("id", ""),
                            "is_local": email in local_emails,
                            "type": "member",
                        }
                    )
                # OpenAI 的 /invites 接口对已 cancelled 的邀请会立即从 items 中移除
                # (DELETE 成功后 GET 不再返回该项),所以这里不再需要按 status 字段过滤。
                # 但 OpenAI 的 status 字段是整数(2 = pending),不是字符串,留个 sanity 提示。
                for inv in invites:
                    email = (inv.get("email_address") or inv.get("email") or "").lower()
                    result.append(
                        {
                            "email": email,
                            "role": inv.get("role", ""),
                            "user_id": inv.get("id", ""),
                            "is_local": email in local_emails,
                            "type": "invite",
                        }
                    )
                return {"members": result, "total": len(members), "invites": len(invites)}

            return _run_with_chatgpt_session(_collect)

        try:
            return _pw_executor.run(_fetch_team_members)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("[API] 获取 Team 成员失败")
            raise HTTPException(status_code=502, detail=str(exc))
    finally:
        _playwright_lock.release()


@app.post("/api/team/members/remove")
def post_team_member_remove(
    params: TeamMemberRemoveParams,
    admin_id: str | None = Depends(get_current_admin_id),
):
    """移出 Team 成员或取消邀请。"""
    from autoteam.admin_state import get_admin_session_token, get_chatgpt_account_id

    if not _admin_state_call(get_admin_session_token, admin_id) or not _admin_state_call(
        get_chatgpt_account_id, admin_id
    ):
        raise HTTPException(status_code=400, detail="请先完成管理员登录")

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再操作"))

    try:
        from autoteam.accounts import find_account, load_accounts, update_account

        email = params.email.strip().lower()
        user_id = params.user_id.strip()
        member_type = params.type.strip().lower()

        if not email or not user_id:
            raise HTTPException(status_code=400, detail="缺少必要参数")
        if _email_is_main(email, admin_id):
            raise HTTPException(status_code=400, detail="主号不允许从 Team 成员页移出")
        if member_type not in ("member", "invite"):
            raise HTTPException(status_code=400, detail="无效的成员类型")

        account_id = _admin_state_call(get_chatgpt_account_id, admin_id)

        def _do_remove_team_member():
            def _remove(chatgpt):
                if member_type == "invite":
                    # OpenAI 当前的取消邀请接口是: DELETE /backend-api/accounts/{id}/invites
                    # (集合端点,不带 invite_id), body 里传 {"email_address": "..."} 标识要取消哪条。
                    # 历史上写过 "PATCH /invites/{id} {status: cancelled}" 是错的: 这个 endpoint
                    # 永远返回 200 {"success":true} 但实际是 no-op (OpenAI 网关的静默假成功),
                    # 所以前端表现为「点了没效果」。
                    path = f"/backend-api/accounts/{account_id}/invites"
                    result = chatgpt._api_fetch("DELETE", path, {"email_address": email})
                    return result, "取消邀请"
                path = f"/backend-api/accounts/{account_id}/users/{user_id}"
                result = chatgpt._api_fetch("DELETE", path)
                return result, "移出 Team"

            return _run_with_chatgpt_session(_remove)

        try:
            result, action_text = _pw_executor.run(_do_remove_team_member)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("[API] Team 成员移除失败")
            raise HTTPException(status_code=502, detail=str(exc))
        if result["status"] not in (200, 204):
            raise HTTPException(status_code=500, detail=f"{action_text}失败: HTTP {result['status']}")

        accounts = _admin_state_call(load_accounts, admin_id)
        acc = find_account(accounts, email)
        if acc:
            _admin_state_call(update_account, admin_id, email, status="standby")

        return {
            "message": f"已{action_text}: {email}",
            "email": email,
            "type": member_type,
        }
    finally:
        _playwright_lock.release()


# ---------------------------------------------------------------------------
# 日志收集
# ---------------------------------------------------------------------------

_log_buffer: list[dict] = []
_LOG_BUFFER_MAX = 500


class _LogCollector(logging.Handler):
    """收集日志到内存 buffer，供前端查询"""

    def emit(self, record):
        entry = {
            "time": record.created,
            "level": record.levelname,
            "message": self.format(record),
        }
        _log_buffer.append(entry)
        if len(_log_buffer) > _LOG_BUFFER_MAX:
            del _log_buffer[: len(_log_buffer) - _LOG_BUFFER_MAX]


_log_collector = _LogCollector()
_log_collector.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_log_collector)


@app.get("/api/logs")
def get_logs(limit: int = 100, since: float = 0):
    """获取最近的日志"""
    if since > 0:
        entries = [e for e in _log_buffer if e["time"] > since]
    else:
        entries = _log_buffer[-limit:]
    return {"logs": entries, "total": len(_log_buffer)}


@app.post("/api/sync/main-codex")
def post_sync_main_codex():
    """兼容旧接口：开始主号 Codex 登录并同步到已启用远端。"""
    return post_main_codex_start()


# ---------------------------------------------------------------------------
# 后台任务端点
# ---------------------------------------------------------------------------


@app.post("/api/tasks/check", status_code=202)
def post_check():
    """检查所有 active 账号额度（后台执行）"""
    from autoteam.manager import cmd_check

    def _run():
        exhausted = cmd_check(force_auth_repair=True)
        return {"exhausted": [a["email"] for a in exhausted]}

    task = _start_task("check", _run, {})
    return task


@app.post("/api/tasks/rotate", status_code=202)
def post_rotate(params: TaskParams = TaskParams()):
    """智能轮转（后台执行）"""
    _require_pool_operation_configs("智能轮转")

    from autoteam.manager import cmd_rotate

    task = _start_task(
        "rotate",
        lambda target: cmd_rotate(target, force_auth_repair=True),
        {"target": params.target},
        params.target,
    )
    return task


@app.post("/api/tasks/add", status_code=202)
def post_add():
    """添加新账号（后台执行）"""
    _require_pool_operation_configs("添加新账号")

    from autoteam.manager import cmd_add

    task = _start_task("add", cmd_add, {})
    return task


@app.post("/api/tasks/add-via-invite", status_code=202)
def post_add_via_invite():
    """通过母号邀请 + 邀请链接登录 + Codex OAuth 添加新账号（后台执行）"""
    _require_pool_operation_configs("邀请加号")

    from autoteam.manager import cmd_add_via_invite

    task = _start_task("add_via_invite", cmd_add_via_invite, {})
    return task


@app.post("/api/tasks/fill", status_code=202)
def post_fill(params: TaskParams = TaskParams()):
    """补满 Team 成员（后台执行）"""
    _require_pool_operation_configs("补满 Team 成员")

    from autoteam.manager import cmd_fill

    task = _start_task("fill", cmd_fill, {"target": params.target}, params.target)
    return task


@app.post("/api/tasks/cleanup", status_code=202)
def post_cleanup(params: CleanupParams = CleanupParams()):
    """清理多余成员（后台执行）"""
    from autoteam.manager import cmd_cleanup

    task = _start_task("cleanup", cmd_cleanup, {"max_seats": params.max_seats}, params.max_seats)
    return task


@app.get("/api/tasks")
def get_tasks(admin_id: str | None = None):
    """查看所有任务

    可选 admin_id query 参数过滤;不传时返回全部任务。前端 TaskHistory 用
    "仅当前 admin / 全部"切换时透传该参数。"all"视作不过滤(显式语义)。
    """
    if admin_id and admin_id != "all":
        filtered = [t for t in _tasks.values() if t.get("admin_id") == admin_id]
    else:
        filtered = list(_tasks.values())
    return sorted(filtered, key=lambda t: t["created_at"], reverse=True)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    """查看任务状态"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


# ---------------------------------------------------------------------------
# 免费号池(FREE)端点(PRD 04-29-free-account-generator)
# ---------------------------------------------------------------------------
#
# FREE 池与 active 池在存储 / UI / 后台任务三个维度全部独立(PRD D2 隔离铁律):
# - 存储独立:``data/admins/{admin_id}/free_accounts.json``,与 ``accounts.json`` 互不读写
# - 业务命令独立:``free_accounts.cmd_generate_free_account / check_free_quota /
#   delete_free_account``,与 ``manager.cmd_*`` 互相不可见
# - 任务调度独立:走和 active 池一样的 ``_start_task`` / ``_playwright_lock``,
#   但任务 command 名前缀 ``free_`` 用于区分,前端 TaskHistory 可按此分流
#
# 共享 sub2api group(PRD D3):FREE 池和 active 池写入同一个 ``SUB2API_GROUP``,
# 删除操作走 ``sub2api_sync.delete_account_from_sub2api`` 命中 ``_KIND_POOL``。


class FreeGenerateParams(BaseModel):
    """``POST /api/free/generate`` 入参。"""

    count: int = 1


class FreeCheckQuotaParams(BaseModel):
    """``POST /api/free/check_quota`` 入参。

    ``emails=None`` → 默认刷新全部 active/exhausted 免费号(R5 默认行为);
    显式传 list → 只刷新列表内的(包括 auth_failed 半成品)。
    """

    emails: list[str] | None = None


def _sanitize_free_record(rec: dict) -> dict:
    """返回前端可见的免费号 dict。

    PRD 要求 FreePage 提供"复制 email + password"按钮(给用户做 fallback 登录用),
    所以本端点会返回明文 password —— 调用方已经过 API Key 鉴权,与 ``/api/admin/*``
    返回管理员凭据的语义一致。注意:**前端不要把 password 写日志或截图**,
    后端 logger 也严守 logging-guidelines.md 的脱敏红线(本模块 logger 永远不
    传 password 入参)。
    """
    # 明文返回所有字段;此处保留 dict.copy 是为了避免前端直接持有 free_accounts.json
    # 引用,导致后续 update_free 写盘后前端误以为是新数据。
    return dict(rec)


@app.post("/api/free/generate", status_code=202)
def post_free_generate(params: FreeGenerateParams):
    """异步生成 N 个免费号(后台任务)。

    内部走 ``free_accounts.cmd_generate_free_account``,该命令串行处理 N 条,
    每轮包含完整 invite Step A + Codex OAuth Step B + remove + F2 二次确认 +
    落库。生成成功后会自动触发一次 FREE → sub2api 同步。

    返回 202 + 任务对象(含 ``task_id``),前端可轮询 ``GET /api/tasks/{task_id}``。
    """
    if params.count <= 0:
        raise HTTPException(status_code=400, detail="生成数量必须为正整数")

    # 与 cmd_add_via_invite 共用前置配置校验(母号会话 + sub2api / cloudmail 等运行时配置)
    _require_pool_operation_configs("生成免费号")

    from autoteam.free_accounts import cmd_generate_free_account

    task = _start_task(
        "free_generate",
        cmd_generate_free_account,
        {"count": params.count},
        params.count,
    )
    return task


@app.get("/api/free/list")
def get_free_list(admin_id: str | None = Depends(get_current_admin_id)):
    """返回当前 admin 的 FREE 池全部记录(含 last_quota 快照)。

    响应包含明文 ``password``(PRD 要求 FreePage 提供"复制 email + password"按钮);
    调用方已经过 API Key 鉴权,与 ``/api/admin/*`` 返回管理员凭据语义一致。详见
    :func:`_sanitize_free_record` 注释。
    """
    from autoteam.free_accounts import load_free

    records = _admin_state_call(load_free, admin_id)
    return [_sanitize_free_record(r) for r in records]


@app.post("/api/free/check_quota", status_code=202)
def post_free_check_quota(params: FreeCheckQuotaParams):
    """异步刷新免费号额度(后台任务)。

    - ``emails=None`` → 刷新全部 status ∈ {active, exhausted} 的免费号(R5 默认);
    - ``emails=[...]`` → 只刷新列表内的(包括 auth_failed 半成品),用于前端单条/选定刷新;
    - 401 自动调 ``codex_auth.refresh_access_token`` 写回 auth_file 后重试一次。

    串行执行,不并发。返回 202 + 任务对象。
    """
    from autoteam.free_accounts import check_free_quota

    task = _start_task(
        "free_check_quota",
        check_free_quota,
        {"emails": params.emails},
        params.emails,
    )
    return task


@app.post("/api/free/sync_sub2api")
def post_free_sync_sub2api():
    """触发一次 FREE → sub2api 同步(同步执行)。

    复用 ``sub2api_sync.sync_free_to_sub2api``;若有任务正在跑则返回 409。
    与 ``cmd_generate_free_account`` 末尾的自动同步是同一入口,这里只是给
    FreePage 提供"手动重试"按钮的兜底。
    """
    _require_sync_target_configs("同步免费号到 sub2api")

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再试"))

    try:
        from autoteam.sub2api_sync import sync_free_to_sub2api

        sync_free_to_sub2api()
        return {"message": "FREE 池已同步到 sub2api"}
    except Exception as exc:
        logger.error("[免费号] sub2api 同步失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"同步失败: {exc}") from exc
    finally:
        _playwright_lock.release()


@app.post("/api/free/{email}/reauth", status_code=202)
def post_free_reauth(email: str):
    """对单条已落库 FREE 号触发 Codex OAuth 重新授权(异步任务)。

    使用场景:FREE 号 token 失效(remove 后被服务端 invalidate / 过期等)时,
    用户在 FreePage 点「重新登录」按钮触发本端点。流程详见
    :func:`free_accounts.reauth_free_account`。

    成功后会自动覆盖 ``auth_file`` 与 status,并触发一次
    ``sync_free_to_sub2api`` 把新 bundle 推到远端。

    并发约束:全局任务锁(_playwright_lock)被占 → 409。同 email 已在
    reauth 中由 ``reauth_free_account`` 内部 ``_reauth_in_progress`` 二次保护。

    返回 202 + 任务对象,前端可轮询 ``GET /api/tasks/{task_id}``。
    """
    _require_pool_operation_configs(f"重新授权 FREE 号 {email}")

    from autoteam.free_accounts import reauth_free_account

    task = _start_task(
        "free_reauth",
        reauth_free_account,
        {"email": email},
        email,
    )
    return task


@app.delete("/api/free/{email}")
def delete_free_email(email: str, admin_id: str | None = Depends(get_current_admin_id)):
    """级联删除免费号(本地 auth_file + sub2api 远端 + cloudmail 邮箱 + JSON 条目)。

    每步独立 try/except(详见 :func:`free_accounts.delete_free_account`),
    单步失败不阻塞其他步骤,最终把 cleanup 摘要透传给前端用于 toast 展示。

    返回 cleanup 摘要 dict(R4 字段固定):
    ``{local_record, local_auth_files, sub2api_accounts, cloudmail_deleted}``。
    """
    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再删除免费号"))

    try:
        from autoteam.free_accounts import delete_free_account, find_free, load_free

        records = _admin_state_call(load_free, admin_id)
        if find_free(records, email) is None:
            raise HTTPException(status_code=404, detail="免费号不存在")

        cleanup = _admin_state_call(delete_free_account, admin_id, email, cleanup_remote=True)
        return {
            "message": "免费号删除完成",
            "deleted_email": email,
            "cleanup": cleanup,
        }
    finally:
        _playwright_lock.release()


# ---------------------------------------------------------------------------
# 后台自动巡检
# ---------------------------------------------------------------------------

from autoteam.config import (
    AUTO_CHECK_INTERVAL as _DEFAULT_INTERVAL,
)
from autoteam.config import (
    AUTO_CHECK_MIN_LOW as _DEFAULT_MIN_LOW,
)
from autoteam.config import (
    AUTO_CHECK_THRESHOLD as _DEFAULT_THRESHOLD,
)

# 运行时可修改的巡检配置
_auto_check_config = {
    "interval": _DEFAULT_INTERVAL,
    "threshold": _DEFAULT_THRESHOLD,
    "min_low": _DEFAULT_MIN_LOW,
}
_auto_check_stop = threading.Event()
_auto_check_restart = threading.Event()  # 配置变更时通知线程重启


def _playwright_probe_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "autoteam.playwright_probe", *args]


def _kill_subprocess_group(proc: subprocess.Popen):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_playwright_probe(*args: str, timeout_seconds: float = 30):
    cmd = _playwright_probe_command(*args)
    env = os.environ.copy()
    env["AUTOTEAM_PROBE_MODE"] = "1"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=max(1.0, float(timeout_seconds)))
    except subprocess.TimeoutExpired as exc:
        _kill_subprocess_group(proc)
        try:
            proc.communicate(timeout=1)
        except Exception:
            pass
        raise TimeoutError(f"Playwright probe timeout: {' '.join(args)}") from exc

    stdout = (stdout or "").strip()
    stderr = (stderr or "").strip()
    if proc.returncode != 0:
        detail = stderr or stdout or f"exit={proc.returncode}"
        raise RuntimeError(detail)

    if not stdout:
        return {}
    return _parse_playwright_probe_stdout(stdout)


def _parse_playwright_probe_stdout(stdout: str):
    text = (stdout or "").strip()
    if not text:
        return {}

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if not line.startswith(("{", "[")):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue

    return json.loads(text)


def _auto_check_team_member_count(timeout_seconds=30, retries=3):
    """查询 Team 实际成员数，供自动巡检的人数兜底判断使用。"""
    for attempt in range(1, max(1, retries) + 1):
        try:
            result = _run_playwright_probe("team-member-count", timeout_seconds=timeout_seconds)
        except TimeoutError:
            if attempt < retries:
                logger.warning(
                    "[巡检] 查询 Team 实际成员数超时（>%ss），准备重试第 %d/%d 次",
                    timeout_seconds,
                    attempt + 1,
                    retries,
                )
                continue
            logger.warning(
                "[巡检] 查询 Team 实际成员数超时（>%ss，已重试 %d 次），跳过本轮人数校验",
                timeout_seconds,
                retries,
            )
            return -1
        except Exception as exc:
            logger.warning("[巡检] 查询 Team 实际成员数失败: %s", exc)
            return -1

        try:
            return int(result.get("count", -1))
        except Exception:
            return -1

    return -1


def _auto_check_wait(interval_seconds, poll_seconds=0.2):
    """等待下一轮巡检，同时允许 stop / restart 尽快生效。"""
    interval = max(0.0, float(interval_seconds))
    poll = max(0.05, float(poll_seconds))
    deadline = time.monotonic() + interval

    while True:
        try:
            _maybe_reload_runtime_config_from_env_file()
        except Exception as exc:
            logger.warning("[配置] 自动热加载失败: %s", exc)

        if _auto_check_restart.is_set():
            _auto_check_restart.clear()
            return "restart"

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if _auto_check_stop.wait(0):
                return "stop"
            return "timeout"

        step = min(remaining, poll)
        if _auto_check_stop.wait(step):
            return "stop"


def _auto_check_loop():
    """后台巡检线程：按 admin 轮流跑额度检查 + 自动轮转/补位/清理/认证修复。

    PR2 多 admin 化要点：

    - 每轮按 ``admin_registry.list_admins()`` 顺序遍历所有 admin。
    - 单 admin 内逻辑保持原貌：``_collect_auto_check_state`` 收集状态，
      触发对应任务（轮转 / 清理 / 认证修复）。
    - admin 间错峰：每个 admin 跑完后 sleep ``interval / max(1, n_admins)``
      秒，避免一次性把所有 admin 的 Playwright 任务塞进队列。
    - 日志带 ``[admin=xxxxxxxx alias=xxx]`` 前缀（**不打** email/password/
      session_token，遵守 spec/backend/logging-guidelines.md 红线）。
    - 0 admin（全新部署）→ 整轮跳过，不抛异常，等下一周期。
    - 单个 admin 巡检失败不阻塞其他 admin。
    """
    from autoteam.accounts import STATUS_ACTIVE, STATUS_AUTH_PENDING, load_accounts
    from autoteam.codex_auth import check_codex_quota
    from autoteam.manager import (
        _auth_repair_skip_reason,
        _count_pool_active_accounts,
        _pool_active_target,
        sync_account_states,
    )

    target_seats = 5
    pool_active_target = _pool_active_target(target_seats)

    def _collect_auto_check_state(accounts, cfg, admin_id):
        account_by_email = {
            (a.get("email") or "").strip().lower(): a for a in accounts if (a.get("email") or "").strip()
        }
        local_active_count = _count_pool_active_accounts(accounts, require_auth=True)
        auth_pending_accounts = [
            a for a in accounts if a["status"] == STATUS_AUTH_PENDING and not _email_is_main(a.get("email"), admin_id)
        ]
        missing_auth_accounts = [
            a
            for a in accounts
            if a["status"] == STATUS_ACTIVE
            and not _email_is_main(a.get("email"), admin_id)
            and not (a.get("auth_file") and Path(a["auth_file"]).exists())
        ]
        active = [
            a
            for a in accounts
            if a["status"] == STATUS_ACTIVE
            and not _email_is_main(a.get("email"), admin_id)
            and a.get("auth_file")
            and Path(a["auth_file"]).exists()
        ]

        low_accounts = []
        auth_problem_accounts = []
        for acc in active:
            try:
                auth_data = json.loads(read_text(Path(acc["auth_file"])))
                access_token = auth_data.get("access_token")
                if not access_token:
                    continue
                status, info = check_codex_quota(access_token)
                if status == "ok" and isinstance(info, dict):
                    remaining = 100 - info.get("primary_pct", 0)
                    if remaining < cfg["threshold"]:
                        low_accounts.append((acc["email"], remaining, status, info))
                elif status == "exhausted":
                    low_accounts.append((acc["email"], 0, status, info))
                elif status == "auth_error":
                    auth_problem_accounts.append(acc["email"])
            except Exception:
                pass

        repair_candidates = list(auth_problem_accounts)
        if auth_pending_accounts:
            repair_candidates.extend(a["email"] for a in auth_pending_accounts)
        if missing_auth_accounts:
            repair_candidates.extend(a["email"] for a in missing_auth_accounts)
        repair_candidates = list(dict.fromkeys(repair_candidates))

        actionable_repair_candidates = []
        throttled_repair_candidates = []
        for candidate_email in repair_candidates:
            acc = account_by_email.get(candidate_email.lower())
            skip_reason = _auth_repair_skip_reason(acc, force=False)
            if skip_reason:
                throttled_repair_candidates.append((candidate_email, skip_reason))
            else:
                actionable_repair_candidates.append(candidate_email)

        return {
            "accounts": accounts,
            "account_by_email": account_by_email,
            "local_active_count": local_active_count,
            "auth_pending_accounts": auth_pending_accounts,
            "missing_auth_accounts": missing_auth_accounts,
            "active": active,
            "low_accounts": low_accounts,
            "auth_problem_accounts": auth_problem_accounts,
            "repair_candidates": repair_candidates,
            "actionable_repair_candidates": actionable_repair_candidates,
            "throttled_repair_candidates": throttled_repair_candidates,
        }

    def _check_single_admin(admin_id: str | None, alias: str, cfg: dict[str, int]) -> None:
        """对单个 admin 跑一轮巡检，**所有数据层调用都显式带 admin_id**。

        触发的任务（cmd_rotate / cmd_check / cmd_cleanup）当前不接受 admin_id，
        本轮已通过外层 ``set_active_admin`` 把激活点切到该 admin，所以它们读到
        的是同一份 accounts.json（PRD 决策"切换激活+串行"）。

        ``admin_id=None`` 时为兼容模式：跳过 set_active_admin，所有数据层调用走
        其内置 fallback（旧版 state.json / accounts.json）。
        """
        log_prefix = f"[巡检][admin={admin_id or '<none>'} alias={alias}]"
        # 切激活：影响 manager.cmd_* 通过数据层 fallback 读到的目标 admin
        if admin_id:
            try:
                admin_registry.set_active_admin(admin_id)
            except ValueError as exc:
                logger.warning("%s 切换激活失败（已忽略，跳过本 admin）: %s", log_prefix, exc)
                return

        accounts = _admin_state_call(load_accounts, admin_id)
        state = _collect_auto_check_state(accounts, cfg, admin_id)
        local_active_count = state["local_active_count"]
        low_accounts = state["low_accounts"]
        auth_problem_accounts = state["auth_problem_accounts"]

        if low_accounts:
            logger.info(
                "%s %d 个账号额度不足: %s",
                log_prefix,
                len(low_accounts),
                ", ".join(f"{e}({r}%)" for e, r, _status, _info in low_accounts),
            )
        if auth_problem_accounts:
            logger.info(
                "%s %d 个账号认证待修复: %s",
                log_prefix,
                len(auth_problem_accounts),
                ", ".join(auth_problem_accounts),
            )

        seat_shortage = max(0, target_seats - 1 - local_active_count)
        actual_team_count = -1
        team_count_check_failed = False
        trigger_rotate = len(low_accounts) >= cfg["min_low"]
        trigger_cleanup = False
        trigger_auth_repair = False
        actionable_repair_candidates = state["actionable_repair_candidates"]
        throttled_repair_candidates = state["throttled_repair_candidates"]

        if not trigger_rotate:
            actual_team_count = _auto_check_team_member_count()
            if actual_team_count < 0:
                team_count_check_failed = True
            elif actual_team_count > target_seats:
                trigger_cleanup = True
                seat_shortage = 0
            else:
                team_shortage = max(0, target_seats - actual_team_count)
                trigger_rotate = team_shortage > 0
                if not trigger_rotate and actual_team_count >= target_seats and actionable_repair_candidates:
                    trigger_auth_repair = True

            if (
                not trigger_rotate
                and not trigger_cleanup
                and not trigger_auth_repair
                and actual_team_count >= target_seats
                and local_active_count < pool_active_target
                and not throttled_repair_candidates
            ):
                logger.info(
                    "%s Team 实际成员数已满足（%d/%d），但本地可用 active 仅 %d/%d，先同步本地 Team 状态后重试判断...",
                    log_prefix,
                    actual_team_count,
                    target_seats,
                    local_active_count,
                    pool_active_target,
                )
                try:
                    sync_account_states()
                except Exception as exc:
                    logger.warning("%s 同步本地 Team 状态失败，继续使用当前本地状态: %s", log_prefix, exc)
                else:
                    accounts = _admin_state_call(load_accounts, admin_id)
                    state = _collect_auto_check_state(accounts, cfg, admin_id)
                    local_active_count = state["local_active_count"]
                    low_accounts = state["low_accounts"]
                    actionable_repair_candidates = state["actionable_repair_candidates"]
                    throttled_repair_candidates = state["throttled_repair_candidates"]
                    seat_shortage = max(0, target_seats - 1 - local_active_count)
                    trigger_rotate = len(low_accounts) >= cfg["min_low"]
                    if not trigger_rotate and actionable_repair_candidates:
                        trigger_auth_repair = True

        if trigger_rotate or trigger_cleanup or trigger_auth_repair:
            # 检查是否有任务在跑
            if not _playwright_lock.acquire(blocking=False):
                logger.info("%s 有任务正在执行，跳过本轮自动轮转/补位/清理/认证修复", log_prefix)
                return
            _playwright_lock.release()

            if trigger_rotate:
                try:
                    _require_pool_operation_configs("自动轮转/补位")
                except HTTPException as exc:
                    logger.warning("%s 跳过自动轮转/补位: %s", log_prefix, exc.detail)
                    return

                # 将低于阈值的账号标记为 exhausted，rotate 会自动移出并补充
                from autoteam.accounts import STATUS_EXHAUSTED, update_account
                from autoteam.codex_auth import quota_result_quota_info, quota_result_resets_at

                for email, remaining, status, info in low_accounts:
                    logger.info("%s %s 剩余 %d%%，标记为 exhausted", log_prefix, email, remaining)
                    status_kwargs = {
                        "status": STATUS_EXHAUSTED,
                        "quota_exhausted_at": time.time(),
                    }
                    if status == "ok":
                        status_kwargs["last_quota"] = info if isinstance(info, dict) else None
                        status_kwargs["quota_resets_at"] = (
                            info.get("primary_resets_at") if isinstance(info, dict) else None
                        ) or int(time.time() + 18000)
                    else:
                        status_kwargs["last_quota"] = quota_result_quota_info(info)
                        status_kwargs["quota_resets_at"] = quota_result_resets_at(info) or int(time.time() + 18000)
                    _admin_state_call(update_account, admin_id, email, **status_kwargs)

                if seat_shortage > 0 and len(low_accounts) >= cfg["min_low"]:
                    logger.info(
                        "%s 当前可用 active 数不足: %d/%d，且检测到低额度账号，触发自动轮转...",
                        log_prefix,
                        local_active_count,
                        pool_active_target,
                    )
                elif actual_team_count >= 0 and actual_team_count < target_seats:
                    logger.info(
                        "%s Team 实际成员数不足（%d/%d），触发自动补位...",
                        log_prefix,
                        actual_team_count,
                        target_seats,
                    )
                else:
                    logger.info("%s 触发自动轮转...", log_prefix)
                from autoteam.manager import cmd_rotate

                try:
                    _start_task(
                        "auto-rotate",
                        cmd_rotate,
                        {
                            "target": target_seats,
                            "trigger": "auto-check",
                            "admin_id": admin_id,
                            "shortage": max(0, target_seats - actual_team_count)
                            if actual_team_count >= 0
                            else seat_shortage,
                            "low_accounts": len(low_accounts),
                        },
                        target_seats,
                    )
                except Exception as e:
                    logger.error("%s 自动轮转失败: %s", log_prefix, e)
            elif trigger_auth_repair:
                try:
                    _require_pool_operation_configs("自动认证修复")
                except HTTPException as exc:
                    logger.warning("%s 跳过自动认证修复: %s", log_prefix, exc.detail)
                    return

                logger.info(
                    "%s Team 实际成员数已满足（%d/%d），但可用 Codex active 仅 %d/%d，触发自动认证修复...",
                    log_prefix,
                    actual_team_count,
                    target_seats,
                    local_active_count,
                    pool_active_target,
                )
                from autoteam.manager import cmd_check

                try:
                    _start_task(
                        "auto-auth-repair",
                        cmd_check,
                        {
                            "trigger": "auto-check",
                            "admin_id": admin_id,
                            "team_count": actual_team_count,
                            "pool_active": local_active_count,
                            "pool_active_target": pool_active_target,
                            "repair_candidates": actionable_repair_candidates,
                        },
                    )
                except Exception as e:
                    logger.error("%s 自动认证修复失败: %s", log_prefix, e)
            else:
                logger.info(
                    "%s Team 实际成员数超出目标（%d/%d），触发自动清理...",
                    log_prefix,
                    actual_team_count,
                    target_seats,
                )
                from autoteam.manager import cmd_cleanup

                try:
                    _start_task(
                        "auto-cleanup",
                        cmd_cleanup,
                        {
                            "max_seats": target_seats,
                            "trigger": "auto-check",
                            "admin_id": admin_id,
                            "team_count": actual_team_count,
                        },
                        target_seats,
                    )
                except Exception as e:
                    logger.error("%s 自动清理失败: %s", log_prefix, e)
        else:
            if low_accounts and actual_team_count >= target_seats:
                logger.info(
                    "%s 低额度账号未达到触发阈值（%d/%d），且 Team 实际成员数已满足（%d/%d），无需轮转",
                    log_prefix,
                    len(low_accounts),
                    cfg["min_low"],
                    actual_team_count,
                    target_seats,
                )
            elif low_accounts:
                logger.info(
                    "%s 低额度账号未达到触发阈值（%d/%d），无需轮转",
                    log_prefix,
                    len(low_accounts),
                    cfg["min_low"],
                )
            elif team_count_check_failed:
                logger.info("%s Team 成员数校验失败，且未达到低额度触发阈值，跳过本轮自动动作", log_prefix)
            elif actual_team_count >= target_seats and actionable_repair_candidates:
                logger.info(
                    "%s Team 实际成员数已满足（%d/%d），但存在 %d 个待修复账号，等待下一轮自动认证修复",
                    log_prefix,
                    actual_team_count,
                    target_seats,
                    len(actionable_repair_candidates),
                )
            elif actual_team_count >= target_seats and throttled_repair_candidates:
                logger.info(
                    "%s Team 实际成员数已满足（%d/%d），但 %d 个待修复账号仍在冷却/暂停中，暂不自动重试",
                    log_prefix,
                    actual_team_count,
                    target_seats,
                    len(throttled_repair_candidates),
                )
            elif actual_team_count >= target_seats and local_active_count < pool_active_target:
                logger.info(
                    "%s Team 实际成员数已满足（%d/%d），但本地可用 active 仅 %d/%d，且未发现可自动修复的本地账号",
                    log_prefix,
                    actual_team_count,
                    target_seats,
                    local_active_count,
                    pool_active_target,
                )
            else:
                logger.info(
                    "%s 额度正常且 active 数充足（%d/%d），无需轮转",
                    log_prefix,
                    local_active_count,
                    pool_active_target,
                )

    while not _auto_check_stop.is_set():
        try:
            _maybe_reload_runtime_config_from_env_file()
        except Exception as exc:
            logger.warning("[配置] 自动热加载失败: %s", exc)

        cfg = _auto_check_config
        logger.info(
            "[巡检] 等待 %d 分钟后执行下一轮检查（阈值: %d%%, 触发: >=%d 个）",
            cfg["interval"] // 60,
            cfg["threshold"],
            cfg["min_low"],
        )

        # 等待 interval 秒，期间可被 restart 或 stop 唤醒
        wait_result = _auto_check_wait(cfg["interval"])
        if wait_result == "stop":
            break
        if wait_result == "restart":
            continue  # 配置变更，跳到下一轮重新读取配置

        cfg = _auto_check_config  # 重新读取
        # 快照本轮 admin 列表（中途新增的 admin 留到下一轮，避免破坏迭代语义）
        admins_snapshot = list(admin_registry.list_admins())
        n_admins = len(admins_snapshot)
        if n_admins == 0:
            # 全新部署/未注册任何 admin：兼容旧行为，按 admin_id=None 跑一轮
            # （数据层 fallback 到模块级 STATE_FILE/ACCOUNTS_FILE）。
            try:
                _check_single_admin(None, "<unconfigured>", cfg)
            except Exception as e:
                logger.error("[巡检] 巡检异常: %s", e)
            continue

        # 错峰间隔：每个 admin 跑完后睡这么久，避免连续触发 Playwright 任务把队列塞满。
        per_admin_pause = max(0.0, cfg["interval"] / n_admins)

        for idx, admin in enumerate(admins_snapshot):
            if _auto_check_stop.is_set():
                break
            try:
                _check_single_admin(admin.admin_id, admin.alias or admin.admin_id, cfg)
            except Exception as e:
                # 单 admin 异常不阻塞其他 admin
                logger.error("[巡检][admin=%s] 巡检异常: %s", admin.admin_id, e)

            # 最后一个 admin 之后不需要再睡，直接进下一轮的 _auto_check_wait
            if idx < n_admins - 1 and per_admin_pause > 0:
                if _auto_check_stop.wait(per_admin_pause):
                    break


class AutoCheckConfig(BaseModel):
    interval: int = 300  # 巡检间隔（秒）
    threshold: int = 10  # 额度阈值（%）
    min_low: int = 2  # 触发轮转的最少账号数


def _normalized_auto_check_config(cfg: AutoCheckConfig | dict[str, int]) -> dict[str, int]:
    if isinstance(cfg, AutoCheckConfig):
        interval = cfg.interval
        threshold = cfg.threshold
        min_low = cfg.min_low
    else:
        interval = cfg.get("interval", _auto_check_config.get("interval", _DEFAULT_INTERVAL))
        threshold = cfg.get("threshold", _auto_check_config.get("threshold", _DEFAULT_THRESHOLD))
        min_low = cfg.get("min_low", _auto_check_config.get("min_low", _DEFAULT_MIN_LOW))

    return {
        "interval": max(60, int(interval)),
        "threshold": max(1, min(100, int(threshold))),
        "min_low": max(1, int(min_low)),
    }


@app.get("/api/config/auto-check")
def get_auto_check_config():
    """获取巡检配置"""
    return _auto_check_config.copy()


@app.put("/api/config/auto-check")
def set_auto_check_config(cfg: AutoCheckConfig):
    """修改巡检配置（运行时生效，并持久化到 .env）"""
    from autoteam.setup_wizard import _write_env

    normalized = _normalized_auto_check_config(cfg)
    _auto_check_config.update(normalized)

    persisted = {
        "AUTO_CHECK_INTERVAL": str(normalized["interval"]),
        "AUTO_CHECK_THRESHOLD": str(normalized["threshold"]),
        "AUTO_CHECK_MIN_LOW": str(normalized["min_low"]),
    }
    for key, value in persisted.items():
        os.environ[key] = value
        _write_env(key, value)

    _sync_runtime_env_reload_state()
    _auto_check_restart.set()  # 唤醒巡检线程，立即应用新配置
    logger.info(
        "[巡检] 配置已更新并持久化: 间隔=%ds 阈值=%d%% 触发=%d个",
        _auto_check_config["interval"],
        _auto_check_config["threshold"],
        _auto_check_config["min_low"],
    )
    return _auto_check_config.copy()


@app.on_event("startup")
def _start_auto_check():
    try:
        from autoteam.admin_registry import bootstrap_admin_registry

        bootstrap_admin_registry()
    except Exception as exc:
        logger.warning("[启动] admin_registry 初始化失败: %s", exc)

    try:
        from autoteam.auth_storage import ensure_auth_file_permissions

        fixed = ensure_auth_file_permissions()
        if fixed:
            logger.info("[启动] 已修复 %d 个 auths 认证文件权限", fixed)
    except Exception as exc:
        logger.warning("[启动] 修复 auths 认证文件权限失败: %s", exc)

    _sync_runtime_env_reload_state()
    thread = threading.Thread(target=_auto_check_loop, daemon=True)
    thread.start()


@app.on_event("shutdown")
def _stop_auto_check():
    _auto_check_stop.set()
    try:
        _pw_executor.stop()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 前端静态文件
# ---------------------------------------------------------------------------

DIST_DIR = Path(__file__).parent / "web" / "dist"

if DIST_DIR.exists():
    # Vite 构建的 assets 目录
    assets_dir = DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{path:path}")
    def serve_frontend(path: str):
        """兜底路由：serve 前端 SPA"""
        file = DIST_DIR / path
        if file.is_file() and ".." not in path:
            return FileResponse(str(file))
        return FileResponse(str(DIST_DIR / "index.html"))


class _QuietAccessLog(logging.Filter):
    """过滤前端轮询产生的高频访问日志"""

    _quiet_paths = (
        "/api/status",
        "/api/tasks",
        "/api/config/auto-check",
        "/api/config/runtime",
        "/api/admin/status",
        "/api/main-codex/status",
        "/api/auth/check",
        "/api/setup/status",
    )

    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self._quiet_paths)


def start_server(host: str = "0.0.0.0", port: int = 8787):
    """启动 API 服务器"""
    import uvicorn

    # 过滤轮询日志，避免刷屏
    logging.getLogger("uvicorn.access").addFilter(_QuietAccessLog())
    # 首次启动检查配置
    from autoteam.setup_wizard import check_and_setup

    check_and_setup(interactive=True)

    # 重新读取 API_KEY（可能刚刚被向导写入）
    global API_KEY
    from autoteam.config import API_KEY as _fresh_key

    API_KEY = _fresh_key or os.environ.get("API_KEY", "")
    if API_KEY:
        logger.info("[API] API Key 鉴权已启用")
    else:
        logger.warning("[API] 未设置 API_KEY，所有接口无需认证")
    logger.info("[API] 启动 AutoTeam API 服务器 http://%s:%d", host, port)
    if DIST_DIR.exists():
        logger.info("[API] 前端面板 http://%s:%d", host, port)
    logger.info("[API] API 文档 http://%s:%d/docs", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
