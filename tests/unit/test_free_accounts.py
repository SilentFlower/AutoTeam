"""免费号池单元测试。

覆盖范围:

- 数据层 CRUD: load / save / find / add / update / delete 的边界与异常路径(PR1);
- 业务命令 ``cmd_generate_free_account``: Step A 失败、Step B 失败、全成功三条主路径(PR1);
- 内部辅助 ``_verify_team_removal``: 首次确认成功 / 两次都失败两种情况(PR1);
- ``check_free_quota``: 不读 active 状态外的免费号、auth_file 缺失的处理(PR1);
- ``delete_free_account``:
  * ``cleanup_remote=False`` 仅本地清理(PR1 兼容路径);
  * ``cleanup_remote=True`` 完整 F3 级联(本地 auth_file + sub2api + cloudmail + JSON 条目)(PR3);
  * 单步失败不阻塞:auth_file unlink / sub2api / cloudmail 任一抛错都不影响其他步骤(PR3)。

外部依赖(``manager`` / ``codex_auth`` / ``mail_provider`` / ``chatgpt_api`` /
``sub2api_sync``)全部走 ``monkeypatch.setattr`` 注入伪实现,不触碰真实 HTTP / 文件系统。
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

    `_login_codex_with_result` 在新流程下被调用两次(Step B + Step C):

    - 默认 ``login_results`` 序列提供 [team_bundle, personal_bundle],对应正常路径
    - 用例若设置 ``state["login_result"]``(非 None),所有调用都返回该 dict
      (兼容老用例的"全失败/单一返回值"语义)
    - 否则按 ``login_results`` 顺序返回(超出长度时返回最后一项)
    """
    default_team_bundle = {
        "ok": True,
        "bundle": {
            "email": "free@example.com",
            "plan_type": "team",
            "access_token": "tok-team",
            "refresh_token": "ref-team",
            "account_id": "acc-1",
        },
    }
    default_personal_bundle = {
        "ok": True,
        "bundle": {
            "email": "free@example.com",
            "plan_type": "personal",
            "access_token": "tok-personal",
            "refresh_token": "ref-personal",
            "account_id": "acc-2",
        },
    }

    state: dict = {
        "mail_client": _FakeMailClient(),
        "chatgpt": _FakeChatGPT(),
        "invite_link": "https://invite.example/link",
        "step_a_result": True,
        # 老接口:设非 None 时所有调用都返回它,适合"全部失败"类用例
        "login_result": None,
        # 新接口:Step B + Step C 序列返回
        "login_results": [default_team_bundle, default_personal_bundle],
        "remove_status": "removed",
        "verify_remove": True,
        "auth_file_path": "/tmp/auths/codex-free@example.com-team-abcd1234.json",
        "login_calls": [],
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

    def _login(*args, **kwargs):
        state["login_calls"].append({"args": args, "kwargs": kwargs})
        if state["login_result"] is not None:
            return state["login_result"]
        idx = len(state["login_calls"]) - 1
        seq = state["login_results"]
        if idx < len(seq):
            return seq[idx]
        return seq[-1] if seq else {"ok": False, "bundle": None}

    monkeypatch.setattr("autoteam.manager._login_codex_with_result", _login)
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


def test_generate_records_auth_failed_when_step_c_fails(free_file, patch_generation_deps):
    """Step B 成功 + Step C 重授权失败 → status=auth_failed,auth_file=None。

    PRD R5:Step C 失败 沿用 FREE_STATUS_AUTH_FAILED 状态,不引入新枚举。
    """
    patch_generation_deps["login_results"] = [
        {"ok": True, "bundle": {"plan_type": "team", "email": "free@example.com"}},
        {"ok": False, "bundle": None, "error_detail": "OAuth 二次授权失败"},
    ]

    result = free_accounts.cmd_generate_free_account(count=1)

    assert len(result) == 1
    rec = result[0]
    assert rec["status"] == free_accounts.FREE_STATUS_AUTH_FAILED
    assert rec["auth_file"] is None


def test_generate_passes_allow_non_team_to_step_c(free_file, patch_generation_deps):
    """验证 Step C(第二次 _login_codex_with_result)调用带 allow_non_team=True。

    Step B 默认调用不传 allow_non_team(保留主号路径行为);Step C 因为账号已被 remove,
    plan 必然是 personal,必须放宽 plan check 否则会被 _reject_non_team 拒绝。
    """
    free_accounts.cmd_generate_free_account(count=1)

    calls = patch_generation_deps["login_calls"]
    assert len(calls) == 2, f"应调用两次 _login_codex_with_result(Step B + Step C),实际 {len(calls)} 次"
    # Step B:不传 allow_non_team(默认 False)
    assert calls[0]["kwargs"].get("allow_non_team") in (None, False)
    # Step C:必须传 allow_non_team=True
    assert calls[1]["kwargs"].get("allow_non_team") is True


def test_generate_skips_step_c_when_step_b_fails(free_file, patch_generation_deps):
    """Step B 失败时不调用 Step C,直接落库 auth_failed。避免在没必要的情况下浪费一次 OAuth。"""
    patch_generation_deps["login_result"] = {"ok": False, "bundle": None}

    result = free_accounts.cmd_generate_free_account(count=1)

    assert len(result) == 1
    assert result[0]["status"] == free_accounts.FREE_STATUS_AUTH_FAILED
    # Step B 失败 → 只调 1 次,Step C 被跳过
    assert len(patch_generation_deps["login_calls"]) == 1


def test_generate_uses_step_c_bundle_for_auth_file(free_file, patch_generation_deps, monkeypatch):
    """落库的 auth_file 必须基于 Step C(personal)bundle,Step B 的 team bundle 应被丢弃。

    若错误使用 Step B bundle 写 auth_file,推 sub2api 时会拿到已被 invalidate 的 token,
    回到 401 token_invalidated 的原始 bug。
    """
    saved_bundles: list[dict] = []

    def _spy_save(bundle, **_k):
        saved_bundles.append(bundle)
        return patch_generation_deps["auth_file_path"]

    monkeypatch.setattr("autoteam.codex_auth.save_auth_file", _spy_save)

    free_accounts.cmd_generate_free_account(count=1)

    assert len(saved_bundles) == 1
    assert saved_bundles[0]["plan_type"] == "personal", f"应落 Step C personal bundle,实际 {saved_bundles[0]}"


# ---------------------------------------------------------------------------
# reauth_free_account
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_reauth_deps(monkeypatch, free_file):
    """reauth_free_account 的依赖 stub。"""
    state: dict = {
        "mail_client": _FakeMailClient(),
        "login_result": {
            "ok": True,
            "bundle": {
                "email": "free@example.com",
                "plan_type": "personal",
                "access_token": "tok-new",
                "refresh_token": "ref-new",
            },
        },
        "auth_file_path": "/tmp/auths/codex-free@example.com-personal-deadbeef.json",
        "login_calls": [],
        "sync_calls": [],
    }

    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda *a, **k: state["mail_client"])

    def _login(*args, **kwargs):
        state["login_calls"].append({"args": args, "kwargs": kwargs})
        return state["login_result"]

    monkeypatch.setattr("autoteam.manager._login_codex_with_result", _login)
    monkeypatch.setattr(
        "autoteam.codex_auth.save_auth_file",
        lambda bundle, **_k: state["auth_file_path"],
    )
    monkeypatch.setattr(
        "autoteam.sub2api_sync.sync_free_to_sub2api",
        lambda: state["sync_calls"].append("called"),
    )

    return state


