# Codex 授权 step loop 检测到错误页时自动点 Try again 续跑

## Goal

`login_codex_via_browser()` 的 consent step loop（`src/autoteam/codex_auth.py:946-1141`）当前只识别 Continue / Allow / 继续 三种按钮：找到就点、找不到就 `break` 退出循环。

但 OpenAI consent 页有一个偶发场景：某次 Continue 后跳到一个**错误页（带 "Try again" 按钮）**。当前 loop 在错误页找不到 Continue/Allow，直接 `break`，下一段 `await auth_code` 拉不到 callback → 整个 OAuth 流程失败 → 免费号生成器 / 主号 invite 加号 / 主号 OAuth 三条链路都受影响。

加一个 **Try again 检测分支**：consent 按钮没命中时再试一次"错误页 Try again"按钮，命中就点击 + `continue` 进入下一轮重新找 Continue/Allow；都没命中才 break。

## What I already know

* **目标 step loop 位置**：`src/autoteam/codex_auth.py:1129-1141`，在 `login_codex_via_browser()` 里 `for step in range(10):` 循环的尾段
* **当前实现要点**：
  * Playwright `page.locator('button:has-text("继续"), button:has-text("Continue"), button:has-text("Allow")').first`
  * `is_visible(timeout=5000)` 命中 → click + sleep(5) + `_screenshot(page, "codex_04_consent_{step+1}.png")`
  * 命中失败 / 任何异常 → `break`
* **同文件第二处 consent 点击**：`SessionCodexAuthFlow._click_workspace_or_consent`（`codex_auth.py:1435-1458`）。timeout 1000ms，不在 step loop 结构里（返回 `acted` 给上层),由 `_advance` 的 `for _ in range(attempts)` 循环消费返回值,主号 OAuth 路径用
* **现有错误关键字识别**：`_classify_oauth_failure`（`codex_auth.py:91-114`）已经把 `"try again later"` 当作 `site_unavailable`（retryable），但**只在 step loop 全部 break + 拉不到 auth_code 之后**才走，无法在 loop 内续跑
* **已有截图机制**：`_screenshot(page, "codex_04_consent_{step+1}.png")` 自带毫秒时间戳前缀，可直接复用一个新文件名
* **没有现场截图**：`data/screenshots/` 没保存过 Try again 错误页样子，按钮的真实文本/选择器需用户确认（推测 OpenAI 错误页是英文 "Try again"，与现有 `_classify_oauth_failure` 的 `"try again later"` body 文案一致）

## Assumptions (temporary)

* **Try again 按钮文本**：以英文 `Try again` 为主，中文环境可能出现 `重试` —— 与现有 consent_btn 双语兼容写法保持一致
* **按钮 tag**：与 Continue/Allow 一样是 `<button>`；如截图显示是 `<a>` 锚点再补 `a:has-text(...)`
* **错误页特征**：除 Try again 按钮外，可能有 `something went wrong` / `An error occurred` 文案，但**MVP 不依赖文案识别**，仅靠按钮可见性触发
* **Try again 点击后**：页面会重新跳回 consent，下一轮 step 能正常找到 Continue/Allow；连续多次错误页（点 Try again 没用）由 `for step in range(10)` 的 10 步上限兜底，不会死循环

## Open Questions

(无,Q1/Q2 均已答复并落入 Decisions Locked)

## Decisions Locked

### D1 — Scope:两处都改(2026-04-29)
* **决定**：本次同时修改两处 consent 点击代码
  * 第 1129-1141 处:`login_codex_via_browser()` step loop（免费号生成 / 主号 invite 加号 都走这条）
  * 第 1447-1458 处:`SessionCodexAuthFlow._click_workspace_or_consent`（主号 OAuth `_advance` 调用）
* **理由**：主号 OAuth 同样会偶发错误页;两处选择器 + 触发场景一致,补丁结构高度对称,一次改完避免分多次提交;两处 PoC 失效模式不同(前者直接 break,后者 acted=False 后 sleep 等下一轮),分别测试

