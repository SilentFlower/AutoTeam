import base64
import json
from datetime import datetime, timezone

import pytest

from autoteam import sub2api_sync
from autoteam.codex_auth import CODEX_CLIENT_ID


def _jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}."


def test_build_credentials_matches_openai_oauth_shape():
    expires_iso = "2026-04-25T05:44:19Z"
    id_token = _jwt(
        {
            "aud": [CODEX_CLIENT_ID],
            "email": "tmp@example.com",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct-1",
                "chatgpt_user_id": "user-1",
                "chatgpt_plan_type": "team",
                "chatgpt_subscription_active_until": "2026-05-05T15:55:38+00:00",
                "organizations": [{"id": "org-1", "is_default": True}],
            },
        }
    )

    credentials = sub2api_sync._build_credentials(
        {
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "id_token": id_token,
            "expired": expires_iso,
        }
    )

    assert credentials == {
        "access_token": "at-1",
        "expires_at": int(datetime.fromisoformat(expires_iso.replace("Z", "+00:00")).timestamp()),
        "refresh_token": "rt-1",
        "id_token": id_token,
        "client_id": CODEX_CLIENT_ID,
        "email": "tmp@example.com",
        "chatgpt_account_id": "acct-1",
        "chatgpt_user_id": "user-1",
        "organization_id": "org-1",
        "plan_type": "team",
        "subscription_expires_at": "2026-05-05T15:55:38+00:00",
    }


def test_build_managed_model_mapping_uses_identity_mapping():
    assert sub2api_sync._build_managed_model_mapping("gpt-5.4, gpt-5.4-mini") == {
        "gpt-5.4": "gpt-5.4",
        "gpt-5.4-mini": "gpt-5.4-mini",
    }


def test_build_managed_model_mapping_returns_none_when_blank():
    assert sub2api_sync._build_managed_model_mapping("") is None


def test_apply_managed_extra_settings_supports_ws_mode_and_passthrough(monkeypatch):
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_WS_MODE", "ctx_pool")
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_PASSTHROUGH", True)

    extra = {"openai_oauth_passthrough": False}
    sub2api_sync._apply_managed_extra_settings(extra)

    assert extra["openai_oauth_responses_websockets_v2_mode"] == "ctx_pool"
    assert extra["openai_oauth_responses_websockets_v2_enabled"] is True
    assert extra["openai_passthrough"] is True
    assert extra["openai_oauth_passthrough"] is False


def test_apply_managed_extra_settings_removes_passthrough_when_disabled(monkeypatch):
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_WS_MODE", "off")
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_PASSTHROUGH", False)

    extra = {"openai_passthrough": True, "openai_oauth_passthrough": True}
    sub2api_sync._apply_managed_extra_settings(extra)

    assert extra["openai_oauth_responses_websockets_v2_mode"] == "off"
    assert extra["openai_oauth_responses_websockets_v2_enabled"] is False
    assert "openai_passthrough" not in extra
    assert "openai_oauth_passthrough" not in extra


def test_build_extra_includes_codex_usage_snapshot(monkeypatch):
    monkeypatch.setattr(sub2api_sync.time, "time", lambda: 1_700_000_000)

    extra = sub2api_sync._build_extra(
        "tmp@example.com",
        "codex-tmp@example.com-team-123.json",
        kind="pool",
        quota_info={
            "primary_pct": 42,
            "primary_resets_at": 1_700_003_600,
            "weekly_pct": 88,
            "weekly_resets_at": 1_700_086_400,
        },
    )

    assert extra["autoteam_managed"] is True
    assert extra["autoteam_kind"] == "pool"
    assert extra["autoteam_email"] == "tmp@example.com"
    assert extra["autoteam_auth_file"] == "sub2api-codex-tmp@example.com-team-123.json"
    assert extra["autoteam_source"] == "autoteam"
    assert extra["email"] == "tmp@example.com"
    assert extra["codex_5h_used_percent"] == 42
    assert extra["codex_5h_reset_after_seconds"] == 3600
    assert extra["codex_5h_reset_at"] == sub2api_sync._to_local_iso(1_700_003_600)
    assert extra["codex_7d_used_percent"] == 88
    assert extra["codex_7d_reset_after_seconds"] == 86_400
    assert extra["codex_7d_reset_at"] == sub2api_sync._to_local_iso(1_700_086_400)
    assert extra["codex_primary_used_percent"] == 42
    assert extra["codex_secondary_used_percent"] == 88
    assert extra["codex_usage_updated_at"] == datetime.fromtimestamp(
        1_700_000_000, timezone.utc
    ).astimezone().isoformat(timespec="seconds")


def test_attach_group_metadata_records_autoteam_group_binding():
    extra = sub2api_sync._build_extra("tmp@example.com", "codex-tmp@example.com-team-123.json", kind="pool")
    sub2api_sync._attach_group_metadata(extra, [7], ["Team Pool"])

    assert extra["autoteam_sub2api_group_ids"] == [7]
    assert extra["autoteam_sub2api_group_names"] == ["Team Pool"]