def test_reauth_records_active_when_oauth_succeeds(free_file, patch_reauth_deps):
    """reauth 成功路径:覆盖 auth_file,status 设回 active,异步触发 sub2api 同步。"""
    free_accounts.add_free(_make_record(email="free@example.com", status=free_accounts.FREE_STATUS_AUTH_FAILED))

    result = free_accounts.reauth_free_account("free@example.com")

    assert result["ok"] is True
    assert result["status"] == free_accounts.FREE_STATUS_ACTIVE
    assert result["auth_file"] == patch_reauth_deps["auth_file_path"]

    # 持久化:status / auth_file 已落盘
    rec = free_accounts.load_free()[0]
    assert rec["status"] == free_accounts.FREE_STATUS_ACTIVE
    assert rec["auth_file"] == patch_reauth_deps["auth_file_path"]

    # 调用一次 OAuth 必须带 allow_non_team=True
    assert len(patch_reauth_deps["login_calls"]) == 1
    assert patch_reauth_deps["login_calls"][0]["kwargs"].get("allow_non_team") is True

    # 成功后触发 sub2api 同步
    assert patch_reauth_deps["sync_calls"] == ["called"]


def test_reauth_records_auth_failed_when_oauth_fails(free_file, patch_reauth_deps):
    """reauth 失败路径:status=auth_failed,auth_file=None,不触发 sub2api 同步。"""
    patch_reauth_deps["login_result"] = {
        "ok": False,
        "bundle": None,
        "error_detail": "OAuth 失败:邮箱 OTP 超时",
    }

    free_accounts.add_free(_make_record(email="free@example.com"))

    result = free_accounts.reauth_free_account("free@example.com")

    assert result["ok"] is False
    assert result["status"] == free_accounts.FREE_STATUS_AUTH_FAILED
    assert result["auth_file"] is None
    assert result["error_detail"] == "OAuth 失败:邮箱 OTP 超时"

    rec = free_accounts.load_free()[0]
    assert rec["status"] == free_accounts.FREE_STATUS_AUTH_FAILED
    assert rec["auth_file"] is None

    # 失败不触发 sub2api 同步
    assert patch_reauth_deps["sync_calls"] == []


