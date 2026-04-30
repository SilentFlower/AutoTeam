# 数据持久化规范

> AutoTeam 实际的"数据库"长什么样、怎么读写、怎么演进。

---

## 总览

**本项目没有 SQL 数据库，也没有 ORM**。一切持久化由三类文件承担：

| 文件 | 用途 | 写入位置 |
|------|------|--------|
| `accounts.json`（项目根或 `data/admins/<admin_id>/`） | active 账号池（每个账号是一条 dict） | `src/autoteam/accounts.py` |
| `free_accounts.json` / `plus_accounts.json` | 完全隔离的 FREE / Plus 资产池 | `src/autoteam/free_accounts.py` / `src/autoteam/plus_accounts.py` |
| `state.json`（项目根） | 管理员登录态、向导进度 | `src/autoteam/admin_state.py` |
| `.env`（项目根） | 全局配置（API Key、邮箱凭证、同步目标等） | `src/autoteam/config.py` + `src/autoteam/api.py` 热加载 |

读写约定：

- 文本编码统一通过 `autoteam.textio.read_text` / `write_text` 完成（UTF-8，读时兼容 BOM `utf-8-sig`，写时纯 UTF-8）。
- JSON 序列化统一 `json.dumps(..., indent=2, ensure_ascii=False)`——保留中文可读、缩进 2 空格。
- 任何要持久化的字段一律写到现有 JSON 文件的对应 dict 里，**不要**再开新文件；完全隔离资产池的例外见下方专章。

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

## 多租户/多账号:目录树拆分而非文件内 owner_id 字段

当一份 JSON(如 `accounts.json`、`state.json`)从"全局唯一"演进为"按 owner_id 多份"时,**首选按目录树拆分**而不是给每条记录加 `owner_id` 字段共用单文件。

### 真例:多管理员主号(`feat/multi-admin` 分支)

```
data/
├── admins.json                       # 索引(admins[] + active_owner_id)
├── admins/
│   ├── <owner_id_a>/                 # 每个 owner 一个工作区目录
│   │   ├── state.json                # 凭据/email/workspace 信息
│   │   ├── accounts.json             # 该 owner 的子账号池
│   │   └── auths/codex-main-*.json   # 凭据文件
│   └── <owner_id_b>/...
└── legacy-backup/<timestamp>/        # 旧单 owner → 多 owner 的自动迁移备份
```

### 选目录树而非单文件加字段的原因

| 方面 | 单文件加 `owner_id` 字段 | 目录树按 owner 拆分 |
|------|--------------------------|--------------------|
| 读写性能 | 每次操作都要全表过滤 | 按 owner_id 直接定位文件 |
| 删除 owner | 需要全表 filter 出该 owner 的记录再批量删除 | `shutil.rmtree(owner_dir)` 一步 |
| 备份/迁移 | 整个文件作为一个原子单位 | 单个 owner 可独立备份/迁移 |
| 测试隔离 | monkeypatch 全局文件路径影响所有 owner | 各 owner 目录天然隔离 |
| 与既有数据层兼容 | 所有 `load_xxx()` / `save_xxx()` 都要加 `owner_id` 过滤参数 | 仅在路径函数加 `owner_id` 入参,业务逻辑无感 |
| Docker 卷挂载 | 单文件挂载,改字段不动挂载 | 必须挂目录,文档需更新 |

### 实施模板

```python
# data 层路径常量改为函数,接受 owner_id 入参
def _accounts_file(owner_id: str) -> Path:
    return PROJECT_ROOT / "data" / "owners" / owner_id / "accounts.json"

# 各 CRUD 函数接收可选 owner_id,缺省 fallback 到当前激活 owner(向后兼容)
def load_accounts(owner_id: str | None = None):
    if owner_id is None:
        owner_id = owner_registry.get_active_owner_id()
    if owner_id is None:
        return []  # 全新部署/未迁移
    path = _accounts_file(owner_id)
    if path.exists():
        text = read_text(path).strip()
        if text:
            return json.loads(text)
    return []
```