def test_resolve_group_binding_supports_name_and_id(monkeypatch):
    monkeypatch.setattr(
        sub2api_sync,
        "_list_openai_groups",
        lambda token: [
            {"id": 7, "name": "Team Pool", "platform": "openai"},
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_get_group_by_id",
        lambda token, group_id: {"id": group_id, "name": f"Group-{group_id}", "platform": "openai"},
    )

    group_ids, group_names = sub2api_sync._resolve_group_binding("token", "Team Pool, 9")

    assert group_ids == [7, 9]
    assert group_names == ["Team Pool", "Group-9"]


def test_resolve_proxy_id_supports_name_and_id(monkeypatch):
    monkeypatch.setattr(
        sub2api_sync,
        "_list_proxies",
        lambda token: [
            {"id": 42, "name": "Residential Pool"},
        ],
    )

    assert sub2api_sync._resolve_proxy_id("token", "") is None
    assert sub2api_sync._resolve_proxy_id("token", "42") == 42
    assert sub2api_sync._resolve_proxy_id("token", "residential pool") == 42


@pytest.mark.parametrize("proxy_spec", ["0", "-1"])
def test_resolve_proxy_id_rejects_invalid_numeric_values(proxy_spec):
    with pytest.raises(RuntimeError, match="代理 ID 必须是正整数"):
        sub2api_sync._resolve_proxy_id("token", proxy_spec)


def test_resolve_proxy_id_reports_unknown_name(monkeypatch):
    monkeypatch.setattr(sub2api_sync, "_list_proxies", lambda token: [{"id": 42, "name": "Residential Pool"}])

    with pytest.raises(RuntimeError, match="未找到代理"):
        sub2api_sync._resolve_proxy_id("token", "Missing Proxy")


def test_create_account_includes_proxy_id_only_when_provided(monkeypatch):
    payloads = []

    def fake_request(method, path, **kwargs):
        payloads.append(kwargs["json"])
        return {"id": len(payloads)}

    monkeypatch.setattr(sub2api_sync, "_request", fake_request)

    sub2api_sync._create_account(
        "token",
        name="with-proxy",
        credentials={"access_token": "at-1"},
        extra={},
        label="创建账号",
        proxy_id=42,
    )
    sub2api_sync._create_account(
        "token",
        name="without-proxy",
        credentials={"access_token": "at-1"},
        extra={},
        label="创建账号",
        proxy_id=None,
    )

    assert payloads[0]["proxy_id"] == 42
    assert "proxy_id" not in payloads[1]


def test_merge_group_ids_preserves_manual_groups_and_replaces_previous_managed_group():
    account = {
        "group_ids": [11, 21],
        "extra": {
            "autoteam_sub2api_group_ids": [21],
        },
    }

    assert sub2api_sync._merge_group_ids(account, [22]) == [11, 22]
    assert sub2api_sync._merge_group_ids(account, []) == [11]


def test_remote_auth_file_candidates_include_legacy_and_prefixed_names():
    assert sub2api_sync._remote_auth_file_candidates(["codex-a.json"]) == {
        "codex-a.json",
        "sub2api-codex-a.json",
    }


def test_sync_to_sub2api_preserves_existing_manual_settings_when_overwrite_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(sub2api_sync, "SUB2API_OVERWRITE_ACCOUNT_SETTINGS", False)
    monkeypatch.setattr(sub2api_sync, "SUB2API_PROXY", "Residential Pool")
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([7], ["Team Pool"]))
    # 任务 04-29-sub2api-main-sync-fix 顺手对齐 pool 池更新分支也补 proxy_id;
    # 原本这里 stub 是"更新分支不应解析 proxy",现已变更为"更新也走代理刷新"。
    monkeypatch.setattr(sub2api_sync, "_resolve_proxy_id", lambda token: 1)
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "tmp@example.com": {
                    "id": 12,
                    "status": "disabled",
                    "credentials": {"model_mapping": {"manual-model": "manual-model"}},
                    "extra": {
                        "openai_oauth_responses_websockets_v2_mode": "passthrough",
                        "openai_oauth_responses_websockets_v2_enabled": True,
                        "openai_passthrough": True,
                    },
                    "group_ids": [99, 21],
                }
            },
            0,
        ),
    )

    auth_path = tmp_path / "codex-tmp@example.com-team-123.json"
    auth_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": "tmp@example.com",
                "status": "active",
                "auth_file": str(auth_path),
                "last_quota": {"primary_pct": 10, "weekly_pct": 20},
            }
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {
            "email": "tmp@example.com",
            "access_token": "at-1",
            "refresh_token": "rt-1",
        },
    )

    captured = {}

    def fake_update_account(token, account, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(sub2api_sync, "_update_account", fake_update_account)

    sub2api_sync.sync_to_sub2api()

    assert captured["credentials"]["model_mapping"] == {"manual-model": "manual-model"}
    assert captured["extra"]["openai_oauth_responses_websockets_v2_mode"] == "passthrough"
    assert captured["extra"]["openai_oauth_responses_websockets_v2_enabled"] is True
    assert captured["extra"]["openai_passthrough"] is True
    assert captured["account_settings"] is None
    # D2 决定:proxy 不在"账号设置"语义里,即使 OVERWRITE_ACCOUNT_SETTINGS=False
    # 也按"始终用配置覆盖"语义刷新远端 proxy_id(配置代理改了下次同步立即生效)。
    assert captured["proxy_id"] == 1


def test_sync_to_sub2api_overwrites_managed_settings_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(sub2api_sync, "SUB2API_OVERWRITE_ACCOUNT_SETTINGS", True)
    monkeypatch.setattr(sub2api_sync, "SUB2API_CONCURRENCY", 12)
    monkeypatch.setattr(sub2api_sync, "SUB2API_PRIORITY", 3)
    monkeypatch.setattr(sub2api_sync, "SUB2API_RATE_MULTIPLIER", 1.5)
    monkeypatch.setattr(sub2api_sync, "SUB2API_AUTO_PAUSE_ON_EXPIRED", False)
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_WS_MODE", "ctx_pool")
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_PASSTHROUGH", False)
    monkeypatch.setattr(sub2api_sync, "SUB2API_MODEL_WHITELIST", "gpt-5.4,gpt-5.4-mini")
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    # 任务 04-29-sub2api-main-sync-fix:更新分支也走代理刷新(D2)。
    monkeypatch.setattr(sub2api_sync, "_resolve_proxy_id", lambda token: 1)
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "tmp@example.com": {
                    "id": 12,
                    "status": "disabled",
                    "credentials": {"model_mapping": {"manual-model": "manual-model"}},
                    "extra": {
                        "openai_oauth_responses_websockets_v2_mode": "off",
                        "openai_oauth_responses_websockets_v2_enabled": False,
                        "openai_passthrough": True,
                        "openai_oauth_passthrough": True,
                    },
                    "group_ids": [],
                }
            },
            0,
        ),
    )

    auth_path = tmp_path / "codex-tmp@example.com-team-123.json"
    auth_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": "tmp@example.com",
                "status": "active",
                "auth_file": str(auth_path),
                "last_quota": None,
            }
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {
            "email": "tmp@example.com",
            "access_token": "at-1",
            "refresh_token": "rt-1",
        },
    )

    captured = {}

    def fake_update_account(token, account, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(sub2api_sync, "_update_account", fake_update_account)

    sub2api_sync.sync_to_sub2api()

    assert captured["account_settings"] == {
        "concurrency": 12,
        "priority": 3,
        "rate_multiplier": 1.5,
        "auto_pause_on_expired": False,
    }
    assert captured["credentials"]["model_mapping"] == {
        "gpt-5.4": "gpt-5.4",
        "gpt-5.4-mini": "gpt-5.4-mini",
    }
    assert captured["extra"]["openai_oauth_responses_websockets_v2_mode"] == "ctx_pool"
    assert captured["extra"]["openai_oauth_responses_websockets_v2_enabled"] is True
    assert "openai_passthrough" not in captured["extra"]
    assert "openai_oauth_passthrough" not in captured["extra"]


def test_sync_to_sub2api_does_not_resolve_proxy_when_only_deleting_non_active_accounts(monkeypatch):
    monkeypatch.setattr(sub2api_sync, "SUB2API_PROXY", "Missing Proxy")
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(
        sub2api_sync,
        "_resolve_proxy_id",
        lambda token: (_ for _ in ()).throw(AssertionError("proxy should not be resolved without create")),
    )
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": "tmp@example.com",
                "status": "standby",
                "auth_file": "",
                "last_quota": None,
            }
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "tmp@example.com": {
                    "id": 12,
                    "status": "active",
                    "credentials": {"email": "tmp@example.com"},
                    "extra": {},
                    "group_ids": [],
                }
            },
            0,
        ),
    )

    deleted = []

    def fake_delete_account(token, account, **kwargs):
        deleted.append((account["id"], kwargs["label"]))
        return {"ok": True}

    monkeypatch.setattr(sub2api_sync, "_delete_account", fake_delete_account)

    result = sub2api_sync.sync_to_sub2api()

    assert result["deleted"] == 1
    assert deleted == [(12, "删除非 active 账号")]


