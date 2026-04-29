# Codebase 改造点索引

> 给 implement 子代理用的"哪里要改、为什么要改"导航。所有路径相对仓库根。

## 一、后端单实例假设的具体位置

### 1.1 存储路径常量（最先要改）

| 位置 | 当前 | 改造方向 |
|------|------|---------|
| `src/autoteam/admin_state.py:23` | `STATE_FILE = PROJECT_ROOT / "state.json"` | 改为函数 `_state_file(admin_id)` 返回 `data/admins/{admin_id}/state.json` |
| `src/autoteam/accounts.py:12` | `ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"` | 改为函数 `_accounts_file(admin_id)` |
| `src/autoteam/codex_auth.py` 中 `auths/codex-main-{account_id}.json` 拼接处（`grep -n "codex-main"`） | 全局唯一目录 `auths/` | 改为 `data/admins/{admin_id}/auths/...` |
| `src/autoteam/auth_storage.py` | 全局 `auths/` 目录 | 路径函数加 admin_id 参数 |
| `src/autoteam/sync_targets.py` | Sub2API 同步目标存储位置（待二次确认是单文件还是 env） | 按 admin 拆分 |

### 1.2 主号识别函数（业务逻辑耦合点）

- `src/autoteam/accounts.py:26-27` `_is_main_account_email(email)` 直接调 `get_admin_email()` 单例 → 改为 `_is_main_account_email(email, admin_id)`。
- `src/autoteam/accounts.py:105` `get_active_accounts()` 过滤主号时同样调 `_is_main_account_email` → 同步加 admin_id 参数。
- `src/autoteam/api.py:2089` `post_team_member_remove()` 调 `_is_main_account_email` 拦截移出主号 → 改为按"当前 admin"判断。
- `src/autoteam/account_ops.py:48` 附近 `get_chatgpt_account_id()` —— 全局获取主号 account_id → 改为 `get_chatgpt_account_id(admin_id)`。

### 1.3 全局运行时变量（在 api.py 模块级）

| 位置 | 变量 | 改造方式 |
|------|------|---------|
| `api.py:923` | `_playwright_lock = threading.Lock()` | **保持全局**（按 PRD 决策"切换激活+串行"，所有 admin 共享一把锁） |
| `api.py:925` | `_admin_login_api = None` | 改为 `dict[admin_id, AdminLoginAPI]`，按目标 admin_id 索引 |
| `api.py:926` | `_admin_login_step: str \| None` | 改为 `dict[admin_id, str]` |
| `api.py:998` | `_pw_executor = _PlaywrightExecutor()` | **保持全局**（执行器单例 OK，每次 submit 时显式传 admin_id 上下文） |
| `api.py:2290` | `_auto_check_config = {...}` | 改为按 admin 取配置（或保持全局配置，循环时按 admin 切换数据源） |

### 1.4 后台巡检循环（PR2 重点）

- `api.py:2422` `_auto_check_loop` 单 admin 流程：load_accounts → check_codex_quota → 自动轮转。
- `api.py:2840` `threading.Thread(target=_auto_check_loop, daemon=True)` 启动一个线程。
- 改造方式：外层加 `for admin in admin_registry.list_admins()` 循环；线程数仍为 1（避免并发）；每轮设当前 admin 上下文。

## 二、前端关键定位点

### 2.1 邀请加号（PRD 决策 #8 提升为顶级）

- `web/src/api.js:107` `startAddViaInvite: () => request('POST', '/tasks/add-via-invite')` —— 已存在的 API 调用。
- `web/src/components/TaskPanel.vue:72` —— 现有按钮定义（保留，作为账号池 tab 内入口）。
- 新建 `web/src/components/InviteFlowModal.vue` —— 顶级"邀请加号"按钮的弹窗壳，调用同一个 `api.startAddViaInvite()`。

### 2.2 工作台合并（PR3 重点）

- `web/src/App.vue:124-148` 当前 v-if 切换 7 页 → 删除 `team` / `pool` / `sync` 三个分支，新增 `workbench` 分支。
- `web/src/components/Sidebar.vue:89-97` `items` 数组 → 删除 `team`/`pool`/`sync`，新增 `workbench`。
- `web/src/components/Dashboard.vue` / `TeamMembers.vue` / `PoolPage.vue` / `SyncPage.vue` 的 template 在新 `Workbench.vue` 中以 4 个 tab 复用；它们各自接受 `currentAdminId` prop 并在 admin 切换时刷新数据。

### 2.3 ConfigPage 的"管理员登录流程"拆分（PR3 子任务）

