# Python 死模块扫描报告

**扫描时间**: 2026-04-28  
**扫描范围**: `/root/project/AutoTeam/src/autoteam/` (21 个模块)  
**扫描方式**: 全仓库 grep + 手动函数追踪  

---

## 一、核心发现

### ✅ 所有模块仍被使用

完整扫描 21 个 Python 模块，**无死模块候选**。所有模块均被以下至少一个位置引用：
- 其他源代码模块 (`src/autoteam/`)
- 测试文件 (`tests/`)
- 配置文件 (`pyproject.toml`)
- CLI 入口 (`__main__.py`)

---

## 二、模块引用清单

### 核心流程模块

| 模块 | 类型 | 外部引用数 | 主要引用位置 |
|------|------|----------|-----------|
| **manager.py** | 主程序入口 | 20+ | `pyproject.toml` (CLI), `__main__.py`, `api.py` |
| **api.py** | FastAPI 服务 | 4 | `manager.py` (启动服务器) |
| **codex_auth.py** | 认证流程 | 28 | `api.py`, `manager.py`, `invite.py` |
| **chatgpt_api.py** | ChatGPT 客户端 | 10 | `account_ops.py`, `api.py`, `codex_auth.py`, `playwright_probe.py` |

### 配置与存储模块

| 模块 | 引用数 | 主要使用 |
|------|-------|--------|
| **config.py** | 23 | 全局配置加载（`chatgpt_api.py`, `codex_auth.py`, `setup_wizard.py`） |
| **admin_state.py** | 13 | 管理员状态持久化（`chatgpt_api.py`, `accounts.py`, `api.py`) |
| **auth_storage.py** | 3 | 认证文件权限管理（`codex_auth.py`, `manager.py`, `api.py`) |
| **textio.py** | 10 | 跨平台文本读写（`config.py`, `admin_state.py`, `accounts.py`, `sub2api_sync.py`) |

### 邮箱与第三方集成模块

| 模块 | 引用数 | 状态 |
|------|-------|------|
| **mail_provider.py** | 18 | 邮箱提供商抽象层（`account_ops.py`, `accounts.py`, `manager.py`, `setup_wizard.py`) |
| **cloudmail.py** | 5 | CloudMail 邮箱客户端（`mail_provider.py`, `setup_wizard.py`, `api.py`) |
| **cloudflare_temp_email.py** | 5 | 临时邮箱客户端（`mail_provider.py`, `setup_wizard.py`, `api.py`) |
| **hero_sms.py** | 13 | **✨ 新增** 短信验证码集成（`codex_auth.py`, `manager.py`, `api.py`) |

### 辅助功能模块

| 模块 | 引用数 | 功能 |
|------|-------|------|
| **display.py** | 4 | 虚拟显示器初始化（`chatgpt_api.py`, `codex_auth.py`, `invite.py`, `manager.py`) |
| **chatgpt_transport.py** | 1 | HTTP 传输层（仅被 `chatgpt_api.py` 导入） |
| **playwright_probe.py** | 1 | 浏览器探针子进程（`api.py` 通过 subprocess 调用） |

### 账户与同步模块

| 模块 | 引用数 | 说明 |
|------|-------|------|
| **accounts.py** | 44 | 账户管理核心（广泛引用） |
| **account_ops.py** | 3 | 账户操作高级接口（`manager.py`, `api.py`) |
| **invite.py** | 3 | 邀请流程（`manager.py`) |
| **sync_targets.py** | 10 | 同步目标处理（`account_ops.py`, `api.py`, `codex_auth.py`) |
| **sub2api_sync.py** | 6 | Sub2API 同步（`sync_targets.py`, `setup_wizard.py`, `api.py`) |
| **setup_wizard.py** | 47 | 初始化向导（`api.py`, `manager.py`, 测试文件） |

---

## 三、特别关注项目的详细分析

### 1. **sub2api_sync.py** ✅
- **状态**: 仍被使用
- **引用位置**:
  - `sync_targets.py` (动态导入 4 个函数)
  - `setup_wizard.py` (verify_sub2api_connection)
  - `api.py` (API 端点)
  - `tests/unit/test_sub2api_sync.py` (完整单元测试套件)
- **结论**: 保留

### 2. **chatgpt_api.py / chatgpt_transport.py** ✅
- **状态**: 核心模块，仍被广泛使用
- **chatgpt_api.py 引用**:
  - `chatgpt_transport.py` (build_chatgpt_transport 导入)
  - `account_ops.py`, `api.py`, `codex_auth.py`, `playwright_probe.py` (ChatGPTTeamAPI 导入)
  - 多个测试文件
- **chatgpt_transport.py 引用**:
  - 仅被 `chatgpt_api.py` 导入（专用 HTTP 传输层）
  - 1 个单元测试
- **结论**: 保留

