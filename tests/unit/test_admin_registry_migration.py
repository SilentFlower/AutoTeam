"""bootstrap_admin_registry 自动迁移流程测试。

覆盖：
- 旧 ``state.json`` + ``accounts.json`` + ``auths/`` 都存在 → 完整迁移路径。
- 旧 state.json 缺字段（无 email / 无 workspace_name）时 alias 兜底逻辑。
- 中途某一步失败 → 回退（不写半成品 admins.json，原文件保留）。
- 已迁移（admins.json 已存在）时跳过。
- 全新部署（无任何旧文件）→ 写空索引。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoteam import admin_registry


@pytest.fixture
def tmp_layout(tmp_path, monkeypatch):
    """重写 admin_registry 的所有路径常量到 tmp 目录。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(admin_registry, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(admin_registry, "DATA_DIR", data_dir)
    monkeypatch.setattr(admin_registry, "ADMINS_DIR", data_dir / "admins")
    monkeypatch.setattr(admin_registry, "ADMINS_INDEX", data_dir / "admins.json")
    monkeypatch.setattr(admin_registry, "LEGACY_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(admin_registry, "LEGACY_ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(admin_registry, "LEGACY_AUTHS_DIR", tmp_path / "auths")
    monkeypatch.setattr(admin_registry, "LEGACY_BACKUP_DIR", data_dir / "legacy-backup")
    return tmp_path


def _write_legacy(tmp_path: Path, state: dict | None, accounts: list | None, auths_files: dict | None):
    """构造旧版部署的文件结构。"""
    if state is not None:
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    if accounts is not None:
        (tmp_path / "accounts.json").write_text(json.dumps(accounts), encoding="utf-8")
    if auths_files is not None:
        auth_dir = tmp_path / "auths"
        auth_dir.mkdir()
        for name, content in auths_files.items():
            (auth_dir / name).write_text(content, encoding="utf-8")


def test_bootstrap_does_nothing_when_index_exists(tmp_layout):
    """已迁移时直接 return，不重做。"""
    tmp_layout = Path(tmp_layout)
    admin_registry.ADMINS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    admin_registry.ADMINS_INDEX.write_text(
        json.dumps({"admins": [], "active_admin_id": None}),
        encoding="utf-8",
    )
    # 同时放着旧 state.json，验证不会触发迁移
    (tmp_layout / "state.json").write_text(json.dumps({"email": "a@x.com"}), encoding="utf-8")

    result = admin_registry.bootstrap_admin_registry()

    assert result is None
    assert (tmp_layout / "state.json").exists()  # 旧文件依然存在
    # admins.json 内容未被覆盖（没冒出新 admin）
    payload = json.loads(admin_registry.ADMINS_INDEX.read_text(encoding="utf-8"))
    assert payload["admins"] == []


def test_bootstrap_writes_empty_index_for_fresh_deployment(tmp_layout):
    """无旧文件且无 admins.json → 写空索引。"""
    result = admin_registry.bootstrap_admin_registry()

    assert result is None
    payload = json.loads(admin_registry.ADMINS_INDEX.read_text(encoding="utf-8"))
    assert payload == {"admins": [], "active_admin_id": None}


def test_bootstrap_migrates_full_legacy_deployment(tmp_layout):
    """旧 state/accounts/auths 都存在时完整迁移到 data/admins/{id}/。"""
    tmp_layout = Path(tmp_layout)
    _write_legacy(
        tmp_layout,
        state={
            "email": "owner@example.com",
            "session_token": "tok-1",
            "password": "pwd",
            "account_id": "acc-uuid-123",
            "workspace_name": "Workspace A",
        },
        accounts=[{"email": "sub@example.com", "status": "active"}],
        auths_files={
            "codex-main-acc-uuid-123.json": json.dumps({"access_token": "main-tok"}),
            "codex-sub@example.com-team-deadbeef.json": json.dumps({"access_token": "sub-tok"}),
        },
    )

    new_admin_id = admin_registry.bootstrap_admin_registry()

    assert new_admin_id is not None
    assert len(new_admin_id) == 8

    # 1. admins.json 写入正确
    payload = json.loads(admin_registry.ADMINS_INDEX.read_text(encoding="utf-8"))
    assert payload["active_admin_id"] == new_admin_id
    assert len(payload["admins"]) == 1
    admin = payload["admins"][0]
    assert admin["admin_id"] == new_admin_id
    assert admin["email"] == "owner@example.com"
    assert admin["workspace_name"] == "Workspace A"
    assert admin["account_id"] == "acc-uuid-123"
    assert admin["alias"] == "Workspace A"
    assert admin["created_at"]

    # 2. 旧文件已挪走
    assert not (tmp_layout / "state.json").exists()
    assert not (tmp_layout / "accounts.json").exists()
    assert not (tmp_layout / "auths").exists()

    # 3. 新结构存在
    new_dir = admin_registry.ADMINS_DIR / new_admin_id
    assert (new_dir / "state.json").exists()
    moved_state = json.loads((new_dir / "state.json").read_text(encoding="utf-8"))
    assert moved_state["email"] == "owner@example.com"

    assert (new_dir / "accounts.json").exists()
    moved_accounts = json.loads((new_dir / "accounts.json").read_text(encoding="utf-8"))
    assert moved_accounts == [{"email": "sub@example.com", "status": "active"}]

    new_auths = new_dir / "auths"
    assert (new_auths / "codex-main-acc-uuid-123.json").exists()
    assert (new_auths / "codex-sub@example.com-team-deadbeef.json").exists()

    # 4. 备份齐全
    backup_root = admin_registry.LEGACY_BACKUP_DIR
    backups = list(backup_root.iterdir())
    assert len(backups) == 1
    backup_dir = backups[0]
    assert (backup_dir / "state.json").exists()
    assert (backup_dir / "accounts.json").exists()
    assert (backup_dir / "auths" / "codex-main-acc-uuid-123.json").exists()


def test_bootstrap_alias_falls_back_to_email_when_workspace_missing(tmp_layout):
    """旧 state.json 没 workspace_name 时 alias 用邮箱前缀。"""
    tmp_layout = Path(tmp_layout)
    _write_legacy(
        tmp_layout,
        state={"email": "owner@example.com"},
        accounts=None,
        auths_files=None,
    )

    new_admin_id = admin_registry.bootstrap_admin_registry()
    assert new_admin_id is not None

    payload = json.loads(admin_registry.ADMINS_INDEX.read_text(encoding="utf-8"))
    assert payload["admins"][0]["alias"] == "owner"
    assert payload["admins"][0]["workspace_name"] == ""
    assert payload["admins"][0]["account_id"] == ""


def test_bootstrap_alias_falls_back_to_admin_id_when_no_email_or_workspace(tmp_layout):
    """旧 state.json 完全空（如已 logout）→ alias 落到 admin_id。"""
    tmp_layout = Path(tmp_layout)
    _write_legacy(tmp_layout, state={}, accounts=[], auths_files=None)

    new_admin_id = admin_registry.bootstrap_admin_registry()
    assert new_admin_id is not None

    payload = json.loads(admin_registry.ADMINS_INDEX.read_text(encoding="utf-8"))
    assert payload["admins"][0]["alias"] == new_admin_id


def test_bootstrap_handles_corrupt_legacy_state(tmp_layout):
    """旧 state.json 是无效 JSON 时仍能创建 admin（用空字段兜底）。"""
    tmp_layout = Path(tmp_layout)
    (tmp_layout / "state.json").write_text("not-json-at-all", encoding="utf-8")
    (tmp_layout / "accounts.json").write_text("[]", encoding="utf-8")

    new_admin_id = admin_registry.bootstrap_admin_registry()
    assert new_admin_id is not None

    payload = json.loads(admin_registry.ADMINS_INDEX.read_text(encoding="utf-8"))
    assert payload["admins"][0]["email"] == ""
    assert payload["admins"][0]["alias"] == new_admin_id


def test_bootstrap_rolls_back_when_move_fails(tmp_layout, monkeypatch, caplog):
    """模拟 shutil.move 抛错 → 不写 admins.json，原文件保留，备份完整。"""
    tmp_layout = Path(tmp_layout)
    _write_legacy(
        tmp_layout,
        state={"email": "owner@example.com", "workspace_name": "WS"},
        accounts=[{"email": "sub@example.com"}],
        auths_files={"codex-main-x.json": "{}"},
    )

    # 让搬运 accounts.json 那一步炸（在备份完成之后、写索引之前）
    real_move = admin_registry.shutil.move
    call_count = {"n": 0}

    def flaky_move(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 2:  # 第二次（accounts.json）失败
            raise OSError("模拟磁盘错误")
        return real_move(src, dst)

    monkeypatch.setattr(admin_registry.shutil, "move", flaky_move)

    with caplog.at_level("ERROR"):
        result = admin_registry.bootstrap_admin_registry()

    assert result is None
    # 关键：不写 admins.json，下次启动重试
    assert not admin_registry.ADMINS_INDEX.exists()
    # accounts.json 在原位置（搬运失败前没动；至少 state.json 已挪走，但累不到 admins.json）
    assert (tmp_layout / "accounts.json").exists()
    # 备份还在
    assert admin_registry.LEGACY_BACKUP_DIR.exists()
    backups = list(admin_registry.LEGACY_BACKUP_DIR.iterdir())
    assert backups
    assert (backups[0] / "state.json").exists()
    assert (backups[0] / "accounts.json").exists()
    # 半成品 admin 子目录已清理
    admin_dirs = list(admin_registry.ADMINS_DIR.iterdir()) if admin_registry.ADMINS_DIR.exists() else []
    assert admin_dirs == []
    # 错误日志包含中文提示
    assert any("自动迁移失败" in record.getMessage() for record in caplog.records)


def test_bootstrap_only_state_present_no_accounts(tmp_layout):
    """只有 state.json 没有 accounts.json 时也能迁移。"""
    tmp_layout = Path(tmp_layout)
    _write_legacy(
        tmp_layout,
        state={"email": "owner@example.com", "workspace_name": "WS"},
        accounts=None,
        auths_files=None,
    )

    new_admin_id = admin_registry.bootstrap_admin_registry()
    assert new_admin_id is not None

    new_dir = admin_registry.ADMINS_DIR / new_admin_id
    assert (new_dir / "state.json").exists()
    # 没有 accounts.json 时新目录里也不该有
    assert not (new_dir / "accounts.json").exists()


def test_bootstrap_only_accounts_present_no_state(tmp_layout):
    """只有 accounts.json 没有 state.json 时也能产生 admin（字段空）。"""
    tmp_layout = Path(tmp_layout)
    _write_legacy(
        tmp_layout,
        state=None,
        accounts=[{"email": "sub@example.com"}],
        auths_files=None,
    )

    new_admin_id = admin_registry.bootstrap_admin_registry()
    assert new_admin_id is not None

    payload = json.loads(admin_registry.ADMINS_INDEX.read_text(encoding="utf-8"))
    assert payload["admins"][0]["email"] == ""
    assert payload["admins"][0]["alias"] == new_admin_id

    new_dir = admin_registry.ADMINS_DIR / new_admin_id
    assert (new_dir / "accounts.json").exists()


def test_bootstrap_does_not_recreate_index_after_partial_failure_then_success(tmp_layout, monkeypatch):
    """迁移失败后再次调用应能继续重试（仍能成功）。"""
    tmp_layout = Path(tmp_layout)
    _write_legacy(
        tmp_layout,
        state={"email": "owner@example.com", "workspace_name": "WS"},
        accounts=[{"email": "sub@example.com"}],
        auths_files=None,
    )

    real_move = admin_registry.shutil.move
    fail_once = {"done": False}

    def flaky_move(src, dst):
        if not fail_once["done"]:
            fail_once["done"] = True
            raise OSError("一次性故障")
        return real_move(src, dst)

    monkeypatch.setattr(admin_registry.shutil, "move", flaky_move)
    first = admin_registry.bootstrap_admin_registry()
    assert first is None
    assert not admin_registry.ADMINS_INDEX.exists()

    # 第二次调用使用真正的 move，应当成功
    second = admin_registry.bootstrap_admin_registry()
    assert second is not None
    assert admin_registry.ADMINS_INDEX.exists()


def test_bootstrap_index_has_correct_active_admin_id(tmp_layout):
    """全 state/accounts 一起迁移时 active_admin_id 必须等于新 admin 的 id。"""
    tmp_layout = Path(tmp_layout)
    _write_legacy(
        tmp_layout,
        state={"email": "owner@example.com"},
        accounts=[],
        auths_files=None,
    )

    new_admin_id = admin_registry.bootstrap_admin_registry()
    assert new_admin_id is not None

    payload = json.loads(admin_registry.ADMINS_INDEX.read_text(encoding="utf-8"))
    assert payload["active_admin_id"] == new_admin_id
    assert payload["admins"][0]["last_active_at"]  # 自动设置
