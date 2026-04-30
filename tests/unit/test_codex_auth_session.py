from autoteam import codex_auth


def test_login_codex_via_session_uses_unified_flow_and_returns_bundle(monkeypatch):
    events = []

    class FakeSessionCodexAuthFlow:
        def __init__(self, **kwargs):
            events.append(("init", kwargs))

        def start(self):
            events.append(("start", None))
            return {"step": "completed", "detail": None}

        def complete(self):
            events.append(("complete", None))
            return {"bundle": {"email": "owner@example.com", "plan_type": "team"}}

        def stop(self):
            events.append(("stop", None))

    monkeypatch.setattr(codex_auth, "SessionCodexAuthFlow", FakeSessionCodexAuthFlow)
    monkeypatch.setattr(codex_auth, "get_admin_email", lambda: "owner@example.com")
    monkeypatch.setattr(codex_auth, "get_admin_session_token", lambda: "session-token")
    monkeypatch.setattr(codex_auth, "get_chatgpt_account_id", lambda: "acc-1")
    monkeypatch.setattr(codex_auth, "get_chatgpt_workspace_name", lambda: "Idapro")

    bundle = codex_auth.login_codex_via_session()

    assert bundle == {"email": "owner@example.com", "plan_type": "team"}
    assert events[0][0] == "init"
    assert events[0][1]["email"] == "owner@example.com"
    assert events[0][1]["session_token"] == "session-token"
    assert events[0][1]["account_id"] == "acc-1"
    assert events[0][1]["workspace_name"] == "Idapro"
    assert callable(events[0][1]["auth_file_callback"])
    assert [name for name, _ in events[1:]] == ["start", "complete", "stop"]


def test_login_codex_via_session_returns_none_when_flow_requires_more_steps(monkeypatch):
    events = []

    class FakeSessionCodexAuthFlow:
        def __init__(self, **kwargs):
            events.append(("init", kwargs))

        def start(self):
            events.append(("start", None))
            return {"step": "email_required", "detail": "https://auth.openai.com/login"}

        def complete(self):
            raise AssertionError("complete should not be called")

        def stop(self):
            events.append(("stop", None))

    monkeypatch.setattr(codex_auth, "SessionCodexAuthFlow", FakeSessionCodexAuthFlow)
    monkeypatch.setattr(codex_auth, "get_admin_email", lambda: "owner@example.com")
    monkeypatch.setattr(codex_auth, "get_admin_session_token", lambda: "session-token")
    monkeypatch.setattr(codex_auth, "get_chatgpt_account_id", lambda: "acc-1")
    monkeypatch.setattr(codex_auth, "get_chatgpt_workspace_name", lambda: "Idapro")

    bundle = codex_auth.login_codex_via_session()

    assert bundle is None
    assert [name for name, _ in events[1:]] == ["start", "stop"]


def test_refresh_main_auth_file_saves_bundle_from_session_login(monkeypatch):
    monkeypatch.setattr(
        codex_auth,
        "login_codex_via_session",
        lambda: {"email": "owner@example.com", "account_id": "acc-1", "plan_type": "team"},
    )
    monkeypatch.setattr(codex_auth, "save_main_auth_file", lambda bundle: f"/tmp/{bundle['account_id']}.json")

    result = codex_auth.refresh_main_auth_file()

    assert result == {
        "email": "owner@example.com",
        "auth_file": "/tmp/acc-1.json",
        "plan_type": "team",
    }


class _FakeElement:
    def __init__(self, text):
        self._text = text
        self.clicked = False

    def is_visible(self, timeout=0):
        return True

    def inner_text(self, timeout=0):
        return self._text

    def click(self, timeout=0, force=False):
        self.clicked = True


class _FakeCollection:
    def __init__(self, items=None, text=None):
        self._items = items or []
        self._text = text

    def all(self):
        return list(self._items)

    def inner_text(self, timeout=0):
        if self._text is None:
            raise AssertionError("unexpected inner_text call")
        return self._text


class _FakePage:
    def __init__(self, *, url, body, elements=None):
        self.url = url
        self._body = body
        self._elements = elements or []

    def locator(self, selector):
        if selector == "body":
            return _FakeCollection(text=self._body)
        return _FakeCollection(items=self._elements)


def test_workspace_selection_detection_ignores_otp_pages():
    page = _FakePage(
        url="https://auth.openai.com/email-verification",
        body="Check your inbox Enter the verification code we just sent to user@example.com",
    )

    assert codex_auth._is_workspace_selection_page(page) is False
    assert codex_auth._select_team_workspace(page, "Idapro") is False


def test_workspace_label_candidates_ignore_action_buttons():
    items = [
        _FakeElement("Cancel"),
        _FakeElement("Log in with a one-time code"),
        _FakeElement("Idapro"),
        _FakeElement("Personal account"),
    ]
    page = _FakePage(
        url="https://auth.openai.com/workspace",
        body="Choose a workspace Workspace Idapro Personal account",
        elements=items,
    )

    candidates = [text for text, _loc in codex_auth._workspace_label_candidates(page)]

    assert candidates == ["Idapro", "Personal account"]


def test_workspace_selection_detection_ignores_generic_organization_setup_page():
    page = _FakePage(
        url="https://auth.openai.com/organization",
        body="New organization Finish setting up on the next page",
        elements=[_FakeElement("New organization Finish setting up on the next page")],
    )

    assert codex_auth._is_workspace_selection_page(page) is False
    assert codex_auth._select_team_workspace(page, "Idapro") is False


