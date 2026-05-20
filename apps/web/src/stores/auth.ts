import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { login as apiLogin, register as apiRegister, getMe } from "../api/auth";
import type { LoginRequest, RegisterRequest, UserInfo } from "../types/auth";
import { ApiError } from "../api/client";

export const useAuthStore = defineStore("auth", () => {
  const token = ref<string | null>(localStorage.getItem("access_token"));
  const currentUser = ref<UserInfo | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  const isAuthenticated = computed(() => !!token.value);
  const isAdmin = computed(() => currentUser.value?.role === "ADMIN");

  async function login(req: LoginRequest) {
    loading.value = true;
    error.value = null;
    try {
      const result = await apiLogin(req);
      token.value = result.access_token;
      localStorage.setItem("access_token", result.access_token);
      await fetchMe();
    } catch (e) {
      if (e instanceof ApiError) {
        error.value = e.message;
      } else {
        error.value = "登录失败，请重试";
      }
      throw e;
    } finally {
      loading.value = false;
    }
  }

  async function register(req: RegisterRequest) {
    loading.value = true;
    error.value = null;
    try {
      await apiRegister(req);
    } catch (e) {
      if (e instanceof ApiError) {
        error.value = e.message;
      } else {
        error.value = "注册失败，请重试";
      }
      throw e;
    } finally {
      loading.value = false;
    }
  }

  async function fetchMe() {
    currentUser.value = await getMe();
  }

  function logout() {
    token.value = null;
    currentUser.value = null;
    localStorage.removeItem("access_token");
  }

  function clearError() {
    error.value = null;
  }

  return {
    token,
    currentUser,
    loading,
    error,
    isAuthenticated,
    isAdmin,
    login,
    register,
    fetchMe,
    logout,
    clearError,
  };
});
