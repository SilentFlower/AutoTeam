"""免费号池单元测试。

覆盖 PR1 范围:

- 数据层 CRUD: load / save / find / add / update / delete 的边界与异常路径;
- 业务命令 ``cmd_generate_free_account``: Step A 失败、Step B 失败、全成功三条主路径;
- 内部辅助 ``_verify_team_removal``: 首次确认成功 / 两次都失败两种情况;
- ``check_free_quota``: 不读 active 状态外的免费号、auth_file 缺失的处理;
- ``delete_free_account``: 仅本地清理,远端清理标志不生效。

外部依赖(``manager`` / ``codex_auth`` / ``mail_provider`` / ``chatgpt_api``)全部走
``monkeypatch.setattr`` 注入伪实现,不触碰真实 HTTP / 文件系统。
"""

from __future__ import annotations

import json

import pytest

from autoteam import free_accounts

# ---------------------------------------------------------------------------
# 共用 fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def free_file(tmp_path, monkeypatch):
    """把模块级默认 free_accounts.json 重定向到 ``tmp_path`` 下,且关闭 admin 解析。"""
    target = tmp_path / "free_accounts.json"
    monkeypatch.setattr(free_accounts, "DEFAULT_FREE_ACCOUNTS_FILE", target)
    # 关闭 admin_registry 解析,避免触碰真实数据目录
    monkeypatch.setattr(free_accounts, "_resolve_admin_id", lambda admin_id: None)
    return target


def _make_record(email="a@example.com", status=free_accounts.FREE_STATUS_ACTIVE, **overrides):
    """构造测试用 record dict,留出 overrides 覆盖关键字段。"""
    rec = {
        "email": email,
        "password": "pwd",
        "auth_file": None,
        "status": status,
        "mail_provider": "cloudmail",
        "mail_account_id": 1,
        "team_residue": False,
        "created_at": 1000,
        "last_quota": None,
        "last_quota_at": None,
        "last_sub2api_synced_at": None,
    }
    rec.update(overrides)
    return rec


# ---------------------------------------------------------------------------
# 数据层
# ---------------------------------------------------------------------------


def test_load_free_returns_empty_when_file_missing(free_file):
    # 文件根本不存在 → 应返回 [],不抛
    assert free_accounts.load_free() == []


def test_save_and_load_round_trip(free_file):
    records = [_make_record(email="x@example.com")]
    free_accounts.save_free(records)

    loaded = free_accounts.load_free()
    assert loaded == records
    # 验证文件是合法 UTF-8 JSON
    raw = json.loads(free_file.read_text(encoding="utf-8"))
    assert raw == records


def test_save_free_is_atomic_replace(free_file, tmp_path):
    # 先写一份旧数据,再用 save_free 覆盖,确认 .tmp 中间文件不会残留
    free_accounts.save_free([_make_record(email="old@example.com")])
    free_accounts.save_free([_make_record(email="new@example.com")])

    assert not (free_file.parent / "free_accounts.json.tmp").exists()
    assert free_accounts.load_free()[0]["email"] == "new@example.com"


def test_find_free_is_case_insensitive(free_file):
    free_accounts.save_free([_make_record(email="Mixed@Example.com")])
    records = free_accounts.load_free()
    assert free_accounts.find_free(records, "MIXED@example.com") is not None
    assert free_accounts.find_free(records, "other@example.com") is None


def test_add_free_rejects_duplicate_email(free_file):
    free_accounts.add_free(_make_record(email="dup@example.com"))
    with pytest.raises(ValueError, match="已存在"):
        free_accounts.add_free(_make_record(email="DUP@example.com"))


def test_add_free_rejects_missing_email(free_file):
    with pytest.raises(ValueError, match="缺少 email"):
        free_accounts.add_free({"password": "x"})


def test_add_free_normalizes_missing_fields(free_file):
    # 调用方只给最少字段,add_free 应补齐 schema
    free_accounts.add_free({"email": "min@example.com", "password": "p"})
    rec = free_accounts.load_free()[0]
    assert rec["status"] == free_accounts.FREE_STATUS_ACTIVE
    assert rec["team_residue"] is False
    assert rec["last_quota"] is None
    assert "created_at" in rec