def test_sync_to_sub2api_resolves_proxy_name_for_new_pool_accounts(monkeypatch, tmp_path):
    monkeypatch.setattr(sub2api_sync, "SUB2API_PROXY", "Residential Pool")
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_list_proxies", lambda token: [{"id": 42, "name": "Residential Pool"}])
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(sub2api_sync, "_dedupe_managed_accounts", lambda token, items, *, kind: ({}, 0))

    auth_path = tmp_path / "codex-tmp@example.com-team-123.json"
    auth_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": "tmp@example.com",
                "status": "active",
                "auth_file": str(auth_path),
                "last_quota": None,
            }
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {
            "email": "tmp@example.com",
            "access_token": "at-1",
            "refresh_token": "rt-1",
        },
    )

    captured = {}

    def fake_create_account(token, **kwargs):
        captured.update(kwargs)
        return {"id": 99}

    monkeypatch.setattr(sub2api_sync, "_create_account", fake_create_account)

    result = sub2api_sync.sync_to_sub2api()

    assert result["created"] == 1
    assert captured["proxy_id"] == 42


def test_sync_main_codex_to_sub2api_creates_account_with_managed_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(sub2api_sync, "SUB2API_PROXY", "Residential Pool")
    monkeypatch.setattr(sub2api_sync, "SUB2API_CONCURRENCY", 8)
    monkeypatch.setattr(sub2api_sync, "SUB2API_PRIORITY", 2)
    monkeypatch.setattr(sub2api_sync, "SUB2API_RATE_MULTIPLIER", 2.5)
    monkeypatch.setattr(sub2api_sync, "SUB2API_AUTO_PAUSE_ON_EXPIRED", True)
    monkeypatch.setattr(sub2api_sync, "SUB2API_MODEL_WHITELIST", "gpt-5.4")
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_WS_MODE", "passthrough")
    monkeypatch.setattr(sub2api_sync, "SUB2API_OPENAI_PASSTHROUGH", True)
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([7], ["Team Pool"]))
    # 修复 `04-29-sub2api-main-sync-fix` 后,主号创建分支会调 _resolve_proxy_id;
    # 用 mock 避免打真实 sub2api `/admin/proxies/all` API。
    monkeypatch.setattr(sub2api_sync, "_resolve_proxy_id", lambda token: 1)
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(sub2api_sync, "_dedupe_managed_accounts", lambda token, items, *, kind: ({}, 0))

    auth_path = tmp_path / "main.json"
    auth_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {
            "email": "main@example.com",
            "access_token": "at-1",
            "refresh_token": "rt-1",
        },
    )

    captured = {}

    def fake_create_account(token, **kwargs):
        captured.update(kwargs)
        return {"id": 99}

    monkeypatch.setattr(sub2api_sync, "_create_account", fake_create_account)

    result = sub2api_sync.sync_main_codex_to_sub2api(str(auth_path))

    assert result["account_id"] == 99
    assert captured["account_settings"] == {
        "concurrency": 8,
        "priority": 2,
        "rate_multiplier": 2.5,
        "auto_pause_on_expired": True,
    }
    assert captured["credentials"]["model_mapping"] == {"gpt-5.4": "gpt-5.4"}
    assert captured["extra"]["openai_oauth_responses_websockets_v2_mode"] == "passthrough"
    assert captured["extra"]["openai_oauth_responses_websockets_v2_enabled"] is True
    assert captured["extra"]["openai_passthrough"] is True
    # 修复点:主号创建必须带 proxy_id(原断言为 "proxy_id" not in captured,与 pool 池行为不一致)
    assert captured["proxy_id"] == 1