### 自动迁移检查清单(首次启动)

```python
def bootstrap_owner_registry():
    new_index = PROJECT_ROOT / "data" / "owners.json"
    if new_index.exists():
        return  # 已迁移,零代价跳过

    # 检测旧文件(全局单文件结构)
    legacy_files = [PROJECT_ROOT / "state.json", PROJECT_ROOT / "accounts.json"]
    if not any(f.exists() for f in legacy_files):
        # 全新部署,写空索引
        write_text(new_index, json.dumps({"owners": [], "active_owner_id": None}, ...))
        return

    # 1. 备份(用 shutil.copy2 不是 move,失败回退要保留原文件)
    backup_dir = PROJECT_ROOT / "data" / "legacy-backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in legacy_files:
        if f.exists():
            shutil.copy2(f, backup_dir / f.name)

    # 2. 生成 owner_id 与目录
    new_owner_id = uuid.uuid4().hex[:8]
    new_dir = PROJECT_ROOT / "data" / "owners" / new_owner_id
    new_dir.mkdir(parents=True, exist_ok=True)

    # 3. 移动旧文件(到这里才 move,因为前面已备份过)
    for f in legacy_files:
        if f.exists():
            shutil.move(str(f), str(new_dir / f.name))

    # 4. 最后写 owners.json,失败时不留半成品
    write_text(new_index, json.dumps({
        "owners": [{"owner_id": new_owner_id, ...}],
        "active_owner_id": new_owner_id,
    }, indent=2, ensure_ascii=False))
```

**关键约束**:

- **先 copy 备份再 move 原文件**——失败时原文件还在,可重试
- **最后才写新索引**——半成品索引会让下次启动以为"已迁移"导致再也不进迁移分支
- **`shutil.copy2` 而非 `copy`**——保留 mtime/权限,Docker 卷下避免权限问题
- **任何异常都不删原文件**——`legacy-backup/` 永远是兜底退路

### 反模式(单文件加字段)

```python
# ❌ 不推荐:在 accounts.json 里给每条记录加 owner_admin_id 字段
[
  {"email": "a@x.com", "owner_admin_id": "abc12345", ...},
  {"email": "b@y.com", "owner_admin_id": "abc12345", ...},
  {"email": "c@z.com", "owner_admin_id": "def67890", ...},  # 不同 owner 的账号混在同一文件
]
```

问题:删除 owner 要扫全表;`monkeypatch` 测试时所有 owner 共享同一份 mock 数据;Docker 卷不变但语义上单文件被多 owner 共享,排查脏数据时谁是谁的难分清。

---

## 完全隔离的资产池：独立 JSON 而非状态字段

`§常见错误 5`（"不要新建第二份 JSON"）仍是默认规则。但当三条特征**全部**满足时，**应当**新开独立 JSON：

1. **命令链路互不可见**——"零回归"硬要求，如 `cmd_check / cmd_rotate / cmd_fill / cmd_cleanup` 不得误触发新池流程
2. **字段集差异显著**——新池字段（如 `team_residue` / `last_quota` / `mail_account_id`）只在新流程消费，混入旧 JSON 增加 schema 噪音
3. **数据生命周期不同**——新池纯静态用户驱动（无后台轮询），旧池有自动巡检；混在一起会让 `_normalize_record` / 状态机制相互干扰

### 真例：FREE 池与 Plus 池（task `04-29-free-account-generator` / `04-30-plus-account-pool-oauth`）

```
data/
└── admins/
    └── <admin_id>/
        ├── accounts.json        # active 池
        ├── free_accounts.json   # FREE 池,与 active 池零交集
        └── plus_accounts.json   # Plus 池,与 active / FREE 池零交集
```

**实施约束**：

