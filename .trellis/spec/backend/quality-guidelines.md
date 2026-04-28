# 代码质量规范

> 提交 PR 之前必须满足的硬性检查、必用与禁用模式、测试要求。

---

## 总览

- 工具链：**Ruff（lint + format）+ pytest**。**不**用 black、flake8、mypy、isort、pylint——Ruff 已覆盖。
- 包管理用 **uv**（`uv sync --dev`、`uv run …`），不直接调 `pip`。
- 配置：`ruff.toml` 独立文件（不在 `pyproject.toml`），pytest 配置在 `pyproject.toml [tool.pytest.ini_options]`。
- pre-commit 启用了 ruff 的 `--fix --exit-non-zero-on-fix`，本地提交会自动修可修的。
- CI 跑 Python 3.10 与 3.12 两个版本，4 步：lint / format check / pytest / compileall。

---

## 提交前必跑 4 条

`CONTRIBUTING.md:24-31` 写明：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python -m compileall -q src/autoteam
```

每条都要绿；任何一条挂都算未通过。**不要**用 `--no-verify` 跳过 pre-commit。

如果 lint 报错可修：

```bash
uv run ruff check . --fix
uv run ruff format .
```

---

## Ruff 配置（`ruff.toml`）

```toml
# /root/project/AutoTeam/ruff.toml
target-version = "py310"
line-length = 120

[lint]
select = ["E", "W", "F", "I", "UP", "B"]
ignore = [
    "E501",  # line too long (ruff-format handles this)
    "E402",  # module-level import not at top (display.py must be imported first)
    "B008",  # function call in default argument
    "B904",  # raise without from inside except
    "B905",  # zip without strict (intentional)
]

[lint.isort]
known-first-party = ["autoteam"]
```

要点：

- **行长 120**，比 PEP 8 默认 79 宽——但仍尽量短。
- **`UP` 规则启用**——pyupgrade 会把旧式语法改成新式（`Optional[X]` → `X | None` 等），**接受 Ruff 的自动改写**，不要手动回退。
- **`B` 规则启用**（flake8-bugbear），但 ignore 了 `B008/B904/B905`：
  - `B008`：FastAPI 端点签名里允许 `Depends()` 等默认值。
  - `B904`：`raise without from`——本项目偏好简洁错误链，不强制每处都 `from exc`（但**新代码请尽量加** `from exc`，详见错误处理规范）。
  - `B905`：`zip` 不传 `strict=` 是有意的（业务上确认两边等长）。
- **isort 内部包**：`autoteam` 走第一方分组——所以本包内 import 会和第三方分开排序。

---

## pytest 配置

`pyproject.toml:33-35`：

```toml
[tool.pytest.ini_options]
addopts = "-ra"
testpaths = ["tests"]
```

要点：

- **`-ra`**：测试结束打印所有非通过项（含 skipped / xfail）的简短摘要，便于一眼看到问题。
- **`testpaths = ["tests"]`**——pytest 只在 `tests/` 下找用例。
- **没有** `conftest.py`、**没有**全局 fixtures、**没有**覆盖率工具（coverage / pytest-cov）。

---

## 测试要求

### 1. 新增/修改业务行为必须有 unit 覆盖

`CONTRIBUTING.md:58-62`：

> When adding or changing behavior:
> - add or update pytest coverage for the affected logic where possible
> - preserve recent regression fixes unless the change intentionally replaces them
> - avoid depending on real secrets in tests

具体做法：

- 在 `tests/unit/test_<模块>.py` 加用例，文件名严格对应被测模块（如 `test_accounts.py` 测 `accounts.py`）。
- 现有真例：`tests/unit/test_accounts.py`、`tests/unit/test_admin_state.py`、`tests/unit/test_api_status.py`、`tests/unit/test_setup_wizard.py`、`tests/unit/test_manager_*.py`。

### 2. 用 `monkeypatch` 隔离副作用

没有全局 fixtures，所有测试用 pytest 内置的 `monkeypatch`：

```python
# 真例风格（参考 tests/unit/test_accounts.py、test_admin_state.py）
def test_save_load_round_trip(tmp_path, monkeypatch):
    fake_file = tmp_path / "accounts.json"
    monkeypatch.setattr("autoteam.accounts.ACCOUNTS_FILE", fake_file)
    accounts.save_accounts([{"email": "a@b.com", ...}])
    assert accounts.load_accounts() == [{"email": "a@b.com", ...}]
