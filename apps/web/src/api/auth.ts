import client from "./client";
import type { LoginRequest, LoginResponse, RegisterRequest, UserInfo } from "../types/auth";

export async function register(req: RegisterRequest): Promise<{ user_id: number }> {
  return client.post("/auth/register", req);
}

export async function login(req: LoginRequest): Promise<LoginResponse> {
  return client.post("/auth/login", req);
}

export async function getMe(): Promise<UserInfo> {
  return client.get("/auth/me");
}