# ---------------------------------------------------------------------------
# PR2 — 同步源参数化(``_collect_managed_targets`` + ``sync_free_to_sub2api``)
# ---------------------------------------------------------------------------
#
# 背景:PRD ``04-29-free-account-generator`` R6 把 ``sync_to_sub2api`` 里
# "枚举 active 账号"那段抽成 ``_collect_managed_targets(source)``,新增 FREE
# 池入口 ``sync_free_to_sub2api()``。这里覆盖三类回归点:
#
# 1. ``_collect_managed_targets`` 按 source 走不同数据源,且分别只读各自的
#    ``status=active`` 记录;
# 2. ``_collect_all_managed_emails`` 在删除分支用并集,确保跨 source 互不误删;
# 3. ``sync_to_sub2api()`` 默认 source=pool 仍用 ``accounts.STATUS_ACTIVE``
#    过滤(零回归),``sync_free_to_sub2api()`` 走 free_accounts 数据源。


def test_collect_managed_targets_pool_filters_only_active_accounts(monkeypatch, tmp_path):
    """source=pool 走 accounts.load_accounts() + STATUS_ACTIVE 过滤。"""
    auth_path = tmp_path / "codex-active@example.com-team-1.json"
    auth_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": "active@example.com", "status": "active", "auth_file": str(auth_path)},
            {"email": "standby@example.com", "status": "standby", "auth_file": str(auth_path)},
            {"email": "exhausted@example.com", "status": "exhausted", "auth_file": str(auth_path)},
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {"email": "active@example.com", "access_token": "at"},
    )

    targets = sub2api_sync._collect_managed_targets("pool")

    assert list(targets.keys()) == ["active@example.com"]
    assert targets["active@example.com"]["name"] == "active@example.com"


def test_collect_managed_targets_free_reads_free_accounts_json(monkeypatch, tmp_path):
    """source=free 走 free_accounts.load_free() + FREE_STATUS_ACTIVE 过滤。"""
    from autoteam import free_accounts

    auth_path = tmp_path / "codex-free@example.com-team-1.json"
    auth_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        free_accounts,
        "load_free",
        lambda: [
            {"email": "free@example.com", "status": "active", "auth_file": str(auth_path)},
            # auth_failed 半成品不应被 sub2api 同步
            {"email": "halfdone@example.com", "status": "auth_failed", "auth_file": None},
            # exhausted 也不进 sub2api(只在用户刷新额度时打标)
            {"email": "exhausted@example.com", "status": "exhausted", "auth_file": str(auth_path)},
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {"email": "free@example.com", "access_token": "at"},
    )

    targets = sub2api_sync._collect_managed_targets("free")

    assert list(targets.keys()) == ["free@example.com"]


