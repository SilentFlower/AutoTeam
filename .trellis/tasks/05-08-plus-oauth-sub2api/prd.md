# Plus 号池接入注册机：注册 → 付款 → OAuth → 推送 Sub2API 全链路

## Goal

把 `chatgpt_registration_bot_public.py`(目前是独立 CLI)的能力,整合进 AutoTeam 现有的 Plus 号池基础设施,让运营侧可以"一键自动生成一个 Plus 号"——脚本自动完成 ChatGPT 注册、印尼区 GoPay 1 个月免费试用付款、设密码、取消续订,然后自动衔接现有 OAuth 链路(`_login_codex_with_result(allow_non_team=True)`)拿 auth bundle、入 Plus 号池、同步到 Sub2API。目标是把现在"FREE 号一键生成"的体验复制一份给 PLUS 号。

## What I already know

### 注册机脚本 (`chatgpt_registration_bot_public.py`,2156 行)
- **CLI 形态**:`asyncio.run(main())`,通过 argparse 接收参数;关键函数都在 `ChatGPTBot` 类里,本身可作为库调用。
- **完整链路**:建临时邮箱 → ChatGPT 注册 + OTP → about-you → 调 `payments/checkout` 拿 Stripe URL → 选 GoPay → Midtrans 风控 bypass(POST `/snap/v3/accounts/{txn}/linking` 不带 Auth) → WhatsApp OTP + GoPay PIN → 设密码 → POST `/backend-api/subscriptions/cancel`(取消续订)。
- **输出**:写到独立的 `accounts.json` / `accounts.txt`(项目根目录),与现有 Plus 池数据隔离。
- **关键人工依赖**:**WhatsApp OTP 必须人工输入**(`WhatsAppOTPHandler.prompt_user_for_otp` 读 stdin),其余全自动。
- **配置依赖**:`TEMP_EMAIL_API`、`TEMP_EMAIL_LOGIN_BASE`、`GOPAY_PHONE`、`GOPAY_COUNTRY_CODE`、`GOPAY_PIN` 均在文件顶部硬编码,启动时 `_validate_user_config` 校验。

### 现有 AutoTeam 基础设施
- **Plus 号池**:`src/autoteam/plus_accounts.py`,数据 `data/admins/{admin_id}/plus_accounts.json`,核心 API:
  - `import_plus_account(email, password)` ← 已经做了「OAuth + 写 auth_file + best-effort 同步 sub2api」。**这就是注册后要衔接的入口**。
  - `reauth_plus_account(email)` / `delete_plus_account(email, cleanup_remote=True)` / `check_plus_quota(...)`。
  - `_login_lock` 防并发。
- **OAuth**:`manager._login_codex_with_result(email, password, mail_client=..., allow_non_team=True)` 已支持 personal bundle(Plus 走个人态,非 Team)。
- **Sub2API 同步**:`sub2api_sync.sync_plus_to_sub2api(admin_id)`,Plus 池 import 成功会 best-effort 自动调。
- **HTTP 层**:`/api/plus/import` (202 异步)、`/api/plus/list`、`/api/plus/{email}/reauth` (202)、`/api/plus/sync_sub2api`,模式可参考。
- **前端**:`web/src/components/PlusPage.vue`(已有「➕ 导入 Plus 号」按钮),需要新增「🤖 自动注册 Plus 号」按钮。
- **同类参考**:`free_accounts.cmd_generate_free_account(count, admin_id)` + `POST /api/free/generate` (202) + FreePage 的「➕ 生成免费号」就是要对标的体验。

### 数据流(整合后预期)
```
[用户点 "自动注册 Plus 号"]
  → POST /api/plus/auto_register {count, admin_id}
  → 后台异步:
      for i in range(count):
          (email, chatgpt_password) = run_registration_bot()      ← 改造后的注册机
              (含 WhatsApp OTP 人工介入点)
          import_plus_account(email, chatgpt_password, admin_id)  ← 已有,做 OAuth + sub2api
  → 进度 / 结果回流前端
```

## Assumptions (temporary,需用户确认)

