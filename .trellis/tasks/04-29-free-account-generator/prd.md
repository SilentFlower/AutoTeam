# 免费号生成器：邀请进 Team 再移出绕过手机号验证

## Goal

新增独立的"免费号"生成与管理流程：通过母号邀请把临时邮箱拉进 Team(走完整 `_run_invite_login_flow` 拿到 codex auth)→ 立即从 Team 移出 → 落入与现有 active/standby 池**完全隔离**的 FREE 池。FREE 池支持手动查额度、同步 sub2api、手动删除(级联清理远端),用作"已注册可登录、不占 Team 席位"的账号资产。

核心价值:利用 invite 流程绕开 OpenAI 主站注册时的手机号强校验,把"加号"动作和"养可用 Codex 池"解耦。

## Decisions Locked

### D1 — 交付物字段(2026-04-29)
**决定**:走完整 invite + Codex OAuth(等同现有 `cmd_add_via_invite`),拿到 `auth_file`(含 codex token + refresh token)。免费号本质上是"完成完整加号流程后立即离场"的账号,因此**不再单独跑一遍 OAuth**。
**含义**:
* 流程 = invite Step A(浏览器加入 Team)+ Step B(Codex OAuth 拿 auth_file)+ 紧跟一步 `remove_from_team` + 落入 FREE 池
* 免费号自带 auth_file,可以查额度、可以同步 sub2api
* 手机号验证仍在 Step B 出现时由 HeroSMS 兜底处理(与现有 invite 加号一致)
* 用户口中的"绕过手机号"= 利用 invite 流程避开 OpenAI 主站注册时的手机号强校验,**而非完全不碰手机号**
* **实现注**:不直接复用 `manager._run_invite_login_flow`(它内部写 `accounts.json`,与 D2 隔离铁律冲突);改为直接调底层组合 `invite.login_with_invite`(Step A)+ `manager._login_codex_with_result`(Step B)+ `codex_auth.save_auth_file`,在 `free_accounts.py` 内自包含等价仿写

### D2 — 隔离粒度:方案 A 完全独立(2026-04-29)
**决定**:存储 / UI / 后台任务三个维度全部独立,只在"无状态函数库"层共享。
**含义**:
* 存储:新建 `data/free_accounts.json`,独立 schema
* UI:侧栏新增 `FREE` 入口 → 独立 `FreePage.vue`,与 PoolPage 完全分离
* 后端:新加 `/api/free/*` 一组端点(`generate / list / check_quota / sync_sub2api / delete`)
* 共享:`codex_auth.check_codex_quota / save_auth_file / refresh_access_token`、`manager.invite_to_team / remove_from_team / _login_codex_with_result`、`account_ops.fetch_team_state`、`mail_provider.get_mail_client`、`invite.login_with_invite`(都是无状态/无副作用工具函数);`sub2api_sync` 在 PR2 抽 `accounts_source` 参数后复用。**不复用** `_run_invite_login_flow`(它写 accounts.json,违隔离铁律)
* **关键约束**:现有 cmd_check / cmd_rotate / cmd_fill / cmd_cleanup / 自动巡检的代码**一行不改**,FREE 池对它们完全不可见

### D3 — sub2api 同步:方案 X 同 group(2026-04-29)
**决定**:FREE 池和现有 active 池**共用同一个** `SUB2API_GROUP` 配置,不引入 `SUB2API_FREE_GROUP`。
**含义**:
* sub2api_sync 的核心同步逻辑抽出 `accounts_source` 参数(默认 active 池,可传 FREE 池),分组绑定逻辑保持不变
* 触发时机:生成成功后自动调一次同步(与 cmd_add 末尾的 `sync_to_configured_targets()` 同惯例)+ FreePage 上加"手动同步"按钮兜底
* 下游消费者无感知:sub2api 侧 FREE 号和正常号混在一组被消费

