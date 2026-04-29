# FREE 号 remove 后重授权 codex 再推 sub2api(放宽 plan 检查)

> 状态:Brainstorm 中,需求收敛尚未结束。Q&A 完成后再确认 Acceptance Criteria 与 Implementation Plan。

## Goal

修复 FREE 号生成成功落库后、推 sub2api / 后续使用时 OpenAI 返回 `401 token_invalidated` 的问题。
根因:Step B `_login_codex_with_result` 在账号还在 Team workspace 时拿到的 codex bundle,在母号 `remove_from_team` 之后立即被 invalidate;落库的 `auth_file` 因此一上线就失效。

修复方向(用户已确认):

- remove 之后再走一遍 OAuth 拿新 bundle,用新 bundle 写 `auth_file`;
- 因为账号已不在 team,新 bundle `plan_type` 不再是 `team`,需放宽顶层 hard fail;
- 同时为「已落库的 FREE 号」补充手动重授权能力(FreePage 行操作新增「重新登录」按钮),覆盖 token 二次失效/过期场景。

## What I already know

### 仓库现状

- 生成主流程:`src/autoteam/free_accounts.py:_generate_one_free_account` (357-474)
- Codex OAuth 二次封装:`src/autoteam/manager.py:_login_codex_with_result` (359-426),内部 `_reject_non_team` 强制 `plan_type=="team"` 才放行
- 顶层 OAuth 实现:`src/autoteam/codex_auth.py:login_codex_via_browser` (590),1184 行有 `if plan_type != "team":` hard fail
- Team 移出:`src/autoteam/manager.py:remove_from_team`,二次确认 `_verify_team_removal`
- 临时邮箱删除:`_safe_delete_temp_email` 仅在失败路径调用;成功路径**不删邮箱**
- sub2api 同步入口:`src/autoteam/sub2api_sync.py:sync_free_to_sub2api` (1032)
- FREE 状态枚举:`FREE_STATUS_ACTIVE` / `FREE_STATUS_AUTH_FAILED` / `FREE_STATUS_EXHAUSTED` (`free_accounts.py:75-79`)
- 测试 fixture:`tests/unit/test_free_accounts.py:patch_generation_deps` (260),已 mock 全套外部依赖

### FreePage 现有行操作风格

`web/src/components/FreePage.vue:122-147`,每行三按钮:

- 刷新额度(蓝色 `bg-blue-600/10`)
- 复制账号密码(青色 `bg-cyan-600/10`)
- 删除(玫红 `bg-rose-600/10`)
- 新按钮统一用 `px-3 py-1.5 rounded-lg text-xs font-medium border` 样式
- 现有 `actionEmail` / `actionType` 状态机驱动 loading 文案

### 后端 API 端点风格

`src/autoteam/api.py:2912+`:

- `POST /api/free/generate`(202 + Task)
- `POST /api/free/check_quota`(202 + Task)
- `POST /api/free/sync_sub2api`
- `DELETE /api/free/{email}`
- 「重新登录」走 `POST /api/free/{email}/reauth` 比较合适(异步任务,202 + Task)

### 已知伏笔

- `FreePage.vue:427` 已有注释 `'OAuth 失败,可手动删除(v2 支持手动重试)'` — 设计稿一开始就预留了这个能力,本任务正是补这块

## Assumptions (待 Q&A 验证)

1. 重授权后的 personal-plan codex token 仍能被 sub2api 接受 — 用户已自行判断,暂作为前提
2. 已 remove 的账号在 ChatGPT 系统里仍是个有效个人账号,可正常登录(login_codex_via_browser 会要求 OTP)
3. 已落库的 FREE 号在「重新登录」时,临时邮箱可能已被外部删除/过期,需要重新申请新邮箱
4. 本任务 P2,父任务 04-29-free-account-generator 仍 in_progress

## Open Questions (Blocking / Preference)

- [x] **Q1**: 「重新登录」按钮是 MVP 必须 — **是**
- [x] **Q1b**: MVP 含并发锁保护 — **是**
- [x] **Q2**: 重授权失败时落库状态 — **A. 沿用 FREE_STATUS_AUTH_FAILED**
- [x] **Q3**: 第一次 OAuth(Step B) bundle — **A. 丢弃**
- [x] **Q4**: 「重新登录」按钮入口 — **A. 行内第四个按钮(总是显示)**
- [x] **Q5**: reauth 时临时邮箱 — **复用 record.mail_account_id**(单一合理路径,ChatGPT OTP 发到原账号邮箱)
- [ ] **Q6**: quota 401 提示集成快捷入口 — **未选**,不进 MVP
- [ ] **Q7**: 后台自动检测 token 失效自动 reauth — **未选**,不进 MVP

## Requirements (locked)

- **R1**: 生成主流程 remove 之后再调一次 OAuth,用第二次 bundle 写 `auth_file`(替换原 team bundle);Step B bundle 丢弃,只作为「Step A 真的进 Team」的验证副产物
- **R2**: 放宽 `plan_type=="team"` 校验 — `login_codex_via_browser` / `_login_codex_with_result` 加 `allow_non_team: bool = False` 入参,默认 `False` 保持向后兼容
- **R3**: FreePage 行操作新增「重新登录」按钮(总是显示,行内第四个),触发 `POST /api/free/{email}/reauth`(202 + Task,与 `post_free_generate` 一致)
- **R4**: 后端 reauth 并发锁 — 同 email 处理中再次触发返回 `409 Conflict`
- **R5**: reauth 失败 → 落库 `status=FREE_STATUS_AUTH_FAILED`,跟 Step B 失败语义压平
- **R6**: reauth 复用 `record.mail_account_id` 收 OTP;邮箱已失效 → 走入 R5 失败兜底

