<template>
  <div class="space-y-6">
    <!-- 顶部说明 + 主操作按钮 -->
    <div class="glass-card p-5">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 class="text-xl font-bold text-white">免费号池（FREE）</h2>
          <p class="mt-1 text-sm leading-6 text-slate-400">
            通过母号邀请把临时邮箱拉进 Team、走完整 Codex OAuth 拿到 auth 之后立即移出。
            这里的账号不占用 Team 席位，也不会被现有"账号池"巡检/轮转触达。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <button
            type="button"
            @click="openGenerate"
            :disabled="!!runningTask"
            class="btn-primary justify-center rounded-2xl px-4 py-2 text-sm"
          >
            ➕ 生成免费号
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

    <!-- 提示 / 错误信息 -->
    <div
      v-if="message"
      class="rounded-2xl border px-4 py-3 text-sm"
      :class="messageClass"
    >
      {{ message }}
    </div>

    <!-- 列表 -->
    <div class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div class="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
        <h3 class="text-lg font-semibold text-white">FREE 池账号</h3>
        <span class="text-xs text-slate-500">共 {{ records.length }} 个</span>
      </div>

      <div v-if="!records.length && !loading" class="px-4 py-8 text-center text-sm text-slate-500">
        暂无免费号。点击右上角"生成免费号"开始。
      </div>

      <div v-if="records.length" class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-gray-400 text-left border-b border-gray-800">
              <th class="px-4 py-3 font-medium">#</th>
              <th class="px-4 py-3 font-medium">邮箱</th>
              <th class="px-4 py-3 font-medium">状态</th>
              <th class="px-4 py-3 font-medium text-right">5h 剩余</th>
              <th class="px-4 py-3 font-medium text-right">周 剩余</th>
              <th class="px-4 py-3 font-medium">最近刷新</th>
              <th class="px-4 py-3 font-medium">创建时间</th>
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
              <td class="px-4 py-3 font-mono text-xs text-slate-200">
                <div class="flex items-center gap-2">
                  <span>{{ rec.email }}</span>
                  <span
                    v-if="rec.team_residue"
                    title="账号未成功移出 Team，请手动检查"
                    class="rounded-full bg-red-500/15 px-2 py-0.5 text-[10px] font-semibold text-red-300"
                  >
                    Team 残留
                  </span>
                </div>
              </td>
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
              <td class="px-4 py-3 text-right font-mono" :class="pctColor(quotaRemaining(rec, 'primary'))">
                {{ quotaPct(rec, 'primary') }}
              </td>
              <td class="px-4 py-3 text-right font-mono" :class="pctColor(quotaRemaining(rec, 'weekly'))">
                {{ quotaPct(rec, 'weekly') }}
              </td>
              <td class="px-4 py-3 text-gray-400 text-xs">{{ relativeTime(rec.last_quota_at) }}</td>
              <td class="px-4 py-3 text-gray-400 text-xs">{{ formatTime(rec.created_at) }}</td>
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

    <!-- 生成数量弹窗 -->
    <div
      v-if="showGenerateModal"
      class="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur"
      @click.self="closeGenerate"
    >
      <div class="glass-card w-full max-w-md p-6">
        <div class="mb-4 flex items-center justify-between">
          <h3 class="text-lg font-semibold text-white">生成免费号</h3>
          <button
            type="button"
            @click="closeGenerate"
            class="text-slate-400 hover:text-white text-sm"
          >
            ✕ 关闭
          </button>
        </div>
        <p class="mb-4 text-xs leading-6 text-slate-400">
          串行生成 N 个免费号：每个会走完整 invite 邀请 + Codex OAuth + remove
          + sub2api 同步。任务在后台执行，可在"任务历史"查看进度。
        </p>
        <label class="mb-2 block text-sm text-slate-300">数量</label>
        <input
          v-model.number="generateCount"
          type="number"
          min="1"
          max="20"
          class="input-dark mb-4"
          @keyup.enter="submitGenerate"
        />
        <div class="flex justify-end gap-2">
          <button
            type="button"
            @click="closeGenerate"
            class="btn-secondary justify-center rounded-2xl px-4 py-2 text-sm"
          >
            取消
          </button>
          <button
            type="button"
            @click="submitGenerate"
            :disabled="!validCount"
            class="btn-primary justify-center rounded-2xl px-4 py-2 text-sm"
          >
            开始生成
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * FreePage.vue
 *
 * 免费号池（FREE）管理页面。与 PoolPage / Dashboard 的"账号池"完全独立，
 * 在侧栏走 'free' 这个独立导航键。
 *
 * 顶部按钮：
 * - 生成免费号（弹窗输入数量 → POST /api/free/generate，202 + 后台任务）
 * - 全量刷新额度（POST /api/free/check_quota {emails: null}，202 + 后台任务）
 * - 同步到 sub2api（POST /api/free/sync_sub2api，同步执行）
 * - 刷新列表（拉一次 GET /api/free/list 重新渲染）
 *
 * 行操作：
 * - 刷新额度（POST /api/free/check_quota {emails: [email]}）
 * - 复制 email + password（剪贴板写入；后端返回明文 password 供操作者使用）
 * - 删除（DELETE /api/free/{email}，级联清理，toast 显示 cleanup 摘要）
 *
 * 行视觉提示：
 * - status=auth_failed → 黄色行高亮 + tooltip "OAuth 失败，可手动重试或删除"
 * - team_residue=true → 红色警告徽章 + tooltip
 * - status=exhausted → 灰色淡化
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

