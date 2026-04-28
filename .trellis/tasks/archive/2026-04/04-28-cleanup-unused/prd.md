# 清理未使用代码以便维护

## Goal

针对仓库当前 42 个未提交变更的混杂状态，识别**真正可以删除**的代码/文件/测试，让后续维护者拿到一个干净、信号-噪声比高的代码库。

## What I already know

- 项目：AutoTeam，Python(FastAPI) + Vue(SPA)单仓库混合架构。
- 当前 git working tree 已经 deleted 了 5 个文件未提交：`cpa_sync.py`、`manual_account.py`、`OAuthPage.vue`、两个旧 hash dist 资源。
- 同时 untracked 包含：`.trellis/workflow.md.bak`、新 hash dist 资源、`.idea/`、`.playwright-mcp/` 等。
- 已派出三路并行扫描（Explore agent），结论落地在 `research/`：
  - `python-dead-modules.md` — 21 个 Python 模块，全活
  - `vue-dead-components.md` — 14 个 Vue 组件，1 死(`TasksPage.vue`)
  - `repo-leftovers.md` — 1 个 .bak、4 类工具缓存未 ignore、1 个死测试用例

## Confirmed delete candidates(交叉验证后)

### A. 已 deleted 但未 commit(零风险，直接收尾)

| 文件 | 风险 | 备注 |
|------|------|------|
| `src/autoteam/cpa_sync.py` | 0 | manual_account 同时被删；模块本身 0 import |
| `src/autoteam/manual_account.py` | 0 | 0 引用 |
| `web/src/components/OAuthPage.vue` | 0 | 0 引用 |
| `src/autoteam/web/dist/assets/index-D-W9oyDB.css`(旧) | 0 | dist/index.html 已切换到新 hash |
| `src/autoteam/web/dist/assets/index-D1YEqQfb.js`(旧) | 0 | 同上 |

### B. 真死代码(扫描新发现)

| 项 | 类型 | 证据 |
|-----|------|------|
| `web/src/components/TasksPage.vue` | 死 Vue 组件 | App.vue 路由 'tasks' 直接到 TaskHistoryPage,绕开 TasksPage;全仓 0 引用 |
| `tests/unit/test_api_main_codex_after_admin.py::test_post_main_codex_delete_cpa_returns_deleted_names`(仅该 1 个 test 函数,~20 行) | 死测试用例 | monkeypatch `autoteam.cpa_sync.delete_main_codex_from_cpa` 与对应 API endpoint `post_main_codex_delete_cpa` 均已删除;同文件其余 3 个 test 仍有效 |

### C. 残留 / 备份文件

| 文件 | 处理方式 |
|------|---------|
| `.trellis/workflow.md.bak` | 直接删 |

### D. 应入 .gitignore 的本地产物

`.idea/`、`.playwright-mcp/`、`.pytest_cache/`、`.ruff_cache/`、`*.bak`
(根 `.gitignore` 缺;`.trellis/.gitignore` 已自管 .trellis 内部)

## NOT to delete(已扫描确认仍在用,避免误删)

- `chatgpt_transport.py`(仅 1 引用,但 chatgpt_api.py 依赖)
- `playwright_probe.py`(仅 1 引用,但 api.py 通过 subprocess 跑独立进程)
- `display.py`(副作用导入,4 处)
- `textio.py`(跨平台文本工具,10+ 引用)
- `hero_sms.py`(新模块,13 处引用,已完全集成)
- `TaskHistory.vue` vs `TaskHistoryPage.vue`(职责分离:Page 路由入口 + History 数据表格)

## Open Questions

- ~~Q1(Blocking)~~: ✅ 已决议为 **档位 B**(2026-04-28 silentflower 拍板)。

## Scope (锁定 — 档位 B)

### 必须删除 / 提交的项

1. **接受已 deleted 的 5 个文件**:
   - `src/autoteam/cpa_sync.py`
   - `src/autoteam/manual_account.py`
   - `web/src/components/OAuthPage.vue`
   - `src/autoteam/web/dist/assets/index-D-W9oyDB.css`
   - `src/autoteam/web/dist/assets/index-D1YEqQfb.js`