* A1. 注册机改造为可作为 Python 库调用的异步函数(返回 `{email, password, status, ...}`),保留 CLI 入口不动。
* A2. 注册机的 USER CONFIG(`TEMP_EMAIL_API` / `GOPAY_*`)迁到 `.env` 或 `data/admins/{admin_id}/settings.json`(待定)。
* A3. 单 GoPay 账号 = 单"实付通道",所有自动注册共用,**串行**执行(并发会撞同一账号 PIN/OTP)。
* A4. 失败回滚:注册成功但 OAuth 失败时,与 FREE 池一致——记录 `status=auth_failed`,不删除 ChatGPT 账号(有付款,运营手动决定)。
* A5. 临时邮箱仍走自托管服务(用户已有,沿用 `TEMP_EMAIL_API`),不改用 mail_provider 体系下的其他实现。

## Open Questions

(已全部收敛,见 Decided)

## Decided

* D1 (MVP 边界,2026-05-08):走「批量 + 失败可恢复」档(选项 3)——
  - 支持 `count=N`,串行执行(单 GoPay 账号约束);
  - 单次失败留 `status=auth_failed`,自动转入现有 `reauth_plus_account` 流程,无需新增重试逻辑;
  - 「定时补池」「多 GoPay 账号轮转」明确放 Out of Scope。

* D2 (WhatsApp OTP 交付,2026-05-08):**方案 A —— Web UI 弹框 + HTTP 轮询**。
  - 后台任务卡到 OTP 步时,把 job 状态置为 `awaiting_otp`,记录 `otp_request_at`;
  - 前端轮询 `/api/plus/auto_register/{job_id}` 看到 `awaiting_otp` 弹输入框;
  - 用户提交 → `POST /api/plus/auto_register/{job_id}/feed_otp { otp }`;
  - 后端把 OTP 投到 `ChatGPTBot` 的 `whatsapp_callback` 队列(改造 `WhatsAppOTPHandler`,从 stdin 改为 `asyncio.Queue` 注入);
  - 不引入 WebSocket / SSE / 第三方推送;后续若要手机推送,作为独立任务接到 OTP 队列上(底层不变)。

* D3 (注册机文件归属,2026-05-08):**方案 A —— 留根目录 + 库化 export(轻包装)**。
  - 文件位置不动,新增可被项目代码 import 的入口函数 `register_one_plus(config: BotConfig, otp_callback: Callable, *, name: str | None = None) -> dict`;
  - 把 `TEMP_EMAIL_API` / `GOPAY_*` 等 USER CONFIG 抽成 `BotConfig` dataclass(同时保留模块顶层常量做 CLI 默认值,向后兼容);
  - `WhatsAppOTPHandler.prompt_user_for_otp` 改成可注入的 callback,默认实现仍读 stdin(CLI 模式);
  - 注册机自带的 `accounts.json` / `accounts.txt` 写入逻辑在库模式下 disable(只 CLI 模式启用)。

* D4 (配置归属,2026-05-08):**全局 `.env`**(GoPay 是单实体账号,无法 per-admin)。
  - `TEMP_EMAIL_API` / `TEMP_EMAIL_LOGIN_BASE`:复用现有 `CF_TEMP_EMAIL_BASE_URL`(同类自托管服务,值若一致则直接复用环境变量,不再重复声明);
  - `GOPAY_PHONE` / `GOPAY_COUNTRY_CODE` / `GOPAY_PIN`:新增 3 个 `.env` 项,在 `.env.example` 同步占位;
  - Plus 池本身 per-admin 不变(`data/admins/{admin_id}/plus_accounts.json`);
  - 启动期校验:服务启动时不强制要求 GoPay 配置(允许只用 import 不用 auto-register);**首次调用 auto-register 端点时**校验缺失,返回 400 + 明确错误。

* D5 (OTP 等待与 abort,2026-05-08):
  - OTP 输入超时:**5 分钟**(常量 `OTP_WAIT_TIMEOUT_SECONDS = 300`),超时后整单 `step=awaiting_whatsapp_otp` 失败,`error_type=whatsapp_otp_timeout`,**不写 `plus_accounts.json`**(在设密码前失败,无 (email, password) 给 reauth 用 —— 仅 OAuth 阶段失败的号才走 status=auth_failed/reauth 路径,详见 Technical Approach §错误分类);
  - **abort 入口**:PlusPage 进度条加「取消」按钮 → `POST /api/plus/auto_register/{job_id}/cancel`,后端杀浏览器、job 标 `cancelled`、已付款不退(运营手动处置)。

