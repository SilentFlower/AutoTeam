# 数据持久化规范

> AutoTeam 实际的"数据库"长什么样、怎么读写、怎么演进。

---

## 总览

**本项目没有 SQL 数据库，也没有 ORM**。一切持久化由三类文件承担：

| 文件 | 用途 | 写入位置 |
|------|------|--------|
| `accounts.json`（项目根） | 账号池（每个账号是一条 dict） | `src/autoteam/accounts.py:39-41` |
| `state.json`（项目根） | 管理员登录态、向导进度 | `src/autoteam/admin_state.py` |
| `.env`（项目根） | 全局配置（API Key、邮箱凭证、同步目标等） | `src/autoteam/config.py` + `src/autoteam/api.py` 热加载 |

读写约定：

- 文本编码统一通过 `autoteam.textio.read_text` / `write_text` 完成（UTF-8，读时兼容 BOM `utf-8-sig`，写时纯 UTF-8）。
- JSON 序列化统一 `json.dumps(..., indent=2, ensure_ascii=False)`——保留中文可读、缩进 2 空格。
- 任何要持久化的字段一律写到现有 JSON 文件的对应 dict 里，**不要**再开新文件。

---

## 写入与读取标准做法

### 1. 文本 IO 必须走 `textio` 模块

```python
# src/autoteam/textio.py:12-19
def read_text(path: str | Path) -> str:
    """以 UTF-8（兼容 BOM）读取文本文件。"""
    return Path(path).read_text(encoding=UTF8_READ_ENCODING)  # utf-8-sig

def write_text(path: str | Path, content: str) -> None:
    """以 UTF-8 写入文本文件。"""
    Path(path).write_text(content, encoding=UTF8_WRITE_ENCODING)  # utf-8
```

**禁止**直接 `open(path, "w")`、`Path(path).read_text()`（不传 encoding）——历史 BOM 文件会读出多一个 `﻿` 导致 `json.loads` 失败。

### 2. JSON 写入的标准模板

参考 `src/autoteam/accounts.py:39-41`：

```python
def save_accounts(accounts):
    """保存账号列表"""
    write_text(ACCOUNTS_FILE, json.dumps(accounts, indent=2, ensure_ascii=False))
```

`ensure_ascii=False` 是硬性约定——账号备注、错误日志可能含中文。

### 3. JSON 读取要兼容空文件

参考 `src/autoteam/accounts.py:30-36`：

```python
def load_accounts():
    if ACCOUNTS_FILE.exists():
        text = read_text(ACCOUNTS_FILE).strip()
        if text:
            return json.loads(text)
    return []
```

要点：判断文件存在 + `strip()` 去空 + 空内容回退默认值，**不要**让用户面对原始 `JSONDecodeError`。

### 4. 文件路径锚定到项目根

各模块用 `Path(__file__).parent.parent.parent` 推导项目根：

```python
# src/autoteam/accounts.py:11-12
PROJECT_ROOT = Path(__file__).parent.parent.parent
ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"
```

**不要**写绝对路径，也**不要**依赖 `os.getcwd()`——CLI 与 systemd / Docker 启动时 CWD 不一致。

---

## CRUD 真例

`src/autoteam/accounts.py` 是数据层最完整的范本，新增持久化逻辑请按它的风格仿写。

### 创建（先查重再 append）

```python
# src/autoteam/accounts.py:52-90
def add_account(email, password, cloudmail_account_id=None, *, ...):
    accounts = load_accounts()
    if find_account(accounts, email):
        return  # 已存在
    accounts.append({...})
    save_accounts(accounts)
```

### 更新（按主键查、merge 字段、回写）

```python
# src/autoteam/accounts.py:93-100
def update_account(email, **kwargs):
    accounts = load_accounts()
    acc = find_account(accounts, email)
    if acc:
        acc.update(kwargs)
        save_accounts(accounts)
    return acc
```

### 查询（list comprehension + 显式过滤）

```python
# src/autoteam/accounts.py:103-105
def get_active_accounts():
    return [a for a in load_accounts() if a["status"] == STATUS_ACTIVE
            and not _is_main_account_email(a.get("email"))]
```

注意：复杂查询条件（如带时间窗口的"待命账号"）请抽成命名函数（`get_standby_accounts`、`get_next_reusable_account`），不要在调用方写一长串 lambda。

---

## 字段命名约定

观察 `accounts.py:71-89` 添加的账号 dict：

