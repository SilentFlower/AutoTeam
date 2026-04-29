<template>
  <div class="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur">
    <div class="glass-card w-full max-w-lg p-6">
      <div class="mb-4 flex items-center justify-between">
        <h3 class="text-lg font-semibold text-white">邀请加号</h3>
        <button
          type="button"
          @click="onClose"
          :disabled="phase === 'submitting'"
          class="text-slate-400 hover:text-white text-sm disabled:opacity-50"
        >
          ✕ 关闭
        </button>
      </div>

      <p class="mb-4 text-xs leading-6 text-slate-400">
        通过当前主号发送 Team 邀请、自动登录新账号、完成 Codex OAuth 后入池。整个流程在后台执行,完成后会反映到任务历史。
      </p>

      <!-- 错误提示 -->
      <div v-if="errorMessage" class="mb-4 rounded-2xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-300">
        {{ errorMessage }}
      </div>

      <!-- 后台已有任务在执行的提醒(仅 idle 阶段) -->
      <div
        v-if="phase === 'idle' && runningTask"
        class="mb-4 rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-200"
      >
        当前已有任务在执行,邀请加号会在前序任务完成后排队执行。
      </div>

      <!-- 进度卡片(submit 之后展示) -->
      <div
        v-if="taskId"
        class="mb-4 space-y-3 rounded-2xl border border-white/10 bg-slate-900/60 p-4 text-sm"
      >
        <div class="flex items-center justify-between">
          <span class="text-xs text-slate-400">任务 ID</span>
          <span class="font-mono text-xs text-slate-300">{{ taskId }}</span>
        </div>
        <div class="flex items-center justify-between">
          <span class="text-xs text-slate-400">状态</span>
          <span class="inline-flex items-center gap-2 text-sm" :class="statusClass">
            <span
              v-if="task?.status === 'running' || task?.status === 'pending'"
              class="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
            ></span>
            <span v-else class="inline-block h-2 w-2 rounded-full" :class="dotClass"></span>
            {{ statusLabel }}
          </span>
        </div>
        <div v-if="elapsed" class="flex items-center justify-between">
          <span class="text-xs text-slate-400">耗时</span>
          <span class="text-xs text-slate-300">{{ elapsed }}</span>
        </div>
        <!-- 失败 error 优先;否则展示 result 摘要 -->
        <div
          v-if="task?.error"
          class="rounded-xl border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300"
        >
          {{ task.error }}
        </div>
        <div
          v-else-if="task?.status === 'completed' && task?.result"
          class="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200"
        >
          {{ resultSummary }}
        </div>
      </div>

      <div class="flex justify-end gap-3 pt-2">
        <!-- idle / submitting 阶段:取消 + 开始 -->
        <template v-if="phase === 'idle' || phase === 'submitting'">
          <button
            type="button"
            @click="onClose"
            :disabled="phase === 'submitting'"
            class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-sm text-gray-200 rounded-lg border border-gray-700 transition disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            @click="confirm"
            :disabled="phase === 'submitting'"
            class="px-4 py-2 bg-orange-600 hover:bg-orange-500 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ phase === 'submitting' ? '提交中...' : '开始邀请加号' }}
          </button>
        </template>
        <!-- polling 阶段:仅"在后台继续"按钮(关闭 modal 但不取消任务) -->
        <template v-else-if="phase === 'polling'">
          <button
            type="button"
            @click="onClose"
            class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-sm text-gray-200 rounded-lg border border-gray-700 transition"
          >
            在后台继续
          </button>
        </template>
        <!-- done 阶段:关闭按钮 -->
        <template v-else-if="phase === 'done'">
          <button
            type="button"
            @click="onClose"
            class="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded-lg transition"
          >
            关闭
          </button>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * InviteFlowModal.vue
 *
 * 邀请加号顶级入口的 modal 壳。点击"开始邀请加号"调
 * api.startAddViaInvite();拿到 task_id 后启动 2 秒轮询,展示状态/耗时/结果,
 * 任务进入终态(completed/failed)后停止轮询、按钮变"关闭",并 emit done。
 *
 * 用户也可以中途点"在后台继续"关闭 modal——任务仍在后端跑,后续在
 * "任务历史"页能看到结果。modal 卸载时清理 timer。
 *
 * 后端的 /api/tasks/add-via-invite 路由可能尚未在 main 分支 ready
 * (视部署版本而定);如果接口不存在会返回 404,错误展示在 modal 内。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { api } from '../api.js'

