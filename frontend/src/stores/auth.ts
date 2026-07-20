import { create } from 'zustand'
import type { User } from '../types'

const SESSION_KEY = 'club-center-session'
type Session = { accessToken: string | null; user: User | null }
type AuthState = Session & { hydrated: boolean; hydrate: () => Promise<void>; setSession: (token: string | null, user: User | null) => void; clear: () => void }
let hydrationPromise: Promise<void> | null = null

function readSession(): Session {
  try {
    const raw = localStorage.getItem(SESSION_KEY) || sessionStorage.getItem(SESSION_KEY)
    if (!raw) return { accessToken: null, user: null }
    const session = JSON.parse(raw) as Session
    localStorage.setItem(SESSION_KEY, JSON.stringify(session))
    sessionStorage.removeItem(SESSION_KEY)
    return session
  } catch {
    localStorage.removeItem(SESSION_KEY)
    sessionStorage.removeItem(SESSION_KEY)
    return { accessToken: null, user: null }
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  hydrated: false,
  hydrate: () => {
    if (hydrationPromise) return hydrationPromise
    hydrationPromise = (async () => {
      const cached = readSession()
      try {
        const response = await fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'include' })
        const payload = await response.json().catch(() => null)
        if (response.ok && payload?.success) {
          const next = { accessToken: payload.data.access_token as string, user: payload.data.user as User }
          localStorage.setItem(SESSION_KEY, JSON.stringify(next))
          set({ ...next, hydrated: true })
          return
        }
        if (response.status === 401 || response.status === 403) {
          localStorage.removeItem(SESSION_KEY)
          set({ accessToken: null, user: null, hydrated: true })
          return
        }
      } catch {
        // 网络暂时不可用时保留尚未过期的本地登录态。
      }
      set({ ...cached, hydrated: true })
    })()
    return hydrationPromise
  },
  setSession: (accessToken, user) => { const next = { accessToken, user }; localStorage.setItem(SESSION_KEY, JSON.stringify(next)); set(next) },
  clear: () => { localStorage.removeItem(SESSION_KEY); sessionStorage.removeItem(SESSION_KEY); set({ accessToken: null, user: null }) },
}))