## Acceptance Criteria

- [ ] `cmd_generate_free_account` 后,落库 `auth_file` 推 sub2api 不再出现 `token_invalidated` 401(端到端,需用户实测)
- [ ] `login_codex_via_browser(email, password, allow_non_team=False)` 默认行为不变 — 单测验证 plan_type != "team" 仍然 fail
- [ ] `login_codex_via_browser(..., allow_non_team=True)` + personal plan bundle → 返回成功
- [ ] `_generate_one_free_account` 全成功路径下 `_login_codex_with_result` 被调两次,第二次带 `allow_non_team=True`
- [ ] Step C reauth 失败 → 落库 `status=auth_failed`,`auth_file=None`
- [ ] `POST /api/free/{email}/reauth` 同 email 并发触发返回 `409`
- [ ] FreePage「重新登录」按钮跑通后,记录 `last_quota_at` / `auth_file` 更新,UI loading 状态机与现有按钮一致
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pytest` 全绿

## Definition of Done

- 单测、ruff、pytest 全绿
- 与 backend spec 一致(目录结构、JSON 模式、错误处理、日志、质量)
- PRD 同步本次决策到 ADR-lite
- 可回滚:`allow_non_team` 默认 `False`,前端按钮可一键隐藏
- 用户实测一次 free 号生成 → 推 sub2api 不再 401

## Out of Scope (explicit)

- 主号(非 FREE)codex 重授权 — manager 路径不动
- sub2api 端 token 主动刷新机制
- 后台自动检测 token 失效自动 reauth(下个任务)
- 「刷新额度」失败提示集成「立即重新登录」快捷入口(下个任务)
- 批量勾选多条 reauth(下个任务)

## Technical Approach

### 改动文件

| 文件 | 用途 |
|------|------|
| `src/autoteam/codex_auth.py` | `login_codex_via_browser` 加 `allow_non_team: bool = False`,1184 行 plan check 改为 `if plan_type != "team" and not allow_non_team:` |
| `src/autoteam/manager.py` | `_login_codex_with_result` 加 `allow_non_team` 透传;`_reject_non_team` 起始 `if allow_non_team: return None` |
| `src/autoteam/free_accounts.py` | `_generate_one_free_account` 加 Step C reauth 调用;新增 `reauth_free_account(email)` 入口(并发锁通过模块级 `set` 实现) |
| `src/autoteam/api.py` | 新增 `POST /api/free/{email}/reauth`(202 + Task,409 并发) |
| `web/src/api.js` (或对应文件) | 客户端 `free.reauth(email)` |
| `web/src/components/FreePage.vue` | 行操作第四个按钮「重新登录」,actionType='reauth' |
| `tests/unit/test_free_accounts.py` | 扩展 `patch_generation_deps` 让 `_login_codex_with_result` 可分两次返回不同 bundle;新增 reauth 路径覆盖 |

### 关键设计点

- **并发锁**:`free_accounts._reauth_in_progress: set[str]` + `threading.Lock()`,API 层入锁前判断,409 兜底
- **reauth 入口**:`reauth_free_account(email, admin_id=None) -> dict`,内部:加锁 → 读 record → 用 `mail_account_id` 起 mail_client → 起 chatgpt_factory(用于结尾 stop) → `_login_codex_with_result(email, password, mail_client, allow_non_team=True)` → 成功覆盖 auth_file/状态/`last_quota_at` 清空 → 失败转 auth_failed → 解锁 → 触发 sync_free_to_sub2api(单条)
- **状态压平**:Step B 失败 / Step C 失败 / reauth 入口失败 都走 `auth_failed`
- **Step B bundle 丢弃**:`oauth_ok` 仅取决于 Step C 结果

## Decision (ADR-lite)

**Context**:FREE 号生成主流程 Step B 拿到的 codex bundle 在母号 remove 后被 invalidate,落库 auth_file 推 sub2api 必然 401。

**Decision**:Step B 仅作 Team 准入验证,bundle 丢弃;remove 后增加 Step C 二次 OAuth 拿 personal bundle 落库;同时为已落库号补充手动 reauth 入口。`allow_non_team` 入参控制 plan check,默认 `False` 保持主号路径不变。

**Consequences**:
- 每次 FREE 号生成多一次 Playwright OAuth 开销(~30-60s)
- personal bundle 推 sub2api 兼容性需用户实测确认
- 状态机不增加新枚举,认知成本低
- 「重新登录」按钮总是显示 — 用户可救已落库的失效号
- 后续若需要批量/自动 reauth,在本次基础上扩展即可

## Implementation Plan (small PRs)

- **PR1**: `codex_auth.py` + `manager.py` 加 `allow_non_team` 入参 + 单测覆盖默认/放宽两条路径
- **PR2**: `free_accounts.py` 改 `_generate_one_free_account` 流程 + 新增 `reauth_free_account` 入口 + 测试扩展
- **PR3**: `api.py` 新增 `POST /api/free/{email}/reauth` + 并发锁 + 测试
- **PR4**: FreePage 第四按钮 + 客户端 reauth 调用 + 走通 happy path

> 实际可能合并成 1-2 个 PR,根据用户偏好定。
