"""管理员注册表 - 多管理员主号支持的核心抽象层。

本模块负责维护 ``data/admins.json`` 索引文件，记录所有已登录的 Team 管理员
（即"主号"）以及当前激活的管理员。每个 admin 拥有独立的 8 位 ``admin_id`` 与
独立的数据目录（``data/admins/{admin_id}/``）。

数据布局（PRD 决策 #3）:

::

    data/
    ├── admins.json                     # 索引文件
    └── admins/
        └── {admin_id}/
            ├── state.json              # 管理员登录态
            ├── accounts.json           # 子账号池
            └── auths/
                └── codex-main-*.json   # 主号 Codex 凭据

设计要点:

- 提供 `Admin` dataclass 与列表/查询/写入函数。
- ``active_admin_id`` 持久化在 ``admins.json`` 中，服务重启后能记得用户最后
  选过的 admin（PRD 决策 #1：切换激活模型）。
- 8 位 admin_id 通过 ``uuid.uuid4().hex[:8]`` 生成，碰撞时自动重试。
- 模块只做"索引文件 + 目录路径"层面的事，不涉及具体业务读写（业务层在
  `admin_state.py` / `accounts.py` / `codex_auth.py` 中按 admin_id 分流）。

兼容性:

- 不做任何 import 时副作用（除了模块级 logger）；自动迁移由
  `bootstrap_admin_registry()` 在 FastAPI startup 显式触发。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoteam.textio import read_text, write_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent

# `data/` 是按 admin 隔离的根目录；不存在则视为全新部署。
DATA_DIR = PROJECT_ROOT / "data"
ADMINS_INDEX = DATA_DIR / "admins.json"
ADMINS_DIR = DATA_DIR / "admins"

# 旧版部署的单实例文件（迁移源）
LEGACY_STATE_FILE = PROJECT_ROOT / "state.json"
LEGACY_ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"
LEGACY_AUTHS_DIR = PROJECT_ROOT / "auths"
LEGACY_BACKUP_DIR = DATA_DIR / "legacy-backup"

# 与 admin_state.STATE_FILE_MODE 保持一致，避免容器/宿主权限不一致。
ADMIN_DATA_FILE_MODE = 0o666
ADMIN_DATA_DIR_MODE = 0o777

_INDEX_FILENAME = "admins.json"

# admin_id 白名单：8 位小写十六进制（PRD 决策 #9）。
# 用于阻止外部传入的非法 admin_id 触发路径穿越（如 ``../../etc``）。
_ADMIN_ID_PATTERN = re.compile(r"^[0-9a-f]{8}$")


def _validate_admin_id(admin_id: str) -> str:
    """对外部传入的 admin_id 做白名单校验。

    :param admin_id: 待校验字符串。
    :return: 通过校验的 admin_id。
    :raises ValueError: 格式不合法时抛出，调用方需翻译成 4xx HTTP 响应。
    """
    if not isinstance(admin_id, str) or not _ADMIN_ID_PATTERN.match(admin_id):
        raise ValueError(f"admin_id 必须是 8 位小写十六进制：{admin_id!r}")
    return admin_id


@dataclass
class Admin:
    """管理员（Team 主号）元信息。

    :param admin_id: 8 位 hex 字符串，主键。
    :param alias: 用户可见别名，默认从 ``workspace_name`` 或 email 前缀派生。
    :param email: 管理员邮箱（登录账号）。
    :param workspace_name: ChatGPT Team workspace 名称。
    :param account_id: ChatGPT account_id（UUID 格式），即 ``_account`` cookie 的值。
    :param created_at: ISO 8601 字符串，admin 首次登录时间。
    :param last_active_at: ISO 8601 字符串，最近一次被激活的时间；从未激活过为空串。
    """

    admin_id: str
    alias: str = ""
    email: str = ""
    workspace_name: str = ""
    account_id: str = ""
    created_at: str = ""
    last_active_at: str = ""

    def to_dict(self) -> dict[str, str]:
        """序列化为可直接写入 JSON 的 dict。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Admin:
        """从 JSON dict 构造实例，对缺失字段使用空值兜底（向后兼容）。"""
        return cls(
            admin_id=str(data.get("admin_id", "") or ""),
            alias=str(data.get("alias", "") or ""),
            email=str(data.get("email", "") or ""),
            workspace_name=str(data.get("workspace_name", "") or ""),
            account_id=str(data.get("account_id", "") or ""),
            created_at=str(data.get("created_at", "") or ""),
            last_active_at=str(data.get("last_active_at", "") or ""),
        )