### D4 — 生命周期:方案 P 纯静态(2026-04-29)
**决定**:FREE 池不起任何后台轮询/巡检线程,完全靠用户手动触发。
**含义**:
* 没有 `_free_auto_check_thread`,没有"自动汰换 exhausted"
* 额度只在用户在 FreePage 点"刷新额度"时才查(单条 / 批量)
* 列表里展示的是上次刷新的快照(`last_quota`、`last_quota_at`)
* exhausted 的 FREE 号继续留着不动(标记成 exhausted 让用户看到,不删数据),删与否完全由用户决定
* FREE 号的"心跳频率"远低于正常池,降低 OpenAI 风控暴露

### D5 — MVP 范围:稳健版(2026-04-29)
**决定**:F1(b) 半成品落库 + F2 末尾二次确认 remove + F3 删除时级联(auth_file + sub2api + cloudmail)。
**E1 批量导出 / E2 手动导入 / E3 账号迁移 / R1 Team 满员自动腾位 / R2 同步状态展示明确放到 Out of Scope**。
**含义**:
* **F1(b)**:Step A 成功(已加入 Team)但 Step B(Codex OAuth)失败时,仍执行 `remove_from_team`,FREE 池写一条 `status=auth_failed, auth_file=null` 的半成品记录,FreePage 上展示并允许用户手动"重试 OAuth"或"删除"
* **F2 末尾二次确认 remove**:`remove_from_team` 调用后,再拉一次 Team 成员列表确认账号确实不在了;不在 → 流程成功;还在 → 重试一次;仍失败 → 写错误日志 + 在 FREE 池条目上打 `team_residue=true` 标记,警示用户手动处理
* **F3 级联删除**:用户在 FreePage 删除时,按现有 `delete_managed_account` 的成熟级联流程清理:本地 auth_file → sub2api 远端账号 → cloudmail 临时邮箱 → 本地 free_accounts.json 条目。每一步独立 try,单步失败不阻塞其他步骤,最终返回 cleanup 摘要给前端

## Requirements

### 后端

* **R1 — 数据层**:新建 `src/autoteam/free_accounts.py`,提供 `load_free / save_free / find_free / add_free / update_free / delete_free` 等 API,操作 `data/free_accounts.json`。**A3**:`add_free` 走 `_normalize_record` 自动补齐 R2 schema 默认值(避免后续读取者命中 KeyError)
* **R2 — 数据 schema**:每条记录包含
  ```json
  {
    "email": "...",
    "password": "...",
    "auth_file": "data/admins/{admin_id}/auths/codex-{email}-{ts}.json | null",
    "status": "active | auth_failed | exhausted",
    "mail_provider": "<取自 mail_client.provider_name,多 provider 通用>",
    "mail_account_id": 123,
    "team_residue": false,
    "created_at": 1234567890,
    "last_quota": { "primary_pct": 0, "weekly_pct": 0, ... } | null,
    "last_quota_at": 1234567890 | null,
    "last_sub2api_synced_at": 1234567890 | null
  }
  ```
  * **status 候选只有 3 个**(active/auth_failed/exhausted),`team_residue` 是**独立布尔字段**,与 status **正交** —— 一个账号可同时 `status=active` + `team_residue=true`(OAuth 成功但移出 Team 失败);合并成 status 会丢"OAuth 成功与否"信息
  * `mail_provider` 取 `getattr(mail_client, "provider_name", "")`,支持 cloudmail / cloudflare 等,与项目现有 mail_provider 模块对齐
* **R3 — 生成命令**:`cmd_generate_free_account(count=1)`,内部串行循环,每轮:
  1. 申请临时邮箱(`mail_provider.get_mail_client` + `create_temp_email`)
  2. 母号发邀请(`manager.invite_to_team`)
  3. **Step A**(浏览器加入 Team):`invite.login_with_invite` —— 在 `free_accounts.py` 内由 `_run_invite_login_step_a` 包装,避免间接走 `_run_invite_login_flow` 触发 accounts.json 写入
  4. **Step B**(Codex OAuth 拿 bundle):`manager._login_codex_with_result`
  5. Step A 失败 → 中止,本轮不落库(临时邮箱按现有惯例处理)
  6. Step A 成功、Step B 失败 → 走 F1(b),`remove_from_team` + F2 后落 `status=auth_failed` 半成品
  7. Step B 成功 → `remove_from_team` + F2 二次确认 → 落 `status=active`(若 `save_auth_file` 落盘失败则降级为 `auth_failed`)
  8. 落库后(PR2 上线后)调一次 `sync_free_to_sub2api()`
