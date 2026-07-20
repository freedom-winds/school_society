import { create } from 'zustand'
import type { User } from '../types'

type AuthState = { user: User | null; accessToken: string | null; hydrate: () => void; setSession: (token: string | null, user: User | null) => void; clear: () => void }

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  hydrate: () => { const raw = sessionStorage.getItem('club-center-session'); if (raw) set(JSON.parse(raw)) },
  setSession: (accessToken, user) => { const next = { accessToken, user }; sessionStorage.setItem('club-center-session', JSON.stringify(next)); set(next) },
  clear: () => { sessionStorage.removeItem('club-center-session'); set({ accessToken: null, user: null }) },
}))
