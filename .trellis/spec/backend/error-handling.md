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
| `try/except TypeError` 围绕 `func(...)` 调用做参数兼容 | catch 范围会**吞掉 func 内部抛的真实 TypeError**(比如某字段类型错),静默退化成不带新参的调用,潜在地写错路径或丢数据。改用 `inspect.signature(func).parameters` 显式探测是否接受新参,详见下方 "动态参数适配" 章节。 |

## 动态参数适配:用 inspect 而非 try/except

当数据层函数从 `func(*args)` 升级为 `func(*args, new_param=None)` 后,代码或测试可能仍持有旧版本签名(典型场景:旧测试 `monkeypatch.setattr(mod, "func", lambda x: ...)` 用 1-arg lambda 替换)。两种适配方式的对比:

```python
# ❌ 反模式:catch 整个 func 调用的 TypeError
def call(func, x, *, new_param=None):
    try:
        return func(x, new_param=new_param)
    except TypeError:
        # 期望兼容不接受 new_param 的旧签名,但实际会同时吞掉 func 内部
        # 抛的真实 TypeError(参数类型错、属性不存在等),让 bug 静默
        return func(x)
```

```python
# ✅ 正确做法:基于签名探测
import inspect

def call(func, x, *, new_param=None):
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        sig = None  # 内置/C 扩展函数可能取不到签名
    if sig is not None and "new_param" in sig.parameters:
        return func(x, new_param=new_param)
    return func(x)
```

**适用场景**:支持渐进式重构(老调用方/老测试零改动迁移到新签名),但又不想为兼容写危险的 `try/except` 包装。真实例:`src/autoteam/api.py` 的 `_admin_state_call` / `_email_is_main`(`feat/multi-admin` 分支)。

**注意**:inspect 探测有可忽略的开销,不要放进高频热路径;如果适配只是为了兼容测试,长期目标是修测试而非保留 wrapper。

---

## 资源创建与回滚一致性

**原则**:在持久化新资源(写 JSON 索引、创建目录树)**之前**,先确认能完成创建流程;否则在异常路径里主动 rollback 残留物,不要留"空壳"。

### 反例:空壳锁死

PR2 `_prepare_admin_login_target` 一开始的实现是先 `add_admin` 写入 `admins.json`、再 `begin_admin_login` 走 Playwright:

```python
# ❌ 反模式
def begin_admin_login_for_new(email):
    admin = Admin(admin_id=uuid.uuid4().hex[:8], email=email, ...)
    admin_registry.add_admin(admin)            # 立即持久化
    admin_registry.set_active_admin(admin.admin_id)
    return _begin_admin_login_playwright(...)  # 任何异常都会留下空壳
```

中途任何步骤(密码错、邮箱验证码错、Playwright 启动失败、用户取消)都会留下一条没有 `workspace_name` / `account_id` 的空壳 admin。叠加 DELETE 路由的"禁止删除唯一 admin"防护,**用户首次唯一一次创建失败 → 系统中只剩这一条空壳 → 删不掉 → 锁死**。

### 修复策略

任选其一:

```python
# ✅ 策略 A:推迟持久化到流程完成后
def begin_admin_login_for_new(email):
    pending_admin_id = uuid.uuid4().hex[:8]    # 仅内存中
    _admin_login_target = pending_admin_id
    result = _begin_admin_login_playwright(...)
    if result.completed:
        admin = Admin(admin_id=pending_admin_id, email=email,
                      workspace_name=result.workspace, ...)
        admin_registry.add_admin(admin)        # 完成后才写 admins.json
    return result
```

```python
# ✅ 策略 B:写入即持久化但异常路径主动回滚
def begin_admin_login_for_new(email):
    admin = Admin(admin_id=uuid.uuid4().hex[:8], email=email, ...)
    admin_registry.add_admin(admin)
    try:
        return _begin_admin_login_playwright(...)
    except Exception:
        # 仅当 admin 还是空壳(没走完流程)时才回滚
        if admin_registry.get_admin(admin.admin_id).workspace_name is None:
            admin_registry.remove_admin(admin.admin_id)
        raise
```

**怎么选**:策略 A 更干净(数据库无中间态),但需要重写流程让中间状态全在内存;策略 B 改动小,适合既有写入路径已深度耦合的情况(本项目 PR2 选了策略 B)。

### 检查清单

