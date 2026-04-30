const BASE = '/api'

function getApiKey() {
  return localStorage.getItem('autoteam_api_key') || ''
}

export function setApiKey(key) {
  localStorage.setItem('autoteam_api_key', key)
}

export function clearApiKey() {
  localStorage.removeItem('autoteam_api_key')
}

// admin_id 注入器：由 store/admins.js 在初始化时 setAdminIdGetter() 注册一个
// 函数指针。这里用注册器模式而不是直接 import store，是为了避开
// "api.js ↔ store/admins.js" 的循环依赖（store 内部要 import api）。
let _adminIdGetter = () => null

/**
 * 注册 admin_id getter，请求拦截器据此往每次请求里注入
 * `X-Autoteam-Admin-Id` header（getter 返回 null/空字符串时不注入，
 * 让后端 fallback 到激活 admin，兼容旧客户端）。
 * @param {() => (string|null)} getter
 */
export function setAdminIdGetter(getter) {
  _adminIdGetter = typeof getter === 'function' ? getter : () => null
}

async function request(method, path, body = null) {
  const headers = { 'Content-Type': 'application/json' }
  const key = getApiKey()
  if (key) {
    headers['Authorization'] = `Bearer ${key}`
  }
  // 注入当前激活 admin_id；空值不注入，让后端走 active fallback。
  let adminId = ''
  try {
    adminId = _adminIdGetter() || ''
  } catch {
    adminId = ''
  }
  if (adminId) {
    headers['X-Autoteam-Admin-Id'] = adminId
  }
  const opts = { method, headers }
  if (body) opts.body = JSON.stringify(body)
  const resp = await fetch(`${BASE}${path}`, opts)
  let data
  try {
    data = await resp.json()
  } catch {
    const err = new Error(`HTTP ${resp.status}: 服务器返回了非 JSON 响应`)
    err.status = resp.status
    throw err
  }
  if (!resp.ok) {
    const msg = data?.message || data?.detail?.message || data?.detail || `HTTP ${resp.status}`
    const err = new Error(msg)
    err.status = resp.status
    throw err
  }
  return data
}