- 数据层 `load_*/save_*/find_*/add_*/update_*/delete_*` 仿 `accounts.py` 范式，不再造轮子
- `_normalize_record()` 入库时按 schema 补默认值，避免读取处 `KeyError`
- 跨池同步模块（如 `sub2api_sync`）参数化数据来源 + **并集保护**避免互删，参考 `_collect_managed_targets(source: Literal["pool","free","plus"])` + `_collect_all_managed_emails()` / `_collect_active_status_emails()` 取 active + FREE + Plus 三池并集
- PRD 必须显式锁定隔离决策（ADR 形式），不能口头同意

### Scenario: Plus 池 OAuth + sub2api 三池隔离契约

#### 1. Scope / Trigger

- Trigger: 新增用户导入的 Plus 号池，流程跨越 JSON 存储、Codex OAuth、HeroSMS 接码、FastAPI、Vue 页面和 sub2api 同步。
- Scope: Plus 池必须独立于 active 池和 FREE 池；active/FREE 的 `cmd_check`、`cmd_rotate`、`cmd_fill`、`cmd_cleanup` 不得读取或写入 Plus 池。

#### 2. Signatures

- 数据层：`load_plus(admin_id=None) -> list[dict]`、`save_plus(records, admin_id=None) -> None`、`find_plus(records, email) -> dict | None`、`import_plus_account(email, password, admin_id=None) -> dict`、`reauth_plus_account(email, admin_id=None) -> dict`、`check_plus_quota(emails=None, admin_id=None) -> dict[str, str]`、`delete_plus_account(email, admin_id=None, cleanup_remote=True) -> dict`。
- API：`POST /api/plus/import`、`GET /api/plus/list`、`POST /api/plus/check_quota`、`POST /api/plus/sync_sub2api`、`POST /api/plus/{email}/reauth`、`DELETE /api/plus/{email}`。
- sub2api：`sync_to_sub2api(source: Literal["pool","free","plus"], admin_id=None)`、`sync_plus_to_sub2api(admin_id=None)`、`delete_plus_account_from_sub2api(email, auth_names=None)`。

#### 3. Contracts

- 存储路径：当前 admin 写 `data/admins/<admin_id>/plus_accounts.json`；无激活 admin 时只允许回退到 `data/plus_accounts.json` 兼容测试/单实例路径。
- Plus 记录字段：`email`、`password`、`auth_file`、`status`、`plan_type`、`last_error`、`created_at`、`last_login_at`、`last_quota`、`last_quota_at`、`last_sub2api_synced_at`。
- 状态值：`active`、`auth_failed`、`plan_mismatch`、`exhausted`，必须用 `PLUS_STATUS_*` 常量，不散落字符串。
- OAuth：调用 `manager._login_codex_with_result(..., allow_non_team=True)`；手机号验证由 `codex_auth` 检测 `phone-verification` / `add-phone` 后调用 HeroSMS。
- Plus 校验：OAuth 成功后只有 `plan_type in {"plus", "chatgpt_plus"}` 才能保存 auth 文件并进入 `active`；非 Plus 进入 `plan_mismatch`。
- sub2api：Plus 远端必须写 `extra.autoteam_kind="plus"`；active/FREE 仍走 `"pool"`，主号走 `"main"`。删除分支必须用 active + FREE + Plus 三池邮箱并集做保护。
- API 响应：`GET /api/plus/list` 返回明文 `password` 供操作者复制；此端点依赖 API Key 鉴权，日志和前端不得输出密码。

#### 4. Validation & Error Matrix

