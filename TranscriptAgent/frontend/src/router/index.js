import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView,
    },
    {
      // Django's chatbot page sends anyone not signed in here, as /signin?next=/chat/
      path: '/signin',
      name: 'signin',
      component: () => import('../views/SignInView.vue'),
    },
  ],
  // Start each new page at the top
  scrollBehavior: () => ({ top: 0 }),
})

export default router
