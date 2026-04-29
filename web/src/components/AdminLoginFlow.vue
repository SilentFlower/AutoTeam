<template>
  <div class="space-y-4">
    <div v-if="message" class="rounded-2xl px-4 py-3 text-sm border" :class="messageClass">
      {{ message }}
    </div>

    <!-- 步骤 0：还未启动登录 → 输入新管理员邮箱 -->
    <div v-if="step === 'idle'" class="space-y-3">
      <div>
        <label class="block text-sm text-gray-400 mb-2">{{ targetAdminId ? '为现有管理员重新登录' : '新管理员邮箱' }}</label>
        <div class="flex flex-col sm:flex-row gap-3">
          <input
            v-model.trim="email"
            type="email"
            autocomplete="username"
            placeholder="请输入主号邮箱"
            :disabled="submitting"
            class="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
          />
          <button
            @click="startLogin"
            :disabled="submitting || !email"
            class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ submitting ? '提交中...' : '开始登录' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 步骤 1：等待密码 -->
    <div v-else-if="step === 'password_required'" class="space-y-3">
      <div class="text-sm text-gray-300">
        当前邮箱：<span class="font-mono">{{ email }}</span>
      </div>
      <div class="flex flex-col sm:flex-row gap-3">
        <input
          v-model="password"
          type="password"
          autocomplete="current-password"
          placeholder="输入主号密码"
          :disabled="submitting"
          class="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
        />
        <button
          @click="submitPassword"
          :disabled="submitting || !password"
          class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50"
        >
          {{ submitting ? '提交中...' : '提交密码' }}
        </button>
      </div>
    </div>

    <!-- 步骤 2：等待验证码 -->
    <div v-else-if="step === 'code_required'" class="space-y-3">
      <div class="text-sm text-gray-300">
        当前邮箱：<span class="font-mono">{{ email }}</span>
      </div>
      <div class="flex flex-col sm:flex-row gap-3">
        <input
          v-model.trim="code"
          type="text"
          inputmode="numeric"
          autocomplete="one-time-code"
          placeholder="输入邮箱验证码"
          :disabled="submitting"
          class="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
        />
        <button
          @click="submitCode"
          :disabled="submitting || !code"
          class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50"
        >
          {{ submitting ? '提交中...' : '提交验证码' }}
        </button>
      </div>
    </div>

    <!-- 步骤 3：选 workspace -->
    <div v-else-if="step === 'workspace_required'" class="space-y-3">
      <div class="text-sm text-gray-300">请选择要进入的组织 / workspace</div>
      <select
        v-model="workspaceOptionId"
        :disabled="submitting"
        class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
      >
        <option disabled value="">请选择组织</option>
        <option v-for="opt in workspaceOptions" :key="opt.id" :value="opt.id">
          {{ opt.label }}{{ opt.kind === 'fallback' ? ' (可能是个人/免费)' : '' }}
        </option>
      </select>
      <button
        @click="submitWorkspace"
        :disabled="submitting || !workspaceOptionId"
        class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50"
      >
        {{ submitting ? '提交中...' : '确认组织选择' }}
      </button>
    </div>

    <!-- 步骤 X：流程进行中或已完成 -->
    <div v-else-if="step === 'completed'" class="rounded-2xl border border-emerald-400/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
      管理员登录已完成，可关闭此面板。
    </div>

    <div class="flex justify-end gap-2 pt-2">
      <button
        v-if="canCancel"
        @click="cancel"
        :disabled="submitting"
        class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-sm text-gray-200 rounded-lg border border-gray-700 transition disabled:opacity-50"
      >
        取消登录
      </button>
    </div>
  </div>
</template>

<script setup>
/**
 * AdminLoginFlow.vue
 *
 * 复用"邮箱 → 密码 → 验证码 → workspace 选择"主线流程的轻量组件。
 * - targetAdminId 为空：调 /api/admins/login/* 系列接口为新 admin 注册凭据。
 * - targetAdminId 非空：为已存在的 admin 重新登录（PR2 接口的 target_admin_id 字段）。
 *
 * 与 Settings.vue 中"管理员登录"区段相比，本组件**只承担注册主线**，
 * 不包含 session_token 导入 / Codex 同步 / 删除 admin 等单 admin 维度的运维能力。
 * 那些场景仍走 ConfigPage → Settings.vue。
 *
 * 状态来源：每次提交后调 api.getAdminStatus() 重新拉 admin_status，按返回的
 * login_step 推进 UI。整个会话由后端的 _admin_login_api 单例锁串行，前端不
 * 重复持有 step 状态，避免与后端不一致。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  /**
   * 已存在 admin 的 admin_id；为空表示创建新 admin。
   */
  targetAdminId: {
    type: String,
    default: '',
  },
  /**
   * 创建新 admin 时的初始邮箱（可选，用户也能在表单里输入）。
   */
  initialEmail: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['done', 'cancel', 'progress'])

