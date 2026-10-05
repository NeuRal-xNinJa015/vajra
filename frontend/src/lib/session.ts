import { create } from 'zustand'
import type { UserInfo } from './api'

// The signed-in user. Held in memory only, so closing the app signs the user out;
// nothing here is written to disk.
interface SessionState {
  token: string | null
  user: UserInfo | null
  start: (token: string, user: UserInfo) => void
  end: () => void
}

export const useSession = create<SessionState>((set) => ({
  token: null,
  user: null,
  start: (token, user) => set({ token, user }),
  end: () => set({ token: null, user: null }),
}))

const ROLES = ['viewer', 'forecaster', 'admin']

// Whether the user may take actions (replay control, alert decisions, feedback).
// With sign-in switched off on the backend there is no user and everything is allowed.
export function useCanAct(): boolean {
  const role = useSession((s) => s.user?.role)
  return role === undefined || ROLES.indexOf(role) >= ROLES.indexOf('forecaster')
}
