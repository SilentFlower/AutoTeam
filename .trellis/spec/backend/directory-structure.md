# 目录结构规范

> AutoTeam 后端代码组织实情，AI 在此项目里写代码必须遵守。

---

## 总览

AutoTeam 是单仓库结构：

- 后端是一个 **Python 包** `autoteam`，源码全部在 `src/autoteam/` 下，**按功能纵向切分**模块（一个文件 = 一个职责），**没有** `services/`、`routes/`、`utils/` 这类分层目录。
- 前端是 **独立 Vue 3 + Vite 应用**，源码在 `web/`，构建产物落到 `src/autoteam/web/dist/` 由 FastAPI 静态托管。
- 测试只有 `tests/unit/`，**没有** `tests/integration/`、`tests/fixtures/`、也**没有** `conftest.py`。
- 所有持久化是 **JSON 文件**（`accounts.json`、`state.json`）和 `.env`，**没有数据库**。

新增功能时不要凭空建分层目录；按既有"一个文件一个职责"的纵切方式落到 `src/autoteam/` 即可。

---

## 实际目录布局

```
AutoTeam/
├── src/autoteam/                  # 后端 Python 包（唯一源码目录）
│   ├── __init__.py                # 包初始化 + 全局 logging 配置
│   ├── manager.py                 # CLI 入口（autoteam 命令），编排账号轮转主流程
│   ├── api.py                     # FastAPI HTTP API，暴露 /api/* 端点 + 鉴权中间件
│   ├── config.py                  # .env 加载、环境变量解析（_get_int_env 等）
│   ├── accounts.py                # 账号池持久化（accounts.json 读写、状态枚举）
│   ├── account_ops.py             # 账号清理 / 远端对账（删邮箱、调 Team API、sync_targets）
│   ├── admin_state.py             # 管理员登录态（state.json + 遗留 session 迁移）
│   ├── codex_auth.py              # Codex OAuth、token 交换、额度检查
│   ├── chatgpt_api.py             # ChatGPT Team API 客户端（成员、邀请、配额）
│   ├── invite.py                  # ChatGPT Team 邀请注册（Playwright + 邮箱验证码）
│   ├── setup_wizard.py            # 初始化向导（交互式配置、启动前校验）
│   ├── mail_provider.py           # 邮箱抽象层（CloudMail / Cloudflare Temp Email）
│   ├── cloudmail.py               # CloudMail 邮箱客户端
│   ├── hero_sms.py                # HeroSMS 短信验证码客户端 + 自定义异常
│   ├── sub2api_sync.py            # Sub2API 远端同步
│   ├── sync_targets.py            # 多端点同步调度
│   ├── textio.py                  # 跨平台 UTF-8 文本读写 + .env 行解析
│   └── web/dist/                  # 前端构建产物（由 web/ 编译生成，不要手改）
├── web/                           # 前端 Vue 3 + Vite 项目（独立 package.json）
│   ├── src/components/            # 11 个 .vue 组件：Dashboard、ConfigPage、Settings…
│   ├── src/api.js                 # 前端 API 客户端
│   ├── package.json               # vue@3.5、vite@6.0、tailwindcss
│   └── vite.config.js             # 输出目录指向 ../src/autoteam/web/dist
├── tests/unit/                    # 仅有单元测试（无 integration、无 fixtures、无 conftest.py）
├── docs/                          # 用户向文档（getting-started、api、architecture）
├── pyproject.toml                 # 包元数据 + 依赖 + pytest 配置
├── ruff.toml                      # Ruff lint/format 配置（独立文件）
├── .pre-commit-config.yaml        # ruff lint + ruff-format
├── .github/workflows/             # CI（ci.yml、pre-commit.yml）
├── AGENTS.md                      # AI 工具入口指引（Trellis 注入块）
└── CONTRIBUTING.md                # 开发者贡献流程
```

---

## 模块组织原则

### 1. 一个文件 = 一个职责

新功能优先落到现有最贴合职责的模块；只有"完全不属于任何现存模块的能力"才建新文件。判断标准看现有命名习惯：

| 情形 | 应该落的位置 |
|------|------------|
| 改动账号池字段或读写逻辑 | `accounts.py` |
| 新增 / 调整 HTTP 端点 | `api.py` |
| 新增 CLI 子命令或主流程编排 | `manager.py` |
| 新增第三方 API 客户端（如新邮箱平台） | 新建 `<provider>.py`，并在 `mail_provider.py` 中接入 |
| 新增配置项 | `config.py`（env 解析） + 调用方 |
| 跨模块的小工具（解析、IO） | `textio.py` 或就近放在调用方 |

### 2. 不要随意建子目录

`src/autoteam/` 当前是**扁平结构**（除 `web/dist/`）。新增文件直接放在 `src/autoteam/` 下；不要建 `services/`、`models/`、`utils/` 之类的子目录——这会和既有约定冲突。

### 3. CLI / API 共享业务逻辑

`manager.py` 是 CLI 入口（`pyproject.toml:17` 配置 `autoteam = "autoteam.manager:main"`），`api.py` 是 HTTP 入口。两者**调用同一组业务模块**（`accounts.py`、`account_ops.py`、`codex_auth.py` 等），所以业务逻辑要写在被调用模块里，**不要**写在 `manager.py` 或 `api.py` 内部，否则 CLI/API 行为会漂移。

---

## 命名规范

| 对象 | 风格 | 示例 |
|------|------|------|
| 模块文件名 | `snake_case.py` | `account_ops.py`、`chatgpt_api.py`、`sub2api_sync.py` |
| 类名 | `PascalCase` | `HeroSmsError`、`HeroSmsClient`、`SetupConfig`、`TaskParams` |
| 函数 / 变量 | `snake_case` | `load_accounts`、`save_accounts`、`get_active_accounts` |
| 模块级常量 | `UPPER_SNAKE_CASE` | `STATUS_ACTIVE`、`ACCOUNTS_FILE`、`UTF8_READ_ENCODING` |
| 私有函数 / 模块内部辅助 | `_leading_underscore` | `_normalized_email`、`_is_main_account_email`、`_save_state` |

第三方品牌名遵循其本身大小写习惯但**整体仍 snake**：`chatgpt_api.py`、`cloudmail.py`、`hero_sms.py`、`sub2api_sync.py`。

---

## 真例参考

- 业务模块的"模块说明 + 常量 + 辅助函数 + 公开函数"骨架：`src/autoteam/accounts.py:1-50`
- 跨模块通用工具落在 `textio.py` 而非 `utils/`：`src/autoteam/textio.py:1-40`
- API 模块开头的 logger / FastAPI 初始化 / 鉴权中间件：`src/autoteam/api.py:1-50`
- CLI 入口在 `pyproject.toml` 注册：`pyproject.toml:16-17`
- 前端构建产物路径硬绑后端包：`web/vite.config.js`（output → `../src/autoteam/web/dist`）

---

## 常见错误

1. **凭空新建子目录**——把新代码放进 `src/autoteam/services/foo.py`。本仓库是扁平的，应该直接 `src/autoteam/foo.py`。
2. **把业务逻辑写进 `api.py` 端点函数**——会和 `manager.py` CLI 行为漂移。请抽到对应业务模块。
3. **手改 `src/autoteam/web/dist/`**——这是 Vite 构建产物，应该改 `web/src/`，重新 `npm run build`。
4. **在 `tests/` 下新建 `integration/` 或 `conftest.py`**——当前约定只有 `tests/unit/`，需要全局 fixture 时请先在 PR 里讨论。
