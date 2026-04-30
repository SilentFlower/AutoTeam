# fix: sync_main_codex_to_sub2api 跨主号互删 + 漏代理

## Goal

修复 `src/autoteam/sub2api_sync.py` 中两个独立 bug：
1. `sync_main_codex_to_sub2api()` 把 sub2api 远端**别的 admin** 的主号当"旧主号"误删（多 admin 共用同一 sub2api 实例的场景下互删）。
2. 主号同步全程不带代理（创建/更新都没绑 `SUB2API_PROXY`，与 pool 池行为不一致）。

顺手把 pool 池**更新分支也不刷新代理**这一同类问题一并对齐。

## Decisions Locked

### D1 — 代理覆盖语义:始终用配置覆盖(2026-04-29)
**决定**:`_update_account` 增加 `proxy_id` 参数后,**调用方在每次更新时都解析 `SUB2API_PROXY` 并传入**;`_resolve_proxy_id(token)` 返回非 None 时写入 payload(覆盖远端原值)。
**含义**:
* 配置代理改了下次同步立即生效,不需要先去 sub2api 后台手动清空
* `_update_account` 内部沿用 `_create_account` 现有的 `if proxy_id is not None: payload["proxy_id"] = int(proxy_id)` 写法,语义统一
* `SUB2API_PROXY` 配置为空时(`_resolve_proxy_id` 返回 None),payload 不写 `proxy_id` 字段 → 远端保留原值。即"配置非空才显式覆盖,配置为空不动"。**不主动清空远端 proxy**,要清空就把 sub2api 后台手动设回无代理(避免 null payload 在 sub2api API 的不确定行为)
* **副作用**:用户在 sub2api 后台手动改的代理在下次 sync 时会被覆盖回 `SUB2API_PROXY` 配置值(已与用户确认接受此权衡)

### D2 — 顺手对齐 pool 池更新分支(2026-04-29)
**决定**:本任务一并修复 pool 池(`sync_to_sub2api`)更新分支不刷新代理的同类问题,行为与 main 完全一致。
**含义**:
* `sync_to_sub2api` 的更新分支(`src/autoteam/sub2api_sync.py:944-965`)也调用 `_resolve_proxy_id(token)` 并传给 `_update_account`
* `_resolve_proxy_id` 在 pool 池循环里**仍然 lazy 一次**(沿用现有 `proxy_id_resolved` flag),避免每条账号都打一次 group 列表 API
* 测试覆盖增加 pool 池更新代理的 AC

### D3 — 不写数据修复脚本(2026-04-29)
**决定**:已经被错删的主号靠 admin 手动重新点"同步主号"恢复,不写一次性迁移脚本。
**含义**:
* 任务交付物只含代码修复 + 测试,无运维脚本
* 修复 PR merge 后由各 admin 重新登录主号(或在 UI 点同步)即可重建远端记录

## Requirements

### 后端

* **R1 — 移除跨邮箱误删循环**:删除 `sync_main_codex_to_sub2api` 内 1081-1088 行的 `for item in existing_by_email.values()` 循环。同邮箱重复由上面 1037 行的 `_dedupe_managed_accounts` 兜底,跨邮箱不应删。
* **R2 — `_update_account` 增加 `proxy_id` 参数**:函数签名增加 `proxy_id: int | None = None`;函数体在 `proxy_id is not None` 时写入 `payload["proxy_id"] = int(proxy_id)`(语义同 `_create_account`)。docstring 注明语义。
* **R3 — 主号同步两个分支都补 proxy**:
  * 创建分支:调 `proxy_id = _resolve_proxy_id(token)`,传给 `_create_account`
  * 更新分支:同样调 `_resolve_proxy_id(token)`,传给 `_update_account`
* **R4 — pool 池更新分支补 proxy**:`sync_to_sub2api` 的更新分支调 `_resolve_proxy_id`(沿用现有 `proxy_id_resolved` lazy flag,但 lazy 触发条件从"仅创建分支"改为"创建或更新分支首次需要"),传给 `_update_account`。

### 测试

* **R5 — 回归测试**:在 `tests/unit/test_sub2api_sync.py` 增加用例:
  * `test_sync_main_codex_does_not_delete_other_admins_main` —— 远端有 `b@x.com` (kind=main),sync `a@x.com` 后 `b@x.com` 不被删
  * `test_sync_main_codex_creates_with_proxy_id` —— 远端无主号,配置 `SUB2API_PROXY`,sync 后 `_create_account` 收到 proxy_id
  * `test_sync_main_codex_updates_with_proxy_id` —— 远端已有同邮箱主号,配置 `SUB2API_PROXY`,sync 后 `_update_account` payload 含 proxy_id
  * `test_sync_main_codex_dedup_still_works` —— 远端两条同邮箱主号,sync 后保留 id 较大者(回归保护)
  * `test_sync_to_sub2api_updates_pool_proxy_id` —— pool 池已存在账号,sync 后 `_update_account` 收到 proxy_id

