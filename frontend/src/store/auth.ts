import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { User } from '@/types'

interface AuthStore {
  user: User | null
  token: string | null
  login: (token: string, user: User) => void
  logout: () => void
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      login: (token, user) => set({ token, user }),
      logout: () => set({ token: null, user: null }),
    }),
    {
      name: 'cliniccare-auth',
      // sessionStorage: scoped to the current tab only.
      // Each tab gets its own independent auth session, so you can be logged
      // in as admin in one tab and doctor in another simultaneously.
      // Also clears automatically when the tab or browser window is closed,
      // so reopening the app always starts at the login page.
      storage: createJSONStorage(() => sessionStorage),
    }
  )
)