def test_collect_managed_targets_pool_does_not_read_free_accounts(monkeypatch):
    """D2 隔离铁律:pool 同步源不应读 free_accounts.json。"""
    monkeypatch.setattr("autoteam.accounts.load_accounts", lambda: [])

    free_load_calls = {"n": 0}

    def _spy_free_load():
        free_load_calls["n"] += 1
        return []

    monkeypatch.setattr("autoteam.free_accounts.load_free", _spy_free_load)

    sub2api_sync._collect_managed_targets("pool")

    assert free_load_calls["n"] == 0


def test_collect_managed_targets_rejects_unknown_source():
    with pytest.raises(ValueError, match="未知同步来源"):
        sub2api_sync._collect_managed_targets("unknown")  # type: ignore[arg-type]


def test_collect_all_managed_emails_unions_pool_and_free(monkeypatch):
    """两边同时贡献邮箱;集合用于删除分支判定"应保留"。"""
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [{"email": "Pool@Example.com"}, {"email": ""}, {"email": "shared@example.com"}],
    )
    monkeypatch.setattr(
        "autoteam.free_accounts.load_free",
        lambda: [{"email": "free@example.com"}, {"email": "SHARED@example.com"}],
    )

    emails = sub2api_sync._collect_all_managed_emails()

    # 全部 lower-case + 非空;active 池和 FREE 池都贡献了元素;共享邮箱去重
    assert emails == {"pool@example.com", "free@example.com", "shared@example.com"}


def test_collect_all_managed_emails_continues_when_one_side_raises(monkeypatch):
    """单边读取失败不影响另一边——任何一边异常都不阻塞全集计算。"""

    def _explode():
        raise RuntimeError("disk error")

    monkeypatch.setattr("autoteam.accounts.load_accounts", _explode)
    monkeypatch.setattr(
        "autoteam.free_accounts.load_free",
        lambda: [{"email": "free@example.com"}],
    )

    emails = sub2api_sync._collect_all_managed_emails()

    # active 侧虽然抛错,但 FREE 侧仍贡献了 free@example.com
    assert emails == {"free@example.com"}


def test_sync_to_sub2api_pool_does_not_delete_remote_account_owned_by_free_pool(monkeypatch, tmp_path):
    """关键互不误删测试:active 同步时,FREE 池里有这个 email,远端账号应被保留。

    场景:
    - active 池本地无 active 状态账号(active_targets 为空);
    - 远端有一条 sub2api 管理账号 ``free@example.com``;
    - FREE 池里也有 ``free@example.com``(active 状态);
    - 期望:删除分支判定"应保留",不删该远端账号。

    若 PR2 没把 ``local_emails`` 切换成并集,这里会回到旧行为
    (active 侧的 local_emails 包含 free@example.com,active_targets 不包含 → 误删)。
    """
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    # 关键:active 池里挂着 free@example.com 但 status=standby(不进 active_targets)
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [{"email": "free@example.com", "status": "standby", "auth_file": ""}],
    )
    # FREE 池里 free@example.com 是 active(应保留)
    monkeypatch.setattr(
        "autoteam.free_accounts.load_free",
        lambda: [{"email": "free@example.com", "status": "active", "auth_file": ""}],
    )
    # 远端只有 free@example.com 一条 managed 账号
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "free@example.com": {
                    "id": 99,
                    "status": "active",
                    "credentials": {"email": "free@example.com"},
                    "extra": {},
                    "group_ids": [],
                }
            },
            0,
        ),
    )

    deleted = []
    monkeypatch.setattr(
        sub2api_sync,
        "_delete_account",
        lambda token, account, **kwargs: deleted.append((account.get("id"), kwargs.get("label"))),
    )

    result = sub2api_sync.sync_to_sub2api()  # 默认 source=pool

    # 关键断言:无任何远端删除发生(FREE 池保护了 free@example.com)
    assert deleted == []
    assert result["deleted"] == 0


def test_sync_to_sub2api_pool_still_deletes_orphan_remote_account(monkeypatch, tmp_path):
    """active 同步删除分支保留:远端孤儿账号(两边本地都没有)仍要删——这是原行为的核心。

    场景:
    - active 池里有 ``orphan@example.com``,但 status=standby(不在 active_targets);
    - FREE 池为空;
    - 远端有 ``orphan@example.com``;
    - 期望:删除该远端账号(原行为:email in local_emails 且 not in active_targets)。
    """
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [{"email": "orphan@example.com", "status": "standby", "auth_file": ""}],
    )
    monkeypatch.setattr("autoteam.free_accounts.load_free", lambda: [])
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "orphan@example.com": {
                    "id": 77,
                    "status": "active",
                    "credentials": {"email": "orphan@example.com"},
                    "extra": {},
                    "group_ids": [],
                }
            },
            0,
        ),
    )

    deleted = []
    monkeypatch.setattr(
        sub2api_sync,
        "_delete_account",
        lambda token, account, **kwargs: deleted.append((account.get("id"), kwargs.get("label"))),
    )

    result = sub2api_sync.sync_to_sub2api()

    # 远端孤儿账号仍被删——这是 active 池原行为的保留断言
    assert deleted == [(77, "删除非 active 账号")]
    assert result["deleted"] == 1