* **R4 — 删除命令**:`delete_free_account(email, *, cleanup_remote=False)`,**返回固定 cleanup 摘要 dict**(A2):
  ```python
  {"local_record": bool, "local_auth_files": [str], "sub2api_accounts": [str], "cloudmail_deleted": bool}
  ```
  PR1 仅实现本地清理(auth_file + json 条目);PR3 接入 `cleanup_remote=True` 时补 sub2api 远端 + cloudmail 的级联调用,函数签名不变
* **R5 — 额度刷新**:`check_free_quota(emails=None)`,默认刷新全部 active/exhausted FREE 号,单条/批量都走相同函数,直接复用 `codex_auth.check_codex_quota`。**A1**:401 响应时调 `codex_auth.refresh_access_token` 写回 auth_file 并重试一次,与 `manager._check_and_refresh` 同行为
* **R6 — sub2api 同步参数化**:把现有 `sub2api_sync` 的核心同步逻辑里"枚举 active 账号"那一段抽成 `_collect_managed_targets(source: Literal["pool", "free"])`,新增 `sync_free_to_sub2api()` 入口
* **R7 — 隔离保证**:
  * `cmd_check / cmd_rotate / cmd_fill / cmd_cleanup` 一律不读 `free_accounts.json`
  * 反向:FREE 流程也不写 `accounts.json`
  * sub2api 同步在 active 池侧仍用 `STATUS_ACTIVE` 过滤,不会因 FREE 号在 sub2api 远端存在而误删

### API 端点

* `POST /api/free/generate` (202) — 后台任务,参数 `{count: int}`,走 `_start_task` 异步
* `GET  /api/free/list` — 返回当前 FREE 池全部记录(含 last_quota 快照)
* `POST /api/free/check_quota` — 参数 `{emails: list[str] | null}`,串行刷新额度(后台任务)
* `POST /api/free/sync_sub2api` — 触发一次 FREE 池 → sub2api 同步
* `DELETE /api/free/{email}` — 级联删除

### 前端(FreePage.vue)

* 列表展示:email / status / 额度快照(primary_pct / weekly_pct + last_quota_at) / created_at / 操作按钮
* 顶部按钮:**生成免费号**(弹窗输入数量)、**全量刷新额度**、**同步到 sub2api**
* 行操作:**刷新额度**(单个) / **复制 email + password** / **删除**(级联)
* 半成品(status=auth_failed)行高亮 + 提示"OAuth 失败,可手动重试或删除"
* `team_residue=true` 行红色警告 + tooltip"账号未成功移出 Team,请手动检查"
* 侧栏新增 `FREE` 入口(在 PoolPage 下方)

## Acceptance Criteria

* [ ] 调用 `POST /api/free/generate {count: 1}` 后,FREE 池新增一条记录,且该邮箱**不在** OpenAI Team 成员列表里
* [ ] FreePage 列表展示新生成的免费号,可点"刷新额度"看到 primary_pct / weekly_pct
* [ ] 点击 FreePage "同步到 sub2api" 后,sub2api 远端能查到该免费号(group_id 与现有 active 池一致)
* [ ] 现有 PoolPage / Dashboard 列表**不显示** FREE 池任何账号
* [ ] 现有 cmd_check / cmd_rotate / cmd_fill / cmd_cleanup / 自动巡检**不会**触碰 FREE 池(单元测试覆盖)
* [ ] 母号 Team 在生成成功后,席位数与开始前一致(净影响 = 0)
* [ ] count=N>1 时按顺序生成,中途单条失败(F1b)不影响其他;部分成功的 N-1 条仍可用
* [ ] `team_residue=true` 的账号在 UI 上显著警示;不会被现有 cmd_cleanup 误清理(因为它不在 accounts.json 里)
* [ ] 删除 FREE 号后:本地 auth_file 已删 + sub2api 远端账号已删 + cloudmail 邮箱已删 + free_accounts.json 条目已移除

