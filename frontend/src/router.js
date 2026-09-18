import { createRouter, createWebHistory } from 'vue-router'


export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/main' },
    { path: '/login', name: 'login', component: () => import('./components/LoginPage.vue') },
    { path: '/main', name: 'workspace', component: () => import('./App.vue') },
    { path: '/admin', name: 'admin-dashboard', component: () => import('./components/AdminApp.vue') },
    { path: '/admin/users', name: 'admin-users', component: () => import('./components/AdminApp.vue') },
    { path: '/admin/database', name: 'admin-database', component: () => import('./components/AdminApp.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/main' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