2. **新加入的 dist 构建产物**(替代上面两个旧 hash):
   - `src/autoteam/web/dist/assets/index-BfHvQ74b.js`
   - `src/autoteam/web/dist/assets/index-CcqNIOTD.css`
   - 修改后的 `src/autoteam/web/dist/index.html`(引用切换)
3. **删除真死代码**:
   - `web/src/components/TasksPage.vue`(完整文件)
   - `tests/unit/test_api_main_codex_after_admin.py` 内 `test_post_main_codex_delete_cpa_returns_deleted_names` 函数(仅该 1 个 test,~20 行,保留同文件其余 3 个 test)
4. **删除残留**:
   - `.trellis/workflow.md.bak`

### 不在本档处理(留待后续/不做)

- `.idea/` / `.playwright-mcp/` / `.pytest_cache/` / `.ruff_cache/` / `*.bak` 加 .gitignore — 留待档位 C
- 其他 modified 文件(API/manager/codex_auth/前端组件等)的提交 — **不属本任务**,仅做"删除收尾",不打包业务变更

## Acceptance Criteria

- [ ] 5 个 deleted 文件已 `git add` 接受
- [ ] 3 个新 dist 文件已 `git add`
- [ ] `web/src/components/TasksPage.vue` 已删除并 `git rm`
- [ ] `tests/unit/test_api_main_codex_after_admin.py` 仅删该 1 个 test 函数,其余 3 个仍存在
- [ ] `.trellis/workflow.md.bak` 已删除
- [ ] `pytest tests/unit/test_api_main_codex_after_admin.py` 通过(剩余 3 个 test)
- [ ] `npm run build`(在 web/) 仍能成功构建,且产物 hash 与 dist 引用一致
- [ ] `git status` 中本任务相关项全部清理完毕

## Definition of Done

- 所有删除均有 PR/commit 记录原因
- `.gitignore` 增量条目带注释说明
- 相关测试运行通过

## Out of Scope(明确不做)

- 重构 `chatgpt_transport.py` 内联进 `chatgpt_api.py`(架构清晰,YAGNI)
- 合并 `playwright_probe.py` 到 `api.py`(进程隔离设计有意为之)
- 重命名/重组 docs/(无孤立文档)
- 迁移 FastAPI on_event → lifespan(deprecation warning,但不属本次清理范围)

## Research References

- [`research/python-dead-modules.md`](research/python-dead-modules.md) — 21 模块全活,无 Python 死代码
- [`research/vue-dead-components.md`](research/vue-dead-components.md) — 1 死组件: TasksPage.vue
- [`research/repo-leftovers.md`](research/repo-leftovers.md) — 1 .bak + .gitignore 缺项 + 修正 dist 误判

## Decision (ADR-lite)

**Context**: 仓库 working tree 有 42 个未提交变更,混杂"删除收尾 + 业务修改 + 工具产物 + 备份"。需要决定本次清理的边界,避免一刀切误删或扩大到非必要重构。

**Decision**: 档位 B —— 仅做"删除收尾 + 真死代码清理 + 1 个备份文件",不动 .gitignore 与本地产物目录,不重构活模块。

**Consequences**:
- ✅ 0 业务回归风险:不动任何活代码
- ✅ git status 显著精简,信号-噪声比提升
- ⚠️ `.idea/`、`.playwright-mcp/` 等仍会出现在未来 `git status` 中,如团队不同 IDE 用户增多需后续补 .gitignore(档位 C)
- ⚠️ FastAPI on_event deprecation warning 不在范围内,需要单独立项

## Implementation Plan

单次提交,不拆 PR(档位 B 工作量小、内聚):

```
commit 1: chore: 清理未使用代码 (TasksPage / cpa_sync 残留 / dist 旧产物 / .bak)
  - 删除 web/src/components/TasksPage.vue
  - 删除 tests/unit/test_api_main_codex_after_admin.py 中
    test_post_main_codex_delete_cpa_returns_deleted_names 函数
  - 删除 .trellis/workflow.md.bak
  - git add 接受 5 个 deleted 文件 + 3 个新 dist 文件
```

**注意**:其它 modified 文件(`docs/*`、`src/autoteam/api.py` 等)**不**纳入本提交,保留它们的当前 working tree 状态。
