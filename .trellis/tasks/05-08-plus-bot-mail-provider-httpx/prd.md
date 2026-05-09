# 注册机融入项目:接入 mail_provider + 删 TempEmailClient + 去 aiohttp

## Goal

把根目录的 `chatgpt_registration_bot_public.py` 注册机**整体迁到 `src/autoteam/plus_register_bot.py`**,并在迁移过程中删掉所有"独立 CLI"遗留物——TempEmailClient / VerificationCodeExtractor / AccountManager / WhatsAppOTPHandler / `main()` / argparse / aiohttp 全部移除——让 ChatGPTBot + register_one_plus 库入口干净地融入项目体系。

效果:
- 注册机邮箱接入直接用 `autoteam.mail_provider.get_mail_client()`,跟 FREE 池 / Plus import / invite 流程一致。运营侧只配 `MAIL_PROVIDER` + `CLOUDMAIL_*`(或 `CF_TEMP_EMAIL_*`)即可,**不必专门部署 cloudflare_temp_email**;
- aiohttp 彻底从仓库依赖中剥离;
- BotConfig 只保留 GoPay 三件套,不再需要 `temp_email_api` / `temp_email_login_base`。

## What I already know

### 项目侧 (Auto-context)

* `src/autoteam/mail_provider.py` 是抽象层入口:`get_mail_client(provider=None)` 按 `MAIL_PROVIDER` env 返回客户端实例。
* 两个 client 实现都是 **同步 + `requests`**,接口完全对齐:
  | 方法 | CloudMail | CFTempEmail |
  |---|---|---|
  | `login()` | ✅ | ✅ |
  | `create_temp_email(prefix=None) → (account_id, email)` | ✅ | ✅ |
  | `wait_for_email(to_email, timeout, sender_keyword) → email_data` | ✅ | ✅ |
  | `extract_verification_code(email_data) → str` | ✅ | ✅ |
  | `delete_account(account_id)` | ✅ | ✅ |
* FREE 池 / invite 流程已经在用 `mail_client.create_temp_email() + wait_for_email + extract_verification_code` 范式。
* 现有 plus_auto_register 在 thread + asyncio 模式下,`run_in_executor` 已经被 `_safe_import_plus_account` 用过,把同步 `mail_client.*` 包成 async 是一致模式。

### 注册机侧

* `chatgpt_registration_bot_public.py` 现 2532 行,关键模块:
  | 模块/类 | 行号 | 处理 |
  |---|---|---|
  | USER CONFIG 区(顶部常量) | L65-121 | **删** |
  | BotConfig dataclass | L138-198 | 保(去 `temp_email_api` / `temp_email_login_base` 字段) |
  | `_build_cli_bot_config` / `_validate_user_config` | L201-242 | **删** |
  | `aiohttp` 可选 import | L42-48 | **删** |
  | 工具函数(name / password / email_prefix / retry / screenshot) | L246-457 | 保 |
  | 自定义异常 | L460-474 | 保 |
  | `VerificationCodeExtractor` | L460-538 | **删**(用 mail_client.extract_verification_code 替代) |
  | `TempEmailClient` | L540-643 | **删** |
  | `ChatGPTBot` 类 | L647-2026 | 保(核心浏览器自动化) |
  | `AccountManager` | L2027-2143 | **删**(只 CLI 模式用) |
  | `WhatsAppOTPHandler` | L2146-2166 | **删**(只 CLI 模式用) |
  | `register_one_plus` 库入口 | L2170-2321 | 保(改签名加 mailbox callback) |
  | CLI `main()` + argparse | L2325-2528 | **删** |
  | `if __name__ == "__main__":` | L2530-2532 | **删** |
* 估算迁移后 `src/autoteam/plus_register_bot.py` 约 **1800 行**(2532 - 约 700 删除)。

## Decided

* **D1 邮箱接入方式**:Approach A —— `register_one_plus` 加 mailbox callback 注入。注册机本身不知道用 cloudmail / cf_temp_email,完全解耦。

* **D2 CLI 处理**:**删掉 CLI `main()`**。Plus 自动注册全功能已通过 plus_auto_register 模块 + Web UI 暴露,CLI 入口冗余。开发期单独调试可写 pytest fixture 替代。

* **D3 文件位置**:`chatgpt_registration_bot_public.py`(根目录)整体迁到 `src/autoteam/plus_register_bot.py`。根目录原文件删除,plus_auto_register 不再 sys.path 注入仓库根。