### 3. **cloudmail.py / cloudflare_temp_email.py / mail_provider.py** ✅
- **状态**: 邮箱集成，仍被使用
- **交叉引用关系**:
  ```
  mail_provider.py (中枢)
    ├─→ cloudmail.py (动态导入)
    ├─→ cloudflare_temp_email.py (动态导入)
    └─← accounts.py, setup_wizard.py, manager.py, api.py
  ```
- **引用场景**:
  - 账户创建时邮箱验证
  - 初始化向导邮箱配置
  - API 邮箱提供商查询端点
- **结论**: 保留

### 4. **playwright_probe.py** ✅
- **状态**: 仍被使用
- **引用位置**:
  - `api.py` (_playwright_probe_command, _run_playwright_probe)
  - 通过 subprocess 调用: `python -m autoteam.playwright_probe`
  - `tests/unit/test_api_status.py`
- **用途**: 获取 Team 成员数（长耗时操作隔离）
- **结论**: 保留

### 5. **admin_state.py** ✅
- **状态**: 仍被使用
- **公开函数引用**:
  - `get_admin_email()` ← api.py
  - `get_chatgpt_account_id()` ← account_ops.py, chatgpt_api.py, api.py
  - `get_admin_session_token()` ← api.py
  - `get_admin_state_summary()` ← api.py, manager.py
  - `clear_admin_state()` ← api.py
  - `update_admin_state()` ← api.py (仅在测试中直接调用)
- **结论**: 保留

### 6. **hero_sms.py** ✨ **新模块**
- **状态**: 已被集成
- **引用位置** (13 处):
  - `codex_auth.py`: is_hero_sms_configured(), handle_add_phone_via_http()
  - `manager.py`: is_hero_sms_configured()
  - `api.py`: HeroSmsClient, HeroSmsError, 三个 API 端点
  - `tests/unit/test_manager_auth_repair.py`: 5 处 monkeypatch
- **用途**: 短信验证码集成
- **结论**: 保留（已完全集成）

### 7. **textio.py** ✅
- **状态**: 跨平台文本工具库
- **公开函数引用**:
  - `read_text()` ← config.py, admin_state.py, accounts.py, chatgpt_api.py, sub2api_sync.py, api.py
  - `write_text()` ← admin_state.py, accounts.py, codex_auth.py, api.py
  - `parse_env_line()` ← config.py, api.py, setup_wizard.py
  - `parse_env_value()` ← config.py, sync_targets.py
- **结论**: 保留

### 8. **display.py** ✅
- **状态**: 虚拟显示器初始化（副作用导入）
- **引用位置** (4 处):
  - `chatgpt_api.py`: import autoteam.display # noqa: F401
  - `codex_auth.py`: import autoteam.display # noqa: F401
  - `invite.py`: import autoteam.display # noqa: F401
  - `manager.py`: import autoteam.display # noqa: F401
- **用途**: Linux 虚拟显示器自动设置（模块加载时执行）
- **结论**: 保留（特殊导入模式，勿删）

---

## 四、删除的模块状态

### ❌ 已删除但未完全清理

**src/autoteam/cpa_sync.py**
- **Git 状态**: Deleted
- **遗留引用**: `tests/unit/test_api_main_codex_after_admin.py` 有 1 处字符串引用
  ```python
  "autoteam.cpa_sync.delete_main_codex_from_cpa",  # monkeypatch target
  ```
- **建议**: 此测试文件中的字符串需更新或该测试需移除（因为函数已不存在）

**src/autoteam/manual_account.py**
- **Git 状态**: Deleted
- **遗留引用**: 0 处
- **结论**: 干净删除

---

## 五、CLI 入口点检查

### pyproject.toml
```toml
[project.scripts]
autoteam = "autoteam.manager:main"
```
✅ `manager.py` 仍存在，`main()` 函数存在

### __main__.py
```python
from autoteam.manager import main
main()
```
✅ 支持 `python -m autoteam` 调用

---

## 六、结论与建议

### 当前状态
- ✅ **无死模块**: 所有 21 个 Python 模块均被引用
- ✅ **无孤立函数**: 所有公开函数/类都有调用方
- ✅ **新模块集成良好**: hero_sms.py 完全集成
- ✅ **特殊导入模式安全**: display.py 副作用导入已覆盖使用点

### 遗留问题（低优先级）
1. **test_api_main_codex_after_admin.py**: 包含已删除 cpa_sync.py 的字符串引用
   - 建议: 检查此测试是否应删除或重构

### 不建议的操作
- ❌ 删除任何 src/autoteam/*.py 模块
- ❌ 删除 display.py（虚拟显示器初始化必需）
- ❌ 删除 textio.py（跨平台标准库，被广泛使用）

### 优化空间
- 可考虑将 chatgpt_transport.py 的逻辑内联到 chatgpt_api.py（仅 1 处引用）
- 可考虑将 playwright_probe.py 合并到 api.py（仅通过 subprocess 调用）
- **但优先级低**：当前架构清晰，关注点分离良好

---

**报告生成**: 仓库全量 grep + 手动代码追踪  
**覆盖范围**: src/ + tests/ + docs/ + pyproject.toml + 配置文件