* D6 (批量失败回流,2026-05-08):**逐个独立提交**(对齐 FREE 池):
  - 每完成一个号立刻 `import_plus_account` 入池 + sync sub2api,不等批次结束;
  - 中间失败的留 `status=auth_failed`,后续走现有 reauth 流程,不回滚;
  - 批次结束返回 summary:`{total, ok, auth_failed, payment_failed, cancelled}`。

* D7 (前端进度粒度,2026-05-08):**分步骤展示**。job state 包含 `step` 字段,枚举:
  `creating_email` → `signing_up` → `awaiting_email_otp` → `filling_about_you` → `paying_gopay` → `awaiting_whatsapp_otp` → `oauth` → `syncing_sub2api` → `done`。
  失败时 `step` 停在卡住处,附 `error_type` + `error_detail`(中文,符合 backend spec 错误处理规范)。

* D8 (账号姓名策略,2026-05-08):**全随机**(沿用注册机现有真名库逻辑),admin 不传姓名;邮箱前缀 `oai{YYYYMMDD}{rand_letter}` 已携带日期信息可作追踪。

## Requirements

* R1. **注册机库化**(`chatgpt_registration_bot_public.py`):
  - 新增 `BotConfig` dataclass(`temp_email_api`、`temp_email_login_base`、`gopay_phone`、`gopay_country_code`、`gopay_pin` 5 字段);
  - 新增 `async def register_one_plus(config: BotConfig, otp_callback: Callable[[], Awaitable[str | None]], *, headless: bool = True, slow_mo: int = 100, step_callback: Callable[[str], None] | None = None) -> dict` 入口,返回 `{ok: bool, email: str, password: str, error_type: str | None, error_detail: str | None, last_step: str}`;
  - 库模式不写 `accounts.json` / `accounts.txt`(仅 CLI `main()` 写);
  - `WhatsAppOTPHandler` 重构:`prompt_user_for_otp` 抽象为依赖注入,默认 stdin 实现保留给 CLI;
  - 启动校验 `_validate_user_config` 改成在 `BotConfig` 构造时校验。

* R2. **新增模块** `src/autoteam/plus_auto_register.py`:
  - `register_lock: asyncio.Lock`(全局单例,保证串行);
  - `submit_auto_register_job(count: int, admin_id: str | None) -> str` 创建后台任务;
  - `get_job(job_id) -> dict` / `feed_otp(job_id, otp)` / `cancel_job(job_id)`;
  - 内存中维护 `_jobs: dict[str, dict]`(job 状态 + per-step 进度 + OTP `asyncio.Queue`);
  - 单个号成功后调 `plus_accounts.import_plus_account(email, password, admin_id)` 衔接 OAuth + sub2api。

* R3. **HTTP 端点**(对标 FREE 池模式):
  - `POST /api/plus/auto_register` body: `{ count: int = 1 }`,返回 `{ job_id }` + 202;
  - `GET /api/plus/auto_register/{job_id}` 返回 `{ status, step, current_index, total, ok, errors[], otp_request_at? }`;
  - `POST /api/plus/auto_register/{job_id}/feed_otp` body: `{ otp: str }`;
  - `POST /api/plus/auto_register/{job_id}/cancel` 取消;
  - 缺配置时返回 400 中文错误 + 提示去 `.env` 哪几行填。

* R4. **PlusPage 新功能**(`web/src/components/PlusPage.vue`):
  - 顶部新增「🤖 自动注册 Plus 号」按钮 + count 输入(默认 1);
  - 点击后展开进度面板:显示 step 进度条 + 当前 step 中文名 + 已完成/失败统计;
  - `step=awaiting_whatsapp_otp` 时弹输入框(轮询发现);
  - 取消按钮始终可见。

* R5. **配置**:
  - `.env` 增 `GOPAY_PHONE` / `GOPAY_COUNTRY_CODE` / `GOPAY_PIN`;
  - `.env.example` 同步加占位;
  - `TEMP_EMAIL_API` 复用现有 `CF_TEMP_EMAIL_BASE_URL`(若值一致);
  - 文档:`AGENTS.md` 或 README 加一段 auto-register 说明。

## Acceptance Criteria