* **D4 PR 拆分**:**单 PR 一次落地**。改动虽然涉及面广,但本质是机械操作(删 + 迁 + 改 import + 改测试)。拆 PR 反而增加 review 负担(中间状态不能跑)。

* **D5 测试更新**:`tests/unit/test_chatgpt_registration_bot.py` 改名为 `tests/unit/test_plus_register_bot.py`,内部 import + 涉及 `AccountManager` / 模块顶层常量的测试相应删除/调整。`tests/unit/test_plus_auto_register.py` 改 mock 路径(从 `bot_module.register_one_plus` 改为 `plus_register_bot.register_one_plus`)。

## Requirements

* R1. **新增 `src/autoteam/plus_register_bot.py`** —— 包含 `BotConfig`(精简版)、`ChatGPTBot`、`register_one_plus`(新签名)、工具函数、自定义异常。
  - `BotConfig` 字段精简为 `gopay_phone / gopay_country_code / gopay_pin` 三件套(去 `temp_email_api` / `temp_email_login_base`),`__post_init__` 校验逻辑保留。
  - `register_one_plus` 新签名:
    ```python
    async def register_one_plus(
        config: BotConfig,
        otp_callback: Callable[[], Awaitable[str | None]],
        *,
        create_mailbox: Callable[[], Awaitable[Mailbox]],
        fetch_email_code: Callable[[Mailbox, int], Awaitable[str | None]],
        cleanup_mailbox: Callable[[Mailbox], Awaitable[None]] | None = None,
        headless: bool = True,
        slow_mo: int = 100,
        step_callback: Callable[[str], None] | None = None,
        chatgpt_password: str | None = None,
        name: str | None = None,
        birthdate: str | None = None,
        email_timeout: int = 180,
    ) -> dict[str, Any]
    ```
    `Mailbox` 是 `dataclass(account_id: str | None, email: str)` 或 `dict[str, Any]`(实施时定),足够让 callback 之间传递必需信息。

* R2. **删除根目录 `chatgpt_registration_bot_public.py`**。

* R3. **更新 `src/autoteam/plus_auto_register.py`**:
  - `from autoteam.plus_register_bot import BotConfig, register_one_plus` 替换 sys.path 注入 + 旧 import;
  - `load_bot_config_from_env` 不再读 `CF_TEMP_EMAIL_BASE_URL`(交给 mail_provider 自己读 `.env`),只读 `GOPAY_*` 三项。错误消息相应精简。
  - 在 `_run_job_async` 内部 build 三个 mailbox callback,用 `loop.run_in_executor` 把同步 `mail_client.*` 包成 async,注入 `register_one_plus`;
  - cleanup_mailbox 在每个号结束(成功/失败)时调用,best-effort 删除临时邮箱(失败不抛)。

* R4. **更新 `pyproject.toml`** 移除 `aiohttp` 依赖(若有);确认 `playwright` 仍在依赖里。

* R5. **测试**:
  - `tests/unit/test_chatgpt_registration_bot.py` → `tests/unit/test_plus_register_bot.py`,删 `test_chatgpt_bot_falls_back_to_module_globals_when_no_config`(模块顶层 GOPAY_* 常量已删) + `test_register_one_plus_does_not_call_account_manager`(AccountManager 已删) + 调整其余 import / fixture;
  - `tests/unit/test_plus_auto_register.py` 调整 `monkeypatch.setattr(plus_auto_register, "register_one_plus", ...)` 路径(模块属性引用方式不变 → 自然通过)。新增 1-2 个测试验证 mailbox callback 注入路径(mock create_mailbox / fetch_email_code / cleanup_mailbox);
  - PR 提交前 PR1 + PR2 全部测试调整后 ≥ 35 测试全绿(具体数随删除/新增浮动),0 回归。

* R6. **文档更新**:
  - `.env.example`: 移除针对"自动注册必须配 CF_TEMP_EMAIL_BASE_URL"的暗示(改为"使用 cloudflare_temp_email 时必填"原始语义);
  - `docs/configuration.md`: 表格不动 GOPAY_*;
  - `chatgpt_registration_bot_public.py` 已删,所有指向它的注释/docstring(主要在 `plus_auto_register.py` 顶部块注释 + spec error-handling.md 真例引用)更新为新文件名。

* R7. **spec 沉淀的"双阶段失败语义" + "跨线程 + asyncio 协作"两段** 用了 `chatgpt_registration_bot_public.py` 作真例,迁移后改为 `src/autoteam/plus_register_bot.py`。