### D2 — Try again 按钮选择器(2026-04-29)
* **决定**:`button:has-text("Try again"), button:has-text("重试")` 双语兼容
* **理由**:与现有 consent 按钮 `button:has-text("继续"), button:has-text("Continue"), button:has-text("Allow")` 双语风格一致;与 `_classify_oauth_failure` 已识别的 `"try again later"` body 文案同源;若实际错误页改用 `<a>` 标签,实施时按需追加 `a:has-text(...)`(此为低成本回归)

## Requirements (evolving)

* **R1 — Try again 分支位置(主路径)**：在 `codex_auth.py:1129-1141` 的 consent_btn `else` 分支里追加 Try again 检测；命中点击 + 截图 + `continue` 进入下一轮 step；不命中保持原有 `break`
* **R1b — Try again 分支位置(主号 OAuth)**：在 `codex_auth.py:1435-1458` 的 `_click_workspace_or_consent` 函数末尾,在两个现有 try 块之后追加第三个 try 块检测 Try again；命中点击 + `acted=True`,让外层 `_advance` 的 `for _ in range(attempts)` 循环 `continue` 进入下一轮 step
* **R2 — 选择器(已锁 D2)**：`button:has-text("Try again"), button:has-text("重试")`，timeout 用 2000ms（短于 consent 的 5000ms / 1000ms，避免 loop 整体慢）
* **R3 — 截图文件名**：第一处用 `codex_04_consent_error_{step+1}.png`,复用现有 `_screenshot` 机制；第二处 `_click_workspace_or_consent` 不截图(与现有两个 consent 分支一致,主号 OAuth 走外层错误兜底机制)
* **R4 — 日志**：
  * 第一处:`[Codex] consent 出现错误页,点击 Try again 续跑 (step %d)...`
  * 第二处:`[Codex] 主号 consent 出现错误页,点击 Try again 续跑`
  * 与现有"[Codex] 点击同意/继续按钮 (step %d)..." / "[Codex] 主号点击继续/授权"风格一致
* **R5 — 步数预算**：Try again 也消耗一个 step,共享原有上限(`for step in range(10)` / `_advance` 的 `attempts` 参数),无需额外 try-again 计数器；连续多步都是错误页 → 仍按原兜底退出

## Acceptance Criteria (evolving)

### 第一处:`login_codex_via_browser` step loop
* [ ] 在 step loop 里手动构造"consent 按钮不可见 + Try again 按钮可见"场景(单测难以可达,详见"变更记录 1·A1";由真实环境手测兜底)
* [ ] 在 step loop 里手动构造"两个按钮都不可见"场景，仍然 `break`(同上,基线一致)
* [ ] 在 step loop 里手动构造"consent 按钮可见"场景，原有路径不受影响(同上)
* [ ] 异常路径：Try again locator 抛异常时，回退到 `break`(由 except Exception: break 已有兜底,同源改动 2 单测 `test_click_workspace_or_consent_try_again_locator_exception_swallowed` 间接证明 except 风格可用)

### 第二处:`SessionCodexAuthFlow._click_workspace_or_consent`
* [ ] mock 出"workspace 不可见 + consent 不可见 + Try again 可见"场景,函数返回 `True`,Try again 按钮 `.click()` 被调用
* [ ] mock 出"三者全不可见"场景,函数返回 `False`(原有兜底行为不变,外层 `_advance` 进入 `time.sleep(1)`)
* [ ] mock 出"consent 可见"场景,Try again 分支不被触发(短路,与第一处对称)
* [ ] Try again locator 抛异常时,函数仍能正常返回 `False`(异常被现有 try/except 吞掉,不冒泡)

### 真实环境
* [ ] 手测一次免费号生成 / 主号 OAuth,日志能看到至少一种命中路径(无异常 backtrace)

## Definition of Done (team quality bar)

* 单元测试覆盖三条分支（Continue 命中 / Try again 命中 / 都不命中）
* `pytest tests/unit/test_codex_auth_session.py` + lint / typecheck 全绿
* 不改 `_classify_oauth_failure`（兜底逻辑保持不动）
* 日志前缀 `[Codex]` 与现有保持一致

## Out of Scope (explicit)

* **不改** `_classify_oauth_failure` 的关键字表（兜底路径不动）
* **不引入** 独立的 try-again 计数器或 retry budget（共享 10 步上限）
* **不识别** 错误页文本/URL 特征（仅靠 Try again 按钮可见性触发）
* **不处理** Try again 点击后仍然跳错误页的二次错误页（10 步上限兜底）
* **不动** Step A invite 浏览器流程（`invite.login_with_invite`）的任何 step loop —— 该流程没有 consent 概念

