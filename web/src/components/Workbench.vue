<template>
  <div class="space-y-6">
    <!-- 顶部 toolbar：admin 切换器 + 邀请加号主按钮 + 刷新 -->
    <div class="glass-card flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between">
      <div class="flex flex-wrap items-center gap-3">
        <AdminSwitcher @add-admin="openAddAdmin" @switched="onAdminSwitched" />
        <span class="text-xs text-slate-500">切换主号后，下方四个 tab 会刷新到该主号上下文</span>
      </div>
      <div class="flex flex-wrap items-center gap-3">
        <InviteActionButton :disabled="!!runningTask" @click="openInvite" />
        <button
          type="button"
          @click="$emit('refresh')"
          :disabled="loading"
          class="btn-secondary justify-center rounded-2xl px-3 py-2 text-sm"
        >
          {{ loading ? '刷新中...' : '🔄 刷新' }}
        </button>
      </div>
    </div>

    <!-- 4 个 tab 选择 -->
    <div class="glass-card p-2">
      <div class="flex flex-wrap gap-2">
        <button
          v-for="t in tabs"
          :key="t.key"
          @click="currentTab = t.key"
          type="button"
          class="pill-tab flex items-center gap-2"
          :class="currentTab === t.key ? 'pill-tab-active' : ''"
        >
          <span class="text-base">{{ t.icon }}</span>
          {{ t.label }}
        </button>
      </div>
    </div>

    <!-- tab 主体 -->
    <div :key="adminScopeKey">
      <Dashboard
        v-if="currentTab === 'dashboard'"
        :status="status"
        :loading="loading"
        :running-task="runningTask"
        :admin-status="adminStatus"
        :current-admin-id="currentAdminId"
        @refresh="$emit('refresh')"
      />

      <TeamMembers
        v-else-if="currentTab === 'team'"
        :current-admin-id="currentAdminId"
      />

      <PoolPage
        v-else-if="currentTab === 'pool'"
        :running-task="runningTask"
        :admin-status="adminStatus"
        :current-admin-id="currentAdminId"
        @task-started="$emit('task-started')"
        @refresh="$emit('refresh')"
      />

      <SyncPage
        v-else-if="currentTab === 'sync'"
        :running-task="runningTask"
        :admin-status="adminStatus"
        :current-admin-id="currentAdminId"
        @task-started="$emit('task-started')"
        @refresh="$emit('refresh')"
      />
    </div>

    <!-- modal: 添加新管理员（复用 AdminLoginFlow） -->
    <div
      v-if="showAddAdminModal"
      class="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur"
    >
      <div class="glass-card w-full max-w-xl p-6">
        <div class="mb-4 flex items-center justify-between">
          <h3 class="text-lg font-semibold text-white">添加新管理员</h3>
          <button
            type="button"
            @click="closeAddAdmin"
            class="text-slate-400 hover:text-white text-sm"
          >
            ✕ 关闭
          </button>
        </div>
        <p class="mb-4 text-xs leading-6 text-slate-400">
          系统会为这个管理员单独维护一个数据目录（state、accounts、auths）。完成登录后会自动切到这个新管理员。
        </p>
        <AdminLoginFlow
          @done="onAdminAdded"
          @cancel="closeAddAdmin"
          @progress="$emit('refresh')"
        />
      </div>
    </div>

    <!-- modal: 邀请加号顶级入口 -->
    <InviteFlowModal
      v-if="showInviteModal"
      :running-task="runningTask"
      @done="onInviteDone"
      @cancel="closeInvite"
    />
  </div>
</template>

<script setup>
/**
 * Workbench.vue
 *
 * 工作台合并页（替代原 dashboard / team / pool / sync 四个独立页签）。
 * 顶部 toolbar 两个一级动作：admin 切换器（含 + 添加新管理员入口）、
 * 邀请加号主按钮。下方四个 tab 复用 Dashboard / TeamMembers / PoolPage /
 * SyncPage 组件，并通过 :current-admin-id 透传当前主号上下文。
 *
 * 切换 admin 后：
 * 1. store.switchAdmin 会派发全局 "autoteam:admin-switched" 事件；
 * 2. 本组件监听该事件并 emit 'refresh' 让上层重新拉 status / adminStatus；
 * 3. tab 主体外层 :key=adminScopeKey 会在 admin 切换时强制重建子组件，
 *    让各 tab 的内部 onMounted 数据加载逻辑自然重跑（避免每个 tab 都得自己
 *    监听全局事件）。
 */
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useAdmins } from '../store/admins.js'
import AdminSwitcher from './AdminSwitcher.vue'
import AdminLoginFlow from './AdminLoginFlow.vue'
import InviteActionButton from './InviteActionButton.vue'
import InviteFlowModal from './InviteFlowModal.vue'
import Dashboard from './Dashboard.vue'
import TeamMembers from './TeamMembers.vue'
import PoolPage from './PoolPage.vue'
import SyncPage from './SyncPage.vue'

defineProps({
  status: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  runningTask: { type: Object, default: null },
  adminStatus: { type: Object, default: null },
})

const emit = defineEmits(['refresh', 'task-started'])

const { state: adminState, refreshAdmins } = useAdmins()
const currentAdminId = computed(() => adminState.currentAdminId)

// 用 currentAdminId 作为 tab 子组件的强制重建 key：admin 切换 → key 变更
// → Vue 销毁旧 tab、挂载新 tab → 各 tab 的 onMounted 自然走一遍数据拉取。
// 比"每个 tab 自己监听 admin-switched 事件"更省心。
const adminScopeKey = computed(() => `admin:${currentAdminId.value || 'none'}`)

const tabs = [
  { key: 'dashboard', icon: '📊', label: '仪表盘' },
  { key: 'team', icon: '👥', label: 'Team 成员' },
  { key: 'pool', icon: '🔁', label: '账号池操作' },
  { key: 'sync', icon: '🔄', label: '同步中心' },
]
const currentTab = ref('dashboard')

const showAddAdminModal = ref(false)
const showInviteModal = ref(false)

function openAddAdmin() {
  showAddAdminModal.value = true
}

function closeAddAdmin() {
  showAddAdminModal.value = false
}

async function onAdminAdded() {
  // 后端 _prepare_admin_login_target 已经把新 admin 切到激活；
  // 这里同步刷新 store + 主面板状态即可。
  try {
    await refreshAdmins()
  } catch (e) {
    console.warn('刷新 admin 列表失败:', e)
  }
  emit('refresh')
  showAddAdminModal.value = false
}

function openInvite() {
  showInviteModal.value = true
}

function closeInvite() {
  showInviteModal.value = false
}

function onInviteDone() {
  showInviteModal.value = false
  emit('refresh')
  emit('task-started')
}

function onAdminSwitched() {
  // 切换后顶层重新拉 status / adminStatus；tab 子组件靠 adminScopeKey 重建自动重拉。
  emit('refresh')
}

/**
 * 监听全局 "autoteam:admin-switched" 事件。AdminSwitcher 内部已经发了这个
 * 事件，但直接通过 emit('switched') 也能同步刷新；监听全局事件主要为了
 * 兼容其他地方（如 InviteFlowModal）触发 admin 变更的场景。
 */
function onAdminSwitchedEvent() {
  emit('refresh')
}

onMounted(() => {
  window.addEventListener('autoteam:admin-switched', onAdminSwitchedEvent)
  // store 还没加载过 admins 时主动拉一次，确保 AdminSwitcher 一打开就有数据。
  if (!adminState.loaded) {
    refreshAdmins().catch(() => {})
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('autoteam:admin-switched', onAdminSwitchedEvent)
})
</script>
