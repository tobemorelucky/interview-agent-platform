import axios from "axios";
import type { ApiEnvelope } from "../types/auth";

const client = axios.create({
  baseURL: "/api/v1",
  timeout: 30000,
  headers: { "Content-Type": "application/json" },
});

client.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

client.interceptors.response.use(
  (response) => {
    const envelope = response.data as ApiEnvelope;
    if (envelope.code === "OK") {
      return envelope.data as unknown as typeof response;
    }
    throw new ApiError(envelope.code, envelope.message);
  },
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("access_token");
    }
    const data = error.response?.data;
    if (data?.code) {
      throw new ApiError(data.code, data.message);
    }
    throw new ApiError("NETWORK_ERROR", error.message || "网络异常");
  }
);

export class ApiError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
    this.name = "ApiError";
  }
}

export default client;