## Definition of Done

* 新增模块单元测试覆盖:生成成功、F1(b) 半成品、F2 二次确认 remove 失败、F3 级联删除单步失败
* 现有 active 池的回归测试通过(cmd_check / cmd_rotate / cmd_fill / cmd_cleanup / 自动巡检在引入 FREE 池后行为不变)
* 端到端手测:生成 1 个 → 刷新额度 → 同步 sub2api → 删除 → 验证 sub2api 远端 + cloudmail 都干净
* 日志全程使用 `[免费号]` 前缀,与 `[邀请加号]`(active 池 invite 流程)区分
* `docs/architecture.md` 增加 FREE 池章节,说明数据流向与隔离边界

## Out of Scope (explicit)

* **E1 批量导出**(JSON / CSV),v2 处理
* **E2 手动导入**已有账号到 FREE 池,v2 处理
* **E3 账号在 active 池 / FREE 池之间迁移**,v2 处理
* **R1 母号 Team 满员时的自动腾位**:首版直接报错"Team 已满,请先 cleanup",由用户主动操作
* **R2 sub2api 同步状态展示**:首版只在 toast 显示"成功/失败",不持久化历史
* FREE 池的自动巡检 / 自动汰换(D4 已锁纯静态)
* FREE 池独立的 sub2api group 配置(D3 已锁同 group)
* `auth_failed` 半成品的"自动重试 OAuth":首版只允许用户手动触发,不开后台

## Technical Approach

### 整体结构

```
新增:
  src/autoteam/free_accounts.py        ← 数据层 + cmd_generate_free + delete + check_quota
  data/free_accounts.json              ← 持久化文件
  web/src/components/FreePage.vue      ← 前端页面
  api 端点: /api/free/*                ← api.py 内新增

修改(向后兼容,无功能变更):
  src/autoteam/sub2api_sync.py         ← 抽参数 accounts_source
  src/autoteam/manager.py              ← _run_invite_login_flow 的返回值若需要更细粒度,可微调
  web/src/api.js                       ← 加 free.* 方法
  web/src/App.vue + Sidebar.vue        ← 加路由 / 侧栏入口
```

### 关键复用点

| 现有能力 | FREE 流程怎么用 |
|---|---|
| ~~`manager._run_invite_login_flow`~~ | **不复用**:它写 accounts.json,违 D2 隔离;改用底层组合下面三条 |
| `invite.login_with_invite` | Step A:浏览器开邀请链接 → 邮箱+密码+OTP → 加入 Team |
| `manager._login_codex_with_result` | Step B:Codex OAuth,返回 `{ok, bundle}` |
| `codex_auth.save_auth_file(bundle, *, admin_id=)` | bundle 拿到后单独写 auth_file,**不**触发 `update_account` |
| `manager.invite_to_team` | 母号发邀请,单条调用 |
| `manager.remove_from_team` | Step B 完成后立即 remove,F2 二次确认中可能再调一次 |
| `account_ops.fetch_team_state` | F2 二次确认拉 Team 成员列表 |
| `account_ops.delete_managed_account` | 抽出级联清理的核心步骤,PR3 在 `delete_free_account` 中复用 |
| `codex_auth.check_codex_quota / refresh_access_token` | 额度查询 + 401 自动刷 token |
| `sub2api_sync._sync_*`(PR2) | 抽 `accounts_source` 参数,新加 `sync_free_to_sub2api()` 入口 |
| `mail_provider.get_mail_client` | 直接调申请临时邮箱 + `wait_for_email` + `extract_invite_link` |
| `api._start_task`(PR3) | 后台任务调度直接复用 |