def test_update_free_only_changes_specified_fields(free_file):
    free_accounts.add_free(_make_record(email="u@example.com", status=free_accounts.FREE_STATUS_ACTIVE))
    updated = free_accounts.update_free(
        "u@example.com",
        status=free_accounts.FREE_STATUS_EXHAUSTED,
        last_quota={"primary_pct": 100},
    )

    assert updated["status"] == free_accounts.FREE_STATUS_EXHAUSTED
    assert updated["last_quota"] == {"primary_pct": 100}
    # 未指定字段应保留原值
    assert updated["password"] == "pwd"
    assert updated["mail_account_id"] == 1


def test_update_free_returns_none_when_email_missing(free_file):
    assert free_accounts.update_free("missing@example.com", status="active") is None


def test_delete_free_removes_only_target(free_file):
    free_accounts.add_free(_make_record(email="keep@example.com"))
    free_accounts.add_free(_make_record(email="drop@example.com"))

    deleted = free_accounts.delete_free("DROP@example.com")
    assert deleted is not None and deleted["email"] == "drop@example.com"
    assert [r["email"] for r in free_accounts.load_free()] == ["keep@example.com"]


def test_delete_free_returns_none_when_missing(free_file):
    assert free_accounts.delete_free("missing@example.com") is None


# ---------------------------------------------------------------------------
# _verify_team_removal
# ---------------------------------------------------------------------------


