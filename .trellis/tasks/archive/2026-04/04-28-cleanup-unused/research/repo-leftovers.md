# 仓库残留文件 / 备份 / 旧产物 扫描报告

**扫描日期**: 2026-04-28
**扫描范围**: 仓库根目录 + `src/autoteam/web/dist/` + `.trellis/` + 各类工具缓存

---

## 一、确认要删除并提交（无价值残留）

### 1. `.trellis/workflow.md.bak`
- **状态**: untracked 备份文件
- **判定**: `.trellis/.gitignore` 没有覆盖 `.bak`,且根 `.gitignore` 也没有
- **建议**: 直接删除文件，可顺手在根 `.gitignore` 加 `*.bak` 规则

### 2. ~~构建产物相互替换~~（**修正前一个 agent 的误判**）
- **dist/index.html** 当前引用：
  - `/assets/index-BfHvQ74b.js`
  - `/assets/index-CcqNIOTD.css`
- 它们正是 untracked 的两个新文件 — 是**活引用**，不能删。
- 实际要清理的是 git status 中 deleted 的旧 hash 文件（已被 vite 自动清理，只需 `git add` 接受删除即可）：
  - `D src/autoteam/web/dist/assets/index-D-W9oyDB.css`
  - `D src/autoteam/web/dist/assets/index-D1YEqQfb.js`
- **建议**: 一次提交收尾——`git add` 接受删除 + `git add` 添加新构建产物 + `git add` 修改后的 `dist/index.html`

---

## 二、应该删除并加入 .gitignore（本地工具产物）

根 `.gitignore` 现状缺失以下条目：

| 路径 | 类别 | 体积 | 备注 |
|------|------|------|------|
| `.idea/` | JetBrains IDE 配置 | 64K | 个人开发环境 |
| `.playwright-mcp/` | Playwright MCP 本地产物 | 228K | 调试痕迹/本会话生成 |
| `.pytest_cache/` | pytest 缓存 | — | 已存在 untracked |
| `.ruff_cache/` | ruff 缓存 | — | 已存在 untracked |

> 说明：`.gitignore` 已有 `__pycache__/`、`*.pyc`、`web/node_modules/`、`.coverage`、`.venv/`，但 IDE / 调试 / lint 缓存遗漏。

**建议**: 在 `.gitignore` 末尾追加：
```
.idea/
.playwright-mcp/
.pytest_cache/
.ruff_cache/
*.bak
```

> ⚠️ `.agents/`、`.claude/`、`.trellis/` 是否要 ignore，取决于团队是否要把 Trellis 流程文档共享到仓库；当前 `.trellis/.gitignore` 已细粒度管理 `.trellis/` 内部产物，建议保持现状。

---

## 三、docs / 旧脚本

- 全量扫 `docs/` 6 篇 .md 均被 `README.md` / `architecture.md` 内部交叉引用，无孤立文档。
- 根目录无重复脚本。

---

## 四、汇总（本份独立结论）

| 优先级 | 动作 | 影响面 |
|--------|------|--------|
| P0 | 删 `.trellis/workflow.md.bak` | 0 风险 |
| P0 | 提交 dist 构建产物切换（删旧 hash + 加新 hash） | 0 风险，仅整理 git 状态 |
| P0 | 补 `.gitignore`(.idea/.playwright-mcp/.pytest_cache/.ruff_cache/*.bak) | 0 风险 |
| P1 | 决定是否清理 `.idea/`、`.playwright-mcp/` 实体目录 | 不影响线上 |

---

**同时被另两份报告确认的清理候选**(供 PRD 汇总用)：
- 死组件：`web/src/components/TasksPage.vue`(0 引用)
- 死测试用例：`tests/unit/test_api_main_codex_after_admin.py::test_post_main_codex_delete_cpa_returns_deleted_names`(对应的 `cpa_sync.delete_main_codex_from_cpa` 与 API endpoint 都已不存在；其余 3 个测试仍有效，**只删该函数**)
- 已 deleted 但未 commit：`cpa_sync.py`、`manual_account.py`、`OAuthPage.vue`(已确认无遗留引用，可安全提交删除)