引入新的"创建-验证-激活"多步流程时,审视:

- [ ] 流程中途异常时,持久化层(JSON / DB)是否会留下半成品?
- [ ] 半成品是否会跟"删除唯一资源"等业务约束冲突,导致用户无法清理?
- [ ] 单元测试是否覆盖了"start → 中间步失败 → 清理"完整路径?
- [ ] error path 是否区分了"业务失败需保留状态供重试"和"系统失败需主动回滚"?

---

## 跨线程 + asyncio 协作:thread 跑 loop 时锁与 cancel 的正确姿势

**适用场景**:长任务跑在后台 thread 里(避免阻塞 FastAPI 的同步端点函数),但内部又必须用 asyncio(Playwright / async HTTP 库)。同时前端通过 HTTP 端点同步触发"中段交互"(喂 OTP)与"取消"。

真例:`src/autoteam/plus_auto_register.py`(Plus 号自动注册)。

### 反模式:用 asyncio.Lock 跨 thread

```python
# ❌ 反模式
_register_lock = asyncio.Lock()  # 同 loop 内有效,跨 thread 不工作

def submit_job():
    threading.Thread(target=_run, daemon=True).start()

def _run():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run_async())

async def _run_async():
    async with _register_lock:  # 第二个 thread 拿到的是另一个 loop 上的锁,无法互斥
        await register_one_plus(...)
```

`asyncio.Lock` 绑定到当前 event loop;每个 thread 用 `asyncio.new_event_loop()` 启动后拿到的是**独立 loop**,互不感知,锁形同虚设。

### 正确做法:threading.Lock + 独立 loop + call_soon_threadsafe

```python
# ✅ 正确
_register_lock = threading.Lock()  # 跨 thread 真互斥
_job_loops: dict[str, asyncio.AbstractEventLoop] = {}
_job_tasks: dict[str, asyncio.Task] = {}

def submit_job() -> str:
    job_id = uuid.uuid4().hex[:12]
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    return job_id

def _run_job(job_id):
    _register_lock.acquire()           # 跨 thread 真互斥
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _job_loops[job_id] = loop
        try:
            task = loop.create_task(_run_async(job_id))
            _job_tasks[job_id] = task  # 暴露给 cancel_job 跨线程取消
            loop.run_until_complete(task)
        finally:
            _job_loops.pop(job_id, None)
            _job_tasks.pop(job_id, None)
            loop.close()
    finally:
        _register_lock.release()

def cancel_job(job_id):
    """同步端点(主线程)发起取消,需要跨线程让 task.cancel 在目标 loop 里跑。"""
    loop = _job_loops.get(job_id)
    task = _job_tasks.get(job_id)
    if loop and task and not task.done():
        loop.call_soon_threadsafe(task.cancel)  # 不能直接 task.cancel(),因为不在同 loop
```

### 中段交互:用 queue.Queue + sentinel 桥接同步端点 ↔ asyncio

前端 HTTP 端点(同步)往 `queue.Queue` 推值,asyncio 协程用 `loop.run_in_executor(None, q.get, timeout)` 在 executor 里阻塞等待——把同步阻塞 IO 转成 async 友好的等待。

```python
# 端点(同步线程)
def feed_otp_endpoint(job_id, otp):
    _otp_queues[job_id].put_nowait(otp)

def cancel_endpoint(job_id):
    _otp_queues[job_id].put_nowait(None)  # sentinel 唤醒等待的 callback

# asyncio 协程
async def otp_callback():
    loop = asyncio.get_running_loop()
    try:
        otp = await loop.run_in_executor(None, lambda: q.get(timeout=300))
    except queue.Empty:
        return None
    return otp  # None = sentinel(cancel)
```

### Checklist

- [ ] thread 启动 loop 时锁用 `threading.Lock` 而非 `asyncio.Lock`?
- [ ] 跨线程 cancel 用 `loop.call_soon_threadsafe(task.cancel)` 而非 `task.cancel()`?
- [ ] 同步端点 ↔ asyncio 跨界数据通道用 `queue.Queue`(线程安全)而非 `asyncio.Queue`?
- [ ] Cancel 路径有 sentinel 兜底唤醒,避免单纯 `task.cancel` 漏到深 await 点失效?
- [ ] thread 主体最外层 try/finally 释放锁,否则异常时锁会泄漏?

---