## Acceptance Criteria

* [ ] AC1:多 admin 主号互不误删 —— 远端 b@x.com (kind=main) 已存在,`sync_main_codex_to_sub2api(a 的 auth_file)` 后远端仍有 b@x.com
* [ ] AC2:主号创建带代理 —— 配置 `SUB2API_PROXY=test-proxy`、远端无主号,sync 后远端主号 `proxy_id` = 配置代理 id
* [ ] AC3:主号更新覆盖代理 —— 远端已有同邮箱主号(无 proxy 或有别的 proxy),sync 后 `proxy_id` 被刷新成配置值
* [ ] AC4:同邮箱重复仍被去重 —— 远端两条同邮箱主号,sync 后保留 id 最大那条
* [ ] AC5:pool 池更新覆盖代理 —— 远端已有 pool 账号,sync 后 `proxy_id` 被刷新
* [ ] AC6:`SUB2API_PROXY` 为空时不动远端 proxy —— 远端已有 proxy_id=5,配置为空,sync 后 proxy_id 仍是 5
* [ ] AC7:全部现有测试不退化(`tests/unit/test_sub2api_sync.py` 全绿)

## Definition of Done

* `tests/unit/test_sub2api_sync.py` 增加 5+ 条回归用例,全绿
* lint / typecheck 通过(项目现有规则)
* `_update_account` docstring 更新,注明 `proxy_id` 参数语义
* 不写新 spec(行为是 PR2 同步契约的补漏,非新契约)
* 本次任务**不**修改 PRD `04-29-free-account-generator` 已沉淀的两条 spec(API 响应脱敏 / 完全隔离资产池)

## Out of Scope

* 数据修复脚本 —— D3 已锁,不做
* `_dedupe_managed_accounts` 改造(按 admin_id 去重而非 email)—— 当前 dedup 是按 email 的,多 admin 用不同邮箱主号已避开冲突,改 dedup 范围超出本 bug
* sub2api 后台手动改的 proxy 受保护策略 —— D1 已确认接受被覆盖,不做
* `SUB2API_PROXY` 为空时显式清空远端 proxy —— D1 已锁不主动清空

## Technical Approach

### 涉及文件

* `src/autoteam/sub2api_sync.py` —— 主体修改
* `tests/unit/test_sub2api_sync.py` —— 增加回归测试

### 改动点

1. **`_update_account` 签名扩展**(`src/autoteam/sub2api_sync.py:682-708`):
   ```python
   def _update_account(
       token, account, *, credentials, extra,
       name=None, status=None, group_ids=None,
       account_settings=None,
       proxy_id: int | None = None,  # 新增
   ):
       payload = {...}
       ...
       if proxy_id is not None:
           payload["proxy_id"] = int(proxy_id)
       ...
   ```

2. **主号创建分支补 proxy**(`src/autoteam/sub2api_sync.py:1067-1078`):
   ```python
   else:
       _apply_managed_credentials_settings(desired_credentials)
       _apply_managed_extra_settings(desired_extra)
       proxy_id = _resolve_proxy_id(token)  # 新增
       created = _create_account(
           token,
           ...
           proxy_id=proxy_id,  # 新增
       )
   ```

3. **主号更新分支补 proxy**(`src/autoteam/sub2api_sync.py:1046-1065`):
   ```python
   if current:
       ...
       proxy_id = _resolve_proxy_id(token)  # 新增
       _update_account(
           ...
           account_settings=account_settings,
           proxy_id=proxy_id,  # 新增
       )
   ```

4. **删除 main 跨邮箱误删循环**(`src/autoteam/sub2api_sync.py:1081-1088`):整段移除,留 dedup 兜底。

5. **pool 池更新分支补 proxy**(`src/autoteam/sub2api_sync.py:944-965`):
   ```python
   if existing:
       ...
       if not proxy_id_resolved:
           proxy_id = _resolve_proxy_id(token)
           proxy_id_resolved = True
       _update_account(
           ...
           account_settings=account_settings,
           proxy_id=proxy_id,
       )
       continue
   ```
   (把 `proxy_id_resolved` 的 lazy 解析点上移到更新/创建分支的入口,确保两处都能拿到。)

### 关键参考点

* pool 池正确范本(创建带 proxy):`src/autoteam/sub2api_sync.py:967-982`
* dedup 实现(同邮箱重复保留 id 最大):`src/autoteam/sub2api_sync.py:286-308`
* 主号 auth 文件存储(每个 admin 独立 auth_dir):`src/autoteam/codex_auth.py:1644-1675`
