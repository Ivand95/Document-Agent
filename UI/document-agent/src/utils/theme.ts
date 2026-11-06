export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'document-agent-theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

export const storedTheme = (): Theme | null => {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null
  }
}

export const systemTheme = (): Theme =>
  window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'

export const applyTheme = (theme: Theme) => {
  document.documentElement.dataset.theme = theme
}

export const currentTheme = (): Theme =>
  document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'

/** Saves an explicit choice; without one the theme follows the operating system. */
export const chooseTheme = (theme: Theme) => {
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // storage blocked: the choice only lasts for this session
  }
  applyTheme(theme)
}

export const watchSystemTheme = (onChange: (theme: Theme) => void) => {
  const query = window.matchMedia(DARK_QUERY)
  const handler = () => {
    if (storedTheme()) return
    const theme = systemTheme()
    applyTheme(theme)
    onChange(theme)
  }
  query.addEventListener('change', handler)
  return () => query.removeEventListener('change', handler)
}