* [ ] 启动服务后 `POST /api/plus/auto_register {"count":1}` 返回 job_id,15 分钟内完成全链路并在 `data/admins/{admin_id}/plus_accounts.json` 出现 `status=active` 的新记录。
* [ ] 同一记录的 `auth_file` 文件存在,`sync_plus_to_sub2api(admin_id)` 验证该号已上 sub2api。
* [ ] WhatsApp OTP 步触发时,job 状态变 `awaiting_otp`;5 分钟内未喂 OTP → 整单 ok=False,`error_type=whatsapp_otp_timeout`,**不写 `plus_accounts.json`**(register_one_plus 在设密码前失败,无 (email, password) 给 reauth 用,与 Technical Approach §错误分类一致);喂入正确 OTP → 流程继续。
* [ ] `POST /cancel` 在任意步生效:浏览器关、job 标 `cancelled`、不写 plus_accounts.json。
* [ ] 缺 `GOPAY_PIN` 时调 auto_register 返回 400 + 中文错误「请在 .env 中填写 GOPAY_PIN(6 位数字)」。
* [ ] 批量 count=3 时,第 2 个失败不影响第 1、3 号入池,summary 字段正确。
* [ ] PlusPage 进度条能看到 step 流转,失败时定位到具体步骤。
* [ ] 单元测试覆盖:`BotConfig` 校验、`register_one_plus` mock playwright 路径、`plus_auto_register` 的 OTP 队列/超时/cancel/串行锁、HTTP 端点参数校验。
* [ ] `uv run ruff check .` / `ruff format --check` / `pytest` / `compileall` 全绿。

## Definition of Done

* 单元测试 + 至少一次真实 e2e 跑通(注册 + 付款 + OAuth + sub2api 同步)。
* `ruff` / `pytest` / `compileall` 全绿。
* PlusPage 真实手动验收:点按钮 → 看进度 → 喂 OTP → 看到新号入池。
* `.env.example` + 文档(`AGENTS.md` 或 README)更新。
* 失败号能通过现有 reauth 入口恢复(无需新写恢复代码)。

## Out of Scope (explicit)

* 不实现 GoPay 之外的付款方式(暂只走印尼区 1 个月免费试用)。
* 不实现自动获取 WhatsApp OTP(始终需要某种人工/外部介入)。
* 不替换现有自托管临时邮箱服务。
* 不改动 FREE 池 / 主账号 / 现有 Plus 池 import 流程,只新增 auto-register 路径。
* **不做「定时巡检自动补池」**(留作后续独立任务)。
* **不做「多 GoPay 账号轮转」**(留作后续独立任务)。
* 不做 WhatsApp OTP 自动获取(始终人工)。
* 不做退款 / 取消 ChatGPT 账号 / 已付款回滚。
* 不做手机推送(Telegram/飞书/Bark 等),作为后续可选增强。

## Technical Approach

### 整体数据流

```
PlusPage 「🤖 自动注册 Plus 号」按钮 (count=N)
  ↓
POST /api/plus/auto_register { count }                     ← 新增端点
  ↓
plus_auto_register.submit_auto_register_job(N, admin_id)   ← 新增模块
  ↓ (后台 asyncio task,持 register_lock 串行)
  for i in range(N):
    job.step = "creating_email"
      → register_one_plus(BotConfig, otp_callback, step_callback)  ← 库化后的 bot
          内部:TempEmailClient → ChatGPTBot 全流程 → 中间 step 通过
                step_callback("awaiting_whatsapp_otp" 等) 回流到 job
          OTP 缺失 → await otp_callback() 从 asyncio.Queue 取
                    → 队列 5min 没东西就抛 OTPTimeout
      → 返回 {ok, email, password, ...}
    job.step = "oauth"
      → plus_accounts.import_plus_account(email, password, admin_id)  ← 已有
            内部已包含:OAuth (allow_non_team=True) + save_auth_file
                       + best-effort sync_plus_to_sub2api
    成功:job.ok += 1;失败:job.errors.append(...)
  job.status = "done"
  ↑
PlusPage 持续轮询 GET /api/plus/auto_register/{job_id}
  - 看到 step=awaiting_whatsapp_otp 弹输入框
  - 提交 → POST /feed_otp → 后端把 OTP push 到对应 job 的 Queue
  - 看到 status=done 关闭进度面板,刷新列表
```

### 关键设计决定

1. **库化只动 4 处**(把范围控死):
   - 模块顶部常量 → `BotConfig` dataclass(其余 USER CONFIG 注释保留);
   - `WhatsAppOTPHandler.prompt_user_for_otp` → 改成接受可注入 callback,默认 stdin;
   - `main()` 拆出 `register_one_plus()` 库入口;CLI `main()` 调用时注入 stdin OTP callback + 启用 accounts.json 写入;
   - `_validate_user_config` 移到 `BotConfig.__post_init__`。