def test_sync_free_to_sub2api_creates_account_from_free_pool(monkeypatch, tmp_path):
    """FREE 入口:从 free_accounts.json 读 active 状态条目并在远端创建。"""
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([7], ["Team Pool"]))
    monkeypatch.setattr(sub2api_sync, "_resolve_proxy_id", lambda token: None)
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(sub2api_sync, "_dedupe_managed_accounts", lambda token, items, *, kind: ({}, 0))

    auth_path = tmp_path / "codex-free@example.com-team-1.json"
    auth_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("autoteam.accounts.load_accounts", lambda: [])
    monkeypatch.setattr(
        "autoteam.free_accounts.load_free",
        lambda: [
            {
                "email": "free@example.com",
                "status": "active",
                "auth_file": str(auth_path),
                "last_quota": {"primary_pct": 5, "weekly_pct": 8},
            }
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {
            "email": "free@example.com",
            "access_token": "at-1",
            "refresh_token": "rt-1",
        },
    )

    captured = {}

    def fake_create_account(token, **kwargs):
        captured.update(kwargs)
        return {"id": 99}

    monkeypatch.setattr(sub2api_sync, "_create_account", fake_create_account)

    result = sub2api_sync.sync_free_to_sub2api()

    assert result["created"] == 1
    # group 与 active 池共用 (D3 锁定:不引入 SUB2API_FREE_GROUP)
    assert captured["group_ids"] == [7]
    # 标记 kind=pool(D3 决定 FREE 与 active 共用一个 sub2api group/kind)
    assert captured["extra"]["autoteam_kind"] == "pool"
    assert captured["extra"]["autoteam_email"] == "free@example.com"


def test_sync_free_to_sub2api_does_not_delete_active_pool_remote_account(monkeypatch, tmp_path):
    """FREE 同步反向不误删:远端属于 active 池的账号,FREE 同步时应保留。

    场景:
    - FREE 池为空;
    - active 池里有 ``active@example.com``;
    - 远端有 ``active@example.com``;
    - 期望:FREE 同步走删除分支时,active 池里有这个 email → 应保留。
    """
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [{"email": "active@example.com", "status": "active", "auth_file": ""}],
    )
    monkeypatch.setattr("autoteam.free_accounts.load_free", lambda: [])
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "active@example.com": {
                    "id": 55,
                    "status": "active",
                    "credentials": {"email": "active@example.com"},
                    "extra": {},
                    "group_ids": [],
                }
            },
            0,
        ),
    )

    deleted = []
    monkeypatch.setattr(
        sub2api_sync,
        "_delete_account",
        lambda token, account, **kwargs: deleted.append((account.get("id"), kwargs.get("label"))),
    )

    result = sub2api_sync.sync_free_to_sub2api()

    assert deleted == []
    assert result["deleted"] == 0


def test_sync_free_to_sub2api_deletes_remote_when_pool_email_no_longer_active(monkeypatch):
    """FREE 同步保留原行为:active 池里 ``email`` 是 standby/exhausted(非 active),
    且 FREE 池没有该 email,远端这条历史 managed 账号应被清理。

    场景:active 池里有 ``orphan@example.com`` 但 status=standby(不在任何 active 集合)。
    并集应保留集合 ``keep_emails`` = active(空)+ FREE active(空)= 空。
    限定符 ``all_local_emails`` 包含该 email(active 池任何 status 都贡献)→ 应删。
    """
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [{"email": "orphan@example.com", "status": "standby", "auth_file": ""}],
    )
    monkeypatch.setattr("autoteam.free_accounts.load_free", lambda: [])
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "orphan@example.com": {
                    "id": 33,
                    "status": "active",
                    "credentials": {"email": "orphan@example.com"},
                    "extra": {},
                    "group_ids": [],
                }
            },
            0,
        ),
    )

    deleted = []
    monkeypatch.setattr(
        sub2api_sync,
        "_delete_account",
        lambda token, account, **kwargs: deleted.append(account.get("id")),
    )

    sub2api_sync.sync_free_to_sub2api()

    # standby 账号在两边都不是 active → 不属于"应保留",FREE 同步顺手清理
    # (这是历史 active 池清理 standby 残留行为的镜像版本,在 FREE 入口同样保留)
    assert deleted == [33]


