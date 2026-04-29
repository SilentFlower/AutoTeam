<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-white">日志</h2>
      <div class="flex flex-wrap items-center gap-3">
        <!--
          按 admin 过滤:勾选后只显示包含 [admin=<id>] 或 alias 的日志行。
          PR2 巡检循环已在日志里加了 admin_id 前缀;其他后端日志(API/登录等)
          多数不带,过滤后这些行不会显示——这是已知限制,后续若有需要可在
          backend 全局 logger formatter 中统一注入 admin_id 上下文。
        -->
        <label class="flex items-center gap-2 text-sm text-gray-400">
          <input
            type="checkbox"
            v-model="filterByAdmin"
            :disabled="!currentAdminId"
            class="rounded bg-gray-800 border-gray-700 disabled:opacity-50"
          />
          仅当前主号 <span v-if="currentAdminAlias" class="text-blue-300">({{ currentAdminAlias }})</span>
        </label>
        <label class="flex items-center gap-2 text-sm text-gray-400">
          <input type="checkbox" v-model="autoScroll" class="rounded bg-gray-800 border-gray-700" />
          自动滚动
        </label>
        <button @click="fetchLogs" :disabled="loading"
          class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-sm rounded-lg border border-gray-700 transition disabled:opacity-50 text-gray-300 hover:text-white">
          刷新
        </button>
        <button @click="clearLogs"
          class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-sm rounded-lg border border-gray-700 transition text-gray-400 hover:text-white">
          清空
        </button>
      </div>
    </div>

    <div ref="logContainer"
      class="bg-gray-950 border border-gray-800 rounded-xl p-3 md:p-4 font-mono text-xs leading-relaxed h-[calc(100vh-200px)] md:h-[600px] overflow-y-auto">
      <div v-if="visibleLogs.length === 0" class="text-gray-600 text-center py-8">
        {{ filterByAdmin && logs.length > 0 ? '当前主号没有相关日志(过滤后)' : '暂无日志' }}
      </div>
      <div v-for="(log, i) in visibleLogs" :key="i"
        class="py-0.5 flex gap-3 hover:bg-gray-900/50">
        <span class="text-gray-600 shrink-0">{{ formatTime(log.time) }}</span>
        <span class="shrink-0 w-16"
          :class="{
            'text-red-400': log.level === 'ERROR',
            'text-yellow-400': log.level === 'WARNING',
            'text-blue-400': log.level === 'INFO',
            'text-gray-500': log.level === 'DEBUG',
          }">{{ log.level }}</span>
        <span class="text-gray-300 break-all">{{ log.message }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { api } from '../api.js'
import { useAdmins } from '../store/admins.js'

const logs = ref([])
const loading = ref(false)
const autoScroll = ref(true)
const filterByAdmin = ref(false)
const logContainer = ref(null)
let pollTimer = null
let lastTime = 0

const { state, refreshAdmins } = useAdmins()
const currentAdminId = computed(() => state.currentAdminId)
const currentAdminAlias = computed(() => {
  const a = state.admins.find(x => x.admin_id === state.currentAdminId)
  return a?.alias || a?.email || ''
})

/**
 * 按当前 admin 过滤可见日志。匹配策略:子串包含 admin_id 或 alias/email 标记。
 * PR2 巡检循环写日志时格式是 "[admin=<id> alias=<alias>] ...",
 * 其他模块的日志多数没有 admin 上下文,过滤后会被一并隐藏。
 */
const visibleLogs = computed(() => {
  if (!filterByAdmin.value || !currentAdminId.value) return logs.value
  const id = currentAdminId.value
  const alias = currentAdminAlias.value
  return logs.value.filter(log => {
    const msg = log.message || ''
    if (msg.includes(`admin=${id}`) || msg.includes(`admin_id=${id}`)) return true
    if (alias && msg.includes(`alias=${alias}`)) return true
    return false
  })
})

function formatTime(ts) {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

async function fetchLogs() {
  loading.value = true
  try {
    const result = await api.getLogs(500, lastTime)
    if (result.logs.length > 0) {
      if (lastTime === 0) {
        logs.value = result.logs
      } else {
        logs.value.push(...result.logs)
        // 保留最新 1000 条
        if (logs.value.length > 1000) {
          logs.value = logs.value.slice(-1000)
        }
      }
      lastTime = result.logs[result.logs.length - 1].time
      if (autoScroll.value) {
        nextTick(() => {
          if (logContainer.value) {
            logContainer.value.scrollTop = logContainer.value.scrollHeight
          }
        })
      }
    }
  } catch (e) {
    console.error('获取日志失败:', e)
  } finally {
    loading.value = false
  }
}

function clearLogs() {
  logs.value = []
  lastTime = 0
}

onMounted(() => {
  fetchLogs()
  pollTimer = setInterval(fetchLogs, 3000)
  // store 可能尚未加载,主动 refresh 一次让 alias 可用;失败也不阻塞
  if (!state.loaded) {
    refreshAdmins().catch(() => {})
  }
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