def test_reauth_raises_on_concurrent_same_email(free_file, patch_reauth_deps):
    """同 email 已在 reauth 中再次触发 → 抛 RuntimeError(由 API 层翻译为 409)。"""
    free_accounts.add_free(_make_record(email="free@example.com"))

    # 模拟该 email 已在锁集合中
    free_accounts._reauth_in_progress.add("free@example.com")
    try:
        with pytest.raises(RuntimeError, match="正在重新授权中"):
            free_accounts.reauth_free_account("free@example.com")
    finally:
        free_accounts._reauth_in_progress.discard("free@example.com")


def test_reauth_raises_when_record_missing(free_file, patch_reauth_deps):
    """目标 email 不在 free_accounts.json 时 → 抛 RuntimeError。"""
    with pytest.raises(RuntimeError, match="找不到 FREE 号记录"):
        free_accounts.reauth_free_account("ghost@example.com")


def test_reauth_raises_when_password_missing(free_file, patch_reauth_deps):
    """record 缺 password 字段 → 抛 RuntimeError(无法走 OAuth)。"""
    free_accounts.add_free(_make_record(email="free@example.com", password=""))

    with pytest.raises(RuntimeError, match="缺少密码字段"):
        free_accounts.reauth_free_account("free@example.com")


def test_reauth_releases_lock_on_exception(free_file, patch_reauth_deps, monkeypatch):
    """OAuth 流程内部抛异常时,_reauth_in_progress 必须被释放,允许重试。"""

    def _raise(*_a, **_k):
        raise RuntimeError("playwright crashed")

    monkeypatch.setattr("autoteam.manager._login_codex_with_result", _raise)
    free_accounts.add_free(_make_record(email="free@example.com"))

    with pytest.raises(RuntimeError, match="playwright crashed"):
        free_accounts.reauth_free_account("free@example.com")

    # 锁集合已清空,可重新 reauth
    assert "free@example.com" not in free_accounts._reauth_in_progress


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
# delete_free_account: 本地清理(cleanup_remote=False) + 级联清理(cleanup_remote=True)
# ---------------------------------------------------------------------------


def test_delete_free_account_removes_local_auth_file_and_record(tmp_path, free_file):
    """``cleanup_remote=False`` 路径:仅删 auth_file + JSON 条目,不触达任何远端。"""
    auth_path = tmp_path / "codex-del@example.com-team.json"
    auth_path.write_text("{}", encoding="utf-8")

    free_accounts.add_free(_make_record(email="del@example.com", auth_file=str(auth_path)))

    cleanup = free_accounts.delete_free_account("del@example.com", cleanup_remote=False)
    assert cleanup["local_record"] is True
    assert auth_path.name in cleanup["local_auth_files"]
    assert cleanup["sub2api_accounts"] == []
    assert cleanup["cloudmail_deleted"] is False
    assert not auth_path.exists()
    assert free_accounts.load_free() == []


