# 多管理员主号 + 工作台四页合一

## 目标

让 AutoTeam 支持登录、管理多个 Team 管理员（即"主号"）账号，并在统一的"工作台"页面中切换不同管理员，查看其下挂的子账号、Team 成员、账号池操作和同步操作。

**为什么要做：**
- 当前架构假设全局只有一个管理员/主号（`state.json` 单文件、`codex-main-{account_id}.json` 单文件、`accounts.json` 不区分 owner），无法承载"一个用户管多个 Team workspace"的场景。
- 当前导航把仪表盘、Team 成员、账号池操作、同步中心拆成 4 个并列页签，但它们其实都围绕"当前主号"展开，频繁跳页且上下文重复。

## 已知事实（来自代码扫描）

### 数据/存储现状（单实例硬编码）
- 管理员凭据：`state.json` 单文件，由 `src/autoteam/admin_state.py` 的 `load_admin_state()` / `update_admin_state()` 读写。
- 主号 Codex 凭据：`auths/codex-main-{account_id}.json`（按 ChatGPT account_id 命名，但目录全局唯一）。
- 子账号：`accounts.json` 单文件，主号识别靠 `_is_main_account_email(email)` 直接对比 `get_admin_email()`（`accounts.py:26-27`）。
- 后端运行时单例：`api.py` 中 `_admin_login_api` / `_admin_login_step` / `_playwright_lock` 都是模块级全局，假设同一时刻只有一个登录流程。

### 受影响 API（无 admin_id 参数，隐含全局唯一）
- `/api/admin/status`、`/api/admin/login/start`、`/api/admin/logout`
- `/api/accounts`、`/api/accounts/{email}/codex-auth`、`/api/accounts/{email}/kick`、`/api/accounts/{email}/login`
- `/api/team/members`、`/api/team/members/remove`
- 所有 `/api/sync/*`、`/api/pool/*`（具体待二次确认）

### 前端现状
- 路由：`web/src/App.vue` 用 v-if + `currentPage` 字符串切换，无 vue-router、无 pinia。
- 页面职责：
  - `Dashboard.vue`（账号池统计 + 5h/周额度卡片，标记主号）
  - `TeamMembers.vue`（拉 ChatGPT Team 成员列表，可移除）
  - `PoolPage.vue` = `<TaskPanel mode="pool">`（轮转/检查/补满/添加/清理）
  - `SyncPage.vue` = `<TaskPanel mode="sync">`（本地对账 + 同步到 Sub2API）
- 状态：通过 props/emit 上下传递，App.vue 持有全局 `status / adminStatus / codexStatus / tasks`。

## 假设（待用户确认）

- 多个管理员账号属于**同一个使用者**（同一 API Key 后），不是多租户隔离场景 → 不需要 RBAC。
- 管理员之间数据不共享：每个管理员有自己的子账号池、自己的 Codex 主号凭据、自己的 Sub2API 同步目标。
- 至少要支持 2-5 个管理员；**不需要**支持几十上百个（决定 UI 用下拉框还是搜索器）。

## Decision (ADR-lite) - 已确定

**Context**: 多管理员主号 + 工作台合并需要先定 7 个根基决策。

**Decisions**:
1. **运行模型 = 切换激活**：用户主动触发的任务（轮转/同步/kick/添加账号等）只作用于"当前激活 admin"。
2. **页面布局 = 页内 Tab 切换**：合并页顶部放 admin 切换器，下方四个 tab（仪表盘 / Team / 账号池 / 同步）。
3. **存储方案 = 目录树按 admin 隔离**：`data/admins/{admin_id}/state.json`、`data/admins/{admin_id}/accounts.json`、`data/admins/{admin_id}/auths/codex-main-*.json`。索引文件 `data/admins.json` 维护 admin 列表（含别名、上次激活时间）。
4. **升级路径 = 首次启动自动迁移**：检测旧 `state.json` + `accounts.json` → 生成 admin_id → 移到新目录树 → 备份到 `legacy-backup/`。
5. **后台巡检 = 轮流巡检所有 admin**：`_auto_check_loop` 外层加 admin 循环，依次跑每个 admin 的额度检查/自动轮转/补位。所有 admin 的账号都受后台守护照顾。
6. **新管理员入口 = 工作台顶部下拉 "+ 添加"**：admin 切换器下拉里直接有 "+ 添加新管理员"项，点开复用现有的邮箱→密码→验证码→workspace 选择流程（modal 形式）。
7. **同步动作 = 只影响当前 admin**：同步中心 tab 显示的 Sub2API 远端、对账范围都以当前 admin 为界，不引入跨 admin 批量同步。
8. **"邀请加号"提升为顶级操作**：工作台顶部 toolbar 放一个醒目的主按钮 "➕ 邀请加号"，与 admin 切换器同级。点击后以 modal 形式跑现有 `add-via-invite` 流程。账号池 tab 内保留原按钮（双入口）。
9. **admin_id 生成 = 8 位 UUID hex**（确认）。