## Acceptance Criteria

* [ ] 仅配 `MAIL_PROVIDER=cloudmail` + `CLOUDMAIL_*`(不配 `CF_TEMP_EMAIL_BASE_URL`)时,POST `/api/plus/auto_register {"count":1}` 全链路能跑(真实 e2e,用户验收)。
* [ ] `aiohttp` 不再出现在仓库代码中(`grep -r 'aiohttp' src/ tests/ chatgpt*.py` 为空) + 不在 `pyproject.toml` 依赖里。
* [ ] 根目录 `chatgpt_registration_bot_public.py` 已删,`src/autoteam/plus_register_bot.py` 已建。
* [ ] `BotConfig` 不再含 `temp_email_api` / `temp_email_login_base` 字段。
* [ ] `register_one_plus` 库模式下 mailbox 完全靠注入 callback;mock 测试覆盖 create / fetch_code / cleanup 三路径。
* [ ] 现有 PR1 + PR2 测试调整后全绿,无 baseline 之外的新 fail。
* [ ] `uv run ruff check / format / pytest / compileall` 全绿。
* [ ] PRD 04-29-free-account-generator / 05-08-plus-oauth-sub2api 引用注册机的地方(spec error-handling.md 真例 + plus_auto_register 模块 docstring)指向新文件路径。

## Definition of Done

* 代码迁移 + 删除 + 测试全绿
* 真实 e2e 用 CloudMail 跑通一次 Plus 自动注册(用户本机)
* spec error-handling.md 真例文件路径更新
* `.env.example` 文档语义更新
* PR 提交时附迁移前后行数对比(预计 -700 净减)

## Out of Scope (explicit)

* 不动 ChatGPTBot 浏览器自动化逻辑(选择器 / 点击策略 / 风控 bypass 等全保留原样)
* 不引入新邮箱平台(只用项目已有 cloudmail / cf_temp_email)
* 不动 plus_auto_register 的 jobs / queue / cancel / 锁机制(继承 PR2 设计)
* 不重写测试(只调整 import + 删过时用例)

## Technical Approach

### 数据流 (after)

```
plus_auto_register._run_job_async
  ↓ (build callback 三件套,wrap mail_provider 同步调用)
register_one_plus(config, otp_callback,
                  create_mailbox=…, fetch_email_code=…, cleanup_mailbox=…)
  ↓
ChatGPTBot 浏览器流程,关键节点:
  step("creating_email") → mailbox = await create_mailbox()
  step("signing_up") → bot.navigate_to_signup(mailbox.email)
  step("awaiting_email_otp") → code = await fetch_email_code(mailbox, timeout=180)
  step("filling_about_you") → bot.enter_verification_code(code) + fill_about_you
  step("paying_gopay") → bot.execute_gopay_payment + handle_stripe_checkout(otp_callback)
  step("setting_password") / step("cancelling_subscription") → 同前
  finally:
    if cleanup_mailbox: await cleanup_mailbox(mailbox)  # best-effort
  ↑
plus_auto_register 拿到 result + 走 import_plus_account(已有逻辑不变)
```

### 关键 callback wrapper(在 plus_auto_register 内)

```python
def _build_mailbox_callbacks(admin_id: str | None):
    """构造 mailbox callback 三件套,wrap 同步 mail_client。"""
    from autoteam.mail_provider import get_mail_client

    mail_client = get_mail_client()
    mail_client.login()
    loop = asyncio.get_running_loop()

    async def create_mailbox():
        prefix = generate_email_prefix()
        return await loop.run_in_executor(
            None, lambda: mail_client.create_temp_email(prefix=prefix)
        )  # 返回 (account_id, email)

    async def fetch_email_code(mailbox, timeout: int):
        account_id, email = mailbox
        # cloudmail / cf_temp_email 都接受 to_email + timeout
        email_data = await loop.run_in_executor(
            None, lambda: mail_client.wait_for_email(email, timeout=timeout)
        )
        if not email_data:
            return None
        return await loop.run_in_executor(
            None, lambda: mail_client.extract_verification_code(email_data)
        )

    async def cleanup_mailbox(mailbox):
        account_id, _ = mailbox
        if not account_id:
            return
        try:
            await loop.run_in_executor(None, lambda: mail_client.delete_account(account_id))
        except Exception as exc:
            logger.warning("[Plus自注册] 清理临时邮箱失败,可忽略: %s", exc)

    return create_mailbox, fetch_email_code, cleanup_mailbox
```

