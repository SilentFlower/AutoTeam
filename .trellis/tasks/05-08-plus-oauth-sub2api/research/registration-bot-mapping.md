# Registration Bot — 库化改造点定位

> 由 brainstorm 阶段对 `chatgpt_registration_bot_public.py`(2156 行,根目录)的逐段扫描得出。
> PR1 改造的所有"接缝点"在此列出,实施时按 file:line 对照原代码。

---

## 1. 文件总体结构(2156 行)

| 行段 | 内容 | PR1 改造 |
|---|---|---|
| L1-50 | 模块文档、imports、logging | 不动 |
| L57-118 | **USER CONFIG 区** | ✅ 抽 `BotConfig` |
| L121-148 | 系统配置常量 + `_validate_user_config` | ✅ 校验逻辑迁到 `BotConfig.__post_init__` |
| L151-253 | 工具函数(随机姓名 / 退避重试 / screenshot / debug_pause) | 不动 |
| L255-269 | 自定义异常 | 不动 |
| L272-349 | `VerificationCodeExtractor` | 不动 |
| L352-442 | `TempEmailClient` | 微调:`BASE = TEMP_EMAIL_API` 改为构造时注入 |
| L445-1805 | `ChatGPTBot`(注册机核心) | 微调:常量(GOPAY_*)改通过 BotConfig 传入 |
| L1808-1924 | `AccountManager`(写 accounts.json/txt) | ✅ 库模式 disable;CLI 模式启用 |
| L1927-1948 | `WhatsAppOTPHandler` | ✅ 重构成可注入 callback |
| L1951-2152 | `main()`(CLI 入口) | ✅ 抽 `register_one_plus()` 库入口 + 保留 main() |
| L2155-2156 | `if __name__ == "__main__": asyncio.run(main())` | 不动 |

---

## 2. USER CONFIG → `BotConfig` dataclass

原始位置:L77-118

### 现状变量
```python
TEMP_EMAIL_API = "https://YOUR_TEMP_EMAIL_API_DOMAIN"             # L77
TEMP_EMAIL_LOGIN_BASE = "https://YOUR_TEMP_EMAIL_API_DOMAIN"      # L79
GOPAY_PHONE = "YOUR_GOPAY_PHONE"                                  # L94
GOPAY_COUNTRY_CODE = "YOUR_COUNTRY_CODE"                          # L95
GOPAY_PIN = "YOUR_6_DIGIT_PIN"                                    # L96
DEFAULT_NAME = "John Doe"                                         # L101
EMAIL_PREFIX_TAG = "oai"                                          # L105
SUBSCRIPTION_PLAN_NAME = "chatgptplusplan"                        # L108
SUBSCRIPTION_BILLING_COUNTRY = "ID"                               # L109
SUBSCRIPTION_CURRENCY = "IDR"                                     # L110
SUBSCRIPTION_PROMO_CAMPAIGN_ID = "plus-1-month-free"              # L113
SUBSCRIPTION_CANCEL_URL = "https://chatgpt.com/#pricing"          # L114
```

### 目标改造
```python
@dataclass(frozen=True)
class BotConfig:
    temp_email_api: str
    temp_email_login_base: str
    gopay_phone: str         # 纯数字,不带 +/国家码
    gopay_country_code: str  # 纯数字,如 "86"
    gopay_pin: str           # 6 位数字
    # 以下保留默认(很少改):
    email_prefix_tag: str = "oai"
    subscription_plan_name: str = "chatgptplusplan"
    subscription_billing_country: str = "ID"
    subscription_currency: str = "IDR"
    subscription_promo_campaign_id: str = "plus-1-month-free"
    subscription_cancel_url: str = "https://chatgpt.com/#pricing"

    def __post_init__(self):
        # 迁移自原 _validate_user_config(L131-148)
        if "YOUR_" in self.temp_email_api.upper():
            raise ValueError("temp_email_api 未配置")
        if "YOUR_" in self.gopay_phone.upper() or not self.gopay_phone.isdigit():
            raise ValueError("gopay_phone 无效")
        if len(self.gopay_pin) != 6 or not self.gopay_pin.isdigit():
            raise ValueError("gopay_pin 必须是 6 位数字")
        # ... 其余字段同样校验
```

### 兼容策略
- 模块顶层 `TEMP_EMAIL_API` / `GOPAY_*` 常量**保留**,作为 CLI `main()` 默认 BotConfig 的来源
- `ChatGPTBot.__init__` 加可选 `config: BotConfig | None = None`,缺省时从模块常量构造(向后兼容 CLI)
- 库模式调用方必须显式传 `BotConfig`

---

## 3. WhatsApp OTP callback 重构

原始位置:`WhatsAppOTPHandler.prompt_user_for_otp`,L1927-1948

### 现状(stdin)
```python
class WhatsAppOTPHandler:
    @staticmethod
    async def prompt_user_for_otp(timeout: int = 120) -> Optional[str]:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: input("OTP > ").strip()),
            timeout=timeout,
        )
        return result
```

### 目标改造
保留这个静态方法作为 CLI 默认实现。
`ChatGPTBot.handle_stripe_checkout`(L1130)的 `whatsapp_callback` 参数本来就是函数引用,**调用方无须改 bot 代码**——只需在新模块构造可注入的 callback:

```python
# CLI 路径(main 中,L2082):
payment_result = await bot.handle_stripe_checkout(
    whatsapp_callback=WhatsAppOTPHandler.prompt_user_for_otp
)

# 库路径(plus_auto_register 中):
otp_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
async def otp_callback() -> str | None:
    try:
        return await asyncio.wait_for(otp_queue.get(), timeout=300)
    except asyncio.TimeoutError:
        return None
payment_result = await bot.handle_stripe_checkout(whatsapp_callback=otp_callback)
```

**这意味着 `ChatGPTBot` 类本身不用改任何代码**——bot 已经是 callback-driven 了。OTP 重构的全部工作量在新模块。

### 注入点
- `bot.handle_stripe_checkout(whatsapp_callback=...)` (L1130)
- 内部 `_handle_midtrans_gopay(whatsapp_callback)` (L1334)
- 内部 `_handle_midtrans_pay_step(whatsapp_callback)` (L1502)

---

## 4. `register_one_plus()` 库入口设计

原始位置:`main()` 在 L1951-2152。提炼出可被项目代码 import 的函数。

### 目标签名
```python
async def register_one_plus(
    config: BotConfig,
    otp_callback: Callable[[], Awaitable[str | None]],
    *,
    headless: bool = True,
    slow_mo: int = 100,
    step_callback: Callable[[str], None] | None = None,
    chatgpt_password: str | None = None,  # 缺省自动生成
) -> dict:
    """库模式:跑一次完整注册 + 付款 + 设密码 + 取消续订。
    返回 {ok, email, password, last_step, error_type, error_detail}。
    不写 accounts.json / accounts.txt(库模式禁用)。
    """
```

### 内部流程(对照 main() L1993-2103)
1. `step_callback("creating_email")` → `TempEmailClient(config).create_address(...)`
2. `step_callback("signing_up")` → `bot.navigate_to_signup(email)`
3. `step_callback("awaiting_email_otp")` → `email_client.poll_for_emails(...)` + 提取
4. `step_callback("filling_about_you")` → `bot.enter_verification_code(...)` + `bot.fill_about_you(...)`
5. 等 `bot.wait_for_login_complete()` → 验证 session
6. `step_callback("paying_gopay")` → `bot.execute_gopay_payment()` + `bot.handle_stripe_checkout(otp_callback)`
   - 中间 OTP 步:`step_callback("awaiting_whatsapp_otp")` 由 `_handle_midtrans_gopay` 触发?
   - **简化做法**:`step_callback` 在 `register_one_plus` 这一层粗粒度回流;细到 OTP 那一步,可以由 `otp_callback` 包一层(取 OTP 前先 step_callback)
7. `step_callback("setting_password")` → `bot.add_password_login(chatgpt_password)`
8. `step_callback("cancelling_subscription")` → `bot.cancel_subscription()`
9. 不调 `AccountManager.create_account`(库模式)
10. 返回 `{ok=True, email, password=chatgpt_password, last_step="done", error_type=None, error_detail=None}`

### 错误分类
对应 PRD D7 的 step 枚举,catch 各步异常映射到 error_type:
- `TempEmailError` → `email_create_failed`
- `SignupFlowError` 在 navigate/enter_code 阶段 → `signup_failed`
- `VerificationTimeout` → `email_otp_timeout`
- `PaymentError` → `payment_failed`
- `otp_callback` 返回 None → `whatsapp_otp_timeout`
- `asyncio.CancelledError` → `cancelled`

### CLI 适配
`main()` 改为构造 BotConfig(从模块常量) + 调 `register_one_plus(...)` + 自己接管 `AccountManager.create_account` + `print(...)`。CLI 行为完全保持。

---

## 5. `AccountManager` 隔离

原始位置:L1808-1924,把账号写到根目录 `accounts.json` / `accounts.txt`。

### 改造
- `register_one_plus` 库模式**不调** `AccountManager.create_account`
- 仅 CLI `main()` (L2107)继续调,行为不变
- 数据隔离:库模式产物只通过返回值传给上层;`plus_auto_register` 调用方自己负责喂给 `import_plus_account`(写 plus_accounts.json)

---

## 6. 不动的部分(PR1 范围之外)

- 整个 `ChatGPTBot` 类的页面操作逻辑(navigate / fill / payment / cancel)— 任何 UI 选择器、点击策略、风控 bypass 不动
- `TempEmailClient` 的 API 调用细节
- `VerificationCodeExtractor` 的正则
- 工具函数(随机姓名、退避重试、screenshot)
- 自定义异常体系

---

## 7. 测试计划(PR1)

- `test_bot_config_validation`:缺字段、PIN 非 6 位、phone 非数字 → 各种 ValueError
- `test_register_one_plus_smoke`(mock Playwright):
  - 注入 fake `TempEmailClient` 返回固定 email/jwt
  - 注入 fake `ChatGPTBot`(mock 所有方法返回成功)
  - 注入 fake `otp_callback`(立即返回 "123456")
  - assert 返回 dict 字段完整,`ok=True`
- `test_register_one_plus_otp_timeout`:`otp_callback` 返回 None → `error_type="whatsapp_otp_timeout"`
- `test_cli_main_still_works`:启动 `main()` 但传无效配置,确保校验能 raise SystemExit(冒烟)
- `test_account_manager_not_called_in_library_mode`:用 mock 监听 `AccountManager.create_account` 没被调
