"""``/api/admins/*`` 路由的单元测试（PR2）。

覆盖：
- GET ``/api/admins`` 列表 / GET ``/api/admins/active``。
- POST ``/api/admins/active`` 切换激活。
- DELETE ``/api/admins/{id}``：成功删除、唯一 admin 拒删、不存在 404、id 非法 400。
- ``get_current_admin_id`` 依赖：header 透传 / 缺省回退 / 非法 header 400。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from autoteam import admin_registry, api


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """让 admin_registry 模块的所有路径写到 ``tmp_path`` 下，互不影响。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(admin_registry, "DATA_DIR", data_dir)
    monkeypatch.setattr(admin_registry, "ADMINS_DIR", data_dir / "admins")
    monkeypatch.setattr(admin_registry, "ADMINS_INDEX", data_dir / "admins.json")
    monkeypatch.setattr(admin_registry, "LEGACY_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(admin_registry, "LEGACY_ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(admin_registry, "LEGACY_AUTHS_DIR", tmp_path / "auths")
    monkeypatch.setattr(admin_registry, "LEGACY_BACKUP_DIR", data_dir / "legacy-backup")
    monkeypatch.setattr(admin_registry, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _add_two_admins() -> tuple[admin_registry.Admin, admin_registry.Admin]:
    a = admin_registry.add_admin(admin_registry.Admin(admin_id="aaaaaaaa", email="a@example.com"))
    b = admin_registry.add_admin(admin_registry.Admin(admin_id="bbbbbbbb", email="b@example.com"))
    return a, b


def test_get_admins_lists_all_with_active_flag(isolated_registry):
    a, b = _add_two_admins()
    admin_registry.set_active_admin(b.admin_id)

    response = api.get_admins()

    assert response["active_admin_id"] == "bbbbbbbb"
    ids = [item["admin_id"] for item in response["admins"]]
    assert ids == ["aaaaaaaa", "bbbbbbbb"]
    flags = {item["admin_id"]: item["is_active"] for item in response["admins"]}
    assert flags == {"aaaaaaaa": False, "bbbbbbbb": True}


def test_get_active_admin_returns_null_when_no_admins(isolated_registry):
    # 主动写一个空索引以模拟全新部署后的状态
    admin_registry._write_index(admin_registry._RegistryIndex())

    response = api.get_active_admin()
    assert response == {"admin": None, "active_admin_id": None}


def test_post_switch_active_admin_updates_active(isolated_registry):
    a, b = _add_two_admins()
    admin_registry.set_active_admin(a.admin_id)
    assert admin_registry.get_active_admin_id() == "aaaaaaaa"

    response = api.post_switch_active_admin(api.SwitchAdminParams(admin_id="bbbbbbbb"))
    assert response["active_admin_id"] == "bbbbbbbb"
    assert admin_registry.get_active_admin_id() == "bbbbbbbb"


def test_post_switch_active_admin_rejects_path_traversal_id(isolated_registry):
    _add_two_admins()
    with pytest.raises(HTTPException) as exc:
        api.post_switch_active_admin(api.SwitchAdminParams(admin_id="../../../etc"))
    assert exc.value.status_code == 400
    assert "管理员标识" in str(exc.value.detail)


def test_post_switch_active_admin_returns_404_when_id_unknown(isolated_registry):
    _add_two_admins()
    with pytest.raises(HTTPException) as exc:
        api.post_switch_active_admin(api.SwitchAdminParams(admin_id="deadbeef"))
    assert exc.value.status_code == 404


def test_delete_admin_removes_index_entry_and_data_dir(isolated_registry, monkeypatch):
    a, b = _add_two_admins()
    admin_registry.set_active_admin(a.admin_id)
    # 创建一些数据让 rmtree 真的有事可做
    data_dir = admin_registry.admin_data_dir(b.admin_id)
    (data_dir / "state.json").write_text("{}", encoding="utf-8")

    # 跳过远端清理，避免依赖网络/Sub2API
    monkeypatch.setattr("autoteam.sync_targets.delete_account_from_configured_targets", lambda *a, **k: {})

    response = api.delete_admin(b.admin_id)
    assert response["deleted_admin_id"] == "bbbbbbbb"
    assert response["active_admin_id"] == "aaaaaaaa"
    assert admin_registry.get_admin("bbbbbbbb") is None
    assert not data_dir.exists()


def test_delete_admin_refuses_when_only_one_left(isolated_registry):
    only = admin_registry.add_admin(admin_registry.Admin(admin_id="cccccccc", email="solo@example.com"))
    with pytest.raises(HTTPException) as exc:
        api.delete_admin(only.admin_id)
    assert exc.value.status_code == 400
    assert "唯一" in str(exc.value.detail)
    # 索引里还在
    assert admin_registry.get_admin("cccccccc") is not None


def test_delete_admin_returns_404_for_unknown_id(isolated_registry):
    _add_two_admins()
    with pytest.raises(HTTPException) as exc:
        api.delete_admin("deadbeef")
    assert exc.value.status_code == 404


def test_delete_admin_rejects_path_traversal(isolated_registry):
    _add_two_admins()
    with pytest.raises(HTTPException) as exc:
        api.delete_admin("../etc")
    assert exc.value.status_code == 400


def test_get_current_admin_id_uses_header_when_present(isolated_registry):
    _add_two_admins()
    assert api.get_current_admin_id(x_autoteam_admin_id="aaaaaaaa") == "aaaaaaaa"


def test_get_current_admin_id_falls_back_to_active_when_header_missing(isolated_registry):
    _, b = _add_two_admins()
    admin_registry.set_active_admin(b.admin_id)
    assert api.get_current_admin_id(x_autoteam_admin_id=None) == "bbbbbbbb"


def test_get_current_admin_id_rejects_invalid_header(isolated_registry):
    _add_two_admins()
    with pytest.raises(HTTPException) as exc:
        # 路径穿越尝试 → 白名单拦截
        api.get_current_admin_id(x_autoteam_admin_id="../../etc")
    assert exc.value.status_code == 400


def test_get_current_admin_id_strips_whitespace(isolated_registry):
    a, _ = _add_two_admins()
    assert api.get_current_admin_id(x_autoteam_admin_id="  aaaaaaaa  ") == "aaaaaaaa"
