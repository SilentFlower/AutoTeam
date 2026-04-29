// Admin 状态单例。
// 设计取舍：项目当前没有 pinia 等状态库，沿用 reactive + 全局事件的轻量风格。
// currentAdminId 同时作为 api.js 请求拦截器的 header 数据源；切换后通过自定义事件
// "autoteam:admin-switched" 通知 Workbench 各 tab 刷新各自的数据。

import { reactive, readonly } from 'vue'
import { api, setAdminIdGetter } from '../api.js'

/** @type {{admins: Array, currentAdminId: string|null, loaded: boolean}} */
const state = reactive({
  admins: [],
  currentAdminId: null,
  loaded: false,
})

// 模块加载时即把 getter 注册给 api.js，让请求拦截器能读到最新 currentAdminId。
// 这里用注册器模式而不是直接 import 反向引用，是为了保持 store 是 api 的"上层"，
// 避免 api ↔ store 互相 import 形成循环依赖。
setAdminIdGetter(() => state.currentAdminId)

/**
 * 获取当前 admin_id（供 api.js 请求拦截器读取）。
 * 这里没用 readonly 包装，是因为 api.js 在请求阶段需要同步读最新值，
 * 反复创建 readonly proxy 会损耗。
 * @return {string|null} 当前激活 admin_id；未加载或无 admin 时返回 null
 */
export function getCurrentAdminId() {
  return state.currentAdminId
}

/**
 * 拉取 /api/admins 列表与激活 admin_id，刷新本地状态。
 * 失败时不修改既有状态，由调用方决定是否重试。
 * @return {Promise<void>}
 */
export async function refreshAdmins() {
  const data = await api.listAdmins()
  state.admins = Array.isArray(data?.admins) ? data.admins : []
  state.currentAdminId = data?.active_admin_id || null
  state.loaded = true
}

/**
 * 切换激活 admin。后端 POST /api/admins/active 成功后再更新本地状态，
 * 并向全局派发 "autoteam:admin-switched" 事件让各 tab 重新拉数据。
 * @param {string} adminId 目标 admin_id（8 位小写 hex）
 * @return {Promise<void>}
 */
export async function switchAdmin(adminId) {
  await api.setActiveAdmin(adminId)
  state.currentAdminId = adminId
  window.dispatchEvent(new CustomEvent('autoteam:admin-switched', { detail: { adminId } }))
}

/**
 * 删除指定 admin（后端会同步清理数据目录），删除后刷新列表。
 * 删除唯一 admin 时后端返回 400,由调用方捕获错误展示。
 * 如果删的是当前激活 admin,后端会自动选择另一个 admin 接替激活;
 * 这里在 refresh 完成后比较前后 currentAdminId,变化时派发切换事件
 * 让 Workbench 各 tab 重新拉数据,避免显示已删除 admin 的残留状态。
 * @param {string} adminId 待删除 admin_id
 * @return {Promise<void>}
 */
export async function removeAdmin(adminId) {
  const before = state.currentAdminId
  await api.deleteAdmin(adminId)
  await refreshAdmins()
  if (state.currentAdminId !== before) {
    window.dispatchEvent(
      new CustomEvent('autoteam:admin-switched', { detail: { adminId: state.currentAdminId } })
    )
  }
}

/**
 * Composable 风格的 store 访问入口。state 暴露为 readonly，避免外部
 * 误改本地缓存（必须经由 switchAdmin / refreshAdmins 等方法走后端）。
 * @return {{state: object, refreshAdmins: function, switchAdmin: function, removeAdmin: function}}
 */
export function useAdmins() {
  return {
    state: readonly(state),
    refreshAdmins,
    switchAdmin,
    removeAdmin,
  }
}
