import './App.css'
import { RouterProvider, createBrowserRouter } from 'react-router'
import { Chat } from './chat/chat'
import { Login } from './login/login'
import { ProtectedRoute } from './routes/ProtectedRoute'
import { PublicRoute } from './routes/PublicRoute'
import { useEffect, useState } from 'react'
import { encrypt, decrypt } from './utils/crypto'
import { isAxiosError } from 'axios'
import { refreshSession } from './utils/axios'
import { tokenExpiry } from './utils/token'

const STORAGE_KEY = 'document-agent-user'
const EMPTY_USER: UserInfo = { access_token: '', name: '', email: '', department: '' }
const REFRESH_MARGIN_MS = 5 * 60 * 1000
const RETRY_MS = 30 * 1000

function App() {
  const [userInfo, setUserInfo] = useState<UserInfo>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored) {
        const decrypted = decrypt(stored)
        if (decrypted) {
          const parsed = JSON.parse(decrypted) as UserInfo
          if (parsed.access_token) return parsed
        }
      }
    } catch {
      // ignore
    }
    return EMPTY_USER
  })

  // Tokens are short-lived: renew before they expire, and sign out when the server ends the session
  useEffect(() => {
    const token = userInfo.access_token
    const expiry = token ? tokenExpiry(token) : null
    if (!token || !expiry) return

    let timer: ReturnType<typeof setTimeout>
    const renew = async () => {
      try {
        const { data } = await refreshSession(token)
        setUserInfo((prev) => ({ ...prev, access_token: data.access_token }))
      } catch (error) {
        if (isAxiosError(error) && error.response?.status === 401) {
          setUserInfo(EMPTY_USER)
        } else {
          timer = setTimeout(renew, RETRY_MS) // network hiccup: try again
        }
      }
    }
    timer = setTimeout(renew, Math.max(expiry - Date.now() - REFRESH_MARGIN_MS, 5000))
    return () => clearTimeout(timer)
  }, [userInfo.access_token])

  useEffect(() => {
    if (userInfo.access_token) {
      localStorage.setItem(STORAGE_KEY, encrypt(JSON.stringify(userInfo)))
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  }, [userInfo])

  const router = createBrowserRouter([
    {
      path: '/',
      element: (
        <PublicRoute isLoggedIn={!!userInfo.access_token}>
          <Login userInfo={userInfo} setUserInfo={setUserInfo} />
        </PublicRoute>
      ),
    },
    {
      path: '/chat',
      element: (
        <ProtectedRoute isLoggedIn={!!userInfo.access_token}>
          <Chat userInfo={userInfo} setUserInfo={setUserInfo} />
        </ProtectedRoute>
      ),
    },
  ])

  return <RouterProvider router={router} />
}

export default App