### 删除清单 (~700 行)

| 模块 | 行数估计 | 替代 |
|---|---|---|
| USER CONFIG 区(L65-121) | ~57 | BotConfig 字段 |
| `_build_cli_bot_config` / `_validate_user_config` | ~42 | BotConfig 自校验 |
| aiohttp 可选 import + 用法 | ~10 | mail_provider 同步 client |
| `VerificationCodeExtractor` | ~78 | mail_client.extract_verification_code |
| `TempEmailClient` | ~104 | mail_provider |
| `AccountManager` | ~117 | 不需要(plus_accounts.json 走 import_plus_account) |
| `WhatsAppOTPHandler.prompt_user_for_otp` | ~21 | 注入式 otp_callback 已经是事实标准 |
| CLI `main()` + argparse | ~204 | plus_auto_register HTTP 端点 |
| `if __name__ == "__main__":` | ~3 | - |
| **小计** | **~636** | |

### 保留清单

| 模块 | 行数估计 |
|---|---|
| 模块 docstring + imports | ~50 |
| 工具函数(姓名 / 密码 / email prefix / retry / screenshot) | ~140 |
| 自定义异常(TempEmailError 改名为 MailboxError 或保留) | ~15 |
| `BotConfig`(精简版) | ~50 |
| `ChatGPTBot` 类 | ~1380 |
| `register_one_plus`(新签名) | ~150 |
| **小计** | **~1785** |

### 迁移步骤(实施时顺序)

1. 新建 `src/autoteam/plus_register_bot.py`,从 `chatgpt_registration_bot_public.py` 复制保留的部分,删掉删除清单的部分
2. 修改 `BotConfig`(去字段)、`ChatGPTBot.__init__`(去 `_gopay_*` 之外的属性回退,直接读 `config.*`)、`register_one_plus`(新签名 + 内部调 mailbox callback)
3. 更新 `src/autoteam/plus_auto_register.py`:删 sys.path 注入,改 import,加 `_build_mailbox_callbacks`,改 `_run_job_async` 调用方式
4. 删除根目录 `chatgpt_registration_bot_public.py`
5. 改测试文件 `tests/unit/test_chatgpt_registration_bot.py` → `test_plus_register_bot.py`,调整 import + 删过时测试 + 加 mailbox callback 测试
6. 调整 `tests/unit/test_plus_auto_register.py`(mock 路径 + 加 mailbox 注入测试)
7. `uv remove aiohttp`(确认 pyproject.toml 不引用)
8. 更新 `.env.example` 文档暗示 + spec error-handling.md 真例路径 + plus_auto_register 顶部 docstring
9. 跑四件套 + 全量 pytest 0 回归

## Decision (ADR-lite)

**Context**:Plus 自动注册任务 PR1 选了"留根目录 + 库化"路线(PRD 04-30 D3),意图是保留注册机"独立 CLI 可单跑"属性。但运营试用时发现注册机内部假设 cloudflare_temp_email API,而项目主线用 CloudMail —— 配置不可复用,需自部署 cloudflare_temp_email 才能跑。叠加用户明确诉求"不需要 aiohttp,要融入项目",CLI 独立性已不再是 must-have。

**Decision**:推翻 04-30 D3 的"留根目录"决策,把注册机整体迁到 `src/autoteam/plus_register_bot.py`,同时彻底剥离独立 CLI 遗留物(TempEmailClient / AccountManager / WhatsAppOTPHandler.stdin / main()),邮箱接入改用 mail_provider 抽象,aiohttp 整体下线。

**Consequences**:
- ✅ 运营配置一致(只配 MAIL_PROVIDER + 对应 client 即可,不必双重部署)
- ✅ 注册机不再"自治",但项目内集成简洁(import 路径规整,无 sys.path 黑魔法)
- ✅ aiohttp 依赖剥离 → 仓库依赖更干净
- ✅ ChatGPTBot + 工具函数迁过去后跟项目其他模块同等地位,未来重构 OAuth / 浏览器自动化更自然
- ⚠️ 失去"`python chatgpt_registration_bot_public.py` 单跑"调试入口 —— 替代方案:写 pytest fixture 调用 register_one_plus(项目内已有 conftest 风格)
- ⚠️ 需要重做之前两个 PRD 的引用更新(spec error-handling.md 真例 + plus_auto_register docstring)

## Research References

(本任务依赖项目已有抽象,无需外部研究。)