def test_delete_free_account_handles_missing_auth_file(tmp_path, free_file):
    """auth_file 字段指向不存在的路径 → 不抛,只删 JSON 条目。"""
    free_accounts.add_free(_make_record(email="lost@example.com", auth_file=str(tmp_path / "missing.json")))

    cleanup = free_accounts.delete_free_account("lost@example.com", cleanup_remote=False)
    assert cleanup["local_record"] is True
    assert cleanup["local_auth_files"] == []  # 文件不存在 → 不计入摘要


def test_delete_free_account_returns_empty_summary_when_not_found(free_file):
    cleanup = free_accounts.delete_free_account("nope@example.com", cleanup_remote=False)
    assert cleanup == {
        "local_record": False,
        "local_auth_files": [],
        "sub2api_accounts": [],
        "cloudmail_deleted": False,
    }


def test_delete_free_account_cascade_calls_sub2api_and_cloudmail(tmp_path, free_file, monkeypatch):
    """PR3 完整级联:auth_file + sub2api 远端 + cloudmail 邮箱 + JSON 条目都被处理。"""
    auth_path = tmp_path / "codex-cascade@example.com.json"
    auth_path.write_text("{}", encoding="utf-8")

    free_accounts.add_free(
        _make_record(
            email="cascade@example.com",
            auth_file=str(auth_path),
            mail_provider="cloudmail",
            mail_account_id=77,
        )
    )

    sub2api_calls: list[dict] = []

    def _stub_sub2api(email, *, auth_names=None):
        sub2api_calls.append({"email": email, "auth_names": list(auth_names or [])})
        return {"deleted": [f"sub2api-{auth_path.name}"], "count": 1}

    monkeypatch.setattr("autoteam.sub2api_sync.delete_account_from_sub2api", _stub_sub2api)

    mail_calls: list[int] = []

    class _StubMailClient:
        provider_name = "cloudmail"

        def login(self):
            mail_calls.append(-1)

        def delete_account(self, account_id):
            mail_calls.append(account_id)
            return {"code": 200}

    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda *_a, **_k: _StubMailClient())

    cleanup = free_accounts.delete_free_account("cascade@example.com")

    # 本地 auth_file
    assert cleanup["local_record"] is True
    assert auth_path.name in cleanup["local_auth_files"]
    assert not auth_path.exists()
    # sub2api 远端
    assert cleanup["sub2api_accounts"] == [f"sub2api-{auth_path.name}"]
    assert sub2api_calls == [{"email": "cascade@example.com", "auth_names": [auth_path.name]}]
    # cloudmail
    assert cleanup["cloudmail_deleted"] is True
    assert 77 in mail_calls
    # JSON 条目
    assert free_accounts.load_free() == []


def test_delete_free_account_cascade_continues_when_sub2api_fails(tmp_path, free_file, monkeypatch):
    """sub2api 删除抛错 → 不阻塞本地 / cloudmail / JSON 条目的清理(PRD F3 单步独立)。"""
    auth_path = tmp_path / "codex-isolate@example.com.json"
    auth_path.write_text("{}", encoding="utf-8")

    free_accounts.add_free(
        _make_record(
            email="isolate@example.com",
            auth_file=str(auth_path),
            mail_account_id=99,
        )
    )

    def _raise(*_a, **_k):
        raise RuntimeError("sub2api down")

    monkeypatch.setattr("autoteam.sub2api_sync.delete_account_from_sub2api", _raise)

    class _OkMail:
        provider_name = "cloudmail"

        def login(self):
            pass

        def delete_account(self, _id):
            return {"code": 200}

    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda *_a, **_k: _OkMail())

    cleanup = free_accounts.delete_free_account("isolate@example.com")

    # sub2api 失败但其他步骤正常
    assert cleanup["sub2api_accounts"] == []
    assert cleanup["local_record"] is True
    assert auth_path.name in cleanup["local_auth_files"]
    assert cleanup["cloudmail_deleted"] is True
    assert free_accounts.load_free() == []