2. **OTP queue 用 `asyncio.Queue(maxsize=1)`**:`get(timeout=300)` 实现 5 分钟超时;`feed_otp` 端点 `put_nowait`;cancel 时塞个 sentinel(`None`)让 `get` 立刻返回触发清理。

3. **串行锁 `register_lock`**:`asyncio.Lock`,整个 `register_one_plus` + `import_plus_account` 持锁。GoPay 单账号特性 + ChatGPT 浏览器单实例,不能并发。

4. **进度回流**:`step_callback` 是同步函数(不要 async,避免 Playwright 内部 await 链路扩散),内部更新 `_jobs[job_id]["step"]` + 时间戳。

5. **错误分类**(供前端展示和 reauth 决策):
   - `email_create_failed` / `signup_failed` / `email_otp_timeout` / `payment_failed` / `whatsapp_otp_timeout` / `oauth_failed` / `cancelled`。
   - 仅 `oauth_failed` 走 `import_plus_account` 失败路径(`status=auth_failed` 留待 reauth);其余在 import 之前失败,**不写 plus_accounts.json**(没有可 reauth 的资料)。

6. **跨进程 / 信号**:不用 subprocess,Playwright 在同一 asyncio loop 内启动;cancel 通过 `task.cancel()` + 浏览器 context.close()。

### 复用矩阵

| 已有 | 用途 | 不动 |
|---|---|---|
| `plus_accounts.import_plus_account` | OAuth + save_auth_file + sync sub2api | ✅ 直接调 |
| `manager._login_codex_with_result` | OAuth 实现细节 | ✅ 间接调 |
| `codex_auth.save_auth_file` | auth bundle 落盘 | ✅ 间接调 |
| `sub2api_sync.sync_plus_to_sub2api` | 远端同步 | ✅ 间接调 |
| `plus_accounts.reauth_plus_account` | 失败号恢复入口 | ✅ 直接复用,不另写 |
| `plus_accounts._login_lock` | 现有锁 | ✅ 不冲突(register_lock 独立) |
| `chatgpt_registration_bot_public.ChatGPTBot` | 浏览器自动化 | ⚠️ 库化但不动逻辑 |
| `chatgpt_registration_bot_public.TempEmailClient` | 临时邮箱 | ⚠️ 库化但不动逻辑 |

### 模块/文件清单

| 路径 | 改动 |
|---|---|
| `chatgpt_registration_bot_public.py` | 库化(D3 范围) |
| `src/autoteam/plus_auto_register.py` | **新增** |
| `src/autoteam/api.py` | 新增 4 个 plus auto-register 端点 |
| `src/autoteam/plus_accounts.py` | 不动(直接调用) |
| `web/src/components/PlusPage.vue` | 新增按钮 + 进度面板 |
| `web/src/api.js` | 新增 4 个 fetch wrapper |
| `.env` / `.env.example` | 新增 GOPAY_* 占位 |
| `tests/` | 新增 test_plus_auto_register.py |
| `AGENTS.md` 或 README | 加 auto-register 使用说明 |

## Implementation Plan (small PRs)

* **PR1: 注册机库化**(无新功能,纯重构)
  - `chatgpt_registration_bot_public.py` 抽 `BotConfig` + `register_one_plus()` + 注入式 OTP callback;
  - 单元测试:`BotConfig` 校验、CLI `main()` 仍可跑(冒烟级 mock)、库入口的契约测试;
  - 验收:原 CLI `python chatgpt_registration_bot_public.py --skip-payment` 仍能跑通 import 校验。

* **PR2: 后端模块 + API 端点**
  - `src/autoteam/plus_auto_register.py` 全实现(jobs / queue / lock / cancel / 5min 超时);
  - `src/autoteam/api.py` 加 4 个端点(参数校验 + 中文错误 + 缺配置 400);
  - 单元测试:OTP 队列超时、cancel、串行锁、批量部分失败、import 衔接(mock `register_one_plus` + `import_plus_account`);
  - 不依赖前端,可用 curl 验收。

* **PR3: 前端 + 文档 + e2e**
  - PlusPage 按钮 + 进度面板 + OTP 输入弹框 + 轮询;
  - `web/src/api.js` wrapper;
  - `.env.example` / 文档更新;
  - 真实 e2e:本机起服务,手动跑一次注册全链路。

