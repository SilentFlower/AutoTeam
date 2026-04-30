"""Plus 号池 API 单元测试。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from autoteam import api


def test_get_plus_list_returns_sanitized_records(monkeypatch):
    """Plus 列表端点应透传明文 password，供操作者复制导入账号凭据。"""
    monkeypatch.setattr(
        "autoteam.plus_accounts.load_plus",
        lambda admin_id=None: [
            {
                "email": "plus@example.com",
                "password": "pwd",
                "status": "active",
            }
        ],
    )

    result = api.get_plus_list(admin_id="abcd1234")

    assert result == [{"email": "plus@example.com", "password": "pwd", "status": "active"}]


def test_post_plus_import_starts_background_task(monkeypatch):
    """导入 Plus 号应创建后台任务，并显式透传 admin_id。"""
    monkeypatch.setattr(api, "_require_mail_provider_configs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "_require_sync_target_configs", lambda *_args, **_kwargs: None)

    captured = {}

    def fake_start_task(command, func, params, *args, **kwargs):
        captured.update(
            {
                "command": command,
                "func": func,
                "params": params,
                "args": args,
                "kwargs": kwargs,
            }
        )
        return {"task_id": "task-1", "command": command}

    monkeypatch.setattr(api, "_start_task", fake_start_task)

    result = api.post_plus_import(
        api.PlusImportParams(email="PLUS@Example.com", password="pwd"),
        admin_id="abcd1234",
    )

    assert result["task_id"] == "task-1"
    assert captured["command"] == "plus_import"
    assert captured["params"] == {"email": "plus@example.com"}
    assert captured["args"] == ("plus@example.com", "pwd")
    assert captured["kwargs"] == {"admin_id": "abcd1234"}


def test_delete_plus_email_returns_404_when_missing(monkeypatch):
    """删除不存在的 Plus 号应返回 404。"""
    lock_state = {"locked": False}

    class FakeLock:
        def acquire(self, blocking=True):
            lock_state["locked"] = True
            return True

        def release(self):
            lock_state["locked"] = False

    monkeypatch.setattr(api, "_playwright_lock", FakeLock())
    monkeypatch.setattr("autoteam.plus_accounts.load_plus", lambda admin_id=None: [])

    with pytest.raises(HTTPException) as exc:
        api.delete_plus_email("missing@example.com", admin_id="abcd1234")

    assert exc.value.status_code == 404
    assert exc.value.detail == "Plus 号不存在"
    assert lock_state["locked"] is False