**Consequences**:
- ✅ 用户操作语义清晰：当前 admin 是什么，操作就作用于谁。
- ✅ 后台守护"全员保活"：切到 admin A 看仪表盘时，admin B/C 的账号池仍在后台跑巡检，不会因为没人看就过期。
- ✅ 调度并发模型不变：所有 admin 共享一把全局 Playwright lock，轮转/巡检串行不并发，Sub2API 调用也不会撞车。
- ⚠️ `_auto_check_loop` 需重构为"遍历 admins → 切换数据源 → 跑原有逻辑"。
- ⚠️ 跨 admin 状态切换发生在三处：① 用户在 UI 切 admin（前端 store 更新）；② 后台循环切到下一个 admin；③ 用户主动触发任务时锁定当前 admin。这三处必须共享同一种"激活 admin"上下文表达方式。

## Requirements（演进中）

- 后端支持注册/登录多个管理员凭据，每个管理员有稳定 ID。
- 子账号、Codex 主号凭据、同步目标都按管理员维度隔离。
- 工作台页面（合并仪表盘 + Team 成员 + 账号池 + 同步）顶部提供管理员切换器，切换后下方四个区段全部刷新到该管理员上下文。
- 现有 `/api/admin/login/*` 流程需要扩展为可指定"为哪个管理员（或新管理员）登录"。

## Acceptance Criteria（演进中）

- [ ] 可以在面板里登录第二个管理员账号，与第一个并存且互不覆盖凭据。
- [ ] 工作台页头有"当前管理员"切换器，列出所有已登录的管理员。
- [ ] 切换管理员后，仪表盘账号池、Team 成员、TaskPanel(pool) 列出的可执行任务、TaskPanel(sync) 列出的同步目标都刷新为该管理员的数据。
- [ ] 对某管理员触发的轮转/同步任务，不会污染其他管理员的状态（凭据文件、accounts.json 行）。
- [ ] 旧的单管理员部署升级后能自动迁移到新结构，不丢账号数据。

## Definition of Done

- 单元测试覆盖 admin_state / accounts 多实例读写。
- 手工跑通：登录 admin A → 登录 admin B → 切换 → 各自巡检与同步。
- 文档更新：`docs/architecture.md`、`docs/getting-started.md` 中"管理员/主号"章节。
- 升级路径：旧 state.json + accounts.json 可被脚本一次性迁移。

## Out of Scope（明确排除）

- RBAC / 多租户隔离（不引入用户系统）。
- 跨管理员的账号借调或混合调度。
- 把"配置面板"、"任务历史"、"日志"也合并进工作台（这三个仍是独立页签）。
- 一次同时为多个管理员并发执行任务（除非 Q1 选 A）。

## Known Tech Debt（PR4 末态）

PR4 实施时识别但**未在本次任务内修复**的已知技术债,留给后续独立 PR:

1. **AdminLoginFlow 与 Settings.vue 主线登录逻辑双份代码**:PR3 新建 `AdminLoginFlow.vue` 用于工作台 modal 场景,但 `Settings.vue`(718 行)中"管理员登录"section 仍保留独立的 step machine 与 submit handlers,违背 PRD 决策 #6"工作台和 ConfigPage 共用"的字面要求。PR4 评估后认为 inline 重构 Settings.vue 风险大于收益(影响面广 + 现有功能稳定),决定推迟。后续 PR 建议方向:抽出 `composables/useAdminLoginFlow.js` 让两边共用,或让 AdminLoginFlow 通过 prop 接收 `adminStatus` 直接复用 Settings.vue 的状态机(单一真相源)。
2. **Sub2API 远端配置 per-admin 决策**:PR2 仅做了"子账号数据按 admin 隔离同步",但 Sub2API 连接配置仍来自全局 env(`SUB2API_URL` / `SUB2API_EMAIL` / `SUB2API_PASSWORD`),所有 admin 共享同一远端。PRD 决策 #7 字面"Sub2API 远端按 admin 为界"未完全满足。如有不同 admin 同步到不同 Sub2API 远端的需求,后续 PR 需把这些 env 改为 per-admin 配置(可能挂在 admin_registry 索引或 `data/admins/{id}/sync_config.json`)。
3. **`manager.cmd_*` 不接 admin_id 形参,靠 `set_active_admin` 切上下文**:PR2 巡检循环改造为遍历 admins 时通过 `set_active_admin` 切换全局 active 来让 `cmd_rotate / cmd_check / cmd_cleanup` 等老 CLI 函数走对应 admin 的数据层 fallback。这是 process-wide 的状态变更,如果同一进程内有 API 线程在巡检循环切换瞬间走 fallback 链(header→active→None),理论上可读到错 admin。当前依赖前端强制注入 `X-Autoteam-Admin-Id` header 兜底。后续 PR 建议把 `manager.cmd_*` 改造为接受显式 `admin_id` 参数,消除 active fallback 的并发污染面。
4. **`_admin_state_call` / `_email_is_main` 兼容包装**:PR2 fix #3 已用 `inspect.signature` 替代了 `try/except TypeError` 的过宽兜底,但兼容包装本身仍是为了兼容旧测试 `monkeypatch` 用 1-arg lambda 的反例存在。后续可在测试侧统一用 spec 而非 monkeypatch,然后删除两个 wrapper 直接调用真实数据层函数。
5. **InviteFlowModal 后端任务路由依赖**:`/api/tasks/add-via-invite` 路由由别人在工作树未提交修改中提供;multi-admin 任务最终合入主分支前需要确认该路由已正式入仓,否则 InviteFlowModal 提交时会 404。

## Technical Approach（最终方案，含次要技术细节）

### 后端

**1. Admin 抽象层（新建 `src/autoteam/admin_registry.py`）**
- 定义 `Admin` 数据类：`admin_id`（8 位 UUID hex）、`alias`（别名，默认 = workspace_name 或 email）、`email`、`workspace_name`、`account_id`（ChatGPT account_id）、`created_at`、`last_active_at`。
- 提供 `list_admins()` / `get_admin(admin_id)` / `add_admin(admin)` / `remove_admin(admin_id)` / `set_active_admin(admin_id)` / `get_active_admin()`。
- "当前激活 admin"持久化到 `data/admins.json` 的 `active_admin_id` 字段（保证服务重启后仍记得用户最后选了哪个）。

**2. 数据隔离改造**
- `admin_state.py`：`load_admin_state(admin_id)` / `update_admin_state(admin_id, ...)`，路径变 `data/admins/{admin_id}/state.json`。
- `accounts.py`：`load_accounts(admin_id)` / `save_accounts(admin_id, ...)`，路径变 `data/admins/{admin_id}/accounts.json`。`_is_main_account_email()` 改成接收 admin_id 入参。
- `codex_auth.py`：所有 `auths/codex-main-*.json` 路径前缀加 `data/admins/{admin_id}/`。
- `sync_targets.py`：Sub2API 同步配置按 admin 分文件存放（或在原文件中加 `admin_id` 字段过滤）。

**3. API 上下文传递（推荐方案）**
- 所有相关 API 通过 HTTP header `X-Autoteam-Admin-Id` 携带 admin_id。
- FastAPI 加 `Depends(get_current_admin_id)`：优先读 header，缺省时读 `data/admins.json` 的 `active_admin_id`。
- 新增 `POST /api/admins`（list 已登录 admin）、`POST /api/admins/active`（切换激活 admin）、`POST /api/admins/login/start` 系列（复用原 `/api/admin/login/*` 但允许指定为新 admin 创建凭据）、`DELETE /api/admins/{admin_id}`（删除 admin 凭据 + 数据目录）。
- 旧 `/api/admin/*` 路由保留并标记 deprecated，内部转发到带 admin_id 的实现，避免立即破坏现有客户端。

**4. 后台巡检改造**
- `_auto_check_loop` 外层加 `for admin in list_admins()` 循环。
- 每个 admin 的巡检间隔保持原 `AUTO_CHECK_INTERVAL`，但改成"按 admin 错峰"避免同一时刻对多个 admin 同时跑 Playwright（依然依赖全局锁串行）。
- 巡检日志加 `admin_id` 字段。

**5. 任务/日志 admin 标识**
- `Task` 数据结构加 `admin_id` 字段（用户主动触发的任务必填）。
- `TaskHistory` / `LogViewer` 仍是全局视图（不动导航），但展示时显示每行的 admin 别名，并支持按 admin 过滤。

### 前端

