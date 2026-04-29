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
        <li v-for="a in admins" :key="a.admin_id" class="group/row relative">
          <button
            @click="onSelect(a)"
            type="button"
            class="group flex w-full items-start gap-3 rounded-xl px-3 py-2 pr-10 text-left transition"
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
          <!--
            删除按钮放在 li 上而不是放进上面的切换 <button> 内部,
            避免 button 嵌套 button 造成的 a11y 与 hit-test 问题。
            悬浮在右侧,hover/focus 时浮现;唯一 admin 时禁用并解释原因。
          -->
          <button
            type="button"
            @click.stop="onDelete(a)"
            :disabled="admins.length <= 1 || deleting === a.admin_id"
            :title="admins.length <= 1
              ? '系统中只剩一个管理员,无法删除;请先添加其他管理员或登录新账号'
              : '删除该管理员的凭据与数据目录'"
            class="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-lg text-slate-500 transition opacity-0 group-hover/row:opacity-100 focus:opacity-100 hover:bg-red-500/15 hover:text-red-300 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-slate-600 disabled:opacity-30"
          >
            <span class="text-sm">{{ deleting === a.admin_id ? '…' : '🗑' }}</span>
          </button>
        </li>
      </ul>

      <!-- 删除错误提示:展示在下拉底部,避免遮挡列表 -->
      <div
        v-if="deleteError"
        class="mt-2 rounded-xl border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300"
      >
        {{ deleteError }}
      </div>

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

const emit = defineEmits(['add-admin', 'switched', 'removed'])

const { state, refreshAdmins, switchAdmin, removeAdmin } = useAdmins()
const open = ref(false)
const rootRef = ref(null)
const deleting = ref(null)
const deleteError = ref('')

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
 * 删除指定 admin。先用 window.confirm 让用户二次确认（不引入额外组件）;
 * 删除请求过程中按钮显示 loading 状态。删除唯一 admin 由后端拦截 400,
 * 这里也通过 disabled 兜底。
 * @param {object} admin 待删除的 admin 记录
 */
async function onDelete(admin) {
  if (!admin?.admin_id) return
  if (state.admins.length <= 1) return
  const label = admin.alias || admin.email || admin.admin_id
  const confirmed = window.confirm(
    `确定要删除管理员【${label}】吗?\n\n这会同时清空该主号在 data/admins/${admin.admin_id}/ 下的凭据与账号池数据,无法恢复。`
  )
  if (!confirmed) return
  deleting.value = admin.admin_id
  deleteError.value = ''
  try {
    await removeAdmin(admin.admin_id)
    emit('removed', { adminId: admin.admin_id })
  } catch (e) {
    deleteError.value = e?.message || '删除失败,请稍后再试'
  } finally {
    deleting.value = null
  }
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
