# Vue 死组件扫描报告

**扫描日期**: 2026-04-28  
**扫描范围**: `/web/src/` 下所有 .vue 组件和关键 .js 文件  
**总组件数**: 14 个

---

## ✅ 已被使用的组件

| 组件 | 引用位置 | 说明 |
|------|---------|------|
| **ConfigPage** | App.vue (import + 路由) | 配置面板主页 |
| **Dashboard** | App.vue (import + 路由) | 仪表盘主页 |
| **LogViewer** | App.vue (import + 路由) | 日志查看器 |
| **PoolPage** | App.vue (import + 路由) | 账号池操作页 |
| **SetupPage** | App.vue (import + 条件加载) | 初始化配置页 |
| **Sidebar** | App.vue (import + 主框架) | 侧边栏导航 |
| **SyncPage** | App.vue (import + 路由) | 同步中心页 |
| **TaskHistoryPage** | App.vue (import + 路由) | 任务历史主页 |
| **TaskPanel** | PoolPage.vue, SyncPage.vue, TasksPage.vue (各1) | 任务执行面板 |
| **TaskHistory** | TaskHistoryPage.vue, TasksPage.vue (各1) | 任务历史表格 |
| **TeamMembers** | App.vue (import + 路由) | Team 成员页 |
| **ThemeToggle** | App.vue (import, 条件加载) | 主题切换按钮 |
| **Settings** | ConfigPage.vue (import, 2处使用) | 配置子组件 |

---

## ⚠️ 组件命名冗余分析

### TaskHistory.vue vs TaskHistoryPage.vue
- **TaskHistoryPage.vue** ✓ 被使用：App.vue 路由 'tasks' → TaskHistoryPage
- **TaskHistory.vue** ✓ 被使用：TaskHistoryPage.vue 内包装、TasksPage.vue 内引用
- **结论**: 两个组件职责不同，非冗余。Page 是路由入口，History 是数据表格。

### TaskPanel.vue vs TasksPage.vue
- **TaskPanel.vue** ✓ 被使用：PoolPage、SyncPage、TasksPage 各引用
- **TasksPage.vue** ✗ **完全未引用** (见下文)
- **结论**: TaskPanel 是共享组件，TasksPage 是冗余页面容器。

---

## ❌ 完全未引用（死组件候选）

### **TasksPage.vue** ⚠️ 死组件

**功能**: 将 TaskPanel 和 TaskHistory 组合到一个页面  
**为何死亡**: 
- App.vue 路由中，`currentPage === 'tasks'` 直接跳转到 **TaskHistoryPage**，不经过 TasksPage
- TasksPage.vue 未被任何 .vue 或 .js 文件导入
- Sidebar.vue 路由导航中，'tasks' 键直接对应任务历史，不对应 TasksPage

**修复建议**: 删除此文件，App.vue 路由逻辑已正确指向 TaskHistoryPage

---

## 依赖文件使用状态

✓ **api.js** - 被 8 个组件导入 (App, ConfigPage, Dashboard, LogViewer, Settings, SetupPage, TaskPanel, TeamMembers)  
✓ **theme.js** - 被 2 处导入 (App.vue initTheme, ThemeToggle.vue useTheme)  
✓ **style.css** - 被 main.js 导入  

---

## 最终结论

- **死组件**: 1 个 (TasksPage.vue)
- **重复冗余**: 0 个 (TaskHistory/TaskHistoryPage 职责分离正常)
- **健康度**: 93% (13/14 组件活跃)