def test_verify_team_removal_returns_true_when_email_absent(monkeypatch):
    """成员列表中不含目标邮箱 → 立即返回 True,不调 remove_from_team。"""
    monkeypatch.setattr(
        "autoteam.account_ops.fetch_team_state",
        lambda chatgpt_api: ([{"email": "other@example.com"}], []),
    )

    def _should_not_call(*args, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("不应再调用 remove_from_team")

    monkeypatch.setattr("autoteam.manager.remove_from_team", _should_not_call)

    assert free_accounts._verify_team_removal(object(), "target@example.com", retries=2) is True


def test_verify_team_removal_returns_false_when_residue_persists(monkeypatch):
    """两次都看到残留 → 返回 False;期间会触发一次 remove_from_team 重试。"""
    members = [{"email": "target@example.com"}]
    monkeypatch.setattr(
        "autoteam.account_ops.fetch_team_state",
        lambda chatgpt_api: (members, []),
    )

    remove_calls = []

    def _record_remove(_chatgpt, email, **_kwargs):
        remove_calls.append(email)
        return False

    monkeypatch.setattr("autoteam.manager.remove_from_team", _record_remove)

    assert free_accounts._verify_team_removal(object(), "TARGET@example.com", retries=2, sleep_seconds=0) is False
    assert remove_calls == ["TARGET@example.com"]


def test_verify_team_removal_handles_fetch_failure(monkeypatch):
    """fetch_team_state 抛错 → 不抛、返回 False,调用方据此打 team_residue。"""

    def _raise(*_args, **_kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr("autoteam.account_ops.fetch_team_state", _raise)
    monkeypatch.setattr("autoteam.manager.remove_from_team", lambda *a, **k: True)

    assert free_accounts._verify_team_removal(object(), "x@example.com", retries=2) is False


# ---------------------------------------------------------------------------
# cmd_generate_free_account: Step A 失败 / Step B 失败 / 全成功
# ---------------------------------------------------------------------------


class _FakeChatGPT:
    """伪 ChatGPTTeamAPI:仅记录 start/stop 顺序,不发任何 HTTP。"""

    def __init__(self):
        self.events = []

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


class _FakeMailClient:
    provider_name = "cloudmail"

    def __init__(self, mail_id=42, email="free@example.com"):
        self.mail_id = mail_id
        self.email = email
        self.deleted_ids: list[int] = []
        self.logged_in = False

    def login(self):
        self.logged_in = True

    def create_temp_email(self):
        return self.mail_id, self.email

    def delete_account(self, account_id):
        self.deleted_ids.append(account_id)
        return {"code": 200}


@pytest.fixture
def patch_generation_deps(monkeypatch, free_file):
    """通用注入:把生成流程依赖的外部函数全替换成可控 stub。

    返回 dict 让用例按需覆盖单个 stub(不返回时使用默认成功路径)。
    """
    state: dict = {
        "mail_client": _FakeMailClient(),
        "chatgpt": _FakeChatGPT(),
        "invite_link": "https://invite.example/link",
        "step_a_result": True,
        "login_result": {
            "ok": True,
            "bundle": {
                "email": "free@example.com",
                "plan_type": "team",
                "access_token": "tok",
                "refresh_token": "ref",
                "account_id": "acc-1",
            },
        },
        "remove_status": "removed",
        "verify_remove": True,
        "auth_file_path": "/tmp/auths/codex-free@example.com-team-abcd1234.json",
    }

    monkeypatch.setattr(free_accounts, "get_mail_client", lambda: state["mail_client"], raising=False)
    # _generate_one_free_account 内部用 from-import,改 module-level binding 不一定生效;
    # 同时打两条路径以覆盖。
    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda *a, **k: state["mail_client"])

    chatgpt_factory_calls = {"count": 0}

    def _factory():
        chatgpt_factory_calls["count"] += 1
        return state["chatgpt"]

    monkeypatch.setattr("autoteam.chatgpt_api.ChatGPTTeamAPI", _factory)

    monkeypatch.setattr("autoteam.manager.invite_to_team", lambda *_a, **_k: True)
    monkeypatch.setattr(free_accounts, "_fetch_invite_link", lambda *_a, **_k: state["invite_link"])
    monkeypatch.setattr(free_accounts, "_run_invite_login_step_a", lambda *_a, **_k: state["step_a_result"])
    monkeypatch.setattr("autoteam.manager._login_codex_with_result", lambda *a, **k: state["login_result"])
    monkeypatch.setattr(
        "autoteam.manager.remove_from_team",
        lambda *a, **k: state["remove_status"],
    )
    monkeypatch.setattr(free_accounts, "_verify_team_removal", lambda *_a, **_k: state["verify_remove"])
    monkeypatch.setattr(
        "autoteam.codex_auth.save_auth_file",
        lambda bundle, **_k: state["auth_file_path"],
    )
    # cmd_generate_free_account 末尾会调 sub2api_sync.sync_free_to_sub2api(),
    # 单元测试不应触达真实 HTTP——把它静默化成 no-op。
    state["sync_calls"] = []
    monkeypatch.setattr(
        "autoteam.sub2api_sync.sync_free_to_sub2api",
        lambda: state["sync_calls"].append("called"),
    )

    state["factory_calls"] = chatgpt_factory_calls
    return state


def test_generate_records_active_when_step_a_and_b_succeed(free_file, patch_generation_deps):
    """全成功路径 → 落库 status=active,带 auth_file,team_residue=False。"""
    result = free_accounts.cmd_generate_free_account(count=1)

    assert len(result) == 1
    rec = result[0]
    assert rec["email"] == "free@example.com"
    assert rec["status"] == free_accounts.FREE_STATUS_ACTIVE
    assert rec["auth_file"] == patch_generation_deps["auth_file_path"]
    assert rec["team_residue"] is False
    assert rec["mail_provider"] == "cloudmail"
    assert rec["mail_account_id"] == 42

    # 持久化验证
    assert free_accounts.load_free() == [rec]


def test_generate_skips_persist_when_step_a_fails(free_file, patch_generation_deps):
    """Step A 失败(邀请链接登录失败)→ 不落库,且会尝试删除临时邮箱。"""
    patch_generation_deps["step_a_result"] = False

    result = free_accounts.cmd_generate_free_account(count=1)

    assert result == []
    assert free_accounts.load_free() == []
    # 失败临时邮箱应被请求删除
    assert patch_generation_deps["mail_client"].deleted_ids == [42]


def test_generate_records_auth_failed_when_step_b_fails(free_file, patch_generation_deps):
    """Step A 成功 + Step B 失败 → 半成品落库 status=auth_failed, auth_file=None。"""
    patch_generation_deps["login_result"] = {"ok": False, "bundle": None}

    result = free_accounts.cmd_generate_free_account(count=1)

    assert len(result) == 1
    rec = result[0]
    assert rec["status"] == free_accounts.FREE_STATUS_AUTH_FAILED
    assert rec["auth_file"] is None
    # 即使 OAuth 失败也不应删邮箱(账号要保留供 UI 重试)
    assert patch_generation_deps["mail_client"].deleted_ids == []


def test_generate_marks_team_residue_when_remove_unverified(free_file, patch_generation_deps):
    """OAuth 成功但 _verify_team_removal 返回 False → team_residue=true。"""
    patch_generation_deps["verify_remove"] = False

    result = free_accounts.cmd_generate_free_account(count=1)

    assert len(result) == 1
    assert result[0]["team_residue"] is True
    # team_residue 不影响 status:仍是 active
    assert result[0]["status"] == free_accounts.FREE_STATUS_ACTIVE


def test_generate_downgrades_to_auth_failed_when_save_auth_file_raises(monkeypatch, free_file, patch_generation_deps):
    """OAuth 拿到 bundle 但 save_auth_file 抛错 → 降级为 auth_failed,流程不崩。"""

    def _raise(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("autoteam.codex_auth.save_auth_file", _raise)

    result = free_accounts.cmd_generate_free_account(count=1)

    assert len(result) == 1
    rec = result[0]
    assert rec["status"] == free_accounts.FREE_STATUS_AUTH_FAILED
    assert rec["auth_file"] is None
    # 落库一致:从持久化里再读出来也是 auth_failed
    assert free_accounts.load_free()[0]["status"] == free_accounts.FREE_STATUS_AUTH_FAILED


def test_generate_count_zero_returns_empty(free_file, patch_generation_deps):
    assert free_accounts.cmd_generate_free_account(count=0) == []
    assert free_accounts.load_free() == []


def test_generate_continues_on_per_iteration_exception(monkeypatch, free_file, patch_generation_deps):
    """单条异常不阻塞其余迭代:第一次抛错,第二次成功落库。"""
    calls = {"n": 0}

    real_generate_one = free_accounts._generate_one_free_account

    def _flaky(factory, *, admin_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("第一次模拟失败")
        return real_generate_one(factory, admin_id=admin_id)

    monkeypatch.setattr(free_accounts, "_generate_one_free_account", _flaky)

    result = free_accounts.cmd_generate_free_account(count=2)
    assert len(result) == 1
    # 落库的应该是第二次的结果(因为第一次抛了异常)
    assert result[0]["email"] == "free@example.com"


def test_generate_swallows_create_temp_email_failure(monkeypatch, free_file, patch_generation_deps):
    """``mail_client.create_temp_email`` 抛错 → 当前迭代失败,但 cmd 不向上抛。"""
    fail_client = _FakeMailClient()

    def _raise():
        raise RuntimeError("mail provider down")

    fail_client.create_temp_email = _raise  # type: ignore[method-assign]
    patch_generation_deps["mail_client"] = fail_client
    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda *a, **k: fail_client)

    # 不应抛异常,应返回空列表(被 cmd_generate_free_account 的外层 try 吞掉)
    result = free_accounts.cmd_generate_free_account(count=1)
    assert result == []
    assert free_accounts.load_free() == []


# ---------------------------------------------------------------------------
# check_free_quota
# ---------------------------------------------------------------------------


def test_check_free_quota_skips_records_without_auth_file(free_file, monkeypatch):
    free_accounts.add_free(_make_record(email="noauth@example.com", auth_file=None))

    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda *_a, **_k: pytest.fail("无 auth_file 不应调 check_codex_quota"),
    )

    summary = free_accounts.check_free_quota()
    assert summary == {"noauth@example.com": "no_auth"}


def test_check_free_quota_writes_snapshot_on_ok(tmp_path, free_file, monkeypatch):
    auth_path = tmp_path / "codex-ok@example.com-team.json"
    auth_path.write_text(json.dumps({"access_token": "tok", "refresh_token": "ref"}), encoding="utf-8")

    free_accounts.add_free(_make_record(email="ok@example.com", auth_file=str(auth_path)))

    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda *_a, **_k: ("ok", {"primary_pct": 30, "weekly_pct": 5}),
    )
    monkeypatch.setattr("autoteam.codex_auth.refresh_access_token", lambda *_a, **_k: pytest.fail("不应触发刷新"))

    summary = free_accounts.check_free_quota(emails=["ok@example.com"])
    assert summary == {"ok@example.com": "ok"}

    saved = free_accounts.load_free()[0]
    assert saved["last_quota"] == {"primary_pct": 30, "weekly_pct": 5}
    assert saved["last_quota_at"] is not None
    assert saved["status"] == free_accounts.FREE_STATUS_ACTIVE