const email = ref(props.initialEmail || '')
const password = ref('')
const code = ref('')
const workspaceOptionId = ref('')
const workspaceOptions = ref([])
const step = ref('idle') // idle | password_required | code_required | workspace_required | completed
const submitting = ref(false)
const message = ref('')
const messageClass = ref('')
let pollTimer = null

// 仅在登录已开始且未完成时显示"取消登录"按钮（idle 与 completed 状态隐藏）。
const canCancel = computed(() => step.value !== 'idle' && step.value !== 'completed')

function setMessage(text, type = 'success') {
  message.value = text
  messageClass.value = type === 'success'
    ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20'
    : 'bg-red-500/10 text-red-300 border-red-500/20'
}

function clearMessage() {
  message.value = ''
}

/**
 * 把后端返回的 step 推到本地，并按 step 同步 workspace_options 缓存。
 * @param {object} adminStatus /api/admin/status 或 login result.admin 字段
 */
function applyStep(adminStatus) {
  const next = adminStatus?.login_step || (adminStatus?.login_in_progress ? '' : 'idle')
  if (next) {
    step.value = next
  }
  if (next === 'workspace_required') {
    workspaceOptions.value = adminStatus?.workspace_options || []
    if (!workspaceOptionId.value) {
      const preferred = workspaceOptions.value.find(o => o.kind === 'preferred')
      workspaceOptionId.value = preferred?.id || workspaceOptions.value[0]?.id || ''
    }
  }
}

/**
 * 启动登录流程：targetAdminId 决定走"创建新 admin"还是"重登已有 admin"。
 * 后端会把 status 推到 password_required / code_required / workspace_required / completed。
 */
async function startLogin() {
  submitting.value = true
  clearMessage()
  try {
    const result = props.targetAdminId
      ? await api.startAdminLoginForTarget(email.value, props.targetAdminId)
      : await api.startAdminLoginAsNew(email.value)
    if (result.status === 'completed') {
      step.value = 'completed'
      setMessage('管理员登录完成')
      emit('done', { admin: result.admin || null, info: result.info || null })
      return
    }
    applyStep(result.admin)
    emit('progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
  }
}

async function submitPassword() {
  submitting.value = true
  clearMessage()
  try {
    const result = await api.submitAdminLoginPasswordTarget(password.value, props.targetAdminId || null)
    if (result.status === 'completed') {
      step.value = 'completed'
      setMessage('管理员登录完成')
      emit('done', { admin: result.admin || null, info: result.info || null })
      return
    }
    applyStep(result.admin)
    password.value = ''
    emit('progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
  }
}

async function submitCode() {
  submitting.value = true
  clearMessage()
  try {
    const result = await api.submitAdminLoginCodeTarget(code.value, props.targetAdminId || null)
    if (result.status === 'completed') {
      step.value = 'completed'
      setMessage('管理员登录完成')
      emit('done', { admin: result.admin || null, info: result.info || null })
      return
    }
    applyStep(result.admin)
    code.value = ''
    emit('progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
  }
}

async function submitWorkspace() {
  submitting.value = true
  clearMessage()
  try {
    const result = await api.submitAdminLoginWorkspaceTarget(workspaceOptionId.value, props.targetAdminId || null)
    if (result.status === 'completed') {
      step.value = 'completed'
      setMessage('管理员登录完成')
      emit('done', { admin: result.admin || null, info: result.info || null })
      return
    }
    applyStep(result.admin)
    emit('progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
  }
}

/**
 * 取消登录：调旧的 /api/admin/login/cancel（PR2 未为多 admin 单独引入新的
 * cancel 接口，但 cancel 操作语义同时影响所有目标 admin 的会话——后端只有
 * 一份 _admin_login_api 单例锁，cancel 即清空当前会话）。
 */
async function cancel() {
  submitting.value = true
  try {
    await api.cancelAdminLogin()
    step.value = 'idle'
    password.value = ''
    code.value = ''
    setMessage('管理员登录已取消')
    emit('cancel')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
  }
}

/**
 * 轮询 admin_status：登录会话进行中时，后端可能因为浏览器侧异步推进了
 * step（如从 password_required 自动跳到 code_required），这里 5 秒拉一次
 * 避免界面卡在过期 step 上。
 */
async function pollStatus() {
  if (step.value === 'idle' || step.value === 'completed') {
    return
  }
  try {
    const status = await api.getAdminStatus()
    applyStep(status)
  } catch {
    // 静默忽略：轮询失败不影响主线，下次重试。
  }
}

onMounted(() => {
  // 进入面板时先拉一次状态：如果用户上次没走完流程刷新了页面，能继续从中间步骤接上。
  if (props.targetAdminId) {
    api.getAdminStatus().then(applyStep).catch(() => {})
  }
  pollTimer = setInterval(pollStatus, 5000)
})

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>