export const api = {
  checkAuth: () => request('GET', '/auth/check'),
  getSetupStatus: () => request('GET', '/setup/status'),
  saveSetup: (config) => request('POST', '/setup/save', config),
  getRuntimeConfig: () => request('GET', '/config/runtime'),
  saveRuntimeConfig: (config) => request('PUT', '/config/runtime', config),
  getRuntimeConfigSource: () => request('GET', '/config/source'),
  saveRuntimeConfigSource: (payload) => request('PUT', '/config/source', payload),

  getHeroSmsCountries: ({ apiKey = '', baseUrl = '' } = {}) => {
    const params = new URLSearchParams()
    if (apiKey) params.set('api_key', apiKey)
    if (baseUrl) params.set('base_url', baseUrl)
    const qs = params.toString()
    return request('GET', `/hero-sms/countries${qs ? `?${qs}` : ''}`)
  },
  getHeroSmsServices: ({ apiKey = '', baseUrl = '', country = '', lang = 'cn' } = {}) => {
    const params = new URLSearchParams()
    if (apiKey) params.set('api_key', apiKey)
    if (baseUrl) params.set('base_url', baseUrl)
    if (country) params.set('country', country)
    if (lang) params.set('lang', lang)
    const qs = params.toString()
    return request('GET', `/hero-sms/services${qs ? `?${qs}` : ''}`)
  },
  getHeroSmsAvailability: ({ apiKey = '', baseUrl = '', service = 'dr', limit = 50 } = {}) => {
    const params = new URLSearchParams()
    if (apiKey) params.set('api_key', apiKey)
    if (baseUrl) params.set('base_url', baseUrl)
    if (service) params.set('service', service)
    if (limit) params.set('limit', String(limit))
    const qs = params.toString()
    return request('GET', `/hero-sms/availability${qs ? `?${qs}` : ''}`)
  },

  getStatus: () => request('GET', '/status'),
  getAdminStatus: () => request('GET', '/admin/status'),
  getMainCodexStatus: () => request('GET', '/main-codex/status'),
  getAccounts: () => request('GET', '/accounts'),
  getActiveAccounts: () => request('GET', '/accounts/active'),
  getStandbyAccounts: () => request('GET', '/accounts/standby'),
  deleteAccount: (email) => request('DELETE', `/accounts/${encodeURIComponent(email)}`),
  loginAccount: (email) => request('POST', '/accounts/login', { email }),
  getCodexAuth: (email) => request('GET', `/accounts/${encodeURIComponent(email)}/codex-auth`),
  kickAccount: (email) => request('POST', `/accounts/${encodeURIComponent(email)}/kick`),

  startAdminLogin: (email) => request('POST', '/admin/login/start', { email }),
  submitAdminSession: (email, sessionToken) => request('POST', '/admin/login/session', { email, session_token: sessionToken }),
  submitAdminPassword: (password) => request('POST', '/admin/login/password', { password }),
  submitAdminCode: (code) => request('POST', '/admin/login/code', { code }),
  submitAdminWorkspace: (optionId) => request('POST', '/admin/login/workspace', { option_id: optionId }),
  cancelAdminLogin: () => request('POST', '/admin/login/cancel'),
  logoutAdmin: () => request('POST', '/admin/logout'),

  // 多 admin（PR2 新增的 /api/admins/* 接口）
  // 列表 / 切换 / 删除：
  listAdmins: () => request('GET', '/admins'),
  getActiveAdmin: () => request('GET', '/admins/active'),
  setActiveAdmin: (adminId) => request('POST', '/admins/active', { admin_id: adminId }),
  deleteAdmin: (adminId) => request('DELETE', `/admins/${encodeURIComponent(adminId)}`),
  // 多 admin 登录流程：targetAdminId 省略 = 创建新 admin；非空 = 为现有 admin 重登。
  startAdminLoginAsNew: (email) => request('POST', '/admins/login/start', { email }),
  startAdminLoginForTarget: (email, targetAdminId) =>
    request('POST', '/admins/login/start', { email, target_admin_id: targetAdminId }),
  submitAdminLoginPasswordTarget: (password, targetAdminId = null) =>
    request('POST', '/admins/login/password', targetAdminId ? { password, target_admin_id: targetAdminId } : { password }),
  submitAdminLoginCodeTarget: (code, targetAdminId = null) =>
    request('POST', '/admins/login/code', targetAdminId ? { code, target_admin_id: targetAdminId } : { code }),
  submitAdminLoginWorkspaceTarget: (optionId, targetAdminId = null) =>
    request('POST', '/admins/login/workspace', targetAdminId
      ? { option_id: optionId, target_admin_id: targetAdminId }
      : { option_id: optionId }),
  startMainCodexLogin: () => request('POST', '/main-codex/login'),
  startMainCodexSync: () => request('POST', '/main-codex/start'),
  submitMainCodexPassword: (password) => request('POST', '/main-codex/password', { password }),
  submitMainCodexCode: (code) => request('POST', '/main-codex/code', { code }),
  cancelMainCodexSync: () => request('POST', '/main-codex/cancel'),

  postSync: () => request('POST', '/sync'),
  postSyncAccounts: () => request('POST', '/sync/accounts'),
  postSyncMainCodex: () => request('POST', '/sync/main-codex'),

  startRotate: (target = 5) => request('POST', '/tasks/rotate', { target }),
  startCheck: () => request('POST', '/tasks/check'),
  startAdd: () => request('POST', '/tasks/add'),
  // 邀请加号：通过母号发邀请 + 自动登录 + Codex OAuth 入池（后台执行）。
  startAddViaInvite: () => request('POST', '/tasks/add-via-invite'),
  startFill: (target = 5) => request('POST', '/tasks/fill', { target }),
  startCleanup: (maxSeats = null) => request('POST', '/tasks/cleanup', { max_seats: maxSeats }),

  getTasks: (adminId = null) => {
    // adminId 非空时按 admin 过滤;'all' 视为显式不过滤;null/undefined 也走全部
    if (adminId && adminId !== 'all') {
      return request('GET', `/tasks?admin_id=${encodeURIComponent(adminId)}`)
    }
    return request('GET', '/tasks')
  },
  getTask: (id) => request('GET', `/tasks/${id}`),

  getAutoCheckConfig: () => request('GET', '/config/auto-check'),
  setAutoCheckConfig: (cfg) => request('PUT', '/config/auto-check', cfg),

  getTeamMembers: () => request('GET', '/team/members'),
  removeTeamMember: (payload) => request('POST', '/team/members/remove', payload),
  getLogs: (limit = 100, since = 0) => request('GET', `/logs?limit=${limit}&since=${since}`),

  // 免费号(FREE)池：与 active 池完全隔离的"已注册可登录、不占 Team 席位"账号资产。
  // 后端实现见 src/autoteam/free_accounts.py + /api/free/* 端点；
  // 前端入口在 FreePage.vue（侧栏导航键 'free'）。
  free: {
    // POST /api/free/generate {count} → 202 + task_id；任务后台跑 cmd_generate_free_account
    generate: (count = 1) => request('POST', '/free/generate', { count }),
    // GET /api/free/list → 当前 admin 的 FREE 池全部记录
    // 注意：响应包含明文 password（PRD 要求"复制 email+password"按钮，已经过 API Key 鉴权）；
    // 前端切勿把 password 写日志或截图。
    list: () => request('GET', '/free/list'),
    // POST /api/free/check_quota {emails: [...] | null} → 202 + task_id
    // emails=null 表示刷新全部 active/exhausted；非空数组表示仅刷新指定邮箱
    checkQuota: (emails = null) => request('POST', '/free/check_quota', { emails }),
    // POST /api/free/sync_sub2api → 同步执行的"FREE → sub2api"手动触发兜底
    syncSub2api: () => request('POST', '/free/sync_sub2api'),
    // POST /api/free/{email}/reauth → 202 + task_id；异步重新授权 codex OAuth
    // 触发场景：已落库 FREE 号的 token 失效（remove 后被 invalidate / 过期），
    // 用户在 FreePage 行操作点「重新登录」救一下。成功后自动 sync sub2api。
    reauth: (email) => request('POST', `/free/${encodeURIComponent(email)}/reauth`),
    // DELETE /api/free/{email} → 级联删除（auth_file + sub2api + cloudmail + JSON 条目）
    delete: (email) => request('DELETE', `/free/${encodeURIComponent(email)}`),
  },

  // Plus 号池：用户导入已有 Plus 账号，后端自动走 Codex OAuth + HeroSMS 接码 + sub2api 同步。
  // 响应包含明文 password（用于复制导入账号凭据）；前端切勿把 password 写日志或截图。
  plus: {
    importAccount: (email, password) => request('POST', '/plus/import', { email, password }),
    list: () => request('GET', '/plus/list'),
    checkQuota: (emails = null) => request('POST', '/plus/check_quota', { emails }),
    syncSub2api: () => request('POST', '/plus/sync_sub2api'),
    reauth: (email) => request('POST', `/plus/${encodeURIComponent(email)}/reauth`),
    delete: (email) => request('DELETE', `/plus/${encodeURIComponent(email)}`),
  },
}
