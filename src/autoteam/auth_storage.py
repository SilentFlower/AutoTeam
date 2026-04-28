"""认证文件目录与权限辅助。

每个 Team 管理员（"主号"）持有独立的 ``auths/`` 目录，路径为
``data/admins/{admin_id}/auths/``，存放 Codex OAuth 凭据文件
（``codex-main-*.json`` 主号本身、``codex-{email}-*.json`` 子账号）。

兼容性:

- 当调用方不传 ``admin_id`` 时回退到当前激活 admin；若无激活 admin 则使用
  模块级 ``AUTH_DIR`` 作为兜底（项目根 ``auths/`` 目录），便于旧测试与未迁移部署。
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent

# 旧版部署的全局 ``auths/`` 目录；admin_registry.bootstrap_admin_registry 会迁出。
AUTH_DIR = PROJECT_ROOT / "auths"

# Docker bind mount 下文件常由容器用户写入；给宿主机用户保留可读写权限。
AUTH_FILE_MODE = 0o666


def _resolve_admin_id(admin_id: str | None) -> str | None:
    if admin_id:
        return admin_id
    try:
        from autoteam.admin_registry import get_active_admin_id

        return get_active_admin_id()
    except Exception:
        return None


def get_auth_dir(admin_id: str | None = None) -> Path:
    """返回某 admin 的 ``auths/`` 目录路径（不保证存在）。

    - admin_id 显式传入 → ``data/admins/{admin_id}/auths/``。
    - admin_id 为空但有激活 admin → 该 admin 的 auths 目录。
    - 否则 → 模块级 ``AUTH_DIR``（旧路径兜底）。
    """
    resolved = _resolve_admin_id(admin_id)
    if resolved:
        from autoteam.admin_registry import admin_data_dir

        return admin_data_dir(resolved) / "auths"
    return AUTH_DIR


def ensure_auth_dir(admin_id: str | None = None) -> Path:
    """确保 ``auths/`` 目录存在，返回该目录路径。"""
    target = get_auth_dir(admin_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def ensure_auth_file_permissions(filepath: str | Path | None = None, admin_id: str | None = None) -> int:
    """统一修复 auths 目录下认证文件权限。

    :param filepath: 指定单个文件时只修该文件；为空时扫描整个 auths 目录。
    :param admin_id: 目标 admin；缺省回退到激活 admin（不影响 ``filepath`` 显式路径）。
    :return: 实际修改的文件数。
    """
    auth_dir = ensure_auth_dir(admin_id)

    if filepath is None:
        candidates = list(auth_dir.glob("codex-*.json"))
    else:
        candidates = [Path(filepath)]

    updated = 0
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            path.chmod(AUTH_FILE_MODE)
            updated += 1
        except Exception:
            continue
    return updated