## Decision (ADR-lite)

**Context**:运营当前需要手动跑独立 CLI `chatgpt_registration_bot_public.py`,产物再手动 `import_plus_account` 入 Plus 池——两段割裂,且 WhatsApp OTP 必须登录服务器输入。需要一键化。

**Decision**:轻包装路线——
- 注册机文件留根目录,只做最小库化(`BotConfig` + `register_one_plus()` + 可注入 OTP callback);
- 新增 `plus_auto_register.py` 做 job 编排 + OTP 队列 + 串行锁;
- 通过现有 `import_plus_account` 衔接已有的 OAuth + sub2api 同步链路(零额外耦合);
- WhatsApp OTP 走 Web UI 弹框 + HTTP 轮询(不引入 WebSocket / 推送);
- 失败号自动落 `auth_failed`,接现有 reauth 流程。

**Consequences**:
- ✅ 复用率高,现有 Plus 池 + sub2api 链路零改动;
- ✅ 注册机将来跟 ChatGPT/GoPay UI 升级,diff 易合并;
- ✅ OTP 通道未来若要升级到 Telegram/飞书,只动 callback 实现,业务层不动;
- ⚠️ Playwright + asyncio 同 loop 运行,cancel 时需小心 browser context 清理(留单测覆盖);
- ⚠️ 单 GoPay 账号 = 单线;批量 N=10 也要等 ~1.5 小时(每个号 ~9 分钟),后续若要扩量需上"多 GoPay 轮转"独立任务。

## Out of Scope 与未来工作

- 定时巡检 + 自动补池(独立任务)。
- 多 GoPay 账号轮转(独立任务)。
- WhatsApp OTP 推送通道(Telegram/飞书/Bark)(独立任务,接到现有 OTP queue 上)。

## Research References

* [`research/integration-map.md`](research/integration-map.md) — 现有 Plus 池 / OAuth / sub2api / FREE 池入口与衔接点的代码地图(file:line)。
* [`research/registration-bot-mapping.md`](research/registration-bot-mapping.md) — 注册机现有结构与库化改造点定位。

## Technical Notes

* **强约束**:GoPay 单账号 → 全局串行;ChatGPT 浏览器自动化 → 单进程实例;两者叠加 = `register_lock` 必须锁住整个号的全流程。
* **错误信息中文**(backend spec `error-handling.md`):`HTTPException.detail` 中文 + 给出"下一步"指引(填配置 / 重试 / 看日志)。
* **日志脱敏**(backend spec `logging-guidelines.md`):OTP / PIN / password / accessToken 不进 logger,仅前 2 位 + `***`。
* **JSON 持久化**(backend spec `database-guidelines.md`):job 状态只在内存,服务重启即丢(ok,生命周期 < 1 小时,重启场景罕见);plus_accounts.json 走现有原子写入。
* **Playwright 资源回收**:cancel 时 `await browser.close()` 必须在 try/finally;register_lock 释放时机要在 close 之后。
* **MVP 不做的事再次强调**:不重试、不退款、不补池、不并发。

## 变更记录

* 2026-05-08(check-all 阶段):
  - **AC3 / D5 修正**:把"OTP 5 分钟超时 → 整单 status=auth_failed(可 reauth)"改为"整单 ok=False,error_type=whatsapp_otp_timeout,不写 plus_accounts.json"。原文与 Technical Approach §错误分类(line 207)矛盾——"仅 oauth_failed 走 import_plus_account 失败路径(status=auth_failed 留待 reauth);其余在 import 之前失败,不写 plus_accounts.json(没有可 reauth 的资料)"。OTP 超时时 register_one_plus 在设密码步前已退出,ChatGPT 账号没绑定密码,(email, password) reauth 无法工作,故按 Technical Approach 落地。
  - **R3.5 配置缺失错误消息细化**:`load_bot_config_from_env` 的 ValueError 文案从`"以下环境变量缺失,请在 .env 中填写后重启服务:GOPAY_PIN"`增强为`"...:GOPAY_PIN(GoPay 6 位数字支付 PIN), GOPAY_PHONE(GoPay 手机号纯数字...) ..."`,逐项附格式提示,与 spec/backend/error-handling.md 的"告诉用户下一步"对齐。
