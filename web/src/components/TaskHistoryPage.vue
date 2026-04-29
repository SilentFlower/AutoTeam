<template>
  <div>
    <div class="flex flex-wrap items-end justify-between gap-3 mb-2">
      <h2 class="text-xl font-bold text-white">任务历史</h2>
      <!--
        过滤范围切换:仅当前 admin / 全部 admin。
        前端做过滤(基于 App.vue 已经轮询到的全量 tasks),不重新拉接口,
        切换响应即时。如果 task.admin_id 是 null(未关联 admin),归到"未关联"。
      -->
      <div class="flex items-center gap-2 text-xs">
        <span class="text-gray-500">范围</span>
        <button
          v-for="opt in scopeOptions"
          :key="opt.value"
          type="button"
          @click="scope = opt.value"
          :class="scope === opt.value
            ? 'bg-blue-500/15 text-blue-200 border-blue-400/30'
            : 'bg-gray-900 text-gray-400 border-gray-800 hover:text-gray-200'"
          class="rounded-lg border px-3 py-1.5 transition"
        >
          {{ opt.label }}
        </button>
      </div>
    </div>
    <p class="text-sm text-gray-400 mb-6">
      查看后台任务的执行状态、耗时、参数和结果,便于排查失败原因。
    </p>
    <TaskHistory :tasks="filteredTasks" :admin-alias-map="aliasMap" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import TaskHistory from './TaskHistory.vue'
import { useAdmins } from '../store/admins.js'

const props = defineProps({
  tasks: { type: Array, default: () => [] },
})

const { state, refreshAdmins } = useAdmins()

// 范围:current=仅当前 admin / all=全部
const scope = ref('current')
const scopeOptions = computed(() => [
  { value: 'current', label: state.currentAdminId ? '仅当前主号' : '仅当前主号(未选)' },
  { value: 'all', label: '全部主号' },
])

/** admin_id → alias 的映射,给 TaskHistory 显示更友好 */
const aliasMap = computed(() => {
  const map = {}
  for (const a of state.admins) {
    map[a.admin_id] = a.alias || a.email || a.admin_id
  }
  return map
})

const filteredTasks = computed(() => {
  if (scope.value === 'all') return props.tasks
  const cur = state.currentAdminId
  if (!cur) return props.tasks  // 没有当前 admin 时退化为全部,避免空白页误导
  return props.tasks.filter(t => t.admin_id === cur)
})

onMounted(() => {
  // store 可能尚未加载过 admins(用户首次进入此页),主动 refresh 一次
  // 让 aliasMap 有数据。失败也不阻塞展示。
  if (!state.loaded) {
    refreshAdmins().catch(() => {})
  }
})
</script>
