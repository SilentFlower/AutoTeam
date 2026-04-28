# 错误处理规范

> AutoTeam 异常分类、传播、上报与常见误区。

---

## 总览

- 项目**只有 2 个自定义异常类**，都集中在 `src/autoteam/hero_sms.py`，专门服务于第三方协议错误的细分。其余业务模块**复用** `RuntimeError` / `ValueError` / `HTTPException` 三类内建/框架异常即可，不要为了"语义明确"乱建异常类。
- API 层（FastAPI）通过 `HTTPException` 上报错误，**用中文 detail 直接给前端展示**（前端会原样展示）。
- 业务模块捕捉异常的标准动作是 `logger.error(...)` + 重抛或返回错误状态值。**不允许吞错（裸 `except: pass`）**。
- 输入校验混合 Pydantic（API 入参）+ 手写（运行时配置）两种，详见下文。

---

## 自定义异常

仅在 `src/autoteam/hero_sms.py:56-71` 定义，给 HeroSMS 短信协议错误打分类标签：

```python
# src/autoteam/hero_sms.py:56-61
class HeroSmsError(Exception):
    """HeroSMS 协议错误。`code` 保留平台短代码(如 NO_NUMBERS)便于上层分支。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code

# src/autoteam/hero_sms.py:64-71
class OpenAIResendError(RuntimeError):
    """OpenAI 拒绝 phone-otp/resend(常见于 HTTP 400)。"""

    def __init__(self, status_code: int, body: str = "") -> None:
        self.status_code = status_code
        self.body = body
        suffix = f" {body}" if body else ""
        super().__init__(f"OpenAI phone-otp/resend HTTP {status_code}{suffix}")
```

**为什么要建这两个类**：调用方需要根据 `code`、`status_code` 走不同分支（如 `NO_NUMBERS` 时换号码池、`HTTP 400` 时降速）。如果未来要新增类似异常，先问自己：**调用方是否真的需要按结构化字段分支处理？** 如果答案是"只是想让错误信息漂亮一点"，请直接用 `RuntimeError(f"…")`。

---

## API 错误响应

`src/autoteam/api.py` 全部使用 FastAPI `HTTPException`：

```python
# src/autoteam/api.py:246
raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板填写：{detail}")

# src/autoteam/api.py:262
raise HTTPException(
    status_code=400,
    detail=f"{action_label} 前请先在配置面板填写当前邮箱服务（{provider_label}）配置：{detail}",
)

# src/autoteam/api.py:1105
raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再试"))

# src/autoteam/api.py:817
raise HTTPException(status_code=502, detail=f"非预期 getPrices 响应: {type(prices).__name__}")
```

### 状态码使用约定

| 场景 | 状态码 | 真例 |
|------|--------|------|
| 配置缺失、参数无效 | `400` | `api.py:246`、`api.py:290` |
| 鉴权失败（API Key 错） | `401` | `api.py` 鉴权中间件 |
| 任务并发冲突（已有任务在跑） | `409` | `api.py:1105` |
| 上游第三方返回非预期内容 | `502` | `api.py:817` |

### detail 文案约定

- **使用中文**——前端不做翻译层，原样渲染。
- **告诉用户下一步**：错误描述 + "请去 X 配置 Y"（参考 `api.py:246`）。
- **包含技术细节**便于排障（异常 code、HTTP 状态码、响应类型），如 `api.py:783`：
  ```python
  raise HTTPException(status_code=400, detail=f"获取国家列表失败: {exc} ({exc.code})") from exc
  ```
- **使用 `from exc` 保留异常链**——便于服务端日志看完整 traceback。

---

## 业务模块的异常处理模板

### 1. 上游 HTTP 调用：检查状态码 → 抛 `RuntimeError`

参考 `src/autoteam/account_ops.py`：

```python
if status != 200:
    raise RuntimeError(f"{label}接口请求失败 (HTTP {status}): {_response_excerpt(body)}")
try:
    return json.loads(body)
except Exception as exc:
    if "<html" in lower_body or "<!doctype" in lower_body:
        raise RuntimeError(f"{label}接口返回了非 JSON 内容（疑似登录页）") from exc
    raise
```

要点：

- 上游返回非 200 → `RuntimeError`，messages 里带 `(HTTP {status})` 和**截断后**的响应正文（避免 traceback 里贴几 KB HTML）。
- JSON 解析失败要分两类：HTML（"疑似登录页"）单独提示；其它真正的 JSON 错重抛保留 traceback。

### 2. 输入校验：`ValueError` + 友好提示

参考 `src/autoteam/api.py:495-528`：

