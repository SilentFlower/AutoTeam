"""admin_registry 模块单元测试。

验证：
- ``Admin`` dataclass 序列化/反序列化。
- ``list_admins`` / ``get_admin`` / ``add_admin`` / ``remove_admin``
  / ``set_active_admin`` / ``update_admin`` 增删改查路径。
- ``get_active_admin_id`` / ``get_active_admin`` 一致性。
- 8 位 admin_id 生成、碰撞重试。
- 索引文件容错（空文件、无效 JSON、缺字段）。
- ``admin_data_dir`` 路径计算正确。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoteam import admin_registry


@pytest.fixture
def tmp_registry(tmp_path, monkeypatch):
    """重写 admin_registry 内的全局路径，把所有文件落到 ``tmp_path``。"""
    data_dir = tmp_path / "data"
    admins_dir = data_dir / "admins"
    monkeypatch.setattr(admin_registry, "DATA_DIR", data_dir)
    monkeypatch.setattr(admin_registry, "ADMINS_DIR", admins_dir)
    monkeypatch.setattr(admin_registry, "ADMINS_INDEX", data_dir / "admins.json")
    # 旧文件路径放到子目录，避免影响真正项目根
    monkeypatch.setattr(admin_registry, "LEGACY_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(admin_registry, "LEGACY_ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(admin_registry, "LEGACY_AUTHS_DIR", tmp_path / "auths")
    monkeypatch.setattr(admin_registry, "LEGACY_BACKUP_DIR", data_dir / "legacy-backup")
    monkeypatch.setattr(admin_registry, "PROJECT_ROOT", tmp_path)
    return tmp_path


def test_admin_to_dict_and_from_dict_round_trip():
    admin = admin_registry.Admin(
        admin_id="abcd1234",
        alias="Team A",
        email="owner@example.com",
        workspace_name="Workspace A",
        account_id="acc-1",
        created_at="2026-04-29T10:00:00Z",
        last_active_at="2026-04-29T11:00:00Z",
    )
    data = admin.to_dict()
    assert data["admin_id"] == "abcd1234"
    assert data["workspace_name"] == "Workspace A"

    restored = admin_registry.Admin.from_dict(data)
    assert restored == admin


def test_admin_from_dict_tolerates_missing_fields():
    admin = admin_registry.Admin.from_dict({"admin_id": "id1"})
    assert admin.admin_id == "id1"
    assert admin.alias == ""
    assert admin.email == ""
    assert admin.workspace_name == ""


def test_list_admins_returns_empty_when_index_missing(tmp_registry):
    assert admin_registry.list_admins() == []
    assert admin_registry.get_active_admin_id() is None
    assert admin_registry.get_active_admin() is None


def test_add_admin_generates_id_and_sets_active_when_first(tmp_registry):
    admin = admin_registry.Admin(admin_id="", email="owner@example.com", workspace_name="Team A")
    saved = admin_registry.add_admin(admin)

    assert len(saved.admin_id) == 8
    assert saved.alias == "Team A"
    assert saved.created_at  # 自动填充时间戳
    assert admin_registry.get_active_admin_id() == saved.admin_id
    assert admin_registry.list_admins() == [saved]
    # 数据目录应存在
    assert (tmp_registry / "data" / "admins" / saved.admin_id).is_dir()


def test_add_admin_keeps_existing_active(tmp_registry):
    first = admin_registry.add_admin(admin_registry.Admin(admin_id="", email="a@example.com"))
    second = admin_registry.add_admin(admin_registry.Admin(admin_id="", email="b@example.com"))

    assert admin_registry.get_active_admin_id() == first.admin_id
    assert {a.admin_id for a in admin_registry.list_admins()} == {first.admin_id, second.admin_id}


def test_add_admin_rejects_duplicate_id(tmp_registry):
    admin_registry.add_admin(admin_registry.Admin(admin_id="aabbccdd", email="a@example.com"))

    with pytest.raises(ValueError, match="admin_id 已存在"):
        admin_registry.add_admin(admin_registry.Admin(admin_id="aabbccdd", email="b@example.com"))


def test_add_admin_alias_falls_back_to_email_local_part(tmp_registry):
    admin = admin_registry.add_admin(admin_registry.Admin(admin_id="", email="user@example.com"))
    assert admin.alias == "user"


def test_add_admin_alias_falls_back_to_admin_id_when_no_email(tmp_registry):
    admin = admin_registry.add_admin(admin_registry.Admin(admin_id="zz112233"))
    assert admin.alias == "zz112233"


def test_get_admin_returns_none_for_missing_id(tmp_registry):
    admin_registry.add_admin(admin_registry.Admin(admin_id="aa000001", email="a@example.com"))
    assert admin_registry.get_admin("not-exist") is None
    assert admin_registry.get_admin("") is None
    assert admin_registry.get_admin(None) is None


def test_set_active_admin_updates_last_active_at(tmp_registry):
    a = admin_registry.add_admin(admin_registry.Admin(admin_id="", email="a@example.com"))
    b = admin_registry.add_admin(admin_registry.Admin(admin_id="", email="b@example.com"))

    switched = admin_registry.set_active_admin(b.admin_id)

    assert switched.admin_id == b.admin_id
    assert switched.last_active_at  # 已写入时间戳
    assert admin_registry.get_active_admin_id() == b.admin_id

    # 切回 a 时 a 也获得 last_active_at
    again = admin_registry.set_active_admin(a.admin_id)
    assert again.last_active_at


def test_set_active_admin_rejects_unknown_id(tmp_registry):
    admin_registry.add_admin(admin_registry.Admin(admin_id="", email="a@example.com"))
    with pytest.raises(ValueError, match="不存在"):
        admin_registry.set_active_admin("nope0001")


def test_remove_admin_reassigns_active(tmp_registry):
    a = admin_registry.add_admin(admin_registry.Admin(admin_id="", email="a@example.com"))
    b = admin_registry.add_admin(admin_registry.Admin(admin_id="", email="b@example.com"))
    admin_registry.set_active_admin(a.admin_id)

    removed = admin_registry.remove_admin(a.admin_id)
    assert removed is True
    assert admin_registry.get_active_admin_id() == b.admin_id
    assert [item.admin_id for item in admin_registry.list_admins()] == [b.admin_id]


def test_remove_admin_returns_false_when_id_unknown(tmp_registry):
    assert admin_registry.remove_admin("nope1234") is False


def test_remove_admin_clears_active_when_no_admin_left(tmp_registry):
    a = admin_registry.add_admin(admin_registry.Admin(admin_id="", email="a@example.com"))
    admin_registry.remove_admin(a.admin_id)
    assert admin_registry.get_active_admin_id() is None
    assert admin_registry.list_admins() == []


def test_update_admin_partial_fields(tmp_registry):
    a = admin_registry.add_admin(admin_registry.Admin(admin_id="", email="a@example.com"))

    updated = admin_registry.update_admin(a.admin_id, alias="新别名", workspace_name="WS-X")
    assert updated is not None
    assert updated.alias == "新别名"
    assert updated.workspace_name == "WS-X"
    # email 未传，保留
    assert updated.email == "a@example.com"

    persisted = admin_registry.get_admin(a.admin_id)
    assert persisted.alias == "新别名"


def test_update_admin_returns_none_for_unknown(tmp_registry):
    assert admin_registry.update_admin("no-id", alias="x") is None
    assert admin_registry.update_admin("", alias="x") is None


def test_index_self_heals_when_active_admin_id_points_to_missing_admin(tmp_registry):
    """``admins.json`` 中 active_admin_id 指向不存在的 id 时应被忽略。"""
    payload = {
        "admins": [{"admin_id": "exists01", "email": "a@example.com"}],
        "active_admin_id": "ghostid1",
    }
    admin_registry.ADMINS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    admin_registry.ADMINS_INDEX.write_text(json.dumps(payload), encoding="utf-8")

    assert admin_registry.get_active_admin_id() is None
    assert [a.admin_id for a in admin_registry.list_admins()] == ["exists01"]


def test_index_handles_corrupt_json(tmp_registry, caplog):
    admin_registry.ADMINS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    admin_registry.ADMINS_INDEX.write_text("not-json", encoding="utf-8")

    with caplog.at_level("ERROR"):
        result = admin_registry.list_admins()

    assert result == []
    assert any("解析" in record.getMessage() for record in caplog.records)


def test_admin_data_dir_returns_expected_path(tmp_registry):
    path = admin_registry.admin_data_dir("abcd1234")
    assert path == tmp_registry / "data" / "admins" / "abcd1234"


def test_admin_data_dir_rejects_empty_id(tmp_registry):
    with pytest.raises(ValueError):
        admin_registry.admin_data_dir("")


def test_generate_admin_id_retries_on_collision(monkeypatch):
    """模拟 uuid 连续返回相同前缀，验证生成器能重试。"""
    sequence = ["aaaaaaaa", "aaaaaaaa", "bbbbbbbb"]

    class _StubUUID:
        def __init__(self, value: str):
            self.hex = value + "00000000"  # 模拟 32 位 hex

    iterator = iter(sequence)

    def _fake_uuid4():
        return _StubUUID(next(iterator))

    monkeypatch.setattr(admin_registry.uuid, "uuid4", _fake_uuid4)

    # 已有 "aaaaaaaa"，应跳过两次后落到 "bbbbbbbb"
    new_id = admin_registry._generate_admin_id({"aaaaaaaa"})
    assert new_id == "bbbbbbbb"


def test_index_round_trip_preserves_fields(tmp_registry):
    admin = admin_registry.add_admin(
        admin_registry.Admin(
            admin_id="",
            email="owner@example.com",
            workspace_name="Workspace 1",
            account_id="acc-uuid",
        )
    )

    raw = json.loads(Path(admin_registry.ADMINS_INDEX).read_text(encoding="utf-8"))
    assert raw["active_admin_id"] == admin.admin_id
    assert raw["admins"][0]["admin_id"] == admin.admin_id
    assert raw["admins"][0]["workspace_name"] == "Workspace 1"
    assert raw["admins"][0]["account_id"] == "acc-uuid"
    assert raw["admins"][0]["created_at"]
