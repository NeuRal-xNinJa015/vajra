import { create } from 'zustand'

export type ThemeChoice = 'system' | 'light' | 'dark'

const KEY = 'vajra-theme'
const systemDark = window.matchMedia('(prefers-color-scheme: dark)')

function saved(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

function isDark(choice: ThemeChoice) {
  return choice === 'dark' || (choice === 'system' && systemDark.matches)
}

// Theme choice: follows the system until the user picks one, then remembers it.
interface ThemeState {
  choice: ThemeChoice
  dark: boolean
  setChoice: (choice: ThemeChoice) => void
}

export const useTheme = create<ThemeState>((set) => ({
  choice: saved(),
  dark: isDark(saved()),
  setChoice: (choice) => {
    try {
      if (choice === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, choice)
    } catch {
      // Storage unavailable: the choice still applies for this session.
    }
    set({ choice, dark: isDark(choice) })
  },
}))

// The `dark` class on <html> drives every colour token.
useTheme.subscribe((state) => document.documentElement.classList.toggle('dark', state.dark))
systemDark.addEventListener('change', () => {
  const { choice } = useTheme.getState()
  if (choice === 'system') useTheme.setState({ dark: systemDark.matches })
})