const showGenerateModal = ref(false)
const generateCount = ref(1)

const validCount = computed(() => Number.isInteger(generateCount.value) && generateCount.value > 0)

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
    records.value = await api.free.list()
  } catch (e) {
    showMessage(`加载 FREE 列表失败: ${e.message}`, 'error')
  } finally {
    loading.value = false
  }
}

function openGenerate() {
  generateCount.value = 1
  showGenerateModal.value = true
}

function closeGenerate() {
  showGenerateModal.value = false
}

async function submitGenerate() {
  if (!validCount.value) return
  const count = generateCount.value
  closeGenerate()
  try {
    const task = await api.free.generate(count)
    showMessage(`已提交生成任务（共 ${count} 个），任务 ID: ${task.task_id}`, 'success')
  } catch (e) {
    showMessage(`提交失败: ${e.message}`, 'error')
  }
}

async function refreshAllQuota() {
  refreshing.value = true
  try {
    const task = await api.free.checkQuota(null)
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
    const task = await api.free.checkQuota([email])
    showMessage(`已提交 ${email} 的额度刷新任务，任务 ID: ${task.task_id}`, 'success')
  } catch (e) {
    showMessage(`提交失败: ${e.message}`, 'error')
  } finally {
    actionEmail.value = ''
    actionType.value = ''
  }
}

async function reauthOne(email) {
  // 触发后端 POST /api/free/{email}/reauth 异步任务,完成后由 LogViewer + GET /api/tasks 反馈进度。
  // 用于 token 失效(remove 后被 invalidate / 过期)的手动恢复;成功后后端自动 sync sub2api。
  if (props.runningTask) return
  actionEmail.value = email
  actionType.value = 'reauth'
  try {
    const task = await api.free.reauth(email)
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
    const result = await api.free.syncSub2api()
    showMessage(result.message || 'FREE 池已同步到 sub2api', 'success')
  } catch (e) {
    showMessage(`同步失败: ${e.message}`, 'error')
  } finally {
    syncing.value = false
  }
}

async function copyCredentials(email) {
  // PRD 要求 FreePage 提供"复制 email + password"。
  // password 由 /api/free/list 返回(前端只在需要时显示给操作用户,不入日志)
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
  // HTTP 下 clipboard API 不可用,用 textarea fallback
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
  const ok = window.confirm(
    `确认删除免费号 ${email}？\n会级联清理：本地 auth 文件 + sub2api 远端账号 + 临时邮箱 + 本地 JSON 条目。`
  )
  if (!ok) return

  actionEmail.value = email
  actionType.value = 'delete'
  try {
    const result = await api.free.delete(email)
    const cleanup = result.cleanup || {}
    const parts = []
    if (cleanup.local_record) parts.push('本地记录')
    if (cleanup.local_auth_files?.length) parts.push(`auth 文件 ${cleanup.local_auth_files.length} 个`)
    if (cleanup.sub2api_accounts?.length) parts.push(`sub2api ${cleanup.sub2api_accounts.length} 个`)
    if (cleanup.cloudmail_deleted) parts.push('临时邮箱')
    const summary = parts.length ? `（已清理：${parts.join(' / ')}）` : ''
    showMessage(`${result.message || '免费号删除完成'}${summary}`, 'success')
    await loadList()
  } catch (e) {
    showMessage(`删除失败: ${e.message}`, 'error')
  } finally {
    actionEmail.value = ''
    actionType.value = ''
  }
}

// ---------------------------------------------------------------------------
// 表格渲染辅助
// ---------------------------------------------------------------------------

function statusLabel(s) {
  return {
    active: 'Active',
    auth_failed: 'OAuth 失败',
    exhausted: '已用尽',
  }[s] || s
}

function statusClass(s) {
  return {
    active: 'bg-green-500/10 text-green-400',
    auth_failed: 'bg-amber-500/15 text-amber-300',
    exhausted: 'bg-gray-500/15 text-gray-400',
  }[s] || 'bg-gray-500/10 text-gray-400'
}

function dotClass(s) {
  return {
    active: 'bg-green-400',
    auth_failed: 'bg-amber-400',
    exhausted: 'bg-gray-400',
  }[s] || 'bg-gray-400'
}

function statusTooltip(rec) {
  if (rec.status === 'auth_failed') {
    return 'OAuth 失败，可手动删除（v2 支持手动重试）'
  }
  if (rec.status === 'exhausted') {
    return '额度用尽，等待重置或删除'
  }
  return ''
}

function rowClass(rec) {
  if (rec.team_residue) return 'bg-red-500/5 hover:bg-red-500/10'
  if (rec.status === 'auth_failed') return 'bg-amber-500/5 hover:bg-amber-500/10'
  if (rec.status === 'exhausted') return 'opacity-60 hover:bg-gray-800/30'
  return 'hover:bg-gray-800/30'
}

function quotaRemaining(rec, type) {
  const qi = rec.last_quota
  if (!qi) return null
  const pct = type === 'primary' ? qi.primary_pct : qi.weekly_pct
  // last_quota 里 primary_pct/weekly_pct 表示"已使用百分比",剩余 = 100 - used
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

function formatTime(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
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
