import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import ViolationRecheck from '@/views/ViolationRecheck.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/violation_recheck', name: 'violation-recheck', component: ViolationRecheck },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