@dataclass
class _RegistryIndex:
    """``admins.json`` 索引文件的内存表示。"""

    admins: list[Admin] = field(default_factory=list)
    active_admin_id: str | None = None


# ---------------------------------------------------------------------------
# 路径帮助函数
# ---------------------------------------------------------------------------


def admin_data_dir(admin_id: str) -> Path:
    """返回某 admin 的数据目录路径（不保证存在）。

    :param admin_id: 8 位 admin_id。
    :return: ``data/admins/{admin_id}/`` 的 ``Path`` 对象。
    :raises ValueError: admin_id 非法（非 8 位 hex）时抛出，避免路径穿越。
    """
    if not admin_id:
        raise ValueError("admin_id 不能为空")
    _validate_admin_id(admin_id)
    return ADMINS_DIR / admin_id


def _ensure_admin_dir(admin_id: str) -> Path:
    """确保某 admin 的数据目录存在并赋予合适权限。"""
    target = admin_data_dir(admin_id)
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target, ADMIN_DATA_DIR_MODE)
    except Exception:
        # 某些容器内 chmod 失败可忽略；目录已经创建就够用。
        pass
    return target


def _now_iso() -> str:
    """当前时间的 ISO 8601 字符串（秒精度，UTC 风格）。"""
    return datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _backup_timestamp() -> str:
    """生成 legacy-backup 子目录名（``YYYYMMDD-HHMMSS``，本地时区）。"""
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


# ---------------------------------------------------------------------------
# 索引文件 IO
# ---------------------------------------------------------------------------


def _normalize_index(raw: Any) -> _RegistryIndex:
    """容错地把 JSON 解析结果归一化为 ``_RegistryIndex``。

    旧版本字段缺失/类型异常时使用空值兜底，不抛异常（向后兼容原则）。
    """
    if not isinstance(raw, dict):
        return _RegistryIndex(admins=[], active_admin_id=None)

    admins_raw = raw.get("admins") or []
    admins: list[Admin] = []
    if isinstance(admins_raw, list):
        for item in admins_raw:
            if isinstance(item, dict) and item.get("admin_id"):
                admins.append(Admin.from_dict(item))

    active = raw.get("active_admin_id")
    if active is not None:
        active = str(active) or None

    # 自我修复：active 不在列表中时清空，避免上层逻辑用一个不存在的 id。
    if active and not any(a.admin_id == active for a in admins):
        active = None

    return _RegistryIndex(admins=admins, active_admin_id=active)


def _read_index() -> _RegistryIndex:
    """读取 ``admins.json``。文件不存在时返回空索引（不写盘）。"""
    if not ADMINS_INDEX.exists():
        return _RegistryIndex()
    try:
        text = read_text(ADMINS_INDEX).strip()
    except Exception as exc:  # pragma: no cover - 防御
        logger.error("[Admin] 读取 %s 失败: %s", ADMINS_INDEX, exc)
        return _RegistryIndex()
    if not text:
        return _RegistryIndex()
    try:
        return _normalize_index(json.loads(text))
    except Exception as exc:
        logger.error("[Admin] 解析 %s 失败: %s", ADMINS_INDEX, exc)
        return _RegistryIndex()


