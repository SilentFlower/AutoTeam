<template>
  <button
    type="button"
    @click="$emit('click')"
    :disabled="disabled"
    class="flex items-center gap-2 rounded-2xl border px-4 py-2.5 text-sm font-medium shadow-md transition disabled:cursor-not-allowed disabled:opacity-50"
    :class="disabled
      ? 'border-orange-500/20 bg-orange-500/10 text-orange-200/70'
      : 'border-orange-400/30 bg-gradient-to-r from-orange-500 to-amber-500 text-white hover:from-orange-400 hover:to-amber-400'"
  >
    <span class="text-base leading-none">➕</span>
    邀请加号
  </button>
</template>

<script setup>
/**
 * InviteActionButton.vue
 *
 * 工作台顶部"邀请加号"主按钮（决策 #8：提升为顶级操作）。
 * 仅承担按钮渲染与 click 事件透传，实际 modal 由父组件控制。
 *
 * 视觉上用橙色渐变区别于其他次级按钮（蓝色/灰色），强调"主操作"属性。
 */
defineProps({
  /**
   * 禁用按钮（通常在已有任务执行中时禁用，避免重复触发）。
   */
  disabled: {
    type: Boolean,
    default: false,
  },
})
defineEmits(['click'])
</script>
