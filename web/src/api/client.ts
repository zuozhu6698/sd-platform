import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'

export interface ErrorEnvelope {
  error: {
    code: string
    message: string
    details: Record<string, unknown>
  }
  request_id: string
}

export class ApiError extends Error {
  readonly code: string
  readonly requestId: string
  readonly status: number
  readonly details: Record<string, unknown>

  constructor(
    message: string,
    code: string,
    requestId: string,
    status: number,
    details: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.requestId = requestId
    this.status = status
    this.details = details
  }
}

let csrfToken: string | null = null

export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 10_000,
  withCredentials: true,
  headers: {
    Accept: 'application/json',
  },
})

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  config.headers.set('X-Request-Id', `web_${crypto.randomUUID()}`)
  // Axios applies the default method before request interceptors run.
  const method = config.method!.toUpperCase()
  if (csrfToken && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    config.headers.set('X-CSRF-Token', csrfToken)
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ErrorEnvelope>) => {
    const response = error.response
    const body = response?.data
    if (response && body?.error && body.request_id) {
      return Promise.reject(
        new ApiError(
          body.error.message,
          body.error.code,
          body.request_id,
          response.status,
          body.error.details,
        ),
      )
    }
    return Promise.reject(
      new ApiError('网络连接失败，请检查连接后重试', 'NETWORK_ERROR', 'unavailable', 0, {}),
    )
  },
)