def _write_index(index: _RegistryIndex) -> None:
    """原子地把索引写入 ``admins.json``，并修正文件权限。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, ADMIN_DATA_DIR_MODE)
    except Exception:
        pass

    payload = {
        "admins": [admin.to_dict() for admin in index.admins],
        "active_admin_id": index.active_admin_id,
    }
    write_text(ADMINS_INDEX, json.dumps(payload, indent=2, ensure_ascii=False))
    try:
        os.chmod(ADMINS_INDEX, ADMIN_DATA_FILE_MODE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 公开查询/操作 API
# ---------------------------------------------------------------------------


def list_admins() -> list[Admin]:
    """返回所有已注册的 admin 列表。"""
    return list(_read_index().admins)


def get_admin(admin_id: str | None) -> Admin | None:
    """按 admin_id 精确查找。

    :param admin_id: 目标 admin_id；为 ``None`` 或空串时直接返回 ``None``。
    """
    if not admin_id:
        return None
    for admin in _read_index().admins:
        if admin.admin_id == admin_id:
            return admin
    return None


def get_active_admin_id() -> str | None:
    """返回当前激活 admin 的 id；若无任何 admin 或未设置激活值则返回 ``None``。"""
    return _read_index().active_admin_id


def get_active_admin() -> Admin | None:
    """返回当前激活 admin 完整对象。"""
    index = _read_index()
    if not index.active_admin_id:
        return None
    for admin in index.admins:
        if admin.admin_id == index.active_admin_id:
            return admin
    return None


def add_admin(admin: Admin) -> Admin:
    """新增 admin 到索引。

    若调用方传入的 ``admin_id`` 为空，自动生成一个不冲突的 8 位 id；
    若传入的 ``admin_id`` 已存在，抛 ``ValueError``。
    若当前没有 active admin，把新加入的设为激活。
    自动确保 admin 数据目录存在。
    """
    index = _read_index()
    existing_ids = {a.admin_id for a in index.admins}

    if not admin.admin_id:
        admin.admin_id = _generate_admin_id(existing_ids)
    else:
        # 外部传入的 admin_id 必须先过白名单（防止路径穿越）。
        _validate_admin_id(admin.admin_id)
        if admin.admin_id in existing_ids:
            raise ValueError(f"admin_id 已存在: {admin.admin_id}")

    if not admin.alias:
        admin.alias = _derive_alias(admin)
    if not admin.created_at:
        admin.created_at = _now_iso()

    index.admins.append(admin)
    if not index.active_admin_id:
        index.active_admin_id = admin.admin_id

    _ensure_admin_dir(admin.admin_id)
    _write_index(index)
    logger.info("[Admin] 新增 admin: %s (%s)", admin.admin_id, admin.alias or admin.email or "<未命名>")
    return admin


def remove_admin(admin_id: str) -> bool:
    """从索引中删除指定 admin。

    若被删的是当前 active，则自动切到列表中第一个剩余 admin（若有）。
    返回是否真的删除了一条记录。**注意**：本函数只动索引和不删数据目录；
    调用方需要清理 ``data/admins/{admin_id}/`` 时请显式 `shutil.rmtree`。
    """
    if not admin_id:
        return False
    index = _read_index()
    before = len(index.admins)
    index.admins = [a for a in index.admins if a.admin_id != admin_id]
    if len(index.admins) == before:
        return False

    if index.active_admin_id == admin_id:
        index.active_admin_id = index.admins[0].admin_id if index.admins else None

    _write_index(index)
    logger.info("[Admin] 删除 admin: %s", admin_id)
    return True


def set_active_admin(admin_id: str) -> Admin:
    """切换当前激活 admin，返回新的激活 admin 对象。

    若 ``admin_id`` 不存在，抛 ``ValueError``。
    """
    if not admin_id:
        raise ValueError("admin_id 不能为空")
    _validate_admin_id(admin_id)
    index = _read_index()
    target = next((a for a in index.admins if a.admin_id == admin_id), None)
    if target is None:
        raise ValueError(f"admin_id 不存在: {admin_id}")

    target.last_active_at = _now_iso()
    index.active_admin_id = admin_id
    _write_index(index)
    logger.info("[Admin] 切换激活 admin: %s (%s)", admin_id, target.alias or target.email or "<未命名>")
    return target


def update_admin(admin_id: str, **fields: Any) -> Admin | None:
    """局部更新 admin 字段（alias/email/workspace_name/account_id）。

    :param admin_id: 目标 admin_id。
    :param fields: 待更新字段。未列出的保留原值。
    :return: 更新后的 admin 对象；admin 不存在时返回 ``None``。
    """
    if not admin_id:
        return None
    index = _read_index()
    for admin in index.admins:
        if admin.admin_id != admin_id:
            continue
        for key in ("alias", "email", "workspace_name", "account_id"):
            if key in fields and fields[key] is not None:
                setattr(admin, key, str(fields[key]))
        _write_index(index)
        return admin
    return None


# ---------------------------------------------------------------------------
# admin_id 生成
# ---------------------------------------------------------------------------


def _generate_admin_id(existing: set[str]) -> str:
    """生成一个不与 ``existing`` 冲突的 8 位 hex admin_id。

    使用 ``uuid.uuid4().hex[:8]``，碰撞概率极低；做最多 100 次重试以防理论碰撞。
    """
    for _ in range(100):
        candidate = uuid.uuid4().hex[:8]
        if candidate not in existing:
            return candidate
    # 进入此分支说明 RNG 异常或 existing 已超 ~10^9 量级，二者都不应发生
    raise RuntimeError("生成 admin_id 失败：100 次连续碰撞")  # pragma: no cover - 防御


def _derive_alias(admin: Admin) -> str:
    """alias 默认 = workspace_name，其次 = email '@' 之前部分，最后 = admin_id。"""
    if admin.workspace_name:
        return admin.workspace_name
    if admin.email and "@" in admin.email:
        return admin.email.split("@", 1)[0]
    if admin.email:
        return admin.email
    return admin.admin_id


# ---------------------------------------------------------------------------
# 首次启动自动迁移
# ---------------------------------------------------------------------------


def bootstrap_admin_registry() -> str | None:
    """检测旧的单实例文件并迁移到新结构。

    触发条件:

    - ``data/admins.json`` 不存在 **且** ``state.json`` / ``accounts.json``
      之一存在 → 真正执行迁移。
    - ``data/admins.json`` 已存在 → 视为已迁移，直接 return。
    - 旧文件全都不存在且 ``data/admins.json`` 也没有 → 视为全新部署，
      写一份空索引（``{"admins": [], "active_admin_id": null}``）。

    迁移步骤（PRD research 第 3.2 节）:

    1. 备份旧文件到 ``data/legacy-backup/{timestamp}/``。
    2. 从旧 ``state.json`` 读 email / workspace_name / account_id 生成 ``Admin``。
    3. 移动 ``state.json`` / ``accounts.json`` / ``auths/`` 到 ``data/admins/{admin_id}/``。
    4. 写新的 ``admins.json`` 索引（active_admin_id = 新 admin_id）。

    任何步骤失败时:

    - 不写半成品 ``admins.json``；
    - 不删原始旧文件（备份目录里的副本 + 原位置都保留，让用户能恢复）；
    - 打印中文 error 日志 + 完整 traceback。

    :return: 迁移后产生的 admin_id；若无须迁移或迁移失败则返回 ``None``。
    """
    # 已迁移：跳过
    if ADMINS_INDEX.exists():
        return None

    legacy_state_exists = LEGACY_STATE_FILE.exists()
    legacy_accounts_exists = LEGACY_ACCOUNTS_FILE.exists()
    legacy_auths_exists = LEGACY_AUTHS_DIR.exists() and LEGACY_AUTHS_DIR.is_dir()

    # 全新部署：写空索引就够
    if not (legacy_state_exists or legacy_accounts_exists or legacy_auths_exists):
        try:
            _write_index(_RegistryIndex())
            logger.info("[Admin] 全新部署，已初始化空 admins.json")
        except Exception as exc:
            logger.error("[Admin] 初始化空 admins.json 失败: %s", exc, exc_info=True)
        return None

    # 真正的迁移流程
    backup_dir = LEGACY_BACKUP_DIR / _backup_timestamp()
    new_admin_dir: Path | None = None
    try:
        # 1. 备份
        backup_dir.mkdir(parents=True, exist_ok=True)
        if legacy_state_exists:
            shutil.copy2(LEGACY_STATE_FILE, backup_dir / "state.json")
        if legacy_accounts_exists:
            shutil.copy2(LEGACY_ACCOUNTS_FILE, backup_dir / "accounts.json")
        if legacy_auths_exists:
            shutil.copytree(LEGACY_AUTHS_DIR, backup_dir / "auths", dirs_exist_ok=True)

        # 2. 解析旧 state，组装 Admin
        legacy_state = _read_legacy_state(LEGACY_STATE_FILE) if legacy_state_exists else {}
        admin_id = _generate_admin_id(set())
        admin = Admin(
            admin_id=admin_id,
            email=str(legacy_state.get("email", "") or ""),
            workspace_name=str(legacy_state.get("workspace_name", "") or ""),
            account_id=str(legacy_state.get("account_id", "") or ""),
            created_at=_now_iso(),
            last_active_at=_now_iso(),
        )
        admin.alias = _derive_alias(admin)

        # 3. 移动文件到新目录
        new_admin_dir = _ensure_admin_dir(admin_id)
        if legacy_state_exists:
            _move_into(LEGACY_STATE_FILE, new_admin_dir / "state.json")
        if legacy_accounts_exists:
            _move_into(LEGACY_ACCOUNTS_FILE, new_admin_dir / "accounts.json")
        if legacy_auths_exists:
            _move_dir_into(LEGACY_AUTHS_DIR, new_admin_dir / "auths")

        # 4. 写索引
        index = _RegistryIndex(admins=[admin], active_admin_id=admin_id)
        _write_index(index)

        logger.info(
            "[Admin] 已迁移单实例数据到新结构: admin_id=%s alias=%s 备份目录=%s",
            admin_id,
            admin.alias,
            backup_dir,
        )
        return admin_id
    except Exception as exc:
        logger.error(
            "[Admin] 自动迁移失败: %s。原始文件保留在 %s 与原位置；备份在 %s。",
            exc,
            PROJECT_ROOT,
            backup_dir,
            exc_info=True,
        )
        # 清掉半成品的 admin 子目录（备份还在），但**不**写 admins.json，下次启动重试
        if new_admin_dir and new_admin_dir.exists():
            try:
                shutil.rmtree(new_admin_dir)
            except Exception as cleanup_exc:  # pragma: no cover - 防御
                logger.warning("[Admin] 清理半成品目录失败: %s", cleanup_exc)
        return None


def _read_legacy_state(path: Path) -> dict[str, Any]:
    """读取旧 state.json 文件。失败时返回空 dict 而不是抛异常。"""
    try:
        text = read_text(path).strip()
    except Exception:
        return {}
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _move_into(src: Path, dst: Path) -> None:
    """``shutil.move`` 兼容包装，目标已存在时先删除避免 cross-device 问题。"""
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.move(str(src), str(dst))


def _move_dir_into(src: Path, dst: Path) -> None:
    """移动目录：跨文件系统时 shutil.move 自动 fallback 到 copy+rm。"""
    if dst.exists():
        # 目标存在则把源里的文件挪进去后删除源
        for child in src.iterdir():
            target = dst / child.name
            _move_into(child, target)
        try:
            src.rmdir()
        except OSError:
            shutil.rmtree(src, ignore_errors=True)
        return
    shutil.move(str(src), str(dst))
