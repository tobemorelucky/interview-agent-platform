export interface RegisterRequest {
  email: string
  username: string
  password: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface UserInfo {
  id: number
  email: string
  username: string | null
  role: "USER" | "ADMIN"
}

export interface LoginResponse {
  access_token: string
  token_type: string
}

export interface ApiEnvelope<T = unknown> {
  code: string
  message: string
  data: T
}