```python
def _normalize_positive_int(key: str):
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"{key} 必须是正整数")
    return value
```

要点：

- 把"类型错"和"值错"用同一句中文提示统一抛出，简化前端处理。
- 一律 `from exc` 保链。
- 调用方在 API 层 catch `ValueError` 转成 `HTTPException(400, str(exc))`。

### 3. 第三方协议错误：分支处理 `code`

参考 `src/autoteam/api.py:783`：

```python
try:
    countries = client.get_countries(...)
except HeroSmsError as exc:
    raise HTTPException(
        status_code=400, detail=f"获取国家列表失败: {exc} ({exc.code})"
    ) from exc
```

`exc.code`（如 `NO_NUMBERS`、`BAD_KEY`）是 HeroSMS 平台短代码，前端可据此切换 UI 提示，所以一定要透传。

### 4. 业务流程内的"软失败"：记日志 + 返回 False

参考 `src/autoteam/invite.py:400-450`：

```python
try:
    invite_link = await wait_for_invite_email(...)
except Exception as e:
    logger.error("[邀请] 获取邀请邮件失败: %s", e)
    return False

if not invite_link:
    logger.error("[邀请] 未获取到邀请链接")
    return False
```

适用场景：单个账号的邀请/注册流程失败**不应该让整个轮转任务挂掉**——记日志、返回布尔值，由编排层（`manager.py`）决定下一步。

---

## 输入校验

### 1. API 入参用 Pydantic（FastAPI 自动校验）

参考 `src/autoteam/api.py`：

```python
class SetupConfig(BaseModel):
    MAIL_PROVIDER: str = "cloudmail"
    CLOUDMAIL_BASE_URL: str = ""

class TaskParams(BaseModel):
    target: int = 5
```

校验失败 FastAPI 自动返回 `422` JSON——**不需要**自己 try/except。

### 2. 运行时配置（.env 解析后）用手写 `_normalize_*`

`src/autoteam/api.py:490-560` 集中了所有运行时配置的归一化函数（`_normalize_positive_int`、`_normalize_bool`、`_normalize_sub2api_proxy_id` 等）。新增配置项请仿这套结构：

- 函数命名 `_normalize_<语义>`。
- 失败抛 `ValueError`，message 用中文。
- `from exc` 保链。

---

## 重试逻辑

**项目不引入 `tenacity` / `backoff` 等重试库**，重试都是手写 for 循环 + 异常捕获。典型结构（`manager.py` 内 `_login_codex_with_result` 附近）：

```python
for attempt in range(max_attempts):
    try:
        result = do_login(...)
        if result.ok:
            return result
    except SomeError as exc:
        logger.warning("[Codex] 第 %d 次登录失败: %s", attempt + 1, exc)
        if attempt + 1 == max_attempts:
            raise
        time.sleep(backoff_seconds)
```

要点：

- 重试次数与退避时间从 `config.py` 读取（避免魔术数字）。
- 每次失败 `logger.warning` 而不是 `error`——只有最终失败才升级为 error。
- 保留最后一次异常的完整 traceback。

---

## 禁止模式

| 禁止 | 为什么 |
|------|------|
| `except: pass` / `except Exception: pass` | 直接吞错，问题难追。需要静默失败时也要 `logger.warning` 一句。 |
| `raise Exception(...)` | 太宽泛，调用方无法分支处理。请用 `RuntimeError` / `ValueError` / `HTTPException` 之一。 |
| `print(f"错误: {e}")` 当错误上报 | 项目用 logging，不用 print（详见 `logging-guidelines.md`）。 |
| 在异常 message 里塞完整 HTML 响应 | traceback 会污染日志。请用 `_response_excerpt(body)` 截断（参考 `account_ops.py`）。 |
| 在 API 端点函数里直接写业务异常处理 | 业务异常在业务模块抛，端点函数只做 `try/except → HTTPException` 翻译。 |
| 自定义异常但未提供结构化字段 | 自定义类只在调用方需要按字段分支时才有意义（如 `HeroSmsError.code`）。 |

---

## 常见错误

1. **写新异常类前没问"调用方真的需要分支处理吗"**——大多数情况 `RuntimeError(f"…")` 就够了。
2. **`raise HTTPException(...)` 没用中文 detail**——前端会原样展示英文给中文用户。
3. **重试循环忘了 `logger.warning`**——出问题时看不到中间过程。
4. **`from exc` 漏掉**——异常链断了，看不到根因。
5. **业务模块 catch 后既不重抛也不记日志**——典型的"静默失败"，最难排查。
