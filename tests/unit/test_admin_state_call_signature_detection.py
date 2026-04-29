"""``_admin_state_call`` / ``_email_is_main`` 的签名检测重构测试（修复 issue #3）。

回归 issue #3：原实现用 try/except TypeError 包住 func 调用，会把生产代码
内部抛的真实 TypeError 静默吞掉并退化成无 admin_id 调用，可能写错 admin
目录。改为基于 ``inspect.signature(func).parameters`` 显式检测 admin_id
是否在签名中。
"""

from __future__ import annotations

import pytest

from autoteam import api

# ---------------------------------------------------------------------------
# _admin_state_call
# ---------------------------------------------------------------------------


def test_admin_state_call_passes_admin_id_when_func_accepts_it():
    """目标函数声明了 admin_id 参数 → 正常透传。"""

    captured = {}

    def fake_loader(*, admin_id=None):
        captured["admin_id"] = admin_id
        return ["row"]

    result = api._admin_state_call(fake_loader, "deadbeef")
    assert result == ["row"]
    assert captured["admin_id"] == "deadbeef"


def test_admin_state_call_skips_admin_id_for_legacy_one_arg_lambda():
    """旧测试 monkeypatch 出的 1-arg lambda 不接受 admin_id → 不传，直接调用。"""

    fake_loader = lambda: ["legacy"]  # noqa: E731

    result = api._admin_state_call(fake_loader, "deadbeef")
    assert result == ["legacy"]


def test_admin_state_call_does_not_swallow_real_typeerror_inside_func():
    """如果目标函数接受 admin_id 但内部抛 TypeError，不应被吞掉。

    旧实现把 try/except TypeError 套在 func 调用外面，会把生产代码 bug
    （比如某个内部 ``int(None)``）静默退化成无 admin_id fallback，导致写错目录。
    新实现用 inspect 探测签名，TypeError 应正常上抛。
    """

    def buggy_loader(*, admin_id=None):
        # 模拟生产代码内部一个真实 TypeError——签名是接受 admin_id 的。
        raise TypeError("生产代码内部真实 bug")

    with pytest.raises(TypeError, match="生产代码内部真实 bug"):
        api._admin_state_call(buggy_loader, "deadbeef")


def test_admin_state_call_supports_var_kwargs_function():
    """**kwargs 透传函数也应被识别为接受 admin_id。"""

    captured = {}

    def variadic_loader(**kwargs):
        captured.update(kwargs)
        return "ok"

    result = api._admin_state_call(variadic_loader, "abcd1234")
    assert result == "ok"
    assert captured.get("admin_id") == "abcd1234"


def test_admin_state_call_passes_positional_args_through():
    """额外的位置/关键字参数应原样透传。"""

    def loader(account_id: str, *, admin_id=None, extra: str = ""):
        return (account_id, admin_id, extra)

    result = api._admin_state_call(loader, "abcd1234", "acct-1", extra="x")
    assert result == ("acct-1", "abcd1234", "x")


# ---------------------------------------------------------------------------
# _email_is_main
# ---------------------------------------------------------------------------


def test_email_is_main_calls_two_arg_signature_when_supported(monkeypatch):
    """生产签名是 (email, admin_id) → 透传两参。"""

    captured = {}

    def fake_check(email, admin_id=None):
        captured["email"] = email
        captured["admin_id"] = admin_id
        return True

    monkeypatch.setattr(api, "_is_main_account_email", fake_check)

    assert api._email_is_main("a@x.com", "deadbeef") is True
    assert captured == {"email": "a@x.com", "admin_id": "deadbeef"}


def test_email_is_main_falls_back_to_one_arg_lambda(monkeypatch):
    """旧测试 patch 出的 1-arg lambda → 自动单参调用，不抛 TypeError。"""

    captured = {}

    def fake_check(email):
        captured["email"] = email
        return False

    monkeypatch.setattr(api, "_is_main_account_email", fake_check)

    assert api._email_is_main("b@y.com", "abcd1234") is False
    assert captured == {"email": "b@y.com"}


def test_email_is_main_does_not_swallow_internal_typeerror(monkeypatch):
    """目标函数接受 admin_id 但内部抛 TypeError → 正常上抛，不被吞掉。"""

    def buggy_check(email, admin_id=None):
        raise TypeError("内部 bug 不应被吞")

    monkeypatch.setattr(api, "_is_main_account_email", buggy_check)

    with pytest.raises(TypeError, match="内部 bug 不应被吞"):
        api._email_is_main("c@z.com", "deadbeef")