### 数据隔离的执行方式

1. 现有 `accounts.load_accounts()` 不动,FREE 流程完全不调它
2. 新模块 `free_accounts.load_free()` 独立读 `data/free_accounts.json`
3. sub2api_sync 的去重逻辑(`_dedupe_managed_accounts`)需要看到 active + FREE 两边的并集,避免互删 → **关键**:同步时用 union 集合做"应保留",而不是单边的 active

### F2 二次确认 remove 的实现

```python
def _verify_team_removal(chatgpt_api, email, *, retries=2):
    for attempt in range(retries):
        members, _ = fetch_team_state(chatgpt_api)
        if email.lower() not in {m.get("email", "").lower() for m in members}:
            return True
        if attempt < retries - 1:
            time.sleep(2)
            remove_from_team(chatgpt_api, email)  # 重试一次
    return False
```

返回 False → FREE 池条目打 `team_residue=true`,前端红色警告

## Decision (ADR-lite)

**Context**:用户希望批量获得"已注册可登录、不占 Team 席位"的 OpenAI 账号,用于绕开 OpenAI 主站注册时的手机号强校验。现有 `cmd_add_via_invite` 已经实现"邀请加入 Team",但加完后账号会留在 Team 里参与正常 active 池流程。

**Decision**:在 `free_accounts.py` 内自包含一条"申请邮箱 → 邀请 → invite Step A → Codex OAuth Step B → remove + F2 二次确认 → 落入独立池"的流程(**不**间接调 `_run_invite_login_flow`,避免它写 accounts.json 破坏 D2 隔离);所有数据/UI/任务三层完全隔离,sub2api 同步参数化复用,生命周期纯静态由用户管理。

**Consequences**:
* 优点:FREE 池对现有 cmd_* 完全不可见,零回归风险;sub2api 现有 group 配置零改动;UI 上"FREE 列表"和"账号池"概念清晰;批量生成接现有 invite 流程的成熟兜底机制(HeroSMS / 邮箱重试)
* 风险:`team_residue` 极端情况下需用户手动介入;`auth_failed` 半成品长期累积没人清理时会膨胀(v2 加自动汰换或用户清理工具)
* 反向兼容:现有 `accounts.json` schema 完全不变,旧数据零迁移

## Implementation Plan (small PRs)

* **PR1 — 后端核心流程**:`free_accounts.py` 数据层 + `cmd_generate_free_account` + F1(b) 半成品 + F2 二次确认 + 单元测试
* **PR2 — sub2api 同步参数化**:`sub2api_sync` 抽 `accounts_source` + `sync_free_to_sub2api()` + 回归测试(active 池行为不变)
* **PR3 — API + 前端 FreePage**:`/api/free/*` 端点 + `FreePage.vue` + 侧栏入口 + F3 级联删除 + E2E 手测
* **PR4(可选)**:文档更新 + `docs/architecture.md` 加 FREE 池章节

## Technical Notes

### 关键文件

* `src/autoteam/manager.py:2431` `_run_invite_login_flow` — **不直接复用**(写 accounts.json),仅作参考结构对照
* `src/autoteam/manager.py:1091` `remove_from_team` — 直接复用
* `src/autoteam/manager.py:1138` `invite_to_team` — 直接复用
* `src/autoteam/manager.py:359` `_login_codex_with_result` — Step B 直接复用
* `src/autoteam/manager.py:2670` `cmd_add_via_invite` — 参考结构
* `src/autoteam/account_ops.py:84` `delete_managed_account` — F3 级联删除的参考(PR3 接入)
* `src/autoteam/account_ops.py:` `fetch_team_state` — F2 二次确认直接复用
* `src/autoteam/sub2api_sync.py:723` 同步主流程 — 需要参数化的位置(PR2)
* `src/autoteam/codex_auth.py:1622` `save_auth_file(bundle, *, admin_id=)` — 单独写 auth_file 不触 accounts.json
* `src/autoteam/codex_auth.py:1763` `check_codex_quota` — 直接复用
* `src/autoteam/codex_auth.py:1821` `refresh_access_token` — 401 自动刷 token
* `src/autoteam/invite.py` `login_with_invite` — Step A 浏览器登录直接复用
* `src/autoteam/api.py:_start_task` — 后台任务调度(PR3)