def test_sync_free_to_sub2api_skips_auth_failed_records(monkeypatch, tmp_path):
    """半成品(status=auth_failed)条目不进 sub2api 同步:它们没 auth_file。"""
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(sub2api_sync, "_dedupe_managed_accounts", lambda token, items, *, kind: ({}, 0))
    monkeypatch.setattr("autoteam.accounts.load_accounts", lambda: [])
    monkeypatch.setattr(
        "autoteam.free_accounts.load_free",
        lambda: [
            {"email": "halfdone@example.com", "status": "auth_failed", "auth_file": None},
        ],
    )

    create_calls = {"n": 0}
    monkeypatch.setattr(
        sub2api_sync,
        "_create_account",
        lambda token, **k: create_calls.update({"n": create_calls["n"] + 1}) or {"id": 1},
    )

    result = sub2api_sync.sync_free_to_sub2api()

    # 半成品被跳过 → 没有任何创建动作
    assert create_calls["n"] == 0
    assert result["created"] == 0


# ---------------------------------------------------------------------------
# 任务 04-29-sub2api-main-sync-fix 回归用例
# ---------------------------------------------------------------------------
#
# 修复两个独立 bug:
# 1. sync_main_codex_to_sub2api 函数末尾按"主号是单例"假设删跨邮箱主号
#    (多 admin 共用同一 sub2api 实例时互删主号);
# 2. 主号同步全程不带代理(创建/更新都没绑 SUB2API_PROXY),与 pool 池行为不一致;
#    顺手对齐 pool 池更新分支(原本只在创建时绑代理,更新不刷新)。


def _build_main_auth_path(tmp_path, email: str) -> "object":
    """构造一个临时主号 auth 文件路径,具体内容由调用方 monkeypatch 替换。"""
    auth_path = tmp_path / f"codex-main-{email}.json"
    auth_path.write_text("{}", encoding="utf-8")
    return auth_path


def test_sync_main_codex_to_sub2api_does_not_delete_other_admins_main(monkeypatch, tmp_path):
    """AC1:多 admin 共用同一 sub2api 时,A 同步不应删 B 的主号。

    场景:
    - 远端已存在 ``b@example.com`` (kind=main,id=200) — admin B 的主号;
    - admin A 同步 ``a@example.com`` 的 auth_file → 触发创建路径;
    - 期望:_delete_account 不被调用(尤其不能误删 b 的远端记录)。

    回归 bug:历史实现末尾循环把所有 main-kind 但不是当前邮箱的远端账号当
    "旧主号"删,跨邮箱误删。
    """
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_resolve_proxy_id", lambda token: None)
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    # dedup 返回:远端只有 b 这一条 main 账号(没有 a),sync a 时 a 不存在 → 走创建分支
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "b@example.com": {
                    "id": 200,
                    "credentials": {},
                    "extra": {"autoteam_managed": True, "autoteam_kind": "main", "autoteam_email": "b@example.com"},
                }
            },
            0,
        ),
    )

    auth_path = _build_main_auth_path(tmp_path, "a@example.com")
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {"email": "a@example.com", "access_token": "at-a"},
    )
    monkeypatch.setattr(sub2api_sync, "_create_account", lambda token, **k: {"id": 999})

    delete_calls = []
    monkeypatch.setattr(
        sub2api_sync,
        "_delete_account",
        lambda token, account, **kwargs: delete_calls.append(account.get("id")),
    )

    result = sub2api_sync.sync_main_codex_to_sub2api(str(auth_path))

    # 关键:b 的远端记录(id=200)不应被删
    assert delete_calls == []
    assert result["account_id"] == 999
    # deleted_old 字段保留为空列表,不再承诺"清理旧主号"语义
    assert result["deleted_old"] == []


def test_sync_main_codex_to_sub2api_updates_with_proxy_id(monkeypatch, tmp_path):
    """AC3:远端已有同邮箱主号时,update 路径要把 proxy_id 写进 payload。"""
    monkeypatch.setattr(sub2api_sync, "SUB2API_PROXY", "Residential Pool")
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_resolve_proxy_id", lambda token: 1)
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    # 远端已存在同邮箱主号 → sync 走更新路径
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "main@example.com": {
                    "id": 555,
                    "credentials": {},
                    "extra": {"autoteam_managed": True, "autoteam_kind": "main", "autoteam_email": "main@example.com"},
                    "group_ids": [],
                }
            },
            0,
        ),
    )

    auth_path = _build_main_auth_path(tmp_path, "main@example.com")
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {"email": "main@example.com", "access_token": "at-1"},
    )

    captured_update = {}

    def fake_update_account(token, account, **kwargs):
        captured_update.update(kwargs)
        captured_update["_account_id"] = account.get("id")
        return {"id": account.get("id")}

    monkeypatch.setattr(sub2api_sync, "_update_account", fake_update_account)
    # 创建分支不应被触发,放一个会爆的 stub 以便定位
    monkeypatch.setattr(
        sub2api_sync,
        "_create_account",
        lambda token, **k: pytest.fail("update 路径不应触发 _create_account"),
    )
    monkeypatch.setattr(sub2api_sync, "_delete_account", lambda token, account, **k: None)

    result = sub2api_sync.sync_main_codex_to_sub2api(str(auth_path))

    assert captured_update["_account_id"] == 555
    assert captured_update["proxy_id"] == 1
    assert result["account_id"] == 555