## Technical Approach

### 改动 1:`login_codex_via_browser` step loop

`src/autoteam/codex_auth.py:1129-1141`，把现有 `try` 块的 `else: break` 改成"先试 Try again，再 break"：

```python
try:
    consent_btn = page.locator(
        'button:has-text("继续"), button:has-text("Continue"), button:has-text("Allow")'
    ).first
    if consent_btn.is_visible(timeout=5000):
        logger.info("[Codex] 点击同意/继续按钮 (step %d)...", step + 1)
        consent_btn.click()
        time.sleep(5)
        _screenshot(page, f"codex_04_consent_{step + 1}.png")
    else:
        # 新增:错误页 Try again 检测,命中就续跑
        try_again_btn = page.locator(
            'button:has-text("Try again"), button:has-text("重试")'
        ).first
        if try_again_btn.is_visible(timeout=2000):
            logger.info("[Codex] consent 出现错误页,点击 Try again 续跑 (step %d)...", step + 1)
            _screenshot(page, f"codex_04_consent_error_{step + 1}.png")
            try_again_btn.click()
            time.sleep(5)
            continue
        break
except Exception:
    break
```

### 改动 2:`SessionCodexAuthFlow._click_workspace_or_consent`

`src/autoteam/codex_auth.py:1435-1458`，在末尾追加第三个 try 块(workspace → consent → try-again 顺序),命中即 `acted = True`:

```python
def _click_workspace_or_consent(self):
    acted = False

    try:
        if self.workspace_name and _is_workspace_selection_page(self.page):
            if _select_team_workspace(self.page, self.workspace_name):
                logger.info("[Codex] 主号已选择目标 workspace")
                acted = True
    except Exception:
        pass

    try:
        consent_btn = self.page.locator(
            'button:has-text("继续"), button:has-text("Continue"), button:has-text("Allow")'
        ).first
        if consent_btn.is_visible(timeout=1000):
            consent_btn.click()
            logger.info("[Codex] 主号点击继续/授权")
            time.sleep(3)
            acted = True
    except Exception:
        pass

    # 新增:错误页 Try again 检测
    try:
        try_again_btn = self.page.locator(
            'button:has-text("Try again"), button:has-text("重试")'
        ).first
        if try_again_btn.is_visible(timeout=1000):
            try_again_btn.click()
            logger.info("[Codex] 主号 consent 出现错误页,点击 Try again 续跑")
            time.sleep(3)
            acted = True
    except Exception:
        pass

    return acted
```

### 设计取舍

* **共享原有上限**：第一处复用 `for step in range(10)` 的 10 步上限,第二处复用 `_advance(attempts=...)` 上限,不引入新计数器
* **Try again timeout 短(2000ms / 1000ms)**：对齐各自原 consent timeout 风格(第一处 5000→2000 缩短,第二处 1000→1000 持平)
* **第一处 `continue` vs 第二处 `acted=True`**：两处控制流不同 —— 第一处 step loop 显式 `continue`,第二处靠返回 `acted` 让外层 `_advance` 决定 continue
* **截图策略不对称**：第一处加新前缀 `consent_error` 便于排错；第二处不截图(与现有两个 try 块一致,主号 OAuth 走外层兜底)
* **异常吞咽统一**：两处的 Try again try 块都用 `except Exception: pass` / `break`,与本地现有 try 块完全一致,不引入新错误传播路径

## Decision (ADR-lite)

**Context**：consent step loop 找不到按钮直接 break，遇到错误页时无法续跑，下游免费号生成器 / 主号 invite 加号被偶发性卡死。

**Decision**：在 step loop 的 `else` 分支增加"Try again 兜底"，靠按钮可见性而非页面文案识别错误页，命中就 `continue` 重试 consent；共享原有 10 步上限避免死循环。

**Consequences**：
* 优点：最小侵入（只改 6-8 行），不改任何现有兜底机制
* 风险：若 OpenAI 错误页改版（按钮文本变 / 改 SPA 路由），分支会静默失效，但不会更糟（仍走原 break + `_classify_oauth_failure`）
* 反向兼容：现有 Continue/Allow 命中路径完全不变

