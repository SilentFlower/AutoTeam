"""HeroSMS 接码客户端 + add-phone HTTP 流程。

参考实现: /root/project/codex-phone/src/core/herosms_client.py

设计目标 (与 codex-phone 对齐, 但简化掉双平台 / 磁盘持久化):
1. SMS-Activate 兼容协议封装(``HeroSmsClient``)
   - ``getNumberV2`` 优先, 失败回退 ``getNumber``
   - 多源轮询拿验证码: ``getStatusV2`` → ``getStatus`` → ``getActiveActivations``
   - 基于 ``sha256(activation_id+code+dateTime)`` 做 SMS 事件去重
2. 走 OpenAI HTTP API 完成 add-phone (``handle_add_phone_via_http``)
   - ``/api/accounts/add-phone/send`` 提交手机号
   - ``/api/accounts/phone-otp/validate`` 提交验证码
   - 每 120s 触发一次 ``/api/accounts/phone-otp/resend`` + 平台 ``setStatus=3``
   - 精准识别号码上限 / VoIP 拒绝 / OTP 错误等
3. 号码 20 分钟内存复用(默认每个号码最多成功验证 3 次)
4. 进程内线程锁(``_phone_verify_lock``)序列化整个 add-phone, 避免并发抢码
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.parse

import requests

logger = logging.getLogger(__name__)

# ── 协议常量 ─────────────────────────────────────────────────
SMS_STATUS_SENT = 1     # 已发送短信, 等待用户输入
SMS_STATUS_RETRY = 3    # 请求平台重发
SMS_STATUS_FINISH = 6   # 完成激活(成功收到并使用了 code)
SMS_STATUS_CANCEL = 8   # 取消激活(返还资金, 仅在未上报"已发送"前有效)

# ── 默认配置 ─────────────────────────────────────────────────
DEFAULT_BASE_URL = "https://hero-sms.com/stubs/handler_api.php"
# hero-sms 上 OpenAI 的服务代码是 ``dr``(不是 SMS-Activate 行业惯例的 ``oai``)
DEFAULT_SERVICE = "dr"
# hero-sms 自家国家 ID 与 SMS-Activate 标准不同, 187 = USA, 0 = Russia
DEFAULT_COUNTRY = "187"
DEFAULT_TIMEOUT = 30
DEFAULT_SMS_WAIT_SECONDS = 180
DEFAULT_PHONE_REUSE_MAX = 3
DEFAULT_PHONE_LIFETIME = 20 * 60
OPENAI_RESEND_AFTER_SECONDS = 120


# ══════════════════════════════════════════════════════════════
# 异常
# ══════════════════════════════════════════════════════════════


class HeroSmsError(Exception):
    """HeroSMS 协议错误。``code`` 保留平台短代码(如 ``NO_NUMBERS``)便于上层分支。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class OpenAIResendError(RuntimeError):
    """OpenAI 拒绝 phone-otp/resend(常见于 HTTP 400)。"""

    def __init__(self, status_code: int, body: str = "") -> None:
        self.status_code = status_code
        self.body = body
        suffix = f" {body}" if body else ""
        super().__init__(f"OpenAI phone-otp/resend HTTP {status_code}{suffix}")


# ══════════════════════════════════════════════════════════════
# HeroSmsClient
# ══════════════════════════════════════════════════════════════


