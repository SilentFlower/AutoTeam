# 日志规范

> AutoTeam 用什么写日志、各级别怎么用、什么不能记。

---

## 总览

- 日志库：**标准库 `logging` + `rich.logging.RichHandler`**——不引入 loguru / structlog。
- 全局初始化在 `src/autoteam/__init__.py:11-23`，按 `AUTOTEAM_PROBE_MODE` 环境变量切换两套配置。
- 各模块**统一用** `logger = logging.getLogger(__name__)` 拿 logger，**不要**全局共享一个 logger。
- 项目使用**纯字符串 + `%s` 占位**，**没有结构化字段**（`extra={...}`）的约定。
- **没有专门的脱敏代码**——靠开发者自觉不把密码/token 传给 logger 实现"脱敏"。

---

## 全局初始化（不要改）

`src/autoteam/__init__.py`：

```python
# src/autoteam/__init__.py:11-23
if os.environ.get("AUTOTEAM_PROBE_MODE") == "1":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        stream=sys.stderr,
    )
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%H:%M:%S]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False, markup=True)],
    )
```

**两套模式**：

- 默认：`INFO` 级别 + RichHandler（带颜色、富 traceback、隐藏路径、支持 markup）。
- `AUTOTEAM_PROBE_MODE=1`：`WARNING` 级别 + 普通 stderr——用于探测/嵌入式调用，避免输出污染。

**禁止**在业务模块再次调 `logging.basicConfig()`——只会被无视，且产生认知负担。

---

## 模块级 logger 标准写法

每个会打日志的模块，文件顶部 import 区下加：

```python
import logging
logger = logging.getLogger(__name__)
```

真例：

- `src/autoteam/api.py:22` — `logger = logging.getLogger(__name__)`
- `src/autoteam/account_ops.py:13` — `logger = logging.getLogger(__name__)`

**不允许**：

- `from logging import getLogger` 然后 `getLogger("autoteam")`——硬编码 logger 名会让子模块日志合并，丢失定位信息。
- 模块里直接 `logging.info(...)`（用 root logger）——同上问题。

---

## 日志级别用法

参考实际调用，约定如下：

### `INFO`：流程节点 / 用户期待看到的进度

```python
# src/autoteam/cloudmail.py:45-60
logger.info("[CloudMail] 登录成功")
logger.info("[CloudMail] 临时邮箱已创建: %s (accountId=%s)", email, account_id)
logger.info("[CloudMail] 等待邮件到达 %s... (超时 %ds)", to_email, timeout)
logger.info("[CloudMail] 收到邮件: %s (from: %s)", subject, sender)

# src/autoteam/chatgpt_api.py
logger.info("[ChatGPT] 等待 Cloudflare... (%ds)", i * 5)
logger.info("[ChatGPT] 已注入 session cookies")
logger.info("[ChatGPT] Team API transport: %s", self.transport_name)
```

### `WARNING`：可恢复的异常 / 重试前的失败 / 配置问题

```python
# src/autoteam/api.py:42
logger.warning("[配置] 自动热加载失败: %s", exc)

# src/autoteam/invite.py:400-450
logger.warning("[注册] 未自动获取到验证码")
```

### `ERROR`：单个流程失败 / 用户需要介入

```python
# src/autoteam/codex_auth.py
logger.error("[Codex] Token 交换失败: %d %s", resp.status_code, resp.text[:200])
logger.error("[Codex] 无法 import curl_cffi: %s", exc)

# src/autoteam/hero_sms.py
logger.error("[HeroSMS] 申请号码失败: %s (%s)", exc, exc.code)
logger.error("[HeroSMS] add-phone/send 失败: %s", body_excerpt)

# src/autoteam/invite.py
except Exception as e:
    logger.error("[邀请] 获取邀请邮件失败: %s", e)
```

### `DEBUG` / `CRITICAL`：项目几乎不用

- `DEBUG`：偶有，仅用于追排细节；**不要**默认级别打 debug（一旦切到 debug 会被 RichHandler 渲染得很吵）。
- `CRITICAL`：本项目无使用——遇到无法继续的错误请直接抛异常，让 traceback 上报。

---

## 消息格式约定

### 1. 模块前缀 `[xxx]`

每条日志开头**必须**带方括号模块前缀，方便人眼扫描：

| 模块 | 前缀 |
|------|------|
| `cloudmail.py` | `[CloudMail]` |
| `chatgpt_api.py` | `[ChatGPT]` |
| `codex_auth.py` | `[Codex]` |
| `hero_sms.py` | `[HeroSMS]` |
| `invite.py` | `[邀请]` / `[注册]` |
| `api.py`（配置相关） | `[配置]` |
| `accounts.py` / `account_ops.py` | `[账号]` |

新增模块时沿用相同风格——**业务前缀用中文，技术前缀（第三方平台/协议）用英文/原品牌**。

### 2. 用 `%s` 占位，不用 f-string

**强制**用 `%s` 让 logging 框架延迟格式化：