```

要点：

- 用 `tmp_path` 提供临时目录，**不要**碰真实的 `accounts.json` / `state.json`。
- 用 `monkeypatch.setattr("autoteam.<module>.<CONST>", ...)` 替换模块级常量。
- **禁止**测试依赖真实 secrets（API Key、token、邮箱凭证）——用 fake 值或 monkeypatch 模拟。

### 3. 不要新建 `integration/` 目录

当前 `tests/` 只有 `unit/` 子目录。需要跨模块"集成"风格的测试时，仍写在 `tests/unit/`，但用 `monkeypatch` 把外部依赖打掉。如果真的需要真实 HTTP 集成测试，先在 PR 里讨论方案。

### 4. 保留回归测试

`CONTRIBUTING.md:60`：

> preserve recent regression fixes unless the change intentionally replaces them

新代码导致旧 regression 测试挂掉时，**默认是新代码的问题**——不要简单删旧用例。要修时在 commit message / PR 里说清"为什么这次替换是有意的"。

---

## 必用模式

| 模式 | 在哪用 |
|------|--------|
| `logger = logging.getLogger(__name__)` 模块级 logger | 所有要打日志的模块 |
| `logger.info("[xxx] %s", value)` `%s` 占位 | 所有日志调用（详见 logging 规范） |
| `from autoteam.textio import read_text, write_text` | 所有文本文件读写 |
| `json.dumps(..., indent=2, ensure_ascii=False)` | 所有 JSON 持久化 |
| `dict.get(key, default)` 读 JSON 字段 | 所有从持久化 JSON 读字段的地方（向后兼容） |
| `from exc` 重抛异常 | 新代码（详见错误处理规范） |
| Pydantic `BaseModel` | FastAPI 端点入参 |
| `_normalize_*` 函数风格 | API 层运行时配置归一化 |

---

## 禁用模式

| 禁止 | 替代 |
|------|------|
| `print(...)` 当日志 | `logger.info/warning/error` |
| `open(path, "w")` / `Path(path).read_text()` 不传 encoding | `from autoteam.textio import read_text, write_text` |
| `except: pass` / `except Exception: pass` 静默吞错 | 至少 `logger.warning(...)` 一句 |
| `raise Exception("...")` 太宽泛 | `RuntimeError` / `ValueError` / `HTTPException` |
| f-string 拼日志 `logger.info(f"... {var}")` | `%s` 占位 `logger.info("... %s", var)` |
| 在业务模块调 `logging.basicConfig()` | 全局已配置，不要重配 |
| 测试硬编码绝对路径或真实文件 | `tmp_path` + `monkeypatch` |
| 在 PR 里跳过 `--no-verify` 提交 | 修好 ruff/pytest 再提 |
| 引入新依赖未经 PR 讨论 | 先在 issue 或 PR 里说明必要性 |

---

## Commit message 风格

`CONTRIBUTING.md:70-77`：

```
fix: guard main account removal
test: cover setup wizard non-interactive flow
docs: clarify Docker startup steps
```

约定：

- **首行 ≤ 72 字符**。
- **前缀**：`fix:` / `feat:` / `chore:` / `docs:` / `test:` / `refactor:` / `style:`。
- **首字母小写**（与上面例子一致）。
- 描述用现在时祈使语气（`guard ...`、`cover ...`），不用过去时。
- 多行时空一行后写正文，正文行 ≤ 100 字符。

---

## 分支与 PR

`CONTRIBUTING.md:39-54`：

- 从 `dev` 切 feature/fix 分支。
- PR **聚焦单一主题**——不要一个 PR 同时改邮箱+CPA+UI。
- PR 描述包含：
  - 问题简介
  - 你的做法
  - 本地验证步骤
  - 涉及 UI 或浏览器行为的，附截图或日志片段
- 涉及登录、同步、配额、Team 成员流程时，附**回归检查清单**。

---

## 安全红线

`CONTRIBUTING.md:64-68`：

- 永远不提交真 session token、auth 文件、邮箱凭证、生产 `.env`。
- 公开分享日志/截图前先脱敏。
- 漏洞按 `SECURITY.md` 报告，**不要**在 PR 里直接讨论。

---

## CI 流水线

`/root/project/AutoTeam/.github/workflows/ci.yml` 的关键步骤：

```yaml
strategy:
  matrix:
    python-version: ["3.10", "3.12"]

steps:
  - uv sync --dev
  - uv run ruff check .
  - uv run ruff format --check .
  - uv run pytest
  - uv run python -m compileall -q src/autoteam
```

PR 触发 + push to `dev` 触发。本地用相同命令自检即可保证 CI 一次过。

`pre-commit.yml` 还跑 changed-files diff 的 pre-commit hook（ruff lint + format），所以**保留 `.pre-commit-config.yaml` 与 ruff 版本一致**。

---

## 代码评审清单

PR reviewer 应检查：

- [ ] `uv run ruff check .` / `format --check` / `pytest` 全绿（CI 已校验，仍人工再确认）。
- [ ] 新增/改动业务行为有 unit 覆盖；旧 regression 测试未被删（除非合理理由）。
- [ ] 没有 `print` 当日志，没有 `except: pass`，没有 `from autoteam.textio` 缺位的 `open()`。
- [ ] 持久化字段读取用 `.get(default)`，写入字段在 `add_account()`/对应初始化处也补了默认值。
- [ ] `HTTPException` detail 是中文且告诉用户下一步。
- [ ] 日志带模块前缀、用 `%s` 占位、不打 token/密码/验证码。
- [ ] commit message 前缀正确、PR 主题聚焦。
- [ ] 涉及 OAuth / 配额 / Team 流程时，PR 描述里有回归检查清单。

---

## 常见错误

1. **本地不跑完 4 条命令直接发 PR**——CI 挂 → 来回返工。
2. **改动后未补单测**——下次 regression 时无法兜底。
3. **测试里写真实 path / 真实 secrets**——CI 跑挂 + 安全风险。
4. **PR 同时改太多无关主题**——评审困难，回滚成本高。
5. **commit message 写英文大写或没有前缀**——风格不统一，git log 难扫。
