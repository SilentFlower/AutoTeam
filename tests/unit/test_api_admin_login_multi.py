"""``/api/admins/login/*`` 多管理员登录路由的关键场景测试（PR2）。

仅覆盖以下核心 case，不追求 happy-path 全覆盖：
- 创建新 admin 流程（不传 ``target_admin_id``）。
- 为已存在的 admin 重新登录（传 ``target_admin_id``）。
- 同时两个 admin 登录请求 → 第二个被全局 Playwright lock 串行拒绝。
"""

from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

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


class _StubChatGPTAPI:
    """伪 ChatGPT 登录引擎：直接进入 password_required 等待状态。"""

    workspace_options_cache: list = []

    def begin_admin_login(self, email: str):
        self.email = email
        return {"step": "password_required", "detail": ""}

    def stop(self):
        pass


@pytest.fixture
def fake_login_engine(monkeypatch):
    """给所有 ChatGPT 调用注入伪实现 + 让 _pw_executor 直接同步执行。"""
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args, **kwargs))
    monkeypatch.setattr(api, "_playwright_lock", threading.Lock())
    monkeypatch.setattr(
        "autoteam.chatgpt_api.ChatGPTTeamAPI",
        lambda: _StubChatGPTAPI(),
    )

    monkeypatch.setattr(
        "autoteam.admin_state.get_admin_state_summary",
        lambda admin_id=None: {
            "configured": False,
            "email": "",
            "password_saved": False,
            "session_present": False,
            "account_id": "",
            "workspace_name": "",
            "updated_at": None,
        },
    )

    yield

    # 测试结束后清理全局
    api._clear_admin_login_session()


def test_login_start_creates_new_admin_when_no_target(isolated_registry, fake_login_engine):
    """不传 target_admin_id：在 admins.json 里预登记一条新 admin。"""
    response = api.post_admins_login_start(api.AdminLoginStartParams(email="newadmin@example.com"))

    assert response["status"] == "password_required"
    admins = admin_registry.list_admins()
    assert len(admins) == 1
    assert admins[0].email == "newadmin@example.com"
    # 预登记的 admin 也成了 active（add_admin 第一条自动激活）
    assert admin_registry.get_active_admin_id() == admins[0].admin_id
    # 在飞会话的 target 指向新建 admin
    assert api._admin_login_target == admins[0].admin_id


def test_login_start_for_existing_admin_switches_active(isolated_registry, fake_login_engine):
    """传 target_admin_id：切到该 admin 而不是新建。"""
    a = admin_registry.add_admin(admin_registry.Admin(admin_id="aaaaaaaa", email="a@example.com"))
    b = admin_registry.add_admin(admin_registry.Admin(admin_id="bbbbbbbb", email="b@example.com"))
    admin_registry.set_active_admin(a.admin_id)
    assert admin_registry.get_active_admin_id() == "aaaaaaaa"

    response = api.post_admins_login_start(api.AdminLoginStartParams(email=b.email, target_admin_id="bbbbbbbb"))

    assert response["status"] == "password_required"
    # 切了 active 但没新增 admin
    assert admin_registry.get_active_admin_id() == "bbbbbbbb"
    assert len(admin_registry.list_admins()) == 2
    assert api._admin_login_target == "bbbbbbbb"


def test_login_start_rejects_invalid_target_admin_id(isolated_registry, fake_login_engine):
    admin_registry.add_admin(admin_registry.Admin(admin_id="aaaaaaaa", email="a@example.com"))
    with pytest.raises(HTTPException) as exc:
        api.post_admins_login_start(api.AdminLoginStartParams(email="x@example.com", target_admin_id="../../passwd"))
    assert exc.value.status_code == 400


def test_login_start_returns_404_when_target_admin_unknown(isolated_registry, fake_login_engine):
    admin_registry.add_admin(admin_registry.Admin(admin_id="aaaaaaaa", email="a@example.com"))
    with pytest.raises(HTTPException) as exc:
        api.post_admins_login_start(api.AdminLoginStartParams(email="x@example.com", target_admin_id="deadbeef"))
    assert exc.value.status_code == 404


def test_two_concurrent_logins_serialize_via_global_lock(isolated_registry, fake_login_engine):
    """全局 Playwright lock 已被占用（如其他任务在跑）时，登录启动请求应被 409 拒绝。

    路由设计上，新一轮 ``/api/admins/login/start`` 在自己持锁前会先 stop 当前在跑的
    登录会话；但若锁被**别的任务**占着（rotate / kick 等），则锁串行机制会把
    第二个请求挡在 409 上，避免两个 admin 同时压垮 Playwright。
    """
    a = admin_registry.add_admin(admin_registry.Admin(admin_id="aaaaaaaa", email="a@example.com"))
    admin_registry.add_admin(admin_registry.Admin(admin_id="bbbbbbbb", email="b@example.com"))

    # 模拟另一种任务占住全局锁（不是登录会话本身）。
    api._playwright_lock.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as exc:
            api.post_admins_login_start(api.AdminLoginStartParams(email=a.email, target_admin_id="aaaaaaaa"))
        assert exc.value.status_code == 409
    finally:
        api._playwright_lock.release()


def test_password_request_with_mismatched_target_returns_409(isolated_registry, fake_login_engine):
    """提交密码时 target_admin_id 与在飞会话不一致 → 409。"""
    admin_registry.add_admin(admin_registry.Admin(admin_id="aaaaaaaa", email="a@example.com"))
    admin_registry.add_admin(admin_registry.Admin(admin_id="bbbbbbbb", email="b@example.com"))

    api.post_admins_login_start(api.AdminLoginStartParams(email="a@example.com", target_admin_id="aaaaaaaa"))

    # 在跑的会话是 a 的，但客户端拿 b 的 id 来交密码 → 409
    with pytest.raises(HTTPException) as exc:
        api.post_admins_login_password(api.AdminLoginPasswordParams(password="x", target_admin_id="bbbbbbbb"))
    assert exc.value.status_code == 409
