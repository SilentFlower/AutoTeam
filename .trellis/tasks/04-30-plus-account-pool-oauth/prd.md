# 独立 Plus 号池 OAuth 接码推送 sub2api

## 目标

实现一个独立的 Plus 号池，用于管理 ChatGPT Plus 账号的 Codex OAuth 授权文件，并把可用账号推送到 sub2api。该号池必须与现有 active 账号池、FREE 号池隔离，流程以 Codex OAuth 登录为核心，登录过程中遇到手机号验证时自动通过 HeroSMS 接码完成。

## 已知信息

* 用户希望实现“单独的 plus 号池”。
* Plus 号池流程走 Codex OAuth 登录。
* OAuth 登录过程中需要自动接码登录。
* OAuth 成功后需要推送到 sub2api。
* 现有 `src/autoteam/codex_auth.py` 已支持 `login_codex_via_browser()`，并在 phone-verification / add-phone 页面调用 HeroSMS HTTP 接码流程。
* 现有 `src/autoteam/hero_sms.py` 已封装 HeroSMS 取号、等待验证码、OpenAI add-phone send/validate/resend 流程。
* 现有 `src/autoteam/free_accounts.py` 已实现与 active 池隔离的 FREE 号池，包含独立 JSON、独立 API、额度刷新、重授权、删除清理和 sub2api 同步。
* 现有 `src/autoteam/sub2api_sync.py` 已支持 active/FREE 两类本地源同步到 sub2api，并用本地邮箱并集避免跨池互删。

## 临时假设

* Plus 池需要采用独立存储，例如 `data/admins/{admin_id}/plus_accounts.json`，不写入 `accounts.json` 或 `free_accounts.json`。
* Plus 池账号登录后必须校验 `plan_type=plus`，否则不应以 active 状态进入 Plus 池。
* Plus 池同步到 sub2api 时应使用独立 source/kind 标记，避免与 active、FREE、main 主号互删。
* Plus 池可以复用现有 Codex OAuth、HeroSMS、CloudMail 邮件验证码、sub2api payload 生成与额度刷新逻辑。

## 待确认问题

* 已确认：Plus 账号来源为导入已有 Plus 邮箱/密码，AutoTeam 不负责自动注册或购买 Plus。

## 需求草案

* [x] 新增独立 Plus 号池数据层，支持列表加载、追加、更新、删除、按邮箱查找。
* [x] 新增 Plus 号导入与 OAuth 登录流程：用户提供已有 Plus 账号邮箱/密码，系统调用 Codex OAuth 自动登录，邮箱验证码由现有 mail provider 获取，手机号验证由现有 HeroSMS 自动接码。
* [x] OAuth 成功后保存 CPA / sub2api 兼容的 Codex auth 文件，并写回 Plus 池记录。
* [x] 登录成功后校验账号实际 plan 类型，只有 Plus 账号进入 active 状态；非 Plus 账号进入失败或异常状态并保留错误原因。
* [x] 新增 Plus 池推送 sub2api 入口，复用现有 OpenAI OAuth 账号 payload，远端标记必须区分 plus 池。
* [x] Plus 池删除时应清理本地 auth_file、sub2api 远端账号和本地 JSON 记录；由于 Plus 账号来自用户导入，不清理邮箱本身。
* [x] Plus 池不参与现有 `cmd_check` / `cmd_rotate` / `cmd_fill` / `cmd_cleanup` 的 active 池逻辑。
* [x] Plus 池不参与 FREE 池 invite/remove 流程。
* [x] Web 面板新增 Plus 池入口，支持导入、列表、额度刷新、重新登录、同步 sub2api、删除和复制账号密码。

## 验收标准草案

* [x] 可以导入已有 Plus 邮箱/密码并触发 Codex OAuth 登录。
* [x] OAuth 登录遇到手机号验证时自动调用 HeroSMS 接码，无需人工输入短信验证码。
* [x] 非 Plus plan 的账号不会以 active Plus 账号写入。
* [x] Plus 池账号成功保存 auth_file 后可以同步到 sub2api。
* [x] active 池、FREE 池、Plus 池同步 sub2api 时互不误删。
* [x] Plus 池删除会清理本地 auth_file 和对应 sub2api 远端账号。
* [x] 增加或更新单元测试覆盖数据层、sub2api 同步隔离、OAuth 结果状态转换。

## 完成定义

* 补充或更新单元测试。
* `uv run ruff check .` 通过。
* `uv run ruff format --check .` 通过。
* 相关行为变更记录到 PRD 和必要的 spec。
* 明确回滚方式：Plus 池独立文件和入口可禁用，不影响 active/FREE 池。

## 明确不做

* 不改动现有 active 池账号生命周期。
* 不复用 FREE 池 invite/remove Team 绕手机号验证流程。
* 不实现 Plus 账号自动注册、购买、支付或订阅开通。

## 技术笔记

* 参考 FREE 池隔离模式：`src/autoteam/free_accounts.py`。
* 复用 OAuth 登录能力：`src/autoteam/codex_auth.py`。
* 复用 HeroSMS 接码能力：`src/autoteam/hero_sms.py`。
* 复用 sub2api OAuth 账号 payload 与远端管理逻辑：`src/autoteam/sub2api_sync.py`。
* 后端规范索引：`.trellis/spec/backend/index.md`。
* 后端实现：`src/autoteam/plus_accounts.py`、`src/autoteam/api.py` 的 `/api/plus/*`。
* sub2api 实现：`src/autoteam/sub2api_sync.py` 新增 `SOURCE_PLUS` / `sync_plus_to_sub2api()` / `autoteam_kind=plus`。
* 前端实现：`web/src/components/PlusPage.vue`、`web/src/api.js`、`web/src/App.vue`、`web/src/components/Sidebar.vue`。
* 测试：`tests/unit/test_plus_accounts.py`、`tests/unit/test_api_plus.py`、`tests/unit/test_sub2api_sync.py`。