def test_sync_main_codex_to_sub2api_keeps_dedup_call(monkeypatch, tmp_path):
    """AC4 回归保护:_dedupe_managed_accounts 仍以 kind="main" 调用。

    历史 dedup 在 sync_main_codex_to_sub2api 入口处按 email key 归集同邮箱重复
    并保留 id 最大的(``_dedupe_managed_accounts`` 实现见 sub2api_sync.py:286)。
    本测试守住这个调用合约,防止重构时把 dedup 拆掉,造成同邮箱重复继续累积。
    """
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_resolve_proxy_id", lambda token: None)
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])

    dedup_calls = []

    def fake_dedupe(token, items, *, kind):
        dedup_calls.append(kind)
        return ({}, 0)

    monkeypatch.setattr(sub2api_sync, "_dedupe_managed_accounts", fake_dedupe)

    auth_path = _build_main_auth_path(tmp_path, "main@example.com")
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {"email": "main@example.com", "access_token": "at"},
    )
    monkeypatch.setattr(sub2api_sync, "_create_account", lambda token, **k: {"id": 1})

    sub2api_sync.sync_main_codex_to_sub2api(str(auth_path))

    # 仅一次,且是 main kind(不是 pool / free)
    assert dedup_calls == ["main"]


def test_sync_to_sub2api_pool_updates_proxy_id_on_existing(monkeypatch, tmp_path):
    """AC5:pool 池更新分支也应携带 proxy_id(原历史只在创建时绑代理一次)。

    本任务 D2 顺手对齐:配置代理改了下次同步立即生效,不再需要先去 sub2api 后台
    手动清空。配套行为与 main 同步路径一致。
    """
    monkeypatch.setattr(sub2api_sync, "SUB2API_PROXY", "Residential Pool")
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_resolve_proxy_id", lambda token: 1)
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])

    auth_path = tmp_path / "codex-pool@example.com-team-1.json"
    auth_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {
                "email": "pool@example.com",
                "status": "active",
                "auth_file": str(auth_path),
                "last_quota": None,
            }
        ],
    )
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {"email": "pool@example.com", "access_token": "at-pool"},
    )
    # 远端已经存在该邮箱账号 → 走更新分支
    monkeypatch.setattr(
        sub2api_sync,
        "_dedupe_managed_accounts",
        lambda token, items, *, kind: (
            {
                "pool@example.com": {
                    "id": 88,
                    "credentials": {},
                    "extra": {"autoteam_managed": True, "autoteam_kind": "pool", "autoteam_email": "pool@example.com"},
                    "group_ids": [],
                }
            },
            0,
        ),
    )

    captured_update = {}

    def fake_update_account(token, account, **kwargs):
        captured_update.update(kwargs)
        return {"id": account.get("id")}

    monkeypatch.setattr(sub2api_sync, "_update_account", fake_update_account)
    monkeypatch.setattr(sub2api_sync, "_delete_account", lambda token, account, **k: None)

    result = sub2api_sync.sync_to_sub2api()

    assert result["updated"] == 1
    assert captured_update["proxy_id"] == 1


def test_sync_main_codex_to_sub2api_skips_proxy_field_when_config_empty(monkeypatch, tmp_path):
    """AC6:SUB2API_PROXY 配置为空(_resolve_proxy_id 返回 None)时,payload 不写
    proxy_id 字段——远端原 proxy 保持不变,不主动清空(避免对未确认的 sub2api
    null 语义做假设,见 PRD D1)。
    """
    monkeypatch.setattr(sub2api_sync, "_login", lambda: "token")
    monkeypatch.setattr(sub2api_sync, "_resolve_group_binding", lambda token: ([], []))
    monkeypatch.setattr(sub2api_sync, "_resolve_proxy_id", lambda token: None)
    monkeypatch.setattr(sub2api_sync, "_list_openai_oauth_accounts", lambda token: [])
    monkeypatch.setattr(sub2api_sync, "_dedupe_managed_accounts", lambda token, items, *, kind: ({}, 0))

    auth_path = _build_main_auth_path(tmp_path, "main@example.com")
    monkeypatch.setattr(
        sub2api_sync,
        "_load_auth_data",
        lambda path: {"email": "main@example.com", "access_token": "at"},
    )

    captured = {}

    def fake_create_account(token, **kwargs):
        captured.update(kwargs)
        return {"id": 1}

    monkeypatch.setattr(sub2api_sync, "_create_account", fake_create_account)

    sub2api_sync.sync_main_codex_to_sub2api(str(auth_path))

    # _create_account 仍接收 proxy_id 参数(语义"调用方传入 None"),但内部不写入 payload。
    # 验证 payload 这一层的语义在另一条针对 _create_account 的 unit 测试已覆盖
    # (test_create_account_includes_proxy_id_only_when_provided)——这里只断言
    # sync 主路径会显式传 None,不会绕过 _resolve_proxy_id 的"配置为空"返回值。
    assert captured["proxy_id"] is None