def test_check_free_quota_marks_exhausted(tmp_path, free_file, monkeypatch):
    auth_path = tmp_path / "codex-exh@example.com-team.json"
    auth_path.write_text(json.dumps({"access_token": "tok"}), encoding="utf-8")

    free_accounts.add_free(_make_record(email="exh@example.com", auth_file=str(auth_path)))

    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda *_a, **_k: ("exhausted", {"primary_pct": 100, "weekly_pct": 100, "resets_at": 9999}),
    )

    summary = free_accounts.check_free_quota()
    assert summary == {"exh@example.com": "exhausted"}

    saved = free_accounts.load_free()[0]
    assert saved["status"] == free_accounts.FREE_STATUS_EXHAUSTED


def test_check_free_quota_default_skips_auth_failed(free_file, monkeypatch):
    """默认刷新只覆盖 active/exhausted,半成品 auth_failed 不被自动触发。"""
    free_accounts.add_free(_make_record(email="bad@example.com", status=free_accounts.FREE_STATUS_AUTH_FAILED))

    monkeypatch.setattr(
        "autoteam.codex_auth.check_codex_quota",
        lambda *_a, **_k: pytest.fail("auth_failed 不应被默认刷新"),
    )

    summary = free_accounts.check_free_quota()
    assert summary == {}


