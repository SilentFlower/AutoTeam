<template>
  <div class="space-y-6">
    <div class="glass-card p-5">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 class="text-xl font-bold text-white">Plus 号池</h2>
          <p class="mt-1 text-sm leading-6 text-slate-400">
            导入已有 Plus 邮箱和密码，自动完成 Codex OAuth 与手机号接码，成功后同步到 sub2api。
            这里的账号不会被 active 池或 FREE 池巡检/轮转触达。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <button
            type="button"
            @click="openImport"
            :disabled="!!runningTask || !!autoRegisterJob"
            class="btn-primary justify-center rounded-2xl px-4 py-2 text-sm"
          >
            ➕ 导入 Plus 号
          </button>
          <button
            type="button"
            @click="openAutoRegister"
            :disabled="!!runningTask || !!autoRegisterJob"
            class="btn-primary justify-center rounded-2xl px-4 py-2 text-sm"
            title="批量自动注册 Plus 号:注册 → GoPay → OAuth → sub2api"
          >
            🤖 自动注册 Plus 号
          </button>
          <button
            type="button"
            @click="refreshAllQuota"
            :disabled="!!runningTask || !!autoRegisterJob || refreshing"
            class="btn-secondary justify-center rounded-2xl px-3 py-2 text-sm"
          >
            {{ refreshing ? '提交中...' : '🔄 全量刷新额度' }}
          </button>
          <button
            type="button"
            @click="syncSub2api"
            :disabled="!!runningTask || !!autoRegisterJob || syncing"
            class="btn-secondary justify-center rounded-2xl px-3 py-2 text-sm"
          >
            {{ syncing ? '同步中...' : '🔁 同步到 sub2api' }}
          </button>
          <button
            type="button"
            @click="loadList"
            :disabled="loading"
            class="btn-secondary justify-center rounded-2xl px-3 py-2 text-sm"
          >
            {{ loading ? '加载中...' : '📋 刷新列表' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 自动注册进度面板:job 存在时常驻顶部直至终态 -->
    <div
      v-if="autoRegisterJob"
      class="glass-card p-5 border border-purple-500/30 bg-purple-500/5"
    >
      <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 mb-2">
            <span class="text-sm font-semibold text-purple-300">🤖 自动注册任务</span>
            <span class="text-xs text-slate-400 font-mono">{{ autoRegisterJob.job_id }}</span>
            <span
              class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
              :class="jobStatusBadge(autoRegisterJob.status)"
            >
              {{ jobStatusLabel(autoRegisterJob.status) }}
            </span>
          </div>
          <div class="text-sm text-slate-300">
            <span class="font-medium">{{ stepLabel(autoRegisterJob.step) }}</span>
            <span class="ml-2 text-xs text-slate-500">
              第 {{ Math.min((autoRegisterJob.current_index || 0) + 1, autoRegisterJob.total) }}/{{ autoRegisterJob.total }} 个
            </span>
          </div>
          <div class="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              class="h-full bg-purple-400 transition-all"
              :style="{ width: jobProgressPct + '%' }"
            ></div>
          </div>
          <div class="mt-2 flex flex-wrap gap-3 text-xs text-slate-400">
            <span>✅ 成功 <span class="text-green-400 font-mono">{{ autoRegisterJob.ok || 0 }}</span></span>
            <span v-if="autoRegisterSummary?.payment_failed">付款失败 <span class="text-red-400 font-mono">{{ autoRegisterSummary.payment_failed }}</span></span>
            <span v-if="autoRegisterSummary?.auth_failed">OAuth 失败 <span class="text-amber-400 font-mono">{{ autoRegisterSummary.auth_failed }}</span></span>
            <span v-if="autoRegisterSummary?.cancelled">已取消 <span class="text-gray-400 font-mono">{{ autoRegisterSummary.cancelled }}</span></span>
            <span v-if="autoRegisterSummary?.other_failed">其他失败 <span class="text-red-400 font-mono">{{ autoRegisterSummary.other_failed }}</span></span>
          </div>
          <div v-if="autoRegisterJob.errors?.length" class="mt-2 text-xs text-red-300">
            最近失败: {{ autoRegisterJob.errors[autoRegisterJob.errors.length - 1].error_type }}
            <span v-if="autoRegisterJob.errors[autoRegisterJob.errors.length - 1].error_detail" class="text-slate-400">
              ({{ autoRegisterJob.errors[autoRegisterJob.errors.length - 1].error_detail }})
            </span>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button
            v-if="!isJobTerminal(autoRegisterJob.status)"
            type="button"
            @click="cancelAutoRegister"
            :disabled="cancelling"
            class="btn-secondary justify-center rounded-2xl px-3 py-2 text-sm"
          >
            {{ cancelling ? '取消中...' : '✕ 取消任务' }}
          </button>
          <button
            v-if="isJobTerminal(autoRegisterJob.status)"
            type="button"
            @click="dismissAutoRegister"
            class="btn-secondary justify-center rounded-2xl px-3 py-2 text-sm"
          >
            收起
          </button>
        </div>
      </div>
    </div>

    <div v-if="message" class="rounded-2xl border px-4 py-3 text-sm" :class="messageClass">
      {{ message }}
    </div>

    <div class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div class="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
        <h3 class="text-lg font-semibold text-white">Plus 池账号</h3>
        <span class="text-xs text-slate-500">共 {{ records.length }} 个</span>
      </div>

      <div v-if="!records.length && !loading" class="px-4 py-8 text-center text-sm text-slate-500">
        暂无 Plus 号。点击右上角"导入 Plus 号"开始。
      </div>

      <div v-if="records.length" class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-gray-400 text-left border-b border-gray-800">
              <th class="px-4 py-3 font-medium">#</th>
              <th class="px-4 py-3 font-medium">邮箱</th>
              <th class="px-4 py-3 font-medium">状态</th>
              <th class="px-4 py-3 font-medium">Plan</th>
              <th class="px-4 py-3 font-medium text-right">5h 剩余</th>
              <th class="px-4 py-3 font-medium text-right">周 剩余</th>
              <th class="px-4 py-3 font-medium">最近刷新</th>
              <th class="px-4 py-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(rec, i) in records"
              :key="rec.email"
              class="border-b border-gray-800/50 transition"
              :class="rowClass(rec)"
            >
              <td class="px-4 py-3 text-gray-500">{{ i + 1 }}</td>
              <td class="px-4 py-3 font-mono text-xs text-slate-200">{{ rec.email }}</td>
              <td class="px-4 py-3">
                <span
                  class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
                  :class="statusClass(rec.status)"
                  :title="statusTooltip(rec)"
                >
                  <span class="w-1.5 h-1.5 rounded-full" :class="dotClass(rec.status)"></span>
                  {{ statusLabel(rec.status) }}
                </span>
              </td>
              <td class="px-4 py-3 text-xs text-slate-400">{{ rec.plan_type || '-' }}</td>
              <td class="px-4 py-3 text-right font-mono" :class="pctColor(quotaRemaining(rec, 'primary'))">
                {{ quotaPct(rec, 'primary') }}
              </td>
              <td class="px-4 py-3 text-right font-mono" :class="pctColor(quotaRemaining(rec, 'weekly'))">
                {{ quotaPct(rec, 'weekly') }}
              </td>
              <td class="px-4 py-3 text-gray-400 text-xs">{{ relativeTime(rec.last_quota_at) }}</td>
              <td class="px-4 py-3 text-right space-x-2 whitespace-nowrap">
                <button
                  type="button"
                  @click="refreshOne(rec.email)"
                  :disabled="!!runningTask || actionEmail === rec.email"
                  class="px-3 py-1.5 rounded-lg text-xs font-medium border transition bg-blue-600/10 text-blue-400 border-blue-500/30 hover:bg-blue-600/20 disabled:opacity-50"
                >
                  {{ actionEmail === rec.email && actionType === 'refresh' ? '提交中...' : '刷新额度' }}
                </button>
                <button
                  type="button"
                  @click="copyCredentials(rec.email)"
                  :disabled="actionEmail === rec.email"
                  class="px-3 py-1.5 rounded-lg text-xs font-medium border transition bg-cyan-600/10 text-cyan-400 border-cyan-500/30 hover:bg-cyan-600/20 disabled:opacity-50"
                >
                  {{ actionEmail === rec.email && actionType === 'copy' ? '复制中...' : '复制账号密码' }}
                </button>
                <button
                  type="button"
                  @click="reauthOne(rec.email)"
                  :disabled="!!runningTask || actionEmail === rec.email"
                  class="px-3 py-1.5 rounded-lg text-xs font-medium border transition bg-amber-600/10 text-amber-400 border-amber-500/30 hover:bg-amber-600/20 disabled:opacity-50"
                >
                  {{ actionEmail === rec.email && actionType === 'reauth' ? '提交中...' : '重新登录' }}
                </button>
                <button
                  type="button"
                  @click="removeOne(rec.email)"
                  :disabled="!!runningTask || actionEmail === rec.email"
                  class="px-3 py-1.5 rounded-lg text-xs font-medium border transition bg-rose-600/10 text-rose-400 border-rose-500/30 hover:bg-rose-600/20 disabled:opacity-50"
                >
                  {{ actionEmail === rec.email && actionType === 'delete' ? '删除中...' : '删除' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div
      v-if="showImportModal"
      class="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur"
      @click.self="closeImport"
    >
      <div class="glass-card w-full max-w-md p-6">
        <div class="mb-4 flex items-center justify-between">
          <h3 class="text-lg font-semibold text-white">导入 Plus 号</h3>
          <button type="button" @click="closeImport" class="text-slate-400 hover:text-white text-sm">
            ✕ 关闭
          </button>
        </div>
        <div class="space-y-4">
          <div>
            <label class="mb-2 block text-sm text-slate-300">邮箱</label>
            <input v-model.trim="importEmail" type="email" class="input-dark" @keyup.enter="submitImport" />
          </div>
          <div>
            <label class="mb-2 block text-sm text-slate-300">密码</label>
            <input v-model="importPassword" type="password" class="input-dark" @keyup.enter="submitImport" />
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button type="button" @click="closeImport" class="btn-secondary justify-center rounded-2xl px-4 py-2 text-sm">
            取消
          </button>
          <button
            type="button"
            @click="submitImport"
            :disabled="!validImport"
            class="btn-primary justify-center rounded-2xl px-4 py-2 text-sm"
          >
            开始 OAuth
          </button>
        </div>
      </div>
    </div>

    <!-- 自动注册启动模态 -->
    <div
      v-if="showAutoRegisterModal"
      class="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur"
      @click.self="closeAutoRegister"
    >
      <div class="glass-card w-full max-w-md p-6">
        <div class="mb-4 flex items-center justify-between">
          <h3 class="text-lg font-semibold text-white">🤖 自动注册 Plus 号</h3>
          <button type="button" @click="closeAutoRegister" class="text-slate-400 hover:text-white text-sm">
            ✕ 关闭
          </button>
        </div>
        <div class="space-y-4">
          <div class="text-xs text-slate-400 leading-5">
            后端将依次执行:创建临时邮箱 → ChatGPT 注册 → GoPay 印尼区 1 个月免费试用付款
            (含 WhatsApp OTP,届时弹框喂入) → 设密码 → 取消续订 → Codex OAuth → 同步 sub2api。
            单 GoPay 账号需串行,每号约 ~9 分钟。
          </div>
          <div>
            <label class="mb-2 block text-sm text-slate-300">数量</label>
            <input
              v-model.number="autoRegisterCount"
              type="number"
              min="1"
              max="20"
              class="input-dark"
              @keyup.enter="submitAutoRegister"
            />
            <p class="mt-1 text-xs text-slate-500">默认 1;批量时单条失败不影响其他号入池。</p>
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button
            type="button"
            @click="closeAutoRegister"
            class="btn-secondary justify-center rounded-2xl px-4 py-2 text-sm"
          >
            取消
          </button>
          <button
            type="button"
            @click="submitAutoRegister"
            :disabled="!validAutoRegisterCount"
            class="btn-primary justify-center rounded-2xl px-4 py-2 text-sm"
          >
            开始注册
          </button>
        </div>
      </div>
    </div>

    <!-- WhatsApp OTP 输入弹框:轮询发现 step=awaiting_whatsapp_otp 时自动弹出 -->
    <div
      v-if="showOtpModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur"
    >
      <div class="glass-card w-full max-w-md p-6 border border-amber-500/30">
        <div class="mb-4 flex items-center justify-between">
          <h3 class="text-lg font-semibold text-amber-300">📱 等待 GoPay WhatsApp OTP</h3>
        </div>
        <div class="space-y-4">
          <div class="text-sm text-slate-300 leading-6">
            后端正在等 GoPay 通过 WhatsApp 发出的 OTP 验证码。请去 WhatsApp 找到验证码,
            <strong class="text-amber-300">5 分钟内</strong>填入下方并提交。
          </div>
          <div>
            <label class="mb-2 block text-sm text-slate-300">OTP 验证码</label>
            <input
              v-model.trim="otpInput"
              type="text"
              autocomplete="off"
              inputmode="numeric"
              class="input-dark font-mono tracking-widest"
              placeholder="例如 123456"
              @keyup.enter="submitOtp"
            />
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button
            type="button"
            @click="cancelAutoRegister"
            :disabled="cancelling"
            class="btn-secondary justify-center rounded-2xl px-4 py-2 text-sm"
          >
            取消整单
          </button>
          <button
            type="button"
            @click="submitOtp"
            :disabled="!otpInput || feedingOtp"
            class="btn-primary justify-center rounded-2xl px-4 py-2 text-sm"
          >
            {{ feedingOtp ? '提交中...' : '提交 OTP' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * PlusPage.vue
 *
 * Plus 号池管理页面。响应包含明文 password，用于导入账号后的操作者复制，
 * 前端不要写日志或截图。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  runningTask: { type: Object, default: null },
})

const records = ref([])
const loading = ref(false)
const refreshing = ref(false)
const syncing = ref(false)
const message = ref('')
const messageClass = ref('')
const actionEmail = ref('')
const actionType = ref('')
const showImportModal = ref(false)
const importEmail = ref('')
const importPassword = ref('')

// 自动注册任务状态(PRD 05-08-plus-oauth-sub2api)
const autoRegisterJob = ref(null)
const showAutoRegisterModal = ref(false)
const autoRegisterCount = ref(1)
const showOtpModal = ref(false)
const otpInput = ref('')
const cancelling = ref(false)
const feedingOtp = ref(false)
let pollTimer = null
const POLL_INTERVAL_MS = 2000

const validImport = computed(() => importEmail.value.includes('@') && importPassword.value.length > 0)
const validAutoRegisterCount = computed(() => {
  const n = autoRegisterCount.value
  return Number.isInteger(n) && n >= 1 && n <= 20
})
const autoRegisterSummary = computed(() => autoRegisterJob.value?.summary || null)
const jobProgressPct = computed(() => {
  const job = autoRegisterJob.value
  if (!job || !job.total) return 0
  // 终态显示 100%;否则按"已完成 / 总数"算
  if (isJobTerminal(job.status)) return 100
  return Math.min(100, Math.round(((job.current_index || 0) / job.total) * 100))
})

function showMessage(text, level = 'info') {
  message.value = text
  messageClass.value = {
    info: 'bg-blue-500/10 text-blue-300 border-blue-500/20',
    success: 'bg-green-500/10 text-green-400 border-green-500/20',
    warn: 'bg-amber-500/10 text-amber-300 border-amber-500/20',
    error: 'bg-red-500/10 text-red-400 border-red-500/20',
  }[level] || 'bg-blue-500/10 text-blue-300 border-blue-500/20'
  setTimeout(() => { message.value = '' }, 8000)
}

async function loadList() {
  loading.value = true
  try {
    records.value = await api.plus.list()
  } catch (e) {
    showMessage(`加载 Plus 列表失败: ${e.message}`, 'error')
  } finally {
    loading.value = false
  }
}

function openImport() {
  importEmail.value = ''
  importPassword.value = ''
  showImportModal.value = true
}

function closeImport() {
  showImportModal.value = false
}

async function submitImport() {
  if (!validImport.value) return
  const email = importEmail.value
  const password = importPassword.value
  closeImport()
  try {
    const task = await api.plus.importAccount(email, password)
    showMessage(`已提交 ${email} 的 Plus OAuth 任务，任务 ID: ${task.task_id}`, 'success')
  } catch (e) {
    showMessage(`提交失败: ${e.message}`, 'error')
  }
}

async function refreshAllQuota() {
  refreshing.value = true
  try {
    const task = await api.plus.checkQuota(null)
    showMessage(`已提交全量刷新额度任务，任务 ID: ${task.task_id}`, 'success')
  } catch (e) {
    showMessage(`提交失败: ${e.message}`, 'error')
  } finally {
    refreshing.value = false
  }
}

async function refreshOne(email) {
  if (props.runningTask) return
  actionEmail.value = email
  actionType.value = 'refresh'
  try {
    const task = await api.plus.checkQuota([email])
    showMessage(`已提交 ${email} 的额度刷新任务，任务 ID: ${task.task_id}`, 'success')
  } catch (e) {
    showMessage(`提交失败: ${e.message}`, 'error')
  } finally {
    actionEmail.value = ''
    actionType.value = ''
  }
}

async function reauthOne(email) {
  if (props.runningTask) return
  actionEmail.value = email
  actionType.value = 'reauth'
  try {
    const task = await api.plus.reauth(email)
    showMessage(`已提交 ${email} 的重新授权任务，任务 ID: ${task.task_id}`, 'success')
  } catch (e) {
    showMessage(`提交失败: ${e.message}`, 'error')
  } finally {
    actionEmail.value = ''
    actionType.value = ''
  }
}

async function syncSub2api() {
  syncing.value = true
  try {
    const result = await api.plus.syncSub2api()
    showMessage(result.message || 'Plus 池已同步到 sub2api', 'success')
  } catch (e) {
    showMessage(`同步失败: ${e.message}`, 'error')
  } finally {
    syncing.value = false
  }
}

async function copyCredentials(email) {
  const rec = records.value.find((r) => (r.email || '').toLowerCase() === email.toLowerCase())
  if (!rec) {
    showMessage(`未找到记录: ${email}`, 'error')
    return
  }
  const text = rec.password ? `${rec.email}\t${rec.password}` : rec.email
  actionEmail.value = email
  actionType.value = 'copy'
  try {
    await writeClipboard(text)
    showMessage(rec.password ? `已复制邮箱+密码: ${rec.email}` : `已复制邮箱: ${rec.email}`, 'success')
  } catch (e) {
    showMessage(`复制失败: ${e.message}`, 'error')
  } finally {
    actionEmail.value = ''
    actionType.value = ''
  }
}

async function writeClipboard(text) {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text)
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  document.execCommand('copy')
  document.body.removeChild(ta)
}

async function removeOne(email) {
  if (props.runningTask) return
  const ok = window.confirm(`确认删除 Plus 号 ${email}？\n会清理：本地 auth 文件 + sub2api 远端账号 + 本地 JSON 条目。`)
  if (!ok) return

  actionEmail.value = email
  actionType.value = 'delete'
  try {
    const result = await api.plus.delete(email)
    const cleanup = result.cleanup || {}
    const parts = []
    if (cleanup.local_record) parts.push('本地记录')
    if (cleanup.local_auth_files?.length) parts.push(`auth 文件 ${cleanup.local_auth_files.length} 个`)
    if (cleanup.sub2api_accounts?.length) parts.push(`sub2api ${cleanup.sub2api_accounts.length} 个`)
    const summary = parts.length ? `（已清理：${parts.join(' / ')}）` : ''
    showMessage(`${result.message || 'Plus 号删除完成'}${summary}`, 'success')
    await loadList()
  } catch (e) {
    showMessage(`删除失败: ${e.message}`, 'error')
  } finally {
    actionEmail.value = ''
    actionType.value = ''
  }
}

function statusLabel(s) {
  return {
    active: 'Active',
    auth_failed: 'OAuth 失败',
    plan_mismatch: '非 Plus',
    exhausted: '已用尽',
  }[s] || s
}

function statusClass(s) {
  return {
    active: 'bg-green-500/10 text-green-400',
    auth_failed: 'bg-amber-500/15 text-amber-300',
    plan_mismatch: 'bg-red-500/15 text-red-300',
    exhausted: 'bg-gray-500/15 text-gray-400',
  }[s] || 'bg-gray-500/10 text-gray-400'
}

function dotClass(s) {
  return {
    active: 'bg-green-400',
    auth_failed: 'bg-amber-400',
    plan_mismatch: 'bg-red-400',
    exhausted: 'bg-gray-400',
  }[s] || 'bg-gray-400'
}

function statusTooltip(rec) {
  if (rec.status === 'plan_mismatch') return rec.last_error || '登录成功，但账号不是 Plus'
  if (rec.status === 'auth_failed') return rec.last_error || 'OAuth 失败，可重新登录或删除'
  if (rec.status === 'exhausted') return '额度用尽，等待重置'
  return ''
}

function rowClass(rec) {
  if (rec.status === 'plan_mismatch') return 'bg-red-500/5 hover:bg-red-500/10'
  if (rec.status === 'auth_failed') return 'bg-amber-500/5 hover:bg-amber-500/10'
  if (rec.status === 'exhausted') return 'opacity-60 hover:bg-gray-800/30'
  return 'hover:bg-gray-800/30'
}

function quotaRemaining(rec, type) {
  const qi = rec.last_quota
  if (!qi) return null
  const pct = type === 'primary' ? qi.primary_pct : qi.weekly_pct
  if (typeof pct !== 'number') return null
  return 100 - pct
}

function quotaPct(rec, type) {
  const val = quotaRemaining(rec, type)
  return val !== null ? `${val}%` : '-'
}

function pctColor(val) {
  if (val === null) return 'text-gray-500'
  if (val > 30) return 'text-green-400'
  if (val > 0) return 'text-yellow-400'
  return 'text-red-400'
}

function relativeTime(ts) {
  if (!ts) return '从未刷新'
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

// =======================================================================
// 自动注册任务(PRD 05-08-plus-oauth-sub2api)
// =======================================================================

function openAutoRegister() {
  if (autoRegisterJob.value) return
  autoRegisterCount.value = 1
  showAutoRegisterModal.value = true
}

function closeAutoRegister() {
  showAutoRegisterModal.value = false
}

async function submitAutoRegister() {
  if (!validAutoRegisterCount.value) return
  const count = autoRegisterCount.value
  closeAutoRegister()
  try {
    const resp = await api.plus.autoRegister(count)
    autoRegisterJob.value = {
      job_id: resp.job_id,
      status: 'pending',
      step: null,
      current_index: 0,
      total: count,
      ok: 0,
      errors: [],
      summary: null,
    }
    showMessage(`已提交自动注册任务,job_id=${resp.job_id}`, 'success')
    startPolling()
  } catch (e) {
    showMessage(`提交失败: ${e.message}`, 'error')
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(pollAutoRegisterJob, POLL_INTERVAL_MS)
  // 立即刷一次,缩短"提交→看到 step"的延迟
  pollAutoRegisterJob()
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function pollAutoRegisterJob() {
  const job = autoRegisterJob.value
  if (!job) {
    stopPolling()
    return
  }
  try {
    const fresh = await api.plus.autoRegisterStatus(job.job_id)
    autoRegisterJob.value = fresh

    // step=awaiting_whatsapp_otp 时弹 OTP 输入框;离开该 step 自动收起
    if (fresh.step === 'awaiting_whatsapp_otp' && !showOtpModal.value) {
      otpInput.value = ''
      showOtpModal.value = true
    } else if (fresh.step !== 'awaiting_whatsapp_otp' && showOtpModal.value) {
      showOtpModal.value = false
    }

    // 终态:停轮询,刷新列表(因新号已落库),OTP 弹框收起
    if (isJobTerminal(fresh.status)) {
      stopPolling()
      showOtpModal.value = false
      const summary = fresh.summary || {}
      const okCount = summary.ok || 0
      if (okCount > 0) {
        await loadList()
      }
      const lvl = fresh.status === 'cancelled' ? 'warn' : okCount > 0 ? 'success' : 'error'
      showMessage(
        `自动注册结束: ok=${okCount}, ` +
          `cancelled=${summary.cancelled || 0}, ` +
          `payment_failed=${summary.payment_failed || 0}, ` +
          `auth_failed=${summary.auth_failed || 0}`,
        lvl,
      )
    }
  } catch (e) {
    // 404 = job 已被清理(服务重启等),停止轮询
    if (e.status === 404) {
      stopPolling()
      showMessage('自动注册任务状态丢失(可能服务已重启),停止轮询', 'warn')
      autoRegisterJob.value = null
      showOtpModal.value = false
    }
    // 其他网络错误:静默,等下一轮再试
  }
}

async function submitOtp() {
  const job = autoRegisterJob.value
  if (!job || !otpInput.value) return
  feedingOtp.value = true
  try {
    await api.plus.autoRegisterFeedOtp(job.job_id, otpInput.value)
    showOtpModal.value = false
    otpInput.value = ''
    showMessage('OTP 已提交,等待后端继续付款流程', 'success')
    // 立即刷一次状态,加快进入下一步显示
    pollAutoRegisterJob()
  } catch (e) {
    showMessage(`OTP 提交失败: ${e.message}`, 'error')
  } finally {
    feedingOtp.value = false
  }
}

async function cancelAutoRegister() {
  const job = autoRegisterJob.value
  if (!job) return
  cancelling.value = true
  try {
    await api.plus.autoRegisterCancel(job.job_id)
    showMessage('已请求取消;已付款的号需运营手动处置(GoPay 不退款)', 'warn')
    // 立即刷一次,通常 1-2 秒内会进入终态
    pollAutoRegisterJob()
  } catch (e) {
    showMessage(`取消失败: ${e.message}`, 'error')
  } finally {
    cancelling.value = false
  }
}

function dismissAutoRegister() {
  // 终态后用户主动收起进度面板
  autoRegisterJob.value = null
  showOtpModal.value = false
  stopPolling()
}

function isJobTerminal(status) {
  return status === 'done' || status === 'cancelled' || status === 'failed'
}

function jobStatusLabel(status) {
  return (
    {
      pending: '已提交',
      running: '运行中',
      done: '完成',
      cancelled: '已取消',
      failed: '失败',
    }[status] || status
  )
}

function jobStatusBadge(status) {
  return (
    {
      pending: 'bg-blue-500/10 text-blue-300',
      running: 'bg-purple-500/15 text-purple-300',
      done: 'bg-green-500/15 text-green-400',
      cancelled: 'bg-gray-500/15 text-gray-300',
      failed: 'bg-red-500/15 text-red-300',
    }[status] || 'bg-gray-500/10 text-gray-400'
  )
}

function stepLabel(step) {
  if (!step) return '准备中...'
  return (
    {
      creating_email: '创建临时邮箱',
      signing_up: '注册 ChatGPT',
      awaiting_email_otp: '等待邮箱验证码',
      filling_about_you: '填写注册资料',
      paying_gopay: 'GoPay 付款中',
      awaiting_whatsapp_otp: '⏳ 等待 WhatsApp OTP(请在弹框中输入)',
      setting_password: '设置 ChatGPT 密码',
      cancelling_subscription: '取消续订',
      oauth: 'Codex OAuth 登录',
      syncing_sub2api: '同步到 sub2api',
      done: '✅ 完成',
    }[step] || step
  )
}

onMounted(() => {
  loadList()
})

onUnmounted(() => {
  stopPolling()
})
</script>