## Implementation Plan (small PRs)

* **PR1（单 PR 即可）**：
  * 改 `codex_auth.py:1129-1141`(改动 1)
  * 改 `codex_auth.py:1435-1458`(改动 2)
  * `tests/unit/test_codex_auth_session.py` 加测试用例(只测改动 2,改动 1 沿用现状基线 — 详见"变更记录 1·A1"):
    * 改动 2 的五条用例(workspace 命中 / consent 命中 / try-again 命中 / 全 miss / try-again locator 异常)
  * 不需要 PR2/PR3 拆分,逻辑简单,两处补丁高度对称

## Technical Notes

### 关键文件

* `src/autoteam/codex_auth.py:946-1141` — `login_codex_via_browser()` step loop 主体
* `src/autoteam/codex_auth.py:1129-1141` — **改动 1 主目标**(免费号 / 主号 invite 加号 都走这条)
* `src/autoteam/codex_auth.py:1435-1458` — **改动 2 主目标**(主号 OAuth `_advance` 调用,属于 `SessionCodexAuthFlow` 类)
* `src/autoteam/codex_auth.py:91-114` — `_classify_oauth_failure`(兜底分类,**不动**)
* `src/autoteam/codex_auth.py:1500-1525` — `_advance` 循环(改动 2 的外层调用方,验证返回 `acted` 的语义)
* `tests/unit/test_codex_auth_session.py` — 新增测试用例

### 关键约束

* Playwright 同步 API（`sync_playwright`）
* 不能引入新依赖
* `_screenshot` 已自带毫秒级时间戳前缀（参考 commit `feb1f33`），文件名冲突无忧

## 变更记录

### 变更 1: 实现完成后的事后回补 (2026-04-29)

- **变更类型**: 实施调整(代码已落,PRD 同步)
- **触发**: implement 阶段发现 PRD 与代码 / 现状基线之间两个偏差,需回补
- **变更内容**:
  - **M1 — 类名校正 `MainCodexAuthFlow` → `SessionCodexAuthFlow`**: PRD 多处提到 `MainCodexAuthFlow._click_workspace_or_consent`,但 `codex_auth.py` 实际只有 `SessionCodexAuthFlow`(class 在第 1268 行) / `MainCodexLoginFlow`(继承 SessionCodexAuthFlow,第 1626 行) / `MainCodexSyncFlow`(继承 MainCodexLoginFlow,第 1647 行) 三个类,**没有** `MainCodexAuthFlow` 这个名字。`_click_workspace_or_consent` 实际定义在父类 `SessionCodexAuthFlow` 上,主号子类继承使用。原 PRD 类名笔误源自 brainstorm 阶段 Explore agent 的报告误差,本次回补修正所有 4 处引用。
  - **A1 — 改动 1 单测策略 = 不写,沿用现状基线**: 改动 1 处于 `login_codex_via_browser` 的巨型 step loop(`for step in range(10)` + `with sync_playwright`)嵌套深处,与同段相邻的 4 个 try 块(workspace 处理 / 密码处理 / OTP 处理 / consent 处理)所属的可测性基线一致 —— 这 4 个 try 块在现有 `test_codex_auth_session.py` 中**都没有单测覆盖**(现有测试均为函数级辅助 `_is_workspace_selection_page` / `_select_team_workspace` / `_workspace_label_candidates` 的独立测试)。本次决策:
    * **不抽 helper 函数让单测可达**(避免 PRD "Out of Scope" 中性原则被破坏,且改动面无谓变大)
    * **不写半模拟 step loop**(脆弱、与现有 test 风格不匹配)
    * 由 PRD AC "真实环境" 那条 + 同源改动 2 的 5 个单测 + spec "写实,不写理想" 原则共同兜底
    * 用户确认采纳推荐方案 #B
- **原因**:
  - M1 是源数据错误的回补,与代码实际语义无歧义
  - A1 是工程实用主义判断 + 与现状基线一致,避免为单一新增分支额外引入测试架构
- **PRD 同步方式**: 事后回补(代码先行)
- **影响范围**: 仅文档/测试覆盖描述同步,不影响已落代码
- **同步后状态**: PRD 与实际产出 100% 一致