def test_team_workspace_selection_requires_exact_workspace_name():
    items = [
        _FakeElement("New organization Finish setting up on the next page"),
        _FakeElement("Personal account"),
    ]
    page = _FakePage(
        url="https://auth.openai.com/workspace",
        body="Choose a workspace Workspace Personal account",
        elements=items,
    )

    assert codex_auth._workspace_label_candidates(page) == [("Personal account", items[1])]
    assert codex_auth._select_team_workspace(page, "Idapro") is False


# ---------------------------------------------------------------------------
# 以下测试覆盖 SessionCodexAuthFlow._click_workspace_or_consent 的"Try again 兜底"
# 分支(2026-04-29 新增)。改动详见 .trellis/tasks/04-29-codex-auth-try-again/prd.md。
# ---------------------------------------------------------------------------


class _FakeButton:
    """模拟 Playwright button locator(不带 .first 包装),用于 _click_workspace_or_consent。"""

    def __init__(self, *, visible=True, raise_on_visible=False):
        self._visible = visible
        self._raise_on_visible = raise_on_visible
        self.clicked = False

    def is_visible(self, timeout=0):
        if self._raise_on_visible:
            raise RuntimeError("locator boom")
        return self._visible

    def click(self, timeout=0, force=False):
        self.clicked = True


class _FakeFirst:
    """模拟 Playwright locator 的 .first 属性,持有同一个底层 button。"""

    def __init__(self, button):
        self.first = button


class _FakeAuthPage:
    """按 selector 路由到 consent / try-again 两类 button 的最小 page。"""

    def __init__(self, *, consent_button=None, try_again_button=None):
        # 默认两个按钮都不可见(用于"all miss"场景)
        self._consent = consent_button or _FakeButton(visible=False)
        self._try_again = try_again_button or _FakeButton(visible=False)

    def locator(self, selector):
        # selector 文本里若含 Try again / 重试 视作错误页按钮选择器
        if "Try again" in selector or "重试" in selector:
            return _FakeFirst(self._try_again)
        return _FakeFirst(self._consent)


def _build_session_flow(page, *, workspace_name=""):
    """绕过 SessionCodexAuthFlow.__init__(它会做 PKCE / build_auth_url),只塞入测试需要的属性。"""
    flow = codex_auth.SessionCodexAuthFlow.__new__(codex_auth.SessionCodexAuthFlow)
    flow.page = page
    flow.workspace_name = workspace_name
    return flow


def test_click_workspace_or_consent_workspace_branch_takes_precedence(monkeypatch):
    # workspace 命中时,函数立即在第一个 try 块里 acted=True;
    # 但它**不会**短路 consent / try-again 分支,只是后两者也不命中,所以最终 acted 仍 True
    consent = _FakeButton(visible=False)
    try_again = _FakeButton(visible=False)
    page = _FakeAuthPage(consent_button=consent, try_again_button=try_again)
    flow = _build_session_flow(page, workspace_name="Idapro")
    monkeypatch.setattr(codex_auth, "_is_workspace_selection_page", lambda p: True)
    monkeypatch.setattr(codex_auth, "_select_team_workspace", lambda p, name: True)
    monkeypatch.setattr(codex_auth.time, "sleep", lambda s: None)

    assert flow._click_workspace_or_consent() is True
    assert consent.clicked is False
    assert try_again.clicked is False


def test_click_workspace_or_consent_consent_clicked(monkeypatch):
    consent = _FakeButton(visible=True)
    try_again = _FakeButton(visible=False)
    page = _FakeAuthPage(consent_button=consent, try_again_button=try_again)
    flow = _build_session_flow(page, workspace_name="")
    monkeypatch.setattr(codex_auth.time, "sleep", lambda s: None)

    assert flow._click_workspace_or_consent() is True
    assert consent.clicked is True
    assert try_again.clicked is False


def test_click_workspace_or_consent_try_again_clicked_when_consent_missing(monkeypatch):
    consent = _FakeButton(visible=False)
    try_again = _FakeButton(visible=True)
    page = _FakeAuthPage(consent_button=consent, try_again_button=try_again)
    flow = _build_session_flow(page, workspace_name="")
    monkeypatch.setattr(codex_auth.time, "sleep", lambda s: None)

    assert flow._click_workspace_or_consent() is True
    assert consent.clicked is False
    assert try_again.clicked is True


def test_click_workspace_or_consent_all_miss_returns_false(monkeypatch):
    page = _FakeAuthPage()  # 默认两个按钮都不可见
    flow = _build_session_flow(page, workspace_name="")
    monkeypatch.setattr(codex_auth.time, "sleep", lambda s: None)

    assert flow._click_workspace_or_consent() is False


def test_click_workspace_or_consent_try_again_locator_exception_swallowed(monkeypatch):
    # try-again locator 的 is_visible 抛异常时,被新增 try 块的 except Exception: pass
    # 吞掉,函数仍正常返回 False(不冒泡破坏外层 _advance 循环)
    try_again = _FakeButton(raise_on_visible=True)
    page = _FakeAuthPage(try_again_button=try_again)
    flow = _build_session_flow(page, workspace_name="")
    monkeypatch.setattr(codex_auth.time, "sleep", lambda s: None)

    assert flow._click_workspace_or_consent() is False
    assert try_again.clicked is False