### 关键约束

* 母号 Team 席位有限,生成期间临时占席,生成完立刻 remove
* 临时邮箱 provider 频控:批量生成必须串行
* OpenAI 风控:同 IP 短时大量 invite + remove 可能触发反作弊;首版按现有 cmd_add_via_invite 的节奏(默认间隔)

## 变更记录

### 变更 1: PR1 实现完成后的事后回补 (2026-04-29)

- **变更类型**: 实现调整(代码已落,PRD 同步)
- **触发**: trellis-implement subagent 完成 PR1 后,trellis-check subagent 又自修了 4 处小问题。多处 PRD 描述与实际实现存在偏差,需回补。
- **变更内容**:
  - **M1 — 流程不复用 `_run_invite_login_flow`**:PRD 原 D1 / R3 / Decision / 关键复用点表多处写"复用 `_run_invite_login_flow`",但该函数内部强依赖 `update_account / save_auth_file` 写 `accounts.json`,与 D2 锁定的"FREE 流程不写 accounts.json"硬冲突。subagent 改为在 `free_accounts.py` 内自包含等价仿写:`invite.login_with_invite`(Step A)+ `_login_codex_with_result`(Step B)+ `save_auth_file`。本次同步把 PRD 全文统一为新写法,并加"不复用"的明确说明。
  - **M2 — status 候选 4 → 3,team_residue 改成独立布尔字段**:R2 schema 原写 `"status": "active | auth_failed | exhausted | team_residue"` 4 候选,但实现选择"team_residue 与 status 正交"设计 —— 一个账号可同时 `status=active` + `team_residue=true`(OAuth 成功但移出失败),合并成 status 会丢信息。trellis-check 自修时已删除死常量 `FREE_STATUS_TEAM_RESIDUE`,本次同步把 PRD schema 描述对齐到 3 候选 + 独立布尔字段。
  - **M3 — `mail_provider` 字段从写死改为通用**:R2 schema 原写 `"mail_provider": "cloudmail"`,实际实现 `getattr(mail_client, "provider_name", "")` 支持多 provider。同步 PRD 描述。
  - **A1 — `check_free_quota` 内置 401 token 自动刷新**:PRD R5 没要求,但实现里参考 `manager._check_and_refresh` 加了"401 → `refresh_access_token` → 重试一次"的逻辑。补到 R5。
  - **A2 — `delete_free_account` cleanup 摘要字段固定**:PRD F3 说"返回 cleanup 摘要",但没明确字段。实现固定为 `{local_record, local_auth_files, sub2api_accounts, cloudmail_deleted}`,签名预留 `cleanup_remote=False`。补到 R4。
  - **A3 — `add_free` 自动补 schema 默认值**:实现 `_normalize_record` 入库时按 R2 补默认,避免 KeyError。补到 R1。
- **原因**:
  - M1 是最大的偏离,但 subagent 选择"守隔离铁律"而非"守 PRD 字面"是合理判断 —— PRD 自身的 D2 与 R3 内部冲突,subagent 选 D2 优先正确。
  - M2/M3 是 schema 描述粒度问题,实现更通用,PRD 应跟齐。
  - A1/A2/A3 是合理增强或工程细节,实现已落,PRD 文档化避免下次有人问"这是不是越界了"。
- **PRD 同步方式**:事后回补(代码先行)
- **影响范围**:仅 PR1 已落代码,PR2/PR3 计划完全不变;Implementation Plan 表保持不动
- **同步后状态**:PRD 与 PR1 实际产出 100% 一致,可作为 PR2/PR3 的真实来源