def test_delete_free_account_cascade_continues_when_cloudmail_fails(tmp_path, free_file, monkeypatch):
    """cloudmail 删除抛错 → sub2api 仍能完成,JSON 条目仍被删。"""
    auth_path = tmp_path / "codex-mailfail@example.com.json"
    auth_path.write_text("{}", encoding="utf-8")

    free_accounts.add_free(
        _make_record(
            email="mailfail@example.com",
            auth_file=str(auth_path),
            mail_account_id=11,
        )
    )

    monkeypatch.setattr(
        "autoteam.sub2api_sync.delete_account_from_sub2api",
        lambda *_a, **_k: {"deleted": ["x"], "count": 1},
    )

    class _BoomMail:
        provider_name = "cloudmail"

        def login(self):
            pass

        def delete_account(self, _id):
            raise RuntimeError("mail provider 5xx")

    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda *_a, **_k: _BoomMail())

    cleanup = free_accounts.delete_free_account("mailfail@example.com")

    assert cleanup["cloudmail_deleted"] is False
    # 其余步骤照旧
    assert cleanup["sub2api_accounts"] == ["x"]
    assert cleanup["local_record"] is True
    assert free_accounts.load_free() == []


def test_delete_free_account_cascade_continues_when_auth_unlink_fails(tmp_path, free_file, monkeypatch):
    """删 auth_file 抛错 → sub2api / cloudmail / JSON 条目仍按原计划处理。"""
    auth_path = tmp_path / "codex-rofs@example.com.json"
    auth_path.write_text("{}", encoding="utf-8")

    free_accounts.add_free(
        _make_record(
            email="rofs@example.com",
            auth_file=str(auth_path),
            mail_account_id=22,
        )
    )

    # 把 Path.unlink 替换成抛错(模拟权限不够 / 卷只读)
    real_unlink = type(auth_path).unlink

    def _raise(self, *args, **kwargs):
        if str(self) == str(auth_path):
            raise PermissionError("readonly fs")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr("pathlib.Path.unlink", _raise)
    monkeypatch.setattr(
        "autoteam.sub2api_sync.delete_account_from_sub2api",
        lambda *_a, **_k: {"deleted": ["a"], "count": 1},
    )

    class _OkMail:
        provider_name = "cloudmail"

        def login(self):
            pass

        def delete_account(self, _id):
            return {"code": 200}

    monkeypatch.setattr("autoteam.mail_provider.get_mail_client", lambda *_a, **_k: _OkMail())

    cleanup = free_accounts.delete_free_account("rofs@example.com")

    # auth_file 删除失败 → 不计入摘要,但其他步骤仍执行
    assert cleanup["local_auth_files"] == []
    assert cleanup["sub2api_accounts"] == ["a"]
    assert cleanup["cloudmail_deleted"] is True
    assert cleanup["local_record"] is True


def test_delete_free_account_cleanup_remote_false_skips_sub2api_and_cloudmail(tmp_path, free_file, monkeypatch):
    """``cleanup_remote=False`` 不应触达任何远端服务(回归保险:PR1 行为不破)。"""
    auth_path = tmp_path / "codex-local@example.com.json"
    auth_path.write_text("{}", encoding="utf-8")
    free_accounts.add_free(
        _make_record(
            email="local@example.com",
            auth_file=str(auth_path),
            mail_account_id=33,
        )
    )

    def _fail(*_a, **_k):
        raise AssertionError("cleanup_remote=False 不应调 sub2api 删除")

    monkeypatch.setattr("autoteam.sub2api_sync.delete_account_from_sub2api", _fail)
    monkeypatch.setattr(
        "autoteam.mail_provider.get_mail_client",
        lambda *_a, **_k: pytest.fail("cleanup_remote=False 不应调 cloudmail"),
    )

    cleanup = free_accounts.delete_free_account("local@example.com", cleanup_remote=False)

    assert cleanup["sub2api_accounts"] == []
    assert cleanup["cloudmail_deleted"] is False
    # 本地清理仍然执行
    assert cleanup["local_record"] is True
    assert auth_path.name in cleanup["local_auth_files"]
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