class HeroSmsClient:
    """HeroSMS / SMS-Activate 兼容协议客户端。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        service: str = DEFAULT_SERVICE,
        country: str | int = DEFAULT_COUNTRY,
        max_price: float = 0,
        operator: str = "",
        timeout: int = DEFAULT_TIMEOUT,
        sms_wait_seconds: int = DEFAULT_SMS_WAIT_SECONDS,
        proxy: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("HeroSMS api_key 不能为空")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("?")
        self.service = (service or DEFAULT_SERVICE).strip() or DEFAULT_SERVICE
        self.country = str(country if country not in (None, "") else DEFAULT_COUNTRY)
        self.max_price = float(max_price or 0)
        self.operator = (operator or "").strip()
        self.timeout = int(timeout or DEFAULT_TIMEOUT)
        self.sms_wait_seconds = int(sms_wait_seconds or DEFAULT_SMS_WAIT_SECONDS)
        self.session = session or requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    # ── 内部工具 ─────────────────────────────────────────────
    def _get(self, action: str, *, needs_key: bool = True, **params) -> requests.Response:
        query: dict = {"action": action}
        if needs_key or self.api_key:
            query["api_key"] = self.api_key
        for key, value in params.items():
            if value is None or value == "":
                continue
            query[key] = value
        # 日志中隐去 api_key, 避免泄漏
        log_query = dict(query)
        if "api_key" in log_query:
            log_query["api_key"] = "***"
        logger.debug("[HeroSMS] GET %s?%s", self.base_url, urllib.parse.urlencode(log_query))
        try:
            resp = self.session.get(self.base_url, params=query, timeout=self.timeout)
        except requests.RequestException as exc:
            raise HeroSmsError("NETWORK_ERROR", f"请求 HeroSMS 失败: {exc}") from exc
        if resp.status_code != 200 and resp.status_code != 204:
            raise HeroSmsError(
                "HTTP_ERROR",
                f"HeroSMS HTTP {resp.status_code}: {(resp.text or '')[:200]}",
            )
        return resp

    @staticmethod
    def _to_int(value, default=None):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value, default=None):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _split_response(text: str) -> tuple[str, str]:
        text = (text or "").strip()
        if ":" not in text:
            return text, ""
        head, _, tail = text.partition(":")
        return head, tail

    # ── 公共能力 ─────────────────────────────────────────────
    def get_balance(self) -> float:
        """查询账户余额(``ACCESS_BALANCE:100.5``)。"""
        text = (self._get("getBalance").text or "").strip()
        head, tail = self._split_response(text)
        if head != "ACCESS_BALANCE":
            raise HeroSmsError(head or "UNKNOWN", f"获取余额失败: {text}")
        return self._to_float(tail, 0.0)

    def get_countries(self) -> list[dict]:
        """获取国家列表。"""
        resp = self._get("getCountries", needs_key=False)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise HeroSmsError("BAD_RESPONSE", f"解析国家列表失败: {(resp.text or '')[:120]}") from exc
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = list(data.values())
        else:
            raise HeroSmsError("BAD_RESPONSE", f"国家列表结构异常: {(resp.text or '')[:120]}")
        normalized = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["id"] = self._to_int(raw.get("id"), raw.get("id"))
            item["rus"] = str(raw.get("rus") or "")
            item["eng"] = str(raw.get("eng") or raw.get("name") or item["id"])
            item["chn"] = str(raw.get("chn") or raw.get("eng") or raw.get("name") or item["id"])
            normalized.append(item)
        return normalized

    def get_services_list(self, country: str | int | None = None, lang: str = "cn") -> list[dict]:
        """获取服务列表(可按国家筛选)。"""
        params: dict = {"lang": lang}
        if country not in (None, ""):
            params["country"] = country
        resp = self._get("getServicesList", needs_key=False, **params)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise HeroSmsError("BAD_RESPONSE", f"解析服务列表失败: {(resp.text or '')[:120]}") from exc
        services = data.get("services") if isinstance(data, dict) else data
        if not isinstance(services, list):
            raise HeroSmsError("BAD_RESPONSE", f"服务列表结构异常: {(resp.text or '')[:120]}")
        normalized = []
        for item in services:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            normalized.append({"code": code, "name": str(item.get("name") or code).strip()})
        return normalized

    def get_prices(self, service: str | None = None, country: str | int | None = None) -> dict:
        params = {}
        if service is not None:
            params["service"] = service
        if country is not None:
            params["country"] = country
        resp = self._get("getPrices", **params)
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise HeroSmsError("BAD_RESPONSE", f"解析价格失败: {(resp.text or '')[:120]}") from exc

    def request_number(
        self,
        service: str | None = None,
        country: str | int | None = None,
        max_price: float | None = None,
    ) -> dict:
        """申请号码(优先 V2 JSON, 失败回退 V1 文本)。返回 dict。"""
        common = {
            "service": service or self.service,
            "country": country if country is not None else self.country,
        }
        price = max_price if max_price is not None else (self.max_price if self.max_price > 0 else None)
        if price and price > 0:
            common["maxPrice"] = price
        if self.operator:
            common["operator"] = self.operator

        # ── V2 (JSON) ──
        v2_err = ""
        try:
            resp = self._get("getNumberV2", **common)
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                data = None
            if isinstance(data, dict) and "activationId" in data:
                logger.info(
                    "[HeroSMS] getNumberV2 ok: id=%s phone=%s cost=%s",
                    data.get("activationId"),
                    data.get("phoneNumber"),
                    data.get("activationCost"),
                )
                return data
            v2_err = f"非预期 V2 响应: {(resp.text or '')[:200]}"
        except HeroSmsError as exc:
            v2_err = str(exc)
            # 平台错误码(如 NO_NUMBERS / NO_BALANCE)直接抛, 不要回退 V1
            if exc.code and exc.code not in {"HTTP_ERROR", "NETWORK_ERROR"}:
                raise
        except Exception as exc:  # pragma: no cover - 防御
            v2_err = str(exc)

        logger.warning("[HeroSMS] getNumberV2 失败 (%s), 尝试回退 getNumber", v2_err)

        # ── V1 文本 ACCESS_NUMBER:id:phone ──
        try:
            resp = self._get("getNumber", **common)
            text = (resp.text or "").strip()
            head, tail = self._split_response(text)
            if head == "ACCESS_NUMBER":
                if ":" in tail:
                    activation_id, _, phone = tail.partition(":")
                else:
                    activation_id, phone = tail, ""
                return {
                    "activationId": activation_id.strip(),
                    "phoneNumber": phone.strip(),
                    "countryPhoneCode": "",
                    "activationCost": None,
                }
            raise HeroSmsError(head or "UNKNOWN", f"申请号码失败: {text}")
        except HeroSmsError:
            raise
        except Exception as exc:  # pragma: no cover - 防御
            raise HeroSmsError("BAD_RESPONSE", f"申请号码失败: V2={v2_err}; V1={exc}") from exc

    # ── 状态 ─────────────────────────────────────────────────
    @staticmethod
    def _parse_status_text(text: str) -> dict:
        text = (text or "").strip()
        if text == "STATUS_WAIT_CODE":
            return {"status": "wait_code"}
        if text.startswith("STATUS_WAIT_RETRY"):
            return {"status": "wait_retry", "raw": text}
        if text == "STATUS_WAIT_RESEND":
            return {"status": "wait_resend"}
        if text.startswith("STATUS_OK:"):
            return {"status": "ok", "code": text.split(":", 1)[1]}
        if text == "STATUS_CANCEL":
            return {"status": "cancel"}
        return {"status": "unknown", "raw": text}

    def get_status(self, activation_id: str) -> dict:
        resp = self._get("getStatus", id=activation_id)
        return self._parse_status_text(resp.text)

    def get_status_v2(self, activation_id: str) -> dict:
        """返回结构化状态, 包含 SMS dateTime 等元数据(用于去重)。"""
        resp = self._get("getStatusV2", id=activation_id)
        text = (resp.text or "").strip()
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return self._parse_status_text(text)
        if isinstance(data, str):
            return self._parse_status_text(data)
        if not isinstance(data, dict):
            return {"status": "unknown", "raw": data}
        raw_status = data.get("status")
        if isinstance(raw_status, str):
            parsed = self._parse_status_text(raw_status)
            if parsed.get("status") != "unknown":
                return parsed
        sms = data.get("sms")
        if isinstance(sms, dict):
            candidate = self._make_sms_candidate(
                activation_id,
                "getStatusV2.sms",
                sms.get("code"),
                {
                    "channel": "sms",
                    "dateTime": sms.get("dateTime"),
                    "text": sms.get("text"),
                    "verificationType": data.get("verificationType"),
                },
            )
            if candidate:
                return candidate
        call = data.get("call")
        if isinstance(call, dict):
            candidate = self._make_sms_candidate(
                activation_id,
                "getStatusV2.call",
                call.get("code"),
                {
                    "channel": "call",
                    "dateTime": call.get("dateTime"),
                    "text": call.get("text"),
                    "from": call.get("from"),
                    "url": call.get("url"),
                    "verificationType": data.get("verificationType"),
                },
            )
            if candidate:
                return candidate
        return {"status": "wait_code", "raw": data}

    def get_active_activations(self, start: int = 0, limit: int = 20) -> list[dict]:
        resp = self._get("getActiveActivations", start=start, limit=limit)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return []
        if isinstance(data, dict):
            if data.get("status") == "success":
                return data.get("data", []) or []
            if "data" in data:
                return data.get("data", []) or []
        return []

    def set_status(self, activation_id: str, status: int) -> str:
        resp = self._get("setStatus", id=activation_id, status=int(status))
        return (resp.text or "").strip()

    def request_resend_sms(self, activation_id: str) -> str:
        return self.set_status(activation_id, SMS_STATUS_RETRY)

    def cancel_activation(self, activation_id: str) -> bool:
        try:
            resp = self._get("cancelActivation", id=activation_id)
            if resp.status_code == 204 or "ACCESS_CANCEL" in (resp.text or ""):
                logger.info("[HeroSMS] 激活 %s 已通过 cancelActivation 取消", activation_id)
                return True
        except HeroSmsError as exc:
            logger.warning("[HeroSMS] cancelActivation 失败: %s", exc)
        try:
            text = self.set_status(activation_id, SMS_STATUS_CANCEL)
            if "ACCESS_CANCEL" in text:
                logger.info("[HeroSMS] 激活 %s 已通过 setStatus(8) 取消", activation_id)
                return True
        except HeroSmsError as exc:
            logger.warning("[HeroSMS] setStatus(8) 取消 fallback 失败: %s", exc)
        return False

    def finish_activation(self, activation_id: str) -> bool:
        try:
            resp = self._get("finishActivation", id=activation_id)
            text = (resp.text or "").strip()
            if resp.status_code in (200, 204) or "ACCESS" in text:
                logger.info("[HeroSMS] 激活 %s 已 finish", activation_id)
                return True
            logger.warning("[HeroSMS] finishActivation 非预期响应: %s", text)
        except HeroSmsError as exc:
            logger.warning("[HeroSMS] finishActivation 失败: %s", exc)
        try:
            self.set_status(activation_id, SMS_STATUS_FINISH)
            return True
        except HeroSmsError:
            return False

    # ── SMS 候选 / 去重 ──────────────────────────────────────
    @staticmethod
    def _valid_sms_code(code) -> str:
        text = str(code or "").strip()
        if not text or text in {"null", "None"}:
            return ""
        return text

    @staticmethod
    def _canonical_sms_event_fields(fields: dict | None) -> dict:
        fields = fields or {}
        canonical = {}
        if fields.get("channel"):
            canonical["channel"] = fields["channel"]
        sms_time = (
            fields.get("dateTime")
            or fields.get("date")
            or fields.get("smsDate")
            or fields.get("smsTime")
            or ""
        )
        if sms_time:
            canonical["time"] = sms_time
        text = fields.get("text") or fields.get("smsText")
        if text:
            canonical["text"] = text
        if fields.get("channel") == "call":
            for key in ("from", "url"):
                if fields.get(key):
                    canonical[key] = fields[key]
        if not sms_time:
            for key in ("repeated", "activationStatus", "verificationType"):
                if fields.get(key) is not None:
                    canonical[key] = fields[key]
        return canonical

    @staticmethod
    def _has_real_sms_time(fields: dict | None) -> bool:
        raw = (fields or {}).get("dateTime") or (fields or {}).get("date") \
            or (fields or {}).get("smsDate") or (fields or {}).get("smsTime") or ""
        raw = str(raw).strip()
        return bool(raw and raw not in {"0", "0000-00-00 00:00:00", "0000-00-00T00:00:00"})

    @classmethod
    def _sms_event_key(cls, activation_id: str, code: str, fields: dict | None) -> str:
        canonical = cls._canonical_sms_event_fields(fields)
        identity = {"activation_id": str(activation_id), "code": code}
        identity.update(
            {str(k): str(v).strip() for k, v in canonical.items() if v not in (None, "")}
        )
        raw = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _make_sms_candidate(
        self,
        activation_id: str,
        source: str,
        code,
        fields: dict | None = None,
    ) -> dict | None:
        code_text = self._valid_sms_code(code)
        if not code_text:
            return None
        fields = fields or {}
        canonical = self._canonical_sms_event_fields(fields)
        sms_key = self._sms_event_key(activation_id, code_text, fields) if fields else None
        return {
            "status": "ok",
            "code": code_text,
            "source": source,
            "sms_key": sms_key,
            "sms_time": canonical.get("time", ""),
            "sms_text": canonical.get("text", ""),
            "allow_same_code": self._has_real_sms_time(fields),
        }

    @staticmethod
    def _candidate_already_attempted(
        candidate: dict,
        used_codes: set | None,
        attempted_sms_keys: set | None,
    ) -> bool:
        sms_key = candidate.get("sms_key")
        code = candidate.get("code", "")
        if sms_key and attempted_sms_keys is not None and sms_key in attempted_sms_keys:
            return True
        if (
            used_codes is not None
            and code in used_codes
            and not candidate.get("allow_same_code")
        ):
            return True
        return False

    # ── 轮询 SMS ─────────────────────────────────────────────
    def wait_for_code(
        self,
        activation_id: str,
        *,
        timeout: int | None = None,
        poll_interval: int = 3,
        used_codes: set | None = None,
        attempted_sms_keys: set | None = None,
        openai_resend_fn=None,
        return_metadata: bool = False,
    ):
        """多源轮询验证码, 期间每 120s 触发 OpenAI resend + 平台 setStatus=3。

        每 30s 输出一行 progress 日志, 让等待过程可见。
        """
        deadline = time.time() + (timeout if timeout is not None else self.sms_wait_seconds)
        next_resend_at = time.time() + OPENAI_RESEND_AFTER_SECONDS
        resend_count = 0
        warned_v2 = False
        attempt = 0
        # 进度日志节流(每 30s 一次, 第一次轮询完就打)
        next_progress_at = time.time() + 30
        last_status_seen = ""

        logger.info(
            "[HeroSMS] 开始轮询 SMS: activation_id=%s, poll=%ds, timeout=%ds",
            activation_id,
            poll_interval,
            int(deadline - time.time()),
        )

        while time.time() < deadline:
            attempt += 1
            # ── getStatusV2 (主路径) ──
            try:
                result = self.get_status_v2(activation_id)
                last_status_seen = result.get("status", "") or last_status_seen
                if result.get("status") == "ok":
                    if not self._candidate_already_attempted(result, used_codes, attempted_sms_keys):
                        logger.info(
                            "[HeroSMS] code via %s: %s (sms_time=%s, attempt=%d)",
                            result.get("source"),
                            result.get("code"),
                            result.get("sms_time") or "-",
                            attempt,
                        )
                        return result if return_metadata else result["code"]
                elif result.get("status") == "cancel":
                    logger.warning("[HeroSMS] 激活已被取消")
                    return None
            except HeroSmsError as exc:
                if not warned_v2:
                    logger.warning("[HeroSMS] getStatusV2 异常: %s", exc)
                    warned_v2 = True

            # ── getStatus (文本兜底) ──
            try:
                result = self.get_status(activation_id)
                last_status_seen = result.get("status", "") or last_status_seen
                if result.get("status") == "ok":
                    candidate = self._make_sms_candidate(activation_id, "getStatus", result.get("code"))
                    if candidate and not self._candidate_already_attempted(
                        candidate, used_codes, attempted_sms_keys
                    ):
                        logger.info("[HeroSMS] code via getStatus: %s", candidate["code"])
                        return candidate if return_metadata else candidate["code"]
                elif result.get("status") == "cancel":
                    return None
            except HeroSmsError as exc:
                logger.debug("[HeroSMS] getStatus 异常: %s", exc)

            # ── getActiveActivations (再兜底, 拿到 smsCode/dateTime) ──
            try:
                for act in self.get_active_activations():
                    if str(act.get("activationId")) != str(activation_id):
                        continue
                    candidate = self._make_sms_candidate(
                        activation_id,
                        "getActiveActivations",
                        act.get("smsCode"),
                        {
                            "channel": "sms",
                            "smsText": act.get("smsText"),
                            "activationStatus": act.get("activationStatus"),
                            "repeated": act.get("repeated"),
                            "dateTime": act.get("dateTime"),
                            "date": act.get("date") or act.get("smsDate") or act.get("smsTime"),
                        },
                    )
                    if candidate and not self._candidate_already_attempted(
                        candidate, used_codes, attempted_sms_keys
                    ):
                        return candidate if return_metadata else candidate["code"]
                    break
            except HeroSmsError as exc:
                logger.debug("[HeroSMS] getActiveActivations 异常: %s", exc)

            # ── 周期性 resend ──
            if openai_resend_fn and time.time() >= next_resend_at:
                resend_count += 1
                next_resend_at = time.time() + OPENAI_RESEND_AFTER_SECONDS
                logger.info(
                    "[HeroSMS] 等待已超 %ds 仍未拿到 code, 触发 OpenAI resend(第 %d 次)",
                    OPENAI_RESEND_AFTER_SECONDS,
                    resend_count,
                )
                openai_ok = False
                try:
                    openai_resend_fn()
                    logger.info("[HeroSMS] OpenAI resend 已发送")
                    openai_ok = True
                except OpenAIResendError as exc:
                    logger.warning("[HeroSMS] OpenAI resend 失败: %s", exc)
                    if exc.status_code == 400:
                        return None
                except Exception as exc:
                    logger.warning("[HeroSMS] OpenAI resend 抛异常: %s", exc)
                if openai_ok:
                    try:
                        self.request_resend_sms(activation_id)
                        logger.info("[HeroSMS] 已请求接码平台重发(setStatus=3)")
                    except HeroSmsError as exc:
                        logger.debug("[HeroSMS] 平台 resend 失败: %s", exc)

            # ── 进度日志(每 30s) ──
            now = time.time()
            if now >= next_progress_at:
                next_progress_at = now + 30
                remaining = int(deadline - now)
                next_resend_in = max(0, int(next_resend_at - now)) if openai_resend_fn else None
                msg = (
                    f"[HeroSMS] 等待 SMS 中... 已轮询 {attempt} 次, "
                    f"最近状态={last_status_seen or 'pending'}, 剩余 {remaining}s"
                )
                if next_resend_in is not None:
                    msg += f", 下次自动 resend 还有 {next_resend_in}s"
                logger.info(msg)

            time.sleep(poll_interval)

        logger.warning(
            "[HeroSMS] wait_for_code 超时(activation=%s, %ds 内未拿到新 code, 最近状态=%s)",
            activation_id,
            int(self.sms_wait_seconds),
            last_status_seen or "unknown",
        )
        return None


# ══════════════════════════════════════════════════════════════
# 号码缓存(进程内, 跨线程复用)
# ══════════════════════════════════════════════════════════════

_phone_cache_lock = threading.Lock()
_phone_verify_lock = threading.Lock()  # 序列化整个 add-phone 流程
_phone_cache: dict | None = None


def _empty_attempt_sets() -> dict:
    return {
        "used_codes": set(),
        "attempted_sms_keys": set(),
        "failed_sms_keys": set(),
    }


def _ensure_attempt_sets(cache: dict | None) -> None:
    if cache is None:
        return
    for key, value in _empty_attempt_sets().items():
        cache.setdefault(key, value)
    cache.setdefault("reuse_stopped", False)
    cache.setdefault("stop_reason", "")


def _phone_remaining_seconds() -> int:
    if _phone_cache is None:
        return 0
    return max(0, DEFAULT_PHONE_LIFETIME - int(time.time() - _phone_cache["acquired_at"]))


def _get_cached_phone() -> dict | None:
    """读取仍有效的号码缓存, 过期/停用则返回 None。"""
    global _phone_cache
    if _phone_cache is None:
        return None
    _ensure_attempt_sets(_phone_cache)
    if _phone_cache.get("reuse_stopped"):
        logger.info("[HeroSMS] 号码缓存已停用: %s", _phone_cache.get("stop_reason", ""))
        _phone_cache = None
        return None
    if time.time() - _phone_cache["acquired_at"] >= DEFAULT_PHONE_LIFETIME:
        logger.info("[HeroSMS] 号码缓存已过期")
        _phone_cache = None
        return None
    return _phone_cache


def _stop_phone_reuse(reason: str = "") -> None:
    if _phone_cache is None:
        return
    _ensure_attempt_sets(_phone_cache)
    _phone_cache["reuse_stopped"] = True
    _phone_cache["stop_reason"] = reason


def _drop_phone_cache(activation_id: str | None = None, reason: str = "") -> None:
    """删除号码缓存, 可选传 activation_id 防止误删别的号码。"""
    global _phone_cache
    if _phone_cache and (activation_id is None or str(_phone_cache.get("activation_id")) == str(activation_id)):
        logger.info("[HeroSMS] 删除号码缓存: %s", reason or "-")
        _phone_cache = None


def _cancel_and_drop(client: HeroSmsClient, activation_id: str, reason: str = "") -> None:
    try:
        client.cancel_activation(activation_id)
    except Exception as exc:  # pragma: no cover - 防御
        logger.debug("[HeroSMS] cancel_activation 异常: %s", exc)
    with _phone_cache_lock:
        _drop_phone_cache(activation_id, reason)


def _record_attempt(activation_id: str, code_result, *, failed: bool) -> None:
    if not _phone_cache or str(_phone_cache.get("activation_id")) != str(activation_id):
        return
    _ensure_attempt_sets(_phone_cache)
    code = ""
    sms_key = ""
    if isinstance(code_result, dict):
        code = str(code_result.get("code") or "").strip()
        sms_key = str(code_result.get("sms_key") or "").strip()
    elif code_result:
        code = str(code_result).strip()
    if code:
        _phone_cache["used_codes"].add(code)
    if sms_key:
        _phone_cache["attempted_sms_keys"].add(sms_key)
        if failed:
            _phone_cache["failed_sms_keys"].add(sms_key)


# ══════════════════════════════════════════════════════════════
# OpenAI 错误判定(从 codex-phone 移植)
# ══════════════════════════════════════════════════════════════


def _is_phone_limit_error(resp) -> bool:
    """OpenAI 提示号码已达使用上限。"""
    try:
        text = (resp.text or "").lower()
    except Exception:
        return False
    for kw in ("limit", "already", "too many", "exceeded", "maximum", "上限", "已达"):
        if kw in text:
            return True
    return False


def _is_phone_unusable_error(resp) -> bool:
    """OpenAI 拒绝该号码本身(VoIP / 无效)。"""
    if getattr(resp, "status_code", None) != 400:
        return False
    try:
        payload = resp.json()
    except Exception:
        text = (getattr(resp, "text", "") or "").lower()
        return "invalid phone number" in text or "voip_phone_disallowed" in text
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    code = str(error.get("code") or "").lower()
    message = str(error.get("message") or "").lower()
    return code in {
        "voip_phone_disallowed",
        "invalid_phone_number",
        "phone_number_invalid",
    } or "invalid phone number" in message


def _is_invalid_otp_response(resp) -> bool:
    """OpenAI 明确指出验证码错。"""
    if getattr(resp, "status_code", None) != 400:
        return False
    try:
        payload = resp.json()
    except Exception:
        text = (getattr(resp, "text", "") or "").lower()
        return "invalid otp code" in text
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    code = str(error.get("code") or "").lower()
    message = str(error.get("message") or "").lower()
    return code == "invalid_input" and "invalid otp code" in message


# ══════════════════════════════════════════════════════════════
# 工厂 & 配置探测
# ══════════════════════════════════════════════════════════════


def get_hero_sms_client() -> HeroSmsClient | None:
    """根据当前运行配置创建客户端, 未配置 API Key 则返回 None。"""
    from autoteam import config as runtime_config

    api_key = (getattr(runtime_config, "HERO_SMS_API_KEY", "") or "").strip()
    if not api_key:
        return None
    return HeroSmsClient(
        api_key=api_key,
        base_url=getattr(runtime_config, "HERO_SMS_BASE_URL", "") or DEFAULT_BASE_URL,
        service=getattr(runtime_config, "HERO_SMS_SERVICE", "") or DEFAULT_SERVICE,
        country=getattr(runtime_config, "HERO_SMS_COUNTRY", "") or DEFAULT_COUNTRY,
        max_price=getattr(runtime_config, "HERO_SMS_MAX_PRICE", 0),
        operator=getattr(runtime_config, "HERO_SMS_OPERATOR", "") or "",
        timeout=getattr(runtime_config, "HERO_SMS_HTTP_TIMEOUT", DEFAULT_TIMEOUT),
        sms_wait_seconds=getattr(runtime_config, "HERO_SMS_WAIT_SECONDS", DEFAULT_SMS_WAIT_SECONDS),
    )


def is_hero_sms_configured() -> bool:
    from autoteam import config as runtime_config

    return bool((getattr(runtime_config, "HERO_SMS_API_KEY", "") or "").strip())


def _phone_reuse_max() -> int:
    from autoteam import config as runtime_config

    try:
        value = int(getattr(runtime_config, "HERO_SMS_PHONE_REUSE_MAX", DEFAULT_PHONE_REUSE_MAX))
    except (TypeError, ValueError):
        value = DEFAULT_PHONE_REUSE_MAX
    return max(0, value)


def _force_new_phone() -> bool:
    """启用 ``HERO_SMS_FORCE_NEW_PHONE`` 时, 即使缓存还在也直接申请新号。

    适用场景: 复用的旧号 OpenAI 端已经标记为已用 / 被风控, 复用反而触发 NO_NUMBERS
    或一直收不到 SMS。打开这个开关让流程申请新号。
    """
    from autoteam import config as runtime_config

    raw = getattr(runtime_config, "HERO_SMS_FORCE_NEW_PHONE", False)
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


# ══════════════════════════════════════════════════════════════
# 公共入口: 通过 HTTP API 完成 add-phone
# ══════════════════════════════════════════════════════════════


def handle_add_phone_via_http(
    *,
    session,
    auth_url: str = "https://auth.openai.com",
    oai_device_id: str = "",
    user_agent: str = "",
    impersonate: str | None = None,
    proxy: str | None = None,
) -> bool:
    """通过 OpenAI HTTP API 完成 add-phone 流程。

    :param session: ``curl_cffi.requests.Session`` 或 ``requests.Session``,
        必须已经携带当前 OAuth 流程的 cookies(从 Playwright ``context.cookies()`` 派生)。
    :param auth_url: ``https://auth.openai.com``(默认)。
    :param oai_device_id: ``oai-did`` cookie 值, 部分 endpoint 会校验。
    :param user_agent: 透传到 ``User-Agent`` 头, 与浏览器一致以避免风控差异。
    :param impersonate: ``curl_cffi`` 的 impersonate profile, requests 不支持时忽略。
    :param proxy: 给接码客户端的代理(``http(s)://...``), 不影响 ``session`` 自身。
    :returns: True 表示 add-phone 完成; False 表示失败(账号已创建, 但需要外层决策)。
    """
    client = get_hero_sms_client()
    if client is None:
        logger.warning("[HeroSMS] 未配置 API Key, 跳过手机验证")
        return False
    if proxy:
        client.session.proxies = {"http": proxy, "https": proxy}

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": auth_url.rstrip("/"),
        "Referer": f"{auth_url.rstrip('/')}/add-phone",
    }
    if user_agent:
        headers["User-Agent"] = user_agent
    if oai_device_id:
        headers["oai-device-id"] = oai_device_id

    send_url = f"{auth_url.rstrip('/')}/api/accounts/add-phone/send"
    validate_url = f"{auth_url.rstrip('/')}/api/accounts/phone-otp/validate"
    resend_url = f"{auth_url.rstrip('/')}/api/accounts/phone-otp/resend"

    def _post(url: str, body: dict | None = None, timeout: int = 30):
        kw: dict = {"headers": headers, "timeout": timeout}
        if body is not None:
            kw["json"] = body
        if impersonate:
            kw["impersonate"] = impersonate
        return session.post(url, **kw)

    def _openai_resend() -> None:
        resp = _post(resend_url)
        body = ""
        try:
            body = (resp.text or "")[:300]
        except Exception:
            pass
        logger.info("[HeroSMS] OpenAI resend → %s %s", resp.status_code, body)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise OpenAIResendError(resp.status_code, body)

    reuse_max = _phone_reuse_max()
    force_new = _force_new_phone()
    if force_new:
        logger.info("[HeroSMS] HERO_SMS_FORCE_NEW_PHONE 已启用, 本次跳过号码复用")
        with _phone_cache_lock:
            _drop_phone_cache(reason="force new phone")

    logger.info("[HeroSMS] 等待手机验证锁...")

    with _phone_verify_lock:
        return _do_phone_verify(
            client=client,
            session=session,
            send_url=send_url,
            validate_url=validate_url,
            headers=headers,
            impersonate=impersonate,
            openai_resend_fn=_openai_resend,
            reuse_max=reuse_max,
        )


def _request_new_phone(client: HeroSmsClient) -> dict:
    """购买新号码并写入全局缓存。"""
    global _phone_cache

    info = client.request_number()
    activation_id = str(info.get("activationId", ""))
    raw_phone = str(info.get("phoneNumber", ""))
    cpc = str(info.get("countryPhoneCode", ""))
    if raw_phone.startswith("+"):
        phone = raw_phone
    elif cpc and raw_phone.startswith(cpc):
        phone = f"+{raw_phone}"
    elif cpc:
        phone = f"+{cpc}{raw_phone}"
    else:
        phone = f"+{raw_phone}"

    _phone_cache = {
        "phone_number": phone,
        "activation_id": activation_id,
        "acquired_at": time.time(),
        "use_count": 0,
        "activation_cost": info.get("activationCost"),
        "activation_operator": info.get("activationOperator", ""),
        "currency": info.get("currency", ""),
        "client": client,
        **_empty_attempt_sets(),
        "reuse_stopped": False,
        "stop_reason": "",
    }
    logger.info(
        "[HeroSMS] 新号码: %s activationId=%s cost=%s",
        phone,
        activation_id,
        info.get("activationCost"),
    )
    return _phone_cache


def _do_phone_verify(
    *,
    client: HeroSmsClient,
    session,
    send_url: str,
    validate_url: str,
    headers: dict,
    impersonate: str | None,
    openai_resend_fn,
    reuse_max: int,
) -> bool:
    """``handle_add_phone_via_http`` 的核心主体, 已在 ``_phone_verify_lock`` 内。"""
    global _phone_cache

    # ── 1. 获取号码(优先复用缓存) ──
    with _phone_cache_lock:
        cached = _get_cached_phone()
        if cached and reuse_max > 0 and int(cached.get("use_count", 0)) >= reuse_max:
            logger.info(
                "[HeroSMS] 号码 %s 已成功验证 %d 次, 达到复用上限, 停止复用",
                cached.get("phone_number"),
                cached.get("use_count"),
            )
            _stop_phone_reuse(f"reuse max {reuse_max}")
            cached = None

        if cached:
            phone_number = cached["phone_number"]
            activation_id = cached["activation_id"]
            logger.info(
                "[HeroSMS] 复用号码 %s (已成功 %d 次, 剩余 %ds)",
                phone_number,
                cached.get("use_count", 0),
                _phone_remaining_seconds(),
            )
        else:
            try:
                info = _request_new_phone(client)
            except HeroSmsError as exc:
                logger.error("[HeroSMS] 申请号码失败: %s (%s)", exc, exc.code)
                return False
            phone_number = info["phone_number"]
            activation_id = info["activation_id"]

    # ── 2. 把手机号提交给 OpenAI ──
    try:
        resp = session.post(
            send_url,
            json={"phone_number": phone_number},
            headers=headers,
            timeout=30,
            **({"impersonate": impersonate} if impersonate else {}),
        )
    except Exception as exc:
        logger.error("[HeroSMS] add-phone/send 请求异常: %s", exc)
        return False

    body_excerpt = ""
    try:
        body_excerpt = (resp.text or "")[:300]
    except Exception:
        pass
    logger.info("[HeroSMS] add-phone/send → %s %s", resp.status_code, body_excerpt)

    if resp.status_code not in (200, 201, 204):
        if _is_phone_limit_error(resp):
            logger.warning("[HeroSMS] 该号码已达 OpenAI 使用上限, 删除本地缓存")
            with _phone_cache_lock:
                _drop_phone_cache(activation_id, "phone limit reached")
        elif _is_phone_unusable_error(resp):
            logger.warning("[HeroSMS] OpenAI 拒绝该号码(VoIP / 无效), 取消激活")
            _cancel_and_drop(client, activation_id, "OpenAI 拒绝该号码")
        else:
            logger.error("[HeroSMS] add-phone/send 失败: %s", body_excerpt)
        return False

    # ── 3. 通知平台已发送, 准备等待 SMS ──
    try:
        client.set_status(activation_id, SMS_STATUS_SENT)
    except HeroSmsError as exc:
        logger.debug("[HeroSMS] setStatus(1) 失败(忽略): %s", exc)

    with _phone_cache_lock:
        _ensure_attempt_sets(_phone_cache)
        if _phone_cache:
            used_codes = _phone_cache["used_codes"]
            attempted_sms_keys = _phone_cache["attempted_sms_keys"]
            wait_timeout = max(60, _phone_remaining_seconds() or client.sms_wait_seconds)
        else:
            used_codes = set()
            attempted_sms_keys = set()
            wait_timeout = client.sms_wait_seconds

    # ── 4. 拿验证码并提交, 容错重试 ──
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        remaining = max(1, int(deadline - time.time()))
        logger.info("[HeroSMS] 等待 SMS code(剩 %ds)...", remaining)
        code_result = client.wait_for_code(
            activation_id,
            timeout=remaining,
            poll_interval=3,
            used_codes=used_codes,
            attempted_sms_keys=attempted_sms_keys,
            openai_resend_fn=openai_resend_fn,
            return_metadata=True,
        )
        if not code_result:
            logger.warning("[HeroSMS] 超时未拿到新 SMS code, 删除本地号码缓存避免下次再用同一坏号")
            with _phone_cache_lock:
                _drop_phone_cache(activation_id, "wait_for_code timeout")
            return False

        code_text = (code_result or {}).get("code", "")
        try:
            resp = session.post(
                validate_url,
                json={"code": code_text},
                headers=headers,
                timeout=30,
                **({"impersonate": impersonate} if impersonate else {}),
            )
        except Exception as exc:
            logger.error("[HeroSMS] phone-otp/validate 请求异常: %s", exc)
            return False

        validate_excerpt = ""
        try:
            validate_excerpt = (resp.text or "")[:300]
        except Exception:
            pass
        logger.info("[HeroSMS] phone-otp/validate → %s %s", resp.status_code, validate_excerpt)

        if resp.status_code in (200, 201, 204):
            break

        if _is_invalid_otp_response(resp):
            with _phone_cache_lock:
                _record_attempt(activation_id, code_result, failed=True)
            logger.warning("[HeroSMS] OpenAI 判定 code 无效, 等下一条 SMS")
            try:
                openai_resend_fn()
                client.request_resend_sms(activation_id)
            except OpenAIResendError as exc:
                if exc.status_code == 400:
                    _cancel_and_drop(client, activation_id, "OpenAI resend 400")
                    return False
            except Exception:
                pass
            continue

        # 其它 4xx/5xx → 直接失败
        logger.error("[HeroSMS] phone-otp/validate 异常状态: %s %s", resp.status_code, validate_excerpt)
        with _phone_cache_lock:
            _record_attempt(activation_id, code_result, failed=True)
        return False
    else:
        logger.warning("[HeroSMS] 总超时未拿到可用验证码")
        return False

    # ── 5. 验证成功, 更新缓存 ──
    with _phone_cache_lock:
        if _phone_cache and str(_phone_cache.get("activation_id")) == str(activation_id):
            _phone_cache["use_count"] = int(_phone_cache.get("use_count", 0)) + 1
            _record_attempt(activation_id, code_result, failed=False)
            remaining_secs = _phone_remaining_seconds()
            count = _phone_cache["use_count"]
            if reuse_max > 0 and count >= reuse_max:
                logger.info(
                    "[HeroSMS] 号码 %s 已 %d 次成功(达上限), finish 激活",
                    _phone_cache.get("phone_number"),
                    count,
                )
                _stop_phone_reuse(f"reuse max {reuse_max}")
                client.finish_activation(activation_id)
                _phone_cache = None
            elif remaining_secs <= 30:
                logger.info("[HeroSMS] 号码即将过期, finish 激活")
                client.finish_activation(activation_id)
                _phone_cache = None
            else:
                logger.info(
                    "[HeroSMS] 号码 %s 已 %d 次成功, 剩余 %ds 可继续复用",
                    _phone_cache.get("phone_number"),
                    count,
                    remaining_secs,
                )
        else:
            client.finish_activation(activation_id)
    return True
