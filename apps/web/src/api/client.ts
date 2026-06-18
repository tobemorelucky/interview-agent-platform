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
    const requestId = response.headers?.["x-request-id"];
    if (import.meta.env.DEV && requestId) {
      console.debug("[api] X-Request-ID:", requestId);
    }

    const envelope = response.data as ApiEnvelope;
    if (envelope.code === "OK") {
      return envelope.data as unknown as typeof response;
    }
    throw new ApiError(envelope.code, envelope.message, requestId);
  },
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("access_token");
    }

    const data = error.response?.data;
    const requestId = error.response?.headers?.["x-request-id"] || data?.error?.request_id;
    if (import.meta.env.DEV && requestId) {
      console.debug("[api] X-Request-ID:", requestId);
    }

    if (data?.error) {
      throw new ApiError(
        data.error.code || "API_ERROR",
        data.error.message || error.message || "Request failed",
        requestId,
        data.error.details
      );
    }
    if (data?.code) {
      throw new ApiError(data.code, data.message, requestId);
    }
    throw new ApiError("NETWORK_ERROR", error.message || "网络异常", requestId);
  }
);

export class ApiError extends Error {
  code: string;
  requestId?: string;
  details?: unknown;

  constructor(code: string, message: string, requestId?: string, details?: unknown) {
    super(message);
    this.code = code;
    this.requestId = requestId;
    this.details = details;
    this.name = "ApiError";
  }
}

export default client;
