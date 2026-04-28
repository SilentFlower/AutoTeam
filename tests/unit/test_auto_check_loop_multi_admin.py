"""``_auto_check_loop`` 多 admin 化的核心场景测试（PR2 commit 3）。

不模拟完整额度/Team 计数链路，仅验证：

- 注册了 2 个 admin 时，单轮巡检会按顺序触达每个 admin 的 ``accounts.json``。
- 0 admin 场景不抛异常（兼容旧行为：fallback 到无 admin_id 的全局 ``load_accounts``）。
- 巡检中途新增 admin → 不破坏当前轮迭代（快照在循环开始时拍下）。
"""

from __future__ import annotations

import threading

import pytest

from autoteam import admin_registry, api


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
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


@pytest.fixture
def loop_environment(monkeypatch):
    """让 _auto_check_loop 跑一轮就停（interval=0 + wait 第一次返回 timeout 第二次 stop）。"""
    monkeypatch.setattr(api, "_auto_check_config", {"interval": 0, "threshold": 10, "min_low": 99})
    monkeypatch.setattr(api, "_auto_check_stop", threading.Event())
    monkeypatch.setattr(api, "_auto_check_restart", threading.Event())
    monkeypatch.setattr(api, "_maybe_reload_runtime_config_from_env_file", lambda *a, **k: False)

    # _auto_check_wait 第一次返回 "timeout"（进入巡检），第二次返回 "stop" 退出。
    counter = {"n": 0}

    def fake_wait(_seconds):
        counter["n"] += 1
        return "timeout" if counter["n"] == 1 else "stop"

    monkeypatch.setattr(api, "_auto_check_wait", fake_wait)
    # 不实际查 Team count（避免 Playwright probe）
    monkeypatch.setattr(api, "_auto_check_team_member_count", lambda *a, **k: 5)
    # 给 cmd_check 传出来的伪造空数据（manager.py 巡检里用到）
    monkeypatch.setattr("autoteam.manager._count_pool_active_accounts", lambda accounts, **k: 5)
    monkeypatch.setattr("autoteam.manager._pool_active_target", lambda target_seats: target_seats)
    monkeypatch.setattr("autoteam.manager._auth_repair_skip_reason", lambda *a, **k: None)
    monkeypatch.setattr("autoteam.manager.sync_account_states", lambda *a, **k: None)
    return counter


def test_auto_check_loop_iterates_each_admin(isolated_registry, loop_environment, monkeypatch):
    """两个 admin → 单轮巡检按顺序对每个 admin 调一次 load_accounts。"""
    a = admin_registry.add_admin(admin_registry.Admin(admin_id="aaaaaaaa", email="a@example.com"))
    admin_registry.add_admin(admin_registry.Admin(admin_id="bbbbbbbb", email="b@example.com"))
    admin_registry.set_active_admin(a.admin_id)

    visits: list[str | None] = []

    def fake_load_accounts(admin_id=None):
        visits.append(admin_id)
        return []

    monkeypatch.setattr("autoteam.accounts.load_accounts", fake_load_accounts)
    # 短路所有可能跑出去的副作用
    monkeypatch.setattr(api, "_start_task", lambda *a, **k: None)

    api._auto_check_loop()

    # 至少为两个 admin 各调用一次 load_accounts；允许"重新拉本地状态"路径多调几次，
    # 关键是两个 admin 都被访问到。
    assert "aaaaaaaa" in visits
    assert "bbbbbbbb" in visits
    # 顺序：a 先于 b（按 list_admins 顺序）
    assert visits.index("aaaaaaaa") < visits.index("bbbbbbbb")


def test_auto_check_loop_handles_zero_admins_gracefully(isolated_registry, loop_environment, monkeypatch):
    """0 admin（全新部署）→ 兼容模式跑一轮，不抛异常。"""
    # 主动写一个空索引以模拟全新部署后的状态
    admin_registry._write_index(admin_registry._RegistryIndex())
    assert admin_registry.list_admins() == []

    visits: list[str | None] = []

    def fake_load_accounts(admin_id=None):
        visits.append(admin_id)
        return []

    monkeypatch.setattr("autoteam.accounts.load_accounts", fake_load_accounts)
    monkeypatch.setattr(api, "_start_task", lambda *a, **k: None)

    # 不能抛异常
    api._auto_check_loop()

    # 兼容模式：load_accounts 仍被调用一次，admin_id=None
    assert visits == [None]


def test_auto_check_loop_uses_snapshot_so_mid_iteration_admin_addition_waits(
    isolated_registry, loop_environment, monkeypatch
):
    """单轮内迭代到一半时新增 admin → 该 admin 留到下一轮，本轮不被访问。"""
    admin_registry.add_admin(admin_registry.Admin(admin_id="aaaaaaaa", email="a@example.com"))
    admin_registry.add_admin(admin_registry.Admin(admin_id="bbbbbbbb", email="b@example.com"))

    visits: list[str | None] = []

    def fake_load_accounts(admin_id=None):
        visits.append(admin_id)
        # 第一次访问 a 的时候，模拟另一处插入新的 admin C；
        # 期望本轮不访问 C，因为快照已锁定。
        if admin_id == "aaaaaaaa" and "cccccccc" not in {x.admin_id for x in admin_registry.list_admins()}:
            admin_registry.add_admin(admin_registry.Admin(admin_id="cccccccc", email="c@example.com"))
        return []

    monkeypatch.setattr("autoteam.accounts.load_accounts", fake_load_accounts)
    monkeypatch.setattr(api, "_start_task", lambda *a, **k: None)

    api._auto_check_loop()

    visited_ids = set(visits)
    # 本轮访问到 a / b，没访问到 c
    assert "aaaaaaaa" in visited_ids
    assert "bbbbbbbb" in visited_ids
    assert "cccccccc" not in visited_ids
