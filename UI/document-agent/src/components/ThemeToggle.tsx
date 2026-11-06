import DarkMode from '@mui/icons-material/DarkMode'
import LightMode from '@mui/icons-material/LightMode'
import { IconButton } from '@mui/material'
import { useEffect, useState } from 'react'
import { chooseTheme, currentTheme, watchSystemTheme } from '../utils/theme'

export const ThemeToggle = ({ className }: { className?: string }) => {
  const [theme, setTheme] = useState(currentTheme)

  useEffect(() => watchSystemTheme(setTheme), [])

  const next = theme === 'dark' ? 'light' : 'dark'

  return (
    <IconButton
      size="small"
      className={className}
      aria-label={next === 'light' ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro'}
      onClick={() => {
        chooseTheme(next)
        setTheme(next)
      }}
      sx={{ color: 'var(--app-text)' }}
    >
      {theme === 'dark' ? <LightMode /> : <DarkMode />}
    </IconButton>
  )
}