def test_check_free_quota_reports_not_found_for_unknown_email(free_file):
    summary = free_accounts.check_free_quota(emails=["ghost@example.com"])
    assert summary == {"ghost@example.com": "not_found"}


# ---------------------------------------------------------------------------
# delete_free_account: 仅本地清理
# ---------------------------------------------------------------------------


def test_delete_free_account_removes_local_auth_file_and_record(tmp_path, free_file):
    auth_path = tmp_path / "codex-del@example.com-team.json"
    auth_path.write_text("{}", encoding="utf-8")

    free_accounts.add_free(_make_record(email="del@example.com", auth_file=str(auth_path)))

    cleanup = free_accounts.delete_free_account("del@example.com")
    assert cleanup["local_record"] is True
    assert auth_path.name in cleanup["local_auth_files"]
    assert not auth_path.exists()
    assert free_accounts.load_free() == []


def test_delete_free_account_handles_missing_auth_file(tmp_path, free_file):
    """auth_file 字段指向不存在的路径 → 不抛,只删 JSON 条目。"""
    free_accounts.add_free(_make_record(email="lost@example.com", auth_file=str(tmp_path / "missing.json")))

    cleanup = free_accounts.delete_free_account("lost@example.com")
    assert cleanup["local_record"] is True
    assert cleanup["local_auth_files"] == []  # 文件不存在 → 不计入摘要


def test_delete_free_account_returns_empty_summary_when_not_found(free_file):
    cleanup = free_accounts.delete_free_account("nope@example.com")
    assert cleanup == {
        "local_record": False,
        "local_auth_files": [],
        "sub2api_accounts": [],
        "cloudmail_deleted": False,
    }


def test_delete_free_account_cleanup_remote_flag_does_nothing_in_pr1(tmp_path, free_file, monkeypatch, caplog):
    """PR1 边界:cleanup_remote=True 当前应仅写 info 日志,不实际清理远端。"""
    auth_path = tmp_path / "codex-pr1@example.com.json"
    auth_path.write_text("{}", encoding="utf-8")
    free_accounts.add_free(_make_record(email="pr1@example.com", auth_file=str(auth_path)))

    cleanup = free_accounts.delete_free_account("pr1@example.com", cleanup_remote=True)

    # 远端清理字段保持 PR1 默认值
    assert cleanup["sub2api_accounts"] == []
    assert cleanup["cloudmail_deleted"] is False
    # 本地仍按原计划清理
    assert cleanup["local_record"] is True
    assert not auth_path.exists()


# ---------------------------------------------------------------------------
# 路径解析:多 admin 隔离
# ---------------------------------------------------------------------------


def test_free_accounts_file_uses_admin_data_dir_when_admin_id_given(tmp_path, monkeypatch):
    """显式传 admin_id 应映射到 ``data/admins/{aid}/free_accounts.json``。"""
    fake_dir = tmp_path / "admins" / "abcd1234"
    fake_dir.mkdir(parents=True)

    monkeypatch.setattr("autoteam.admin_registry.admin_data_dir", lambda admin_id: tmp_path / "admins" / admin_id)

    path = free_accounts._free_accounts_file("abcd1234")
    assert path == fake_dir / "free_accounts.json"


def test_free_accounts_file_falls_back_to_default_without_admin(tmp_path, monkeypatch):
    """无激活 admin 时回退到 ``DEFAULT_FREE_ACCOUNTS_FILE``。"""
    custom = tmp_path / "fallback.json"
    monkeypatch.setattr(free_accounts, "DEFAULT_FREE_ACCOUNTS_FILE", custom)
    monkeypatch.setattr(free_accounts, "_resolve_admin_id", lambda admin_id: None)

    assert free_accounts._free_accounts_file() == custom