**1. 状态层（`web/src/store/admins.js`，新建）**
- `reactive({ admins: [], currentAdminId: null })` 单例（不引 pinia，沿用项目轻量风格）。
- `useAdmins()` 暴露：`refreshAdmins()` / `switchAdmin(id)` / `addAdmin()` / `removeAdmin(id)`。
- 切换 admin 时：① 更新 currentAdminId；② 调 `POST /api/admins/active`；③ emit 全局事件让 Workbench 各 tab 刷新。

**2. API 拦截器（改 `web/src/api.js`）**
- 在 axios/fetch 请求拦截器里统一注入 `X-Autoteam-Admin-Id: <currentAdminId>`。

**3. 工作台页面（`web/src/components/Workbench.vue`,新建）**
- 顶部 toolbar 三栏：左侧 `AdminSwitcher.vue`（admin 下拉，含 "+ 添加新管理员"项）/ 中部 `InviteActionButton.vue`（"➕ 邀请加号"主按钮，橙色突出）/ 右侧刷新按钮。
- "邀请加号"按钮点击后弹 modal，复用 TaskPanel 中 `startAddViaInvite` 的执行逻辑（封装为 `InviteFlowModal.vue`），无需切到账号池 tab。
- 下方四个 tab：复用现有 `Dashboard.vue` / `TeamMembers.vue` / `PoolPage.vue` / `SyncPage.vue` 的 template，但改造为"接收 currentAdminId prop / 在 admin 切换时重新拉数据"。
- 账号池 tab 内的 `add-via-invite` 按钮保留（双入口，兼顾顶部快捷操作与 tab 内的并列语义）。
- "添加新管理员"点击后弹 modal，复用 `ConfigPage.vue` 中现有的"管理员登录流程"组件（拆出 `AdminLoginFlow.vue`）。

**4. Sidebar 改造**
- 删除 `team` / `pool` / `sync` 三项。
- 新增 `workbench`（图标 🎛️ 或保留 📊，hint："统一查看与操作当前主号"）。
- 保留 `config` / `tasks` / `logs`。

### 升级迁移（首次启动）
- `admin_registry.bootstrap()`：启动时检测 `data/admins.json` 是否存在；若不存在但旧 `state.json` 存在 → 生成 admin_id → 创建 `data/admins/{admin_id}/` 目录 → 移动 `state.json` / `accounts.json` / `auths/codex-main-*.json` → 写入 `data/admins.json`（active_admin_id = 新 admin_id）→ 备份原文件到 `data/legacy-backup/{timestamp}/`。
- 失败时打印明确错误，不删除原文件。

## Implementation Plan（小步切分）

**PR1：后端 admin 抽象 + 数据隔离 + 自动迁移**（无前端改动）
- 新建 `admin_registry.py`、改造 `admin_state.py` / `accounts.py` / `codex_auth.py` / `auth_storage.py` 接受 admin_id。
- 写迁移函数 + 单元测试（旧数据 → 新结构）。
- API 层暂不改（仍走全局唯一 admin），但内部已经是"通过 admin_id 读数据"的形态。

**PR2：API 多 admin 路由 + 后台巡检改造**
- 新增 `/api/admins/*` 路由。
- 改造 `_auto_check_loop` 轮流跑所有 admin。
- 旧 `/api/admin/*` 路由转发到新实现（保持向后兼容）。
- API 层 Depends 注入 admin_id。

**PR3：前端工作台合并 + admin 切换器 + 邀请加号顶级入口**
- 新建 `Workbench.vue` / `AdminSwitcher.vue` / `InviteActionButton.vue` / `InviteFlowModal.vue` / `store/admins.js`。
- `api.js` 拦截器注入 header。
- 改 `Sidebar.vue` 导航项。
- 改 `App.vue` 路由切换逻辑。
- 拆 `AdminLoginFlow.vue`，工作台和 ConfigPage 共用。
- "邀请加号"双入口：工作台顶部 + 账号池 tab 内现有按钮保留。

**注：PR1 内部按 commit 拆分以便 review**
- commit 1: 新增 `admin_registry.py` 抽象层（不动现有调用）。
- commit 2: 批量改 `admin_state.py` / `accounts.py` / `codex_auth.py` / `auth_storage.py` / `sync_targets.py` 接受 admin_id，旧 API 保留 wrapper。
- commit 3: 新增首次启动自动迁移函数 + 单元测试（旧 → 新结构、回滚、损坏数据）。

**PR4：edge cases + 文档 + 清理**
- TaskHistory / LogViewer 加 admin_id 过滤。
- `docs/architecture.md` / `docs/getting-started.md` 更新多管理员章节。
- 删除 `PoolPage.vue` / `SyncPage.vue`（如不再被引用）。
- 端到端手测 checklist。
