import { useAuthStore } from '../stores/auth'

const API = '/api/v1'

export class ApiError extends Error { constructor(public code: string, message: string, public fields: Record<string, string> = {}) { super(message) } }

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = useAuthStore.getState().accessToken
  const headers = new Headers(options.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API}${path}`, { ...options, headers, credentials: 'include' })
  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload?.success) {
    if (response.status === 401 && token) useAuthStore.getState().clear()
    throw new ApiError(payload?.error?.code || 'NETWORK_ERROR', payload?.error?.message || '请求失败', payload?.error?.fields || {})
  }
  return payload.data as T
}

export const get = <T,>(path: string) => api<T>(path)
export const post = <T,>(path: string, data?: unknown) => api<T>(path, { method: 'POST', body: data instanceof FormData ? data : JSON.stringify(data ?? {}) })
export const patch = <T,>(path: string, data: unknown) => api<T>(path, { method: 'PATCH', body: JSON.stringify(data) })
export const put = <T,>(path: string, data: unknown) => api<T>(path, { method: 'PUT', body: JSON.stringify(data) })
export const del = <T,>(path: string) => api<T>(path, { method: 'DELETE' })
