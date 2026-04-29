<template>
  <div class="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur">
    <div class="glass-card w-full max-w-lg p-6">
      <div class="mb-4 flex items-center justify-between">
        <h3 class="text-lg font-semibold text-white">邀请加号</h3>
        <button
          type="button"
          @click="$emit('cancel')"
          :disabled="submitting"
          class="text-slate-400 hover:text-white text-sm disabled:opacity-50"
        >
          ✕ 关闭
        </button>
      </div>

      <p class="mb-4 text-xs leading-6 text-slate-400">
        通过当前主号发送 Team 邀请、自动登录新账号、完成 Codex OAuth 后入池。整个流程在后台执行，结果会反映到任务历史。
      </p>

      <div v-if="message" class="mb-4 rounded-2xl px-4 py-3 text-sm border" :class="messageClass">
        {{ message }}
      </div>

      <div v-if="runningTask" class="mb-4 rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
        当前已有任务在执行，邀请加号会在前序任务完成后排队执行。
      </div>

      <div class="flex justify-end gap-3 pt-2">
        <button
          type="button"
          @click="$emit('cancel')"
          :disabled="submitting"
          class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-sm text-gray-200 rounded-lg border border-gray-700 transition disabled:opacity-50"
        >
          取消
        </button>
        <button
          type="button"
          @click="confirm"
          :disabled="submitting"
          class="px-4 py-2 bg-orange-600 hover:bg-orange-500 text-white text-sm rounded-lg transition disabled:opacity-50"
        >
          {{ submitting ? '提交中...' : '开始邀请加号' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * InviteFlowModal.vue
 *
 * 邀请加号顶级入口的 modal 壳。点击"开始邀请加号"调
 * api.startAddViaInvite()，后端会在后台执行"邀请 → 登录 → Codex OAuth → 入池"。
 * 提交成功后 emit done，关闭 modal；失败展示错误。
 *
 * 注意：后端的 /api/tasks/add-via-invite 路由可能尚未在 main 分支 ready
 * （视部署版本而定）；如果接口不存在会返回 404，错误会展示在 modal 内。
 */
import { ref } from 'vue'
import { api } from '../api.js'

defineProps({
  runningTask: { type: Object, default: null },
})
const emit = defineEmits(['done', 'cancel'])

const submitting = ref(false)
const message = ref('')
const messageClass = ref('')

function setMessage(text, type = 'error') {
  message.value = text
  messageClass.value = type === 'success'
    ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20'
    : 'bg-red-500/10 text-red-300 border-red-500/20'
}

async function confirm() {
  submitting.value = true
  message.value = ''
  try {
    const result = await api.startAddViaInvite()
    setMessage(`任务已提交：${result?.task_id || ''}`, 'success')
    // 给用户看一秒成功提示再关闭，避免闪烁。
    setTimeout(() => emit('done', result), 600)
  } catch (e) {
    setMessage(e.message || '邀请加号请求失败', 'error')
  } finally {
    submitting.value = false
  }
}
</script>
