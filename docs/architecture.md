# 工作原理

## 总体目标

AutoTeam 的目标不是单纯“多开号”，而是：

1. 维护 **Team 总人数** 在目标值附近
2. 让 active 账号尽量保持可用额度
3. 将可用认证文件同步到已启用远端（CPA / Sub2API）
4. 在需要时从 CPA 反向恢复认证文件到本地

## 轮转流程

```text
同步 Team 实际状态
        ↓
检查 active 账号额度
        ↓
额度不足 → 标记 exhausted → 移出 Team → standby
        ↓
优先复用 standby 旧号
        ↓
不够再创建新号
        ↓
同步 active 认证文件到已启用远端
```

> 轮转目标是 **Team 总人数**。
> Team 中已有的 owner / 外部成员也会计入目标人数。

## 账号状态机

```text
active ──额度不足──> exhausted ──移出 Team──> standby
   ↑                                      │
   └──────── 额度恢复 / 登录成功 ──────────┘
```

| 状态 | 含义 |
|------|------|
| `active` | 当前在 Team 中，且本地认为可用 |
| `exhausted` | 当前在 Team 中，但额度不足，等待移出 |
| `standby` | 已不在当前轮转席位中，等待后续复用 |
| `pending` | 注册 / 创建流程尚未完成 |

## 同步模型

项目中主要有三类“同步”：

| 动作 | 方向 | 用途 |
|------|------|------|
| `同步账号` | Team / `auths/` → `accounts.json` | 修复本地账号池记录 |
| `同步远端` | 本地 active / 主号 → 已启用远端 | 将认证同步到 CPA / Sub2API |
| `拉取 CPA` | CPA → 本地 | 从 CPA 反向恢复 / 导入认证文件 |

### 反向同步特点

- 同账号去重（CPA 与本地都只保留一份）
- 按本地命名规范重写文件名
- 比较 `last_refresh` / `expired`，避免用旧 CPA 文件覆盖本地新 token
- 新导入账号默认标记为 `standby`

## OAuth 导入模型

手动 OAuth 导入支持两种回调方式：

### 1. 自动回调

系统尝试在本机启动：

```text
http://localhost:1455/auth/callback
```

如果浏览器和 AutoTeam 在同一台机器上，OpenAI 成功回跳后可自动完成认证。

### 2. 手动回调

如果浏览器不在同一台机器上，或 `localhost:1455` 无法回到 AutoTeam：

- 用户在浏览器完成登录
- 再把最终回调 URL 粘贴给 AutoTeam
- 系统提取 `code/state` 完成 token 交换

## 核心模块

| 模块 | 作用 |
|------|------|
| `manager.py` | CLI 入口与核心轮转逻辑 |
| `api.py` | HTTP API、鉴权、后台任务、自动巡检 |
| `accounts.py` | 本地账号池持久化 |
| `account_ops.py` | 删除 / 清理 / 远端对账 |
| `chatgpt_api.py` | 通过浏览器上下文调用 ChatGPT 内部接口 |
| `codex_auth.py` | Codex OAuth、refresh、额度检查 |
| `invite.py` | 自动注册流程 |
| `cloudmail.py` | CloudMail 客户端 |
| `cloudflare_temp_email.py` | Cloudflare Temp Email 客户端 |
| `mail_provider.py` | 邮箱服务选择与账号绑定辅助 |
| `cpa_sync.py` | CPA 双向同步与去重 |
| `sub2api_sync.py` | Sub2API 同步与分组处理 |
| `sync_targets.py` | 统一分发 CPA / Sub2API 同步目标 |
| `manual_account.py` | 手动 OAuth 导入（自动 / 手动回调） |

## 项目结构

```text
autoteam/
├── docs/                       # 文档
├── src/autoteam/
│   ├── manager.py              # CLI 入口
│   ├── api.py                  # HTTP API + 后台任务 + 自动巡检
│   ├── setup_wizard.py         # 首次配置向导
│   ├── admin_state.py          # 管理员登录态 (state.json)
│   ├── config.py               # 配置加载
│   ├── accounts.py             # 账号池持久化
│   ├── account_ops.py          # 删除 / 清理 / 对账
│   ├── chatgpt_api.py          # ChatGPT Team 内部 API 调用
│   ├── cloudmail.py            # CloudMail 客户端
│   ├── cloudflare_temp_email.py # Cloudflare Temp Email 客户端
│   ├── mail_provider.py        # 邮箱服务选择与账号绑定
│   ├── codex_auth.py           # Codex OAuth 与 token 管理
│   ├── cpa_sync.py             # CPA 正反向同步
│   ├── sub2api_sync.py         # Sub2API 同步与分组
│   ├── sync_targets.py         # 统一远端同步目标
│   ├── manual_account.py       # 手动 OAuth 导入
│   ├── invite.py               # 自动注册流程
│   └── web/dist/               # 前端构建产物
└── web/src/components/         # Web 面板各页面与组件
```

## 前端结构

当前 Web 面板已按职责拆分为：

- 工作台（仪表盘 / Team 成员 / 账号池 / 同步 4 tab 合并 + 顶部 admin 切换器 + 邀请加号主按钮）
- 配置面板
- 任务历史
- 日志

## 多管理员主号

AutoTeam 支持登录、管理多个 Team 管理员（"主号"），同一时刻只有一个**激活管理员**——用户操作（轮转 / 同步 / kick / 邀请加号 等）都作用于激活 admin 的账号池;后台巡检则**轮流**遍历所有 admin 各自跑一次,保证已登录但未激活的 admin 也得到守护。

### 数据布局

```
data/
├── admins.json                       # 索引(admins[] + active_admin_id)
├── admins/
│   ├── <admin_id_a>/                 # 每个 admin 一个工作区目录
│   │   ├── state.json                # 凭据/email/workspace_name/account_id
│   │   ├── accounts.json             # 该主号的子账号池
│   │   └── auths/codex-main-*.json   # 主号 Codex 凭据
│   └── <admin_id_b>/...
└── legacy-backup/<timestamp>/        # 单 admin → 多 admin 自动迁移备份
```

`admin_id` 是 8 位小写 hex（来自 UUID4 截取),首次启动时若检测到旧 `state.json + accounts.json + auths/` 会自动迁移到新结构,原文件备份到 `legacy-backup/`。

### 抽象层

- `src/autoteam/admin_registry.py` —— admin 增删改查 / 激活切换 / 自动迁移
- 数据层（`admin_state.py` / `accounts.py` / `auth_storage.py` / `codex_auth.py` / `account_ops.py`）所有读写函数接收可选 `admin_id` 参数,缺省 fallback 到 `get_active_admin_id()`

### API 上下文传递

HTTP 请求头 `X-Autoteam-Admin-Id: <8 位 hex>` 指定操作目标 admin;FastAPI 的 `Depends(get_current_admin_id)` 解析并白名单校验。前端 `web/src/api.js` 拦截器从 store 自动注入;旧客户端不传 header 时后端 fallback 到 active admin。

## 开发

```bash
cd web
npm install
npm run dev
npm run build
```