- `web/src/components/ConfigPage.vue` 当前包含管理员邮箱→密码→验证码→workspace 选择的流程组件（具体行号在文件内 grep `admin.*login`）。
- 拆为独立 `web/src/components/AdminLoginFlow.vue`，工作台顶部"+ 添加新管理员"和原 ConfigPage 都引用它。

## 三、自动迁移（首次启动）

### 3.1 检测条件

- `data/admins.json` 不存在 **且** 旧 `state.json` 或 `accounts.json` 存在 → 触发迁移。
- `data/admins.json` 存在 → 跳过迁移（已新结构）。
- 都不存在 → 全新部署，初始化空 `data/admins.json`。

### 3.2 迁移步骤（伪代码）

```python
def bootstrap_admin_registry():
    if Path("data/admins.json").exists():
        return  # 已迁移
    legacy_state = Path("state.json")
    legacy_accounts = Path("accounts.json")
    if not legacy_state.exists() and not legacy_accounts.exists():
        Path("data/admins.json").write_text('{"admins": [], "active_admin_id": null}')
        return
    # 1. 备份
    backup_dir = Path(f"data/legacy-backup/{timestamp()}")
    backup_dir.mkdir(parents=True)
    copy(legacy_state, backup_dir)
    copy(legacy_accounts, backup_dir)
    copy_tree("auths", backup_dir / "auths")
    # 2. 生成 admin_id
    state_data = json.load(open(legacy_state))
    admin_id = uuid.uuid4().hex[:8]
    # 3. 移动文件到 data/admins/{admin_id}/
    new_dir = Path(f"data/admins/{admin_id}")
    new_dir.mkdir(parents=True)
    move(legacy_state, new_dir / "state.json")
    move(legacy_accounts, new_dir / "accounts.json")
    move("auths", new_dir / "auths")
    # 4. 写 admins.json 索引
    json.dump({"admins": [{"admin_id": admin_id, "alias": ..., "email": ...}], "active_admin_id": admin_id}, ...)
```

### 3.3 失败回退

- 任何一步失败：从 `legacy-backup/` 还原；删除半成品 `data/admins/` 目录；不写 `data/admins.json`；下次启动重试。
- 关键：迁移过程必须有完整异常捕获 + 中文日志告知用户去 `legacy-backup/` 找原文件。

## 四、API 路由迁移

### 4.1 新增（PR2）

| 路由 | 方法 | 用途 |
|------|------|------|
| `/api/admins` | GET | 列出所有 admin（含别名、上次激活时间） |
| `/api/admins/active` | POST | 切换激活 admin（body: `{admin_id}`） |
| `/api/admins/active` | GET | 获取当前激活 admin |
| `/api/admins/login/start` | POST | 为新 admin 启动登录流程（不复用旧 single-admin 接口） |
| `/api/admins/login/password` | POST | 同上 |
| `/api/admins/login/code` | POST | 同上 |
| `/api/admins/login/workspace` | POST | 同上 |
| `/api/admins/{admin_id}` | DELETE | 删除 admin（凭据 + 数据目录） |

### 4.2 旧路由保留（向后兼容）

- 旧 `/api/admin/*`、`/api/accounts/*`、`/api/team/*` 路由全部保留。
- 内部实现：从 header `X-Autoteam-Admin-Id` 取 admin_id；缺省时 fallback 到 `data/admins.json` 的 `active_admin_id`。
- FastAPI Depends：`def get_current_admin_id(x_autoteam_admin_id: str | None = Header(None)) -> str`。

## 五、可能踩坑的细节

1. **Docker bind mount**：旧部署 `docker-compose.yml` 若挂了 `state.json` / `accounts.json` 单文件，新结构下需要挂 `data/` 目录。文档需要更新 + 给迁移脚本写明指引。
2. **STATE_FILE_MODE = 0o666**：admin_state.py 设置了文件权限，新结构下每个 admin 的目录和文件也要保持相同权限，否则非 root 容器读不到。
3. **ChatGPT API session 隔离**：每个 admin 的 session_token 独立，但底层 HTTP client 不能共享 cookies；`chatgpt_transport.py` / `chatgpt_api.py` 需要按 admin 创建独立 session。
4. **关于 hero_sms.py**：git status 显示这是新增文件，PRD 暂未涉及。implement 时如果发现它跟 admin 流程相关，需评估改造点。
5. **AUTO_CHECK_INTERVAL 错峰**：所有 admin 循环跑会延长一轮总耗时；如果 admin 数 > 5，可能需要让 interval 变成"每个 admin 间隔"而非"全局间隔"。
