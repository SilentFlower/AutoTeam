# 后端开发规范

> AutoTeam 后端开发的真实约定，AI 与新成员请按此处条目落地代码，不要凭训练记忆推断。

---

## 项目快照

- **语言/版本**：Python ≥ 3.10（CI 跑 3.10 与 3.12）。
- **包管理**：`uv`（`uv sync --dev`、`uv run …`），不直接 `pip`。
- **HTTP 框架**：FastAPI（`src/autoteam/api.py`），Pydantic 入参校验。
- **CLI 入口**：`autoteam = "autoteam.manager:main"`（见 `pyproject.toml:17`）。
- **质量工具**：Ruff（lint + format）+ pytest + pre-commit。**没有** mypy / black / coverage。
- **持久化**：仅 JSON 文件（`accounts.json`、`free_accounts.json`、`plus_accounts.json`、`state.json`） + `.env`。**没有** SQL / ORM。
- **日志**：标准 `logging` + `rich.logging.RichHandler`，全局在 `src/autoteam/__init__.py` 初始化。
- **前端**：独立 Vue 3 + Vite，构建产物落到 `src/autoteam/web/dist/` 由 FastAPI 静态托管。

---

## 规范索引

| 规范 | 关注点 | 状态 |
|------|------|------|
| [目录结构](./directory-structure.md) | 模块组织、文件命名、扁平结构铁律 | 已填充 |
| [数据持久化](./database-guidelines.md) | JSON 读写、向后兼容、状态枚举、字段命名 | 已填充 |
| [错误处理](./error-handling.md) | 自定义异常、API 错误响应、重试策略 | 已填充 |
| [日志](./logging-guidelines.md) | 级别、模块前缀、`%s` 占位、脱敏红线 | 已填充 |
| [代码质量](./quality-guidelines.md) | Ruff/pytest 命令、必用/禁用模式、PR 与 commit | 已填充 |

> 通用思维指引（代码复用、跨层一致性等）见 `.trellis/spec/guides/`。

---

## 落地原则

1. **写实，不写理想**——以上规范全部基于 `src/autoteam/` 现有代码归纳。**当代码与规范冲突时**，先看是否是规范过期需要更新，再判断代码是否需要迁移；**不要悄悄按规范"修复"已有代码**。
2. **新增功能优先复用既有模块** —— 见 `directory-structure.md` 的"模块组织原则"。
3. **持久化优先合并到现有 JSON** —— 不要新建 `xxx.json`；能塞进 `accounts.json` 字段的就塞。完全隔离资产池例外见 `database-guidelines.md`。
4. **错误信息中文 + 下一步指引** —— 用户面向的 `HTTPException.detail` 必须中文，且告诉用户该去哪做什么。
5. **日志默认假设会被外发** —— 任何 token、密码、验证码、API Key、邮件正文都不进 logger。

---

## 提交前自检

参考 `quality-guidelines.md`：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python -m compileall -q src/autoteam
```

四条全绿才发 PR。

---

**语言**：本目录下所有规范文档使用**中文**编写。
