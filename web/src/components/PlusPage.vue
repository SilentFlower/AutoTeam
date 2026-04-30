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
            :disabled="!!runningTask"
            class="btn-primary justify-center rounded-2xl px-4 py-2 text-sm"
          >
            ➕ 导入 Plus 号
          </button>
          <button
            type="button"
            @click="refreshAllQuota"
            :disabled="!!runningTask || refreshing"
            class="btn-secondary justify-center rounded-2xl px-3 py-2 text-sm"
          >
            {{ refreshing ? '提交中...' : '🔄 全量刷新额度' }}
          </button>
          <button
            type="button"
            @click="syncSub2api"
            :disabled="!!runningTask || syncing"
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
  </div>
</template>

<script setup>
/**
 * PlusPage.vue
 *
 * Plus 号池管理页面。响应包含明文 password，用于导入账号后的操作者复制，
 * 前端不要写日志或截图。
 */
import { computed, onMounted, ref } from 'vue'
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

const validImport = computed(() => importEmail.value.includes('@') && importPassword.value.length > 0)

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

onMounted(() => {
  loadList()
})
</script>
