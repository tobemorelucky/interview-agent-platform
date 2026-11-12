import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "../stores/auth";

const routes = [
  { path: '/training', name: 'Training', component: () => import('../pages/TrainingPage.vue'), meta: { requiresAuth: true } },
  { path: "/admin/public-career", name: "AdminPublicCareer", component: () => import("../pages/admin/AdminPublicCareerPage.vue"), meta: { requiresAuth: true, requiresAdmin: true } },
  { path: "/career", name: "PublicCareer", component: () => import("../pages/PublicCareerPage.vue"), meta: { requiresAuth: true } },
  { path: "/:pathMatch(.*)*", name: "NotFound", component: () => import("../pages/NotFoundPage.vue"), meta: { requiresAuth: true } },
  { path: "/jobs/matches/:id?", name: "JobMatches", component: () => import("../pages/JobMatchingPage.vue"), meta: { requiresAuth: true } },
  {
    path: "/profile",
    name: "CandidateProfile",
    component: () => import("../pages/CandidateProfilePage.vue"),
    meta: { requiresAuth: true },
  },
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
    path: "/interview",
    name: "Interview",
    component: () => import("../pages/InterviewChatPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/interview/reports",
    name: "InterviewReports",
    component: () => import("../pages/InterviewReportPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/interview/reports/:id",
    name: "InterviewReport",
    component: () => import("../pages/InterviewReportPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/memory",
    name: "Memory",
    component: () => import("../pages/MemoryPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/jobs/targets",
    name: "JobTargets",
    component: () => import("../pages/JobTargetPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/resumes",
    name: "ResumeList",
    component: () => import("../pages/ResumeListPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/resumes/:id",
    name: "ResumeReport",
    component: () => import("../pages/ResumeReportPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/admin/kb/documents",
    name: "AdminKbDocuments",
    component: () => import("../pages/admin/AdminKbDocumentsPage.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
  {
    path: "/admin/experiences",
    name: "AdminExperiences",
    component: () => import("../pages/AdminExperiencesPage.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
  {
    path: "/admin/experience-review",
    name: "AdminExperienceReview",
    component: () => import("../pages/admin/AdminExperienceReviewPage.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },
  {
    path: "/admin/audit",
    name: "AdminAuditLogs",
    component: () => import("../pages/admin/AdminAuditLogsPage.vue"),
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
