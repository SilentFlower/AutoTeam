# Integration Map — Plus 池 / OAuth / Sub2API / FREE 池 现状

> 由 brainstorm 阶段 Explore agent 摸清,作为 PR2 / PR3 实现的 ground truth。
> 引用代码用 `file:line`,实施时请直接打开 `read` 验证。

---

## 1. Plus 号池 `src/autoteam/plus_accounts.py`

### 数据模型(L180-194)
```json
{
  "email": "<lowercased>",
  "password": "<user_supplied>",
  "auth_file": "data/admins/{admin_id}/auths/codex-{email}-{plan}-{hash}.json | null",
  "status": "active | auth_failed | plan_mismatch | exhausted",
  "plan_type": "plus | other",
  "last_error": "<error_message | null>",
  "created_at": <ts>,
  "last_login_at": <ts>,
  "last_quota": {"primary_pct": ..., "weekly_pct": ...} | null,
  "last_quota_at": <ts>,
  "last_sub2api_synced_at": <ts>
}
```

### 路径 / 常量
- 数据文件:`data/admins/{admin_id}/plus_accounts.json`(per-admin) 或 `data/plus_accounts.json`(单实例 fallback)
- 路径计算:`_plus_accounts_file(admin_id)` (L56)
- admin_id 解析:`_resolve_admin_id(admin_id)` (L40),缺省时回退到 `admin_registry.get_active_admin_id()`
- 状态枚举:`ACTIVE = "active"` / `AUTH_FAILED = "auth_failed"` / `PLAN_MISMATCH = "plan_mismatch"` / `EXHAUSTED = "exhausted"`
- 并发锁:`_login_lock`(threading.Lock)L36-37

### 核心函数(implement 时直接调用)
| 函数 | 行号 | 说明 |
|---|---|---|
| `load_plus(admin_id)` | L79 附近 | 读全部记录 |
| `save_plus(records, admin_id)` | — | 原子写入(temp + rename) |
| `find_plus(records, email)` | — | 大小写不敏感查找 |
| `add_plus(record)` | — | 追加(校验 email 唯一) |
| `update_plus(email, **fields)` | — | 部分字段更新 |
| `delete_plus(email)` | — | 仅删本地 JSON |
| **`import_plus_account(email, password, admin_id=...)`** | **L225** | **本任务关键衔接点** —— 内部完成:OAuth(`_login_codex_with_result(allow_non_team=True)`)→ `save_auth_file` → `add_plus` → best-effort `_sync_plus_to_sub2api_best_effort` |
| `reauth_plus_account(email)` | L243 | 用存的 password 重做 OAuth(失败号恢复入口) |
| `check_plus_quota(emails=None, admin_id=None)` | L338 | 刷新额度(401 自动 refresh token) |
| `delete_plus_account(email, admin_id, cleanup_remote=True)` | L434 | 级联清理 auth_file + sub2api |
| `_sync_plus_to_sub2api_best_effort(admin_id)` | L215 | best-effort 同步包装 |
| `_login_and_store_plus(email, password, admin_id)` | L276 附近 | OAuth + 存 auth_file 的内部组合 |

### Plus 校验
`import_plus_account` 校验 `plan_type` 必须是 `"plus"` 或 `"chatgpt_plus"`(case-insensitive,L199)。**这意味着自动注册产物送进来时,OAuth 拿到的 bundle 必须是 Plus plan**——这正好是注册机付款成功后的预期状态。

---

## 2. OAuth 登录链路

### 顶层入口 `manager._login_codex_with_result()`
文件:`src/autoteam/manager.py:359`
签名 (L359-366):
```python
def _login_codex_with_result(
    email: str,
    password: str,
    *,
    mail_client=None,
    max_attempts: int = 3,
    allow_non_team: bool = False,
) -> dict
```
返回:`{ok: bool, bundle: dict|None, error_type: str|None, error_detail: str|None, retryable: bool}`

### 底层 `codex_auth.login_codex_via_browser()`
文件:`src/autoteam/codex_auth.py:590`
签名 (L590-598):
```python
def login_codex_via_browser(email, password, mail_client=None, *,
                             return_result=False, sms_client=None,
                             allow_non_team=False)
```
- 用 Playwright **自启浏览器** 完成 ChatGPT 登录 + OAuth + 手机验证(HeroSMS, L616-617)
- `return_result=True` 返回 dict;否则 raise
- 返回 bundle 字段:`access_token / refresh_token / id_token / account_id / email / plan_type` (L609)

### `allow_non_team` 标志(L370-371, L605-607)
- 默认 `False`:强制 `plan_type == "team"`(主号路径)
- `True`:允许 personal / plus 等非 team plan 通过
- **本任务必须传 `True`**(Plus 是 personal plan)

### Auth 文件落盘 `codex_auth.save_auth_file()`
文件:`src/autoteam/codex_auth.py:1662`
- 路径:`data/admins/{admin_id}/auths/codex-{email}-{plan_type}-{hash_id}.json` (L1679)
- 写前清理同 email 旧文件 (L1675),原子写入
- 内容:JSON 含 `access_token / refresh_token / id_token / account_id / email / plan_type`

### 不能直接在已登录浏览器 page 上调
`login_codex_via_browser` 自启 Playwright context (L634)。**注册机付款成功后的浏览器实例不能复用**——OAuth 是一次独立的浏览器会话。所以本任务衔接顺序必须是:
```
注册机:浏览器实例 A 完成注册 + 付款 + 设密码 + 取消续订 → 关闭实例 A
↓
import_plus_account(email, password) → 内部启动浏览器实例 B 跑 OAuth
```
这样符合现有约定,不需要任何 ChatGPTBot 改造去暴露 page 对象。

