import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "../stores/auth";

const routes = [
  {
    path: "/login",
    name: "Login",
    component: () => import("../pages/LoginPage.vue"),
    meta: { layout: "public" },
  },
  {
    path: "/register",
    name: "Register",
    component: () => import("../pages/RegisterPage.vue"),
    meta: { layout: "public" },
  },
  {
    path: "/dashboard",
    name: "Dashboard",
    component: () => import("../pages/DashboardPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/qa",
    name: "Qa",
    component: () => import("../pages/QaPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/admin/kb/documents",
    name: "AdminKbDocuments",
    component: () => import("../pages/admin/AdminKbDocumentsPage.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
  {
    path: "/",
    redirect: "/dashboard",
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to, _from, next) => {
  const publicPaths = ["/login", "/register"];
  const authStore = useAuthStore();

  if (publicPaths.includes(to.path)) {
    if (authStore.isAuthenticated && to.path !== "/") {
      next("/dashboard");
      return;
    }
    next();
    return;
  }

  if (!authStore.isAuthenticated) {
    next("/login");
    return;
  }

  if (!authStore.currentUser) {
    try {
      await authStore.fetchMe();
    } catch {
      authStore.logout();
      next("/login");
      return;
    }
  }

  if (to.path.startsWith("/admin") && !authStore.isAdmin) {
    next("/dashboard");
    return;
  }

  next();
});

export default router;
