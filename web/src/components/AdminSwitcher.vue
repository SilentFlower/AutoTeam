<template>
  <div class="relative" ref="rootRef">
    <button
      @click="toggle"
      type="button"
      class="flex items-center gap-3 rounded-2xl border border-white/10 bg-slate-900/70 px-4 py-2.5 text-left text-sm text-white shadow-inner shadow-white/5 transition hover:border-white/20"
    >
      <span class="flex h-8 w-8 items-center justify-center rounded-xl bg-blue-500/15 text-base">👤</span>
      <span class="flex flex-col leading-tight">
        <span class="text-xs text-slate-400">当前管理员</span>
        <span class="font-medium text-white truncate max-w-[180px]">
          {{ activeAdmin?.alias || activeAdmin?.email || '尚未选择' }}
        </span>
      </span>
      <span class="ml-2 text-slate-400 text-xs">{{ open ? '▲' : '▼' }}</span>
    </button>

    <!-- 下拉面板 -->
    <div
      v-if="open"
      class="absolute z-30 mt-2 w-80 rounded-2xl border border-white/10 bg-slate-950/95 p-2 shadow-2xl backdrop-blur-xl"
    >
      <div v-if="!loaded" class="px-3 py-2 text-sm text-slate-400">加载管理员列表中...</div>
      <div v-else-if="admins.length === 0" class="px-3 py-2 text-sm text-slate-400">还没有任何管理员</div>

      <ul v-if="loaded" class="space-y-1 max-h-[60vh] overflow-y-auto">
        <li v-for="a in admins" :key="a.admin_id">
          <button
            @click="onSelect(a)"
            type="button"
            class="group flex w-full items-start gap-3 rounded-xl px-3 py-2 text-left transition"
            :class="a.is_active ? 'bg-blue-500/15 ring-1 ring-blue-400/30' : 'hover:bg-white/5'"
          >
            <span class="mt-0.5 flex h-6 w-6 items-center justify-center rounded-lg text-xs"
              :class="a.is_active ? 'bg-cyan-500/30 text-cyan-200' : 'bg-white/5 text-slate-400 group-hover:text-white'">
              {{ a.is_active ? '✓' : '·' }}
            </span>
            <span class="min-w-0 flex-1">
              <span class="block truncate text-sm font-medium text-white">{{ a.alias || a.email || a.admin_id }}</span>
              <span class="mt-0.5 block truncate text-xs text-slate-400">{{ a.email || '—' }}</span>
              <span v-if="a.workspace_name" class="mt-0.5 block truncate text-[11px] text-slate-500">{{ a.workspace_name }}</span>
            </span>
            <span v-if="a.is_active" class="text-[10px] text-emerald-300">激活中</span>
          </button>
        </li>
      </ul>

      <div class="mt-2 border-t border-white/10 pt-2">
        <button
          type="button"
          @click="onAddAdmin"
          class="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm text-blue-300 transition hover:bg-blue-500/10"
        >
          <span class="text-base">＋</span>
          添加新管理员
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * AdminSwitcher.vue
 *
 * 工作台顶部 admin 切换器。下拉显示所有 admin（别名 / email / workspace），
 * 点击列表项调 store.switchAdmin，点击底部"+ 添加新管理员"emit add-admin
 * 让父组件弹 modal。
 *
 * 数据来源：useAdmins() store 的 readonly state。挂载时如果 store 还没加载
 * 过 admins，会主动 refreshAdmins 一次。
 */
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useAdmins } from '../store/admins.js'

const emit = defineEmits(['add-admin', 'switched'])

const { state, refreshAdmins, switchAdmin } = useAdmins()
const open = ref(false)
const rootRef = ref(null)

const admins = computed(() => state.admins)
const loaded = computed(() => state.loaded)
const activeAdmin = computed(() => state.admins.find(a => a.admin_id === state.currentAdminId) || null)

function toggle() {
  open.value = !open.value
}

async function onSelect(admin) {
  if (!admin?.admin_id) return
  if (admin.admin_id === state.currentAdminId) {
    open.value = false
    return
  }
  try {
    await switchAdmin(admin.admin_id)
    open.value = false
    emit('switched', { adminId: admin.admin_id })
  } catch (e) {
    console.error('切换 admin 失败:', e)
  }
}

function onAddAdmin() {
  open.value = false
  emit('add-admin')
}

/**
 * 点击下拉外部时关闭。挂在 document mousedown 上而不是 click，避免和子组件
 * 的 click 事件抢先后顺序。
 */
function onDocClick(evt) {
  if (!open.value) return
  if (rootRef.value && !rootRef.value.contains(evt.target)) {
    open.value = false
  }
}

onMounted(async () => {
  document.addEventListener('mousedown', onDocClick)
  if (!state.loaded) {
    try {
      await refreshAdmins()
    } catch (e) {
      console.warn('加载 admin 列表失败:', e)
    }
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
})
</script>