```python
{
    "email": ...,
    "password": ...,
    "status": STATUS_PENDING,
    "auth_file": None,
    "quota_exhausted_at": None,        # *_at = Unix 时间戳（time.time()）
    "quota_resets_at": None,
    "created_at": time.time(),
    "auth_retry_count": 0,
    "auth_retry_paused": False,
    "auth_last_error": None,
}
```

**约定**：

- JSON key 全部 `snake_case`。
- 时间字段以 `_at` 结尾，存 **Unix 浮点时间戳**（`time.time()`），不存 ISO 字符串、不存 `datetime` 对象。
- 状态字段值用 `STATUS_*` 模块常量（`accounts.py:14-19`），**不要**散落字符串字面量。
- 布尔字段使用 `auth_retry_paused` 这类形容词命名，默认 `False`。

---

## 状态枚举：模块常量而非 Enum

`accounts.py:14-19` 用模块级字符串常量而不是 `enum.Enum`：

```python
STATUS_ACTIVE = "active"
STATUS_EXHAUSTED = "exhausted"
STATUS_STANDBY = "standby"
STATUS_PENDING = "pending"
STATUS_AUTH_PENDING = "auth_pending"
```

**原因**：JSON 序列化天然兼容字符串；用 Enum 反而要写 `Enum(value).value`。新增状态请沿用这种风格——`STATUS_<NAME> = "<lowercase_value>"`。

---

## 并发与原子性

### 1. 进程内并发用 `threading.Lock`

API 服务的运行时 env 热加载用锁保护：

```python
# src/autoteam/api.py:90-120 附近
_runtime_env_reload_lock = threading.Lock()
def _maybe_reload_runtime_config_from_env_file(*, force: bool = False):
    with _runtime_env_reload_lock:
        ...
```

### 2. 文件写入**没有**显式临时文件 + rename

当前 `save_accounts` 直接 `Path.write_text` 覆盖。这是已知行为——绝大部分写入由单进程主线程串行触发。**如果未来你引入多进程并发写**，需要先在 PRD 里讨论方案（可能是 `tempfile.NamedTemporaryFile + os.replace` 原子替换），不要悄悄改。

### 3. Docker 共享卷的 chmod

`state.json` 写入后会显式 chmod 让宿主与容器都能读：

```python
# src/autoteam/admin_state.py:_save_state 内部
target = STATE_FILE.resolve()  # 解析软链
write_text(target, json.dumps(_normalize_state(state), indent=2, ensure_ascii=False))
os.chmod(target, STATE_FILE_MODE)  # 0o666
```

仿写跨容器持久化时记得保留 `resolve() + chmod` 两步。

---

## Schema 演进与向后兼容

**没有迁移工具**——靠 `dict.get()` 默认值在读取侧兼容旧文件：

```python
# 推荐：读取时给默认值
resets_at = a.get("quota_resets_at")  # 旧账号没有这个字段
auth_retry_count = a.get("auth_retry_count", 0)
```

**禁止**：

- 假设字段一定存在（`a["quota_resets_at"]` 在旧账号上 `KeyError`）。
- 改字段名（旧 JSON 找不到新名会"丢数据"）。

新增字段时：

1. 在 `add_account()` 默认值列表里加一项。
2. 所有读取处用 `dict.get(key, default)`。
3. 不要写"一次性脚本去回填旧记录"——除非用户主动触发（参考 `admin_state.py` 的 `_migrate_legacy_state()` 是用户首次进入时自动迁移）。

历史迁移真例：`src/autoteam/admin_state.py` 把旧版纯文本 session 文件迁移到 `state.json`，触发时机是首次 `_load_state()`。新增迁移请仿这一模式（**进程启动惰性迁移**，不写独立 CLI 子命令）。

---

## 常见错误

1. **直接读 `accounts.json`** 不走 `load_accounts()`——会绕过 BOM 容错和空文件兜底。
2. **新增字段忘了 `.get(default)`**——线上历史账号会爆 `KeyError`。
3. **存 `datetime.now()` 字符串到 JSON**——要么是 `time.time()` 浮点，要么不存。
4. **改 `STATUS_*` 常量值**（如 `"active"` → `"ACTIVE"`）——历史 JSON 里全是旧值，会瞬间让所有账号"消失"。
5. **新建第二份 JSON 文件**（如 `quota.json`）——能合进 `accounts.json` 的字段就合，别拆。
