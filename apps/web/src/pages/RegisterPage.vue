<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const router = useRouter();

const form = reactive({
  email: "",
  username: "",
  password: "",
});

const submitting = ref(false);

async function handleSubmit() {
  submitting.value = true;
  auth.clearError();
  try {
    await auth.register(form);
    router.push("/login");
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
      <label for="username">用户名</label>
      <input id="username" v-model="form.username" type="text" required placeholder="请输入用户名" />
    </div>
    <div class="form-item">
      <label for="password">密码</label>
      <input id="password" v-model="form.password" type="password" required minlength="6" placeholder="请输入密码（至少6位）" />
    </div>
    <div v-if="auth.error" class="form-error">{{ auth.error }}</div>
    <button class="btn-submit" :disabled="submitting">
      {{ submitting ? "注册中..." : "注册" }}
    </button>
    <div class="form-footer">
      已有账号？
      <router-link to="/login">立即登录</router-link>
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