```python
# 推荐
logger.info("[CloudMail] 收到邮件: %s (from: %s)", subject, sender)
logger.error("[Codex] Token 交换失败: %d %s", resp.status_code, resp.text[:200])

# 禁止
logger.info(f"[CloudMail] 收到邮件: {subject} (from: {sender})")
```

**为什么**：当 logger 级别高于该条日志时，f-string 已经做完字符串拼接（白白消耗），而 `%s` 可以延后到真正要输出时再格式化。这是 Python logging 的官方推荐做法。

### 3. 截断长响应体

打第三方响应时**必须**截断，避免日志被几 KB HTML 淹没：

```python
# src/autoteam/codex_auth.py
logger.error("[Codex] Token 交换失败: %d %s", resp.status_code, resp.text[:200])
```

或用统一辅助函数（如 `_response_excerpt(body)`）。

### 4. 使用 `logger.exception` 记录异常 + traceback

只有当**确实需要 traceback** 时才用 `logger.exception`（它在 ERROR 级别记录并附带 traceback）。日常已知的捕获用 `logger.error("[X] 失败: %s", exc)` 即可。

---

## 不能写日志的内容

项目里**没有自动脱敏**，靠纪律严守以下红线：

| 类别 | 禁止 | 真例处理 |
|------|------|--------|
| 账号密码 | 永远不传给 logger | `account_ops.py` 删账号时只打 `email` 不打 `password` |
| OAuth/Session token | 完整 token 不打 | `codex_auth.py` 只打 `resp.status_code`，需要排查时打 `resp.text[:200]`（前 200 字符） |
| API Key（HeroSMS / Sub2API / 自身 API_KEY） | 永远不打 | 配置加载日志只说"已加载"不打值 |
| 用户验证码 | 不打完整码，需要时只打"已获取" | `invite.py` 只 `logger.info("[注册] 已获取验证码")` |
| 邮箱 mailbox 内容（除主题/发件人） | 不打邮件正文 | `cloudmail.py` 打 subject + sender，不打 body |

**原则**：**默认假设日志会被外发**（用户截图、`AUTOTEAM_PROBE_MODE` 输出到嵌入它的工具、bug 上报）。任何能用来登录/调用付费接口的字符串都不进日志。

---

## 日志脱敏 ≠ API 响应脱敏

`§不能写日志的内容` 的红线只约束 `logger.*`，**不**约束 API 响应。两者各自由 PRD 决策驱动：

| 维度 | 默认行为 | 例外 |
|------|---------|------|
| 日志（`logger.*`） | password / token / api_key 永远不打 | 无 |
| API 响应（FastAPI / dict 返回） | password / token / api_key 默认剥离 | **PRD 显式要求暴露**时保留明文 |

### 真例：免费号 list 端点返回明文 password

`/api/free/list` 的 `_sanitize_free_record` **故意保留**明文 password（PRD R3 要求"复制 email + password"）：

```python
# src/autoteam/api.py
def _sanitize_free_record(rec):
    """转 dict 副本。响应包含明文 ``password``(PRD R3),前端切勿写日志/截图。"""
    return dict(rec)
```

前端 `web/src/api.js` 同样明示：

```js
// 响应含明文 password(用于"复制 email+password");前端切勿写日志或截图。
list: () => request("get", "/api/free/list"),
```

**对比参考**：active 池的 `_sanitize_account` 在 `api.py` 里**剥离 password** —— 任何"新端点要不要剥"的问题，**先查 PRD 决策**，不要凭"为安全起见"的直觉。

### 强制约定

故意暴露敏感字段时：

1. **后端 docstring 必须**说明"PRD 第 X 条要求 ..."，避免后续重构者下意识剥离
2. **前端注释必须**警示"切勿写日志 / 截图"
3. **`_sanitize_*` 包装层不要拿掉** —— 哪怕当前等价 `dict(rec)`，这是未来加字段过滤的扩展点

### 反模式

```python
# ❌ 直接 return,字段全暴露
@app.get("/api/x")
def get_x():
    return load_x()  # password 也会出去

# ❌ 看着"更安全"的剥离,实际破坏 PRD 要求的功能
def _sanitize_free_record(rec):
    rec = dict(rec)
    rec.pop("password", None)  # 破坏 FreePage 的复制功能
    return rec
```

正确做法：先查 PRD；不暴露则 `_sanitize_*` 显式剥离；暴露则 docstring + 前端注释明示。

---

## 常见错误

1. **写 `logger.info(f"用户 {email} 登录")` 把 PII / 凭据 f-string 进日志**——既丢延迟格式化，又可能泄漏。
2. **打第三方响应不截断**——`logger.error("response: %s", resp.text)` 一条几 KB，日志难读。
3. **在 catch 块里 `print(exc)`**——绕过日志系统，没有时间戳和级别。
4. **日志前缀漏带**——新模块的日志混在一堆带前缀的日志里，搜不到。
5. **改全局 logging 配置**（在业务模块调 `basicConfig` 或 `setLevel`）——会和 `__init__.py` 冲突，行为不可预测。
6. **重试循环里全部 `logger.error`**——会让真正最终失败的那一条淹没在中间过程里；中间过程用 `warning`，最终失败才 `error`。