---

## 3. Sub2API 同步 `src/autoteam/sub2api_sync.py`

### 函数
- `sync_plus_to_sub2api(admin_id=None)` — L1100
- `sync_free_to_sub2api(admin_id=None)` — L1087
- 核心 `sync_to_sub2api(source: Literal["pool", "free", "plus"], admin_id=None)`(被上面两个调用)

### 触发点
- 自动:`plus_accounts._sync_plus_to_sub2api_best_effort(admin_id)` 在 `import_plus_account` 成功后调 (L215)
- 手动:`POST /api/plus/sync_sub2api`

### 上传字段
读 `plus_accounts.json` 中 `status=active` 记录 → 加载 `auth_file` 拿 `access_token / account_id` → POST 到 sub2api,带远端元数据 (L63-76):
- `autoteam_managed: true`
- `autoteam_kind: "plus"`(本任务)
- `autoteam_email`、`autoteam_auth_file`(filename)
- `autoteam_source: "plus"`
- `autoteam_last_sync_at`(ts)
- `autoteam_sub2api_group_ids` / `autoteam_sub2api_group_names`

同步后回写 `last_sub2api_synced_at` 到 plus_accounts.json。

---

## 4. FREE 池(参考蓝本)

### 入口 `free_accounts.cmd_generate_free_account(count=1, admin_id=None)`
文件:`src/autoteam/free_accounts.py:307`
流程 (L307-361):
1. 循环 N 次,每次:
   - `mail_client.create_temp_email()` 建临时邮箱
   - `manager.invite_to_team(chatgpt, email)` 邀请入 team
   - `_fetch_invite_link()` 从邮件取邀请链接
   - **Step A**:`invite.login_with_invite()` 浏览器加入
   - **Step B**:`manager._login_codex_with_result(allow_non_team=True)` OAuth
   - 双成功:`remove_from_team()` + F2 验证 + `save_auth_file`
   - A 成功 B 失败:仍 remove + 存为 `status=auth_failed`(可 reauth)
   - A 失败:跳过不存
2. 全部完成后自动调 `sync_free_to_sub2api()` (L357)

### Reauth `reauth_free_account(email)` (L594)
- 读存的 password
- 调 `_login_codex_with_result(allow_non_team=True)`
- 更新 auth_file + status

### 数据隔离原则
- **不动**:`accounts.json`(主号)/ `plus_accounts.json`(plus 池)
- **共享**:`manager.invite_to_team` / `manager.remove_from_team` / `manager._login_codex_with_result` / `codex_auth.save_auth_file` / `mail_provider.get_mail_client`
- **不共享**:`manager._run_invite_login_flow`(它会写 accounts.json,破坏隔离)

### 本任务对标
FREE 池的「`cmd_generate_free_account` → 循环 → 中间 step 失败留 auth_failed → 全部完成后 sync sub2api」就是要复制的模式。差异:
- FREE 用 invite + OAuth,本任务用 注册机(注册+付款) + OAuth
- FREE 失败号也自动 sync,本任务失败号不进 sync(没 auth_file)
- FREE 是顺序但无人工介入,本任务每个号都需要等 WhatsApp OTP(必须有 OTP 队列 + 超时)

---

## 5. HTTP API 层 `src/autoteam/api.py`

### Plus 池端点(已有)
- `POST /api/plus/import` (202 异步) — L3100
- `GET /api/plus/list`
- `POST /api/plus/check_quota` (202)
- `POST /api/plus/sync_sub2api`
- `POST /api/plus/{email}/reauth` (202)

### FREE 池端点(已有,参考)
- `POST /api/free/generate` (202) — L2945,**这就是本任务对标的端点风格**
- `GET /api/free/list`
- `POST /api/free/check_quota` (202)
- `POST /api/free/sync_sub2api`
- `POST /api/free/{email}/reauth` (202)
- `DELETE /api/free/{email}` 级联返回 `{local_record, local_auth_files, sub2api_accounts, cloudmail_deleted}` (L3081-3086)

### 本任务新增(D2 + R3)
- `POST /api/plus/auto_register` (202) `{count}`  → `{job_id}`
- `GET /api/plus/auto_register/{job_id}` → `{status, step, ok, errors[], otp_request_at?, ...}`
- `POST /api/plus/auto_register/{job_id}/feed_otp` `{otp}`
- `POST /api/plus/auto_register/{job_id}/cancel`

---

## 6. 前端 `web/src/`

### 页面
- `web/src/components/PlusPage.vue` — 现有按钮:➕ 导入 / 🔄 全量刷新 / 🔁 同步 sub2api / 📋 刷新列表
- `web/src/components/FreePage.vue` — 现有按钮:➕ 生成免费号(count input) / ✅ 全量刷新 / 🔁 同步 sub2api / 📋 刷新列表
- 侧边栏有这两个入口

### API client
- `web/src/api.js` — 现有 fetch wrapper,本任务加 4 个新方法

### 本任务新增
PlusPage 加 「🤖 自动注册 Plus 号」按钮 + count 输入 + 进度面板(step 进度条 + OTP 输入弹框)。

---

## 7. 关键约束总结

1. **GoPay 单账号** → 全局串行,不能并发跑多个 register 任务。
2. **Playwright 单进程实例** → 同样单 worker。
3. **OAuth 必须独立浏览器会话**,不能复用注册机的 page。
4. **失败号留 `auth_failed`** → 走现有 `reauth_plus_account`,不另写恢复代码。
5. **错误信息中文 + 下一步指引**(backend spec 要求)。
6. **OTP / PIN / token / password 不进 logger**(脱敏红线)。
