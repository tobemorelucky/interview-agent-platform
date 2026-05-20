<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const router = useRouter();

const form = reactive({
  email: "",
  password: "",
});

const submitting = ref(false);

async function handleSubmit() {
  submitting.value = true;
  auth.clearError();
  try {
    await auth.login(form);
    router.push("/dashboard");
  } catch {
    // error is set in store
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <form class="auth-form" @submit.prevent="handleSubmit">
    <div class="form-item">
      <label for="email">邮箱</label>
      <input id="email" v-model="form.email" type="email" required placeholder="请输入邮箱" />
    </div>
    <div class="form-item">
      <label for="password">密码</label>
      <input id="password" v-model="form.password" type="password" required minlength="6" placeholder="请输入密码" />
    </div>
    <div v-if="auth.error" class="form-error">{{ auth.error }}</div>
    <button class="btn-submit" :disabled="submitting">
      {{ submitting ? "登录中..." : "登录" }}
    </button>
    <div class="form-footer">
      还没有账号？
      <router-link to="/register">立即注册</router-link>
    </div>
  </form>
</template>

<style scoped>
.auth-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.form-item label {
  display: block;
  margin-bottom: 6px;
  font-size: 14px;
  color: #333;
}

.form-item input {
  width: 100%;
  height: 40px;
  padding: 0 12px;
  font-size: 14px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  outline: none;
  transition: border-color 0.2s;
}

.form-item input:focus {
  border-color: #409eff;
}

.form-error {
  color: #f56c6c;
  font-size: 13px;
}

.btn-submit {
  height: 42px;
  font-size: 15px;
  color: #fff;
  background: #409eff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}

.btn-submit:hover {
  background: #337ecc;
}

.btn-submit:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.form-footer {
  text-align: center;
  font-size: 14px;
  color: #999;
}

.form-footer a {
  color: #409eff;
}
</style>