## 双阶段流程的失败语义:落库的判断标准

**适用场景**:业务流程分两阶段(注册 → OAuth、邀请 → 接受、付款 → 入库等),中间任意阶段都可能失败。常见误区:所有失败都落库,后续 reauth/重试因数据不全静默失败。

真例:`src/autoteam/plus_auto_register.py`(Plus 自动注册:register 阶段含 GoPay 付款 + ChatGPT 设密码;OAuth 阶段调 `import_plus_account`)。

### 判断标准:落库后能否走 reauth/重试?

每条失败记录写入 JSON 都隐含承诺"用户后续可以救活它"。如果落库后没有可用的 (email, password) 给重试函数,这条记录就是僵尸——既无法 reauth 又因业务约束(如"禁止删除唯一资源"、级联清理需要远端 ID)而难以清理。

```python
# Plus 自动注册的失败分类(plus_auto_register.py)
if not register_result.ok:
    # register 阶段失败(注册 / 付款 / OTP 超时):
    # ChatGPT 账号没设密码,无 (email, password) 给 reauth_plus_account 用,
    # 强制落库会产生"既不能 reauth 又难删"的僵尸记录。
    # → 不写 plus_accounts.json,只在 job.errors 登记并展示给用户。
    summary["other_failed"] += 1
    continue

# register 成功 → 调 import_plus_account(OAuth + sub2api 同步)
import_result = import_plus_account(email, password, admin_id)
if not import_result.ok:
    # OAuth 阶段失败:ChatGPT 账号已设密码,(email, password) 完整,
    # → 写 status=auth_failed,用户可在 PlusPage 点「重新登录」走 reauth_plus_account。
    summary["auth_failed"] += 1
```

### 反模式:无差别落库

```python
# ❌ 反模式:任何失败都落库
try:
    register(email)
    oauth(email, password)
    save({"email": email, "password": password, "status": "active"})
except Exception:
    save({"email": email, "password": "", "status": "auth_failed"})  # 没密码也落!
```

副作用:
- reauth 入口拿到没密码的记录会失败 → 用户疑惑;
- 级联删除需要远端账号 ID,没注册成功的号没 ID → 删除路径残留;
- "禁止删除唯一资源"等业务约束 + 僵尸记录 = 死锁(参考"资源创建与回滚一致性"段同类反例)。

### Checklist

引入双阶段(或多阶段)流程时:

- [ ] 列清楚每个阶段失败时**已经产生**的状态(资源是否已创建 / 凭据是否完整 / 远端是否扣款)?
- [ ] 失败落库前问:这条记录能给后续重试函数(reauth / retry)提供完整入参吗?不能就**别落**;
- [ ] 不落的那部分错误,是否在 job 状态 / 日志 / 前端 toast 里有去处,让用户知道发生了什么?
- [ ] 单元测试是否覆盖"早期阶段失败 → 不落库"和"晚期阶段失败 → 落库走 reauth"两条独立路径?

---

## 常见错误

1. **写新异常类前没问"调用方真的需要分支处理吗"**——大多数情况 `RuntimeError(f"…")` 就够了。
2. **`raise HTTPException(...)` 没用中文 detail**——前端会原样展示英文给中文用户。
3. **重试循环忘了 `logger.warning`**——出问题时看不到中间过程。
4. **`from exc` 漏掉**——异常链断了，看不到根因。
5. **业务模块 catch 后既不重抛也不记日志**——典型的"静默失败"，最难排查。
6. **资源创建过早写持久化、异常路径不回滚**——会留空壳与业务约束(如"禁止删除唯一资源")冲突,见上方"资源创建与回滚一致性"章节。
7. **用 `try/except TypeError` 适配函数签名变化**——会吞掉 func 内部真实 TypeError,改用 `inspect.signature` 探测,见"动态参数适配"章节。
8. **后台 thread 跑 asyncio loop 时用 `asyncio.Lock`**——跨 thread 完全失效,改用 `threading.Lock`;cancel 同理用 `loop.call_soon_threadsafe(task.cancel)`,见"跨线程 + asyncio 协作"章节。
9. **多阶段流程失败一律落库**——产生没有 (email, password) 等可重试入参的僵尸记录,reauth 入口失败,删除路径残留;落库前先判断"这条记录能给后续重试提供完整入参吗",见"双阶段流程的失败语义"章节。
