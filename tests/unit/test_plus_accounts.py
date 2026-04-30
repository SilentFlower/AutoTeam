"""Plus 号池单元测试。

覆盖范围：

- 数据层 CRUD 与 schema 默认值；
- 导入已有 Plus 账号时的 OAuth 成功、非 Plus plan 拒绝；
- 删除时只清理本地 auth_file、sub2api 远端与 JSON 记录，不删除用户邮箱。
"""

from __future__ import annotations

import json

import pytest

from autoteam import plus_accounts


@pytest.fixture
def plus_file(tmp_path, monkeypatch):
    """把 Plus 池默认文件重定向到临时目录。"""
    target = tmp_path / "plus_accounts.json"
    monkeypatch.setattr(plus_accounts, "DEFAULT_PLUS_ACCOUNTS_FILE", target)
    monkeypatch.setattr(plus_accounts, "_resolve_admin_id", lambda admin_id: None)
    return target


def _make_record(email="plus@example.com", status=plus_accounts.PLUS_STATUS_ACTIVE, **overrides):
    """构造 Plus 号记录。"""
    rec = {
        "email": email,
        "password": "pwd",
        "auth_file": None,
        "status": status,
        "plan_type": "plus",
        "last_error": None,
        "created_at": 1000,
        "last_login_at": None,
        "last_quota": None,
        "last_quota_at": None,
        "last_sub2api_synced_at": None,
    }
    rec.update(overrides)
    return rec


def test_plus_crud_round_trip(plus_file):
    """Plus 池 CRUD 应大小写不敏感，并补齐默认字段。"""
    saved = plus_accounts.add_plus({"email": "Mixed@Example.com", "password": "pwd"})

    assert saved["email"] == "mixed@example.com"
    assert saved["status"] == plus_accounts.PLUS_STATUS_AUTH_FAILED
    assert plus_accounts.find_plus(plus_accounts.load_plus(), "MIXED@example.com") is not None

    updated = plus_accounts.update_plus("mixed@example.com", status=plus_accounts.PLUS_STATUS_ACTIVE)
    assert updated["status"] == plus_accounts.PLUS_STATUS_ACTIVE

    deleted = plus_accounts.delete_plus("MIXED@example.com")
    assert deleted["email"] == "mixed@example.com"
    assert plus_accounts.load_plus() == []

    raw = json.loads(plus_file.read_text(encoding="utf-8"))
    assert raw == []


def test_add_plus_rejects_duplicate_email(plus_file):
    """同邮箱重复导入应拒绝，避免覆盖用户已有状态。"""
    plus_accounts.add_plus(_make_record(email="dup@example.com"))

    with pytest.raises(ValueError, match="已存在"):
        plus_accounts.add_plus(_make_record(email="DUP@example.com"))


def test_import_plus_account_saves_active_record_and_syncs(monkeypatch, plus_file, tmp_path):
    """OAuth 返回 plus plan 时保存 auth_file、active 状态，并触发 sub2api 同步。"""
    auth_path = tmp_path / "codex-plus@example.com-plus-abc.json"

    class FakeMailClient:
        def login(self):
            return True

    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda: FakeMailClient())
    monkeypatch.setattr(
        "autoteam.manager._login_codex_with_result",
        lambda email, password, **kwargs: {
            "ok": True,
            "bundle": {
                "email": email,
                "account_id": "acc-1",
                "plan_type": "plus",
                "access_token": "at",
                "refresh_token": "rt",
            },
        },
    )
    monkeypatch.setattr("autoteam.codex_auth.save_auth_file", lambda bundle, admin_id=None: str(auth_path))

    sync_calls = []
    monkeypatch.setattr("autoteam.sub2api_sync.sync_plus_to_sub2api", lambda admin_id=None: sync_calls.append(admin_id))

    result = plus_accounts.import_plus_account("PLUS@example.com", "pwd", admin_id="abcd1234")

    assert result["ok"] is True
    assert result["status"] == plus_accounts.PLUS_STATUS_ACTIVE
    assert result["auth_file"] == str(auth_path)
    assert sync_calls == ["abcd1234"]
    rec = plus_accounts.load_plus()[0]
    assert rec["email"] == "plus@example.com"
    assert rec["status"] == plus_accounts.PLUS_STATUS_ACTIVE


def test_import_plus_account_rejects_non_plus_plan(monkeypatch, plus_file):
    """OAuth 成功但 plan 不是 Plus 时应落 plan_mismatch，不推 sub2api。"""

    class FakeMailClient:
        def login(self):
            return True

    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda: FakeMailClient())
    monkeypatch.setattr(
        "autoteam.manager._login_codex_with_result",
        lambda email, password, **kwargs: {
            "ok": True,
            "bundle": {"email": email, "account_id": "acc-1", "plan_type": "free"},
        },
    )

    def should_not_save(*_args, **_kwargs):  # pragma: no cover - 不应调用
        raise AssertionError("非 Plus plan 不应保存 auth_file")

    monkeypatch.setattr("autoteam.codex_auth.save_auth_file", should_not_save)
    monkeypatch.setattr("autoteam.sub2api_sync.sync_plus_to_sub2api", should_not_save)

    result = plus_accounts.import_plus_account("not-plus@example.com", "pwd")

    assert result["ok"] is False
    assert result["status"] == plus_accounts.PLUS_STATUS_PLAN_MISMATCH
    rec = plus_accounts.load_plus()[0]
    assert rec["status"] == plus_accounts.PLUS_STATUS_PLAN_MISMATCH
    assert rec["auth_file"] is None
    assert "不是 Plus" in rec["last_error"]


def test_delete_plus_account_cleans_auth_file_and_sub2api(monkeypatch, plus_file, tmp_path):
    """删除 Plus 号只清理 auth_file、sub2api 与本地记录。"""
    auth_path = tmp_path / "codex-plus@example.com-plus-abc.json"
    auth_path.write_text("{}", encoding="utf-8")
    plus_accounts.add_plus(_make_record(email="plus@example.com", auth_file=str(auth_path)))

    deleted_remote = []
    monkeypatch.setattr(
        "autoteam.sub2api_sync.delete_plus_account_from_sub2api",
        lambda email, auth_names=None: deleted_remote.append((email, auth_names)) or {"deleted": ["remote-1"]},
    )

    cleanup = plus_accounts.delete_plus_account("PLUS@example.com")

    assert cleanup["local_record"] is True
    assert cleanup["local_auth_files"] == [auth_path.name]
    assert cleanup["sub2api_accounts"] == ["remote-1"]
    assert deleted_remote == [("PLUS@example.com", [auth_path.name])]
    assert not auth_path.exists()
    assert plus_accounts.load_plus() == []