- 空 email / password -> `POST /api/plus/import` 返回 400；业务函数抛 `ValueError`。
- 同邮箱正在导入或重授权 -> 业务函数抛 `RuntimeError`，避免同一账号并发 OAuth。
- OAuth 失败 -> 记录 `status=auth_failed`、`last_error`，不保存 auth 文件，不自动推 sub2api。
- OAuth 成功但非 Plus -> 记录 `status=plan_mismatch`，不保存 auth 文件，不自动推 sub2api。
- `auth_file` 缺失或读失败 -> sub2api 同步跳过该条 payload；若记录仍是 `active`，删除保护并集仍保留远端账号。
- 删除不存在的 Plus 号 -> API 返回 404；业务层删除函数对缺失记录返回 no-op cleanup。
- sub2api 配置缺失 -> 导入、重授权、手动同步入口在启动任务前返回 400，提示去配置面板补齐。

#### 5. Good/Base/Bad Cases

- Good: 已有 Plus 邮箱/密码导入成功，保存 `codex-*.json`，记录变为 `active`，`sync_plus_to_sub2api()` 创建或更新 `autoteam_kind=plus` 远端。
- Base: active/FREE/Plus 三池有同邮箱或历史远端残留时，同步任一池都只删除“本地曾管理但当前三池都非 active”的远端。
- Bad: 把 Plus 写进 `accounts.json` 或用 `autoteam_kind=pool` 推送，会让 active/FREE 同步误判所有权，后续删除分支可能互删。

#### 6. Tests Required

- `tests/unit/test_plus_accounts.py`：CRUD 默认字段、Plus plan 成功入池、非 Plus plan 拒绝、删除清理 auth_file + sub2api。
- `tests/unit/test_api_plus.py`：API 任务参数、列表响应包含 password、删除缺失记录 404。
- `tests/unit/test_sub2api_sync.py`：`source="plus"` 只读 Plus 池、创建远端写 `autoteam_kind=plus`、三池并集防互删、Plus 删除只命中 plus kind。

#### 7. Wrong vs Correct

```python
# ❌ Wrong:Plus 混入 active 池,并用 pool kind 同步
accounts.add_account(email, password, status=STATUS_ACTIVE)
sync_to_sub2api(source="pool")
```

```python
# ✅ Correct:Plus 独立入池,OAuth 后按 plus kind 同步
result = plus_accounts.import_plus_account(email, password, admin_id=admin_id)
if result["status"] == plus_accounts.PLUS_STATUS_ACTIVE:
    sub2api_sync.sync_plus_to_sub2api(admin_id=admin_id)
```

### 反向：什么时候**不**该拆

- 只是"字段干净"或"日志好看" —— 继续合进现有 JSON
- 命令链路有交集（如新数据需要被 `cmd_cleanup` 处理） —— 加 `pool_kind` 字段更便宜
- 数据生命周期与旧池一致 —— 拆完徒增维护成本

### 与 `index.md §3 持久化优先合并到现有 JSON` 的关系

`index.md §3` 仍然是首选（默认规则）。本节是**例外条款** —— 三条特征全满足才走拆分。如果只满足一两条，仍合进现有 JSON。

---

## 常见错误

1. **直接读 `accounts.json`** 不走 `load_accounts()`——会绕过 BOM 容错和空文件兜底。
2. **新增字段忘了 `.get(default)`**——线上历史账号会爆 `KeyError`。
3. **存 `datetime.now()` 字符串到 JSON**——要么是 `time.time()` 浮点，要么不存。
4. **改 `STATUS_*` 常量值**（如 `"active"` → `"ACTIVE"`）——历史 JSON 里全是旧值，会瞬间让所有账号"消失"。
5. **新建第二份 JSON 文件**（如 `quota.json`）——能合进 `accounts.json` 的字段就合，别拆。**例外见 §"完全隔离的资产池"**——三条特征（命令链路互不可见 / 字段集差异显著 / 生命周期不同）全满足时才走拆分。
6. **多租户/多账号场景给单文件加 `owner_id` 字段而不拆目录**——见上方"多租户/多账号"章节。
7. **自动迁移先 move 后备份**——失败时原文件已丢,无法重试;正确顺序是先 copy 备份、最后才 move + 写新索引。