defineProps({
  runningTask: { type: Object, default: null },
})
const emit = defineEmits(['done', 'cancel'])

/**
 * 阶段机:
 *   idle      - 用户尚未点击开始
 *   submitting- 正在 POST /api/tasks/add-via-invite 等待 task_id
 *   polling   - 任务已启动,按 2s 间隔轮询
 *   done      - 任务进入终态(completed/failed),停止轮询
 */
const phase = ref('idle')
const taskId = ref(null)
const task = ref(null)
const errorMessage = ref('')
let pollTimer = null

const POLL_INTERVAL_MS = 2000

const statusLabel = computed(() => {
  const map = { pending: '等待中', running: '执行中', completed: '已完成', failed: '失败' }
  return map[task.value?.status] || (task.value?.status ?? '提交中')
})

const statusClass = computed(() => {
  return {
    pending: 'text-slate-300',
    running: 'text-amber-300',
    completed: 'text-emerald-300',
    failed: 'text-red-300',
  }[task.value?.status] || 'text-slate-300'
})

const dotClass = computed(() => {
  return {
    pending: 'bg-slate-400',
    completed: 'bg-emerald-400',
    failed: 'bg-red-400',
  }[task.value?.status] || 'bg-slate-400'
})

const elapsed = computed(() => {
  const start = task.value?.started_at || task.value?.created_at
  const end = task.value?.finished_at || (
    task.value?.status === 'running' || task.value?.status === 'pending'
      ? Date.now() / 1000
      : null
  )
  if (!start || !end) return ''
  const sec = Math.max(0, Math.round(end - start))
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
})

/**
 * 任务 result 字段是后端 dict,展示时取关键字段做摘要;不直接打印 JSON
 * 避免长串污染 modal 视觉。
 */
const resultSummary = computed(() => {
  const r = task.value?.result
  if (!r) return ''
  if (typeof r === 'string') return r
  // 邀请加号任务一般返回 {email, status, info...} 之类;只取人类可读的摘要
  const parts = []
  if (r.email) parts.push(`新账号: ${r.email}`)
  if (r.status) parts.push(`结果: ${r.status}`)
  if (r.message) parts.push(r.message)
  return parts.length > 0 ? parts.join(' · ') : '任务完成'
})

async function confirm() {
  phase.value = 'submitting'
  errorMessage.value = ''
  try {
    const result = await api.startAddViaInvite()
    taskId.value = result?.task_id || null
    if (!taskId.value) {
      // 后端没返回 task_id 视为同步完成,不做轮询
      phase.value = 'done'
      emit('done', result)
      return
    }
    phase.value = 'polling'
    // 立即拉一次再开 timer,缩短首次状态出现的延迟
    await pollOnce()
    if (phase.value === 'polling') {
      pollTimer = setInterval(pollOnce, POLL_INTERVAL_MS)
    }
  } catch (e) {
    errorMessage.value = e?.message || '邀请加号请求失败'
    phase.value = 'idle'
  }
}

async function pollOnce() {
  if (!taskId.value) return
  try {
    const t = await api.getTask(taskId.value)
    task.value = t
    if (t?.status === 'completed' || t?.status === 'failed') {
      stopPolling()
      phase.value = 'done'
      emit('done', t)
    }
  } catch (e) {
    // 轮询失败不立即终止;允许下次重试。404 例外:任务被清理了直接收尾。
    if (e?.status === 404) {
      stopPolling()
      errorMessage.value = '任务记录已不存在,可能已超时清理'
      phase.value = 'done'
    }
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function onClose() {
  stopPolling()
  emit('cancel')
}

onBeforeUnmount(() => {
  stopPolling()
})
</script>
