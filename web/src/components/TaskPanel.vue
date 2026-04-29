<template>
  <div class="mt-6 bg-gray-900 border border-gray-800 rounded-xl p-4">
    <h2 class="text-lg font-semibold text-white mb-4">{{ panelTitle }}</h2>
    <div v-if="showAdminHint" class="mb-4 px-4 py-3 rounded-lg text-sm border bg-amber-500/10 text-amber-300 border-amber-500/20">
      {{ adminHint }}
    </div>
    <div class="flex flex-wrap gap-3">
      <!-- 每个按钮包一层 group,用于 hover 时浮出 tooltip(仅 action.tooltip 存在时渲染) -->
      <div v-for="action in visibleActions" :key="action.key" class="relative group">
        <button
          @click="execute(action)"
          :disabled="isDisabled(action)"
          class="px-4 py-2 rounded-lg text-sm font-medium transition border"
          :class="isDisabled(action)
            ? 'bg-gray-800 text-gray-500 border-gray-700 cursor-not-allowed'
            : `${action.style} hover:opacity-80`">
          {{ action.label }}
        </button>
        <div v-if="action.tooltip"
          class="invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-opacity absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 bg-gray-950 border border-gray-700 rounded-lg text-xs leading-relaxed text-gray-200 w-72 z-20 pointer-events-none shadow-lg">
          {{ action.tooltip }}
          <!-- 三角箭头,贴在浮层底部指向按钮 -->
          <div class="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-gray-700"></div>
        </div>
      </div>
    </div>

    <!-- 参数输入 -->
    <div v-if="showParams" class="mt-4 flex items-center gap-3">
      <label class="text-sm text-gray-400">{{ paramLabel }}:</label>
      <input v-model.number="paramValue" type="number" min="1" max="20"
        class="w-20 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500" />
      <button @click="confirmAction" :disabled="pendingAction && isDisabled(pendingAction)"
        class="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition">
        确认执行
      </button>
      <button @click="showParams = false"
        class="px-3 py-1.5 text-gray-400 hover:text-white text-sm transition">
        取消
      </button>
    </div>

    <!-- 结果提示 -->
    <div v-if="message" class="mt-4 px-4 py-3 rounded-lg text-sm" :class="messageClass">
      {{ message }}
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  runningTask: Object,
  adminStatus: {
    type: Object,
    default: null,
  },
  mode: {
    type: String,
    default: 'all',
  },
})
const emit = defineEmits(['task-started', 'refresh'])

const actions = [
  { key: 'rotate', group: 'pool', label: '智能轮转', method: 'startRotate', needParam: true, paramName: 'target', style: 'bg-blue-600 text-white border-blue-500' },
  { key: 'check', group: 'pool', label: '检查额度', method: 'startCheck', needParam: false, style: 'bg-emerald-600 text-white border-emerald-500' },
  { key: 'fill', group: 'pool', label: '补满成员', method: 'startFill', needParam: true, paramName: 'target', style: 'bg-violet-600 text-white border-violet-500' },
  { key: 'add', group: 'pool', label: '添加账号', method: 'startAdd', needParam: false, style: 'bg-amber-600 text-white border-amber-500' },
  { key: 'add-via-invite', group: 'pool', label: '邀请加号', method: 'startAddViaInvite', needParam: false, style: 'bg-orange-600 text-white border-orange-500' },
  { key: 'cleanup', group: 'pool', label: '清理成员', method: 'startCleanup', needParam: false, style: 'bg-rose-600 text-white border-rose-500' },
  { key: 'sync', group: 'sync', label: '同步远端', method: 'postSync', needParam: false, sync: true, allowWithoutAdmin: true, style: 'bg-cyan-600 text-white border-cyan-500',
    tooltip: '把本地 active 账号推送到 Sub2API：远端已存在则更新，不存在则创建，本地非 active 的反向删除。不写本地。' },
  { key: 'sync-accounts', group: 'sync', label: '同步账号', method: 'postSyncAccounts', needParam: false, sync: true, allowWithoutAdmin: true, style: 'bg-sky-600 text-white border-sky-500',
    tooltip: '用 Team 实际成员名单 + auths 目录回灌本地 accounts.json：对账状态、自动补建丢失的本地记录。不动远端。' },
]

const showParams = ref(false)
const paramLabel = ref('')
const paramValue = ref(5)
const pendingAction = ref(null)
const message = ref('')
const messageClass = ref('')
const adminReady = computed(() => !!props.adminStatus?.configured)
const visibleActions = computed(() => {
  if (props.mode === 'all') return actions
  return actions.filter(action => action.group === props.mode)
})
const panelTitle = computed(() => {
  if (props.mode === 'pool') return '账号池操作'
  if (props.mode === 'sync') return '同步操作'
  return '操作'
})
const adminHint = computed(() => {
  if (props.mode === 'sync') {
    return '同步类操作可独立使用：同步账号、同步已启用远端。'
  }
  return '请先在「配置面板」页完成管理员登录后，轮转/补满/清理等账号池操作才会开放。'
})
const showAdminHint = computed(() => !adminReady.value && (props.mode === 'pool' || props.mode === 'sync'))

function isDisabled(action) {
  if (props.runningTask) return true
  if (!adminReady.value && !action.allowWithoutAdmin) return true
  return false
}

async function execute(action) {
  if (isDisabled(action)) return
  message.value = ''
  if (action.needParam) {
    pendingAction.value = action
    paramLabel.value = action.paramName === 'target' ? '目标成员数' : '最大席位'
    paramValue.value = 5
    showParams.value = true
    return
  }
  await doExecute(action)
}

async function confirmAction() {
  showParams.value = false
  if (pendingAction.value) {
    await doExecute(pendingAction.value, paramValue.value)
    pendingAction.value = null
  }
}

async function doExecute(action, param) {
  try {
    if (action.sync) {
      const result = await api[action.method]()
      message.value = result.message || '操作完成'
      messageClass.value = 'bg-green-500/10 text-green-400 border border-green-500/20'
      emit('refresh')
    } else {
      const result = await api[action.method](param)
      message.value = `任务已提交: ${result.task_id}`
      messageClass.value = 'bg-blue-500/10 text-blue-400 border border-blue-500/20'
      emit('task-started')
    }
  } catch (e) {
    message.value = e.message
    messageClass.value = 'bg-red-500/10 text-red-400 border border-red-500/20'
  }
  setTimeout(() => { message.value = '' }, 8000)
}
</script>
