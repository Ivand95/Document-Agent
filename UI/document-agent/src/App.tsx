import './App.css'
import { BrowserRouter, Route, Routes } from 'react-router'
import { ProtectedRoute } from './routes/ProtectedRoute'
import { PublicRoute } from './routes/PublicRoute'
import { lazy, Suspense, useEffect, useState } from 'react'
import { encrypt, decrypt } from './utils/crypto'
import { isAxiosError } from 'axios'
import { refreshSession } from './utils/axios'
import { tokenExpiry } from './utils/token'

// Each screen is its own chunk: login doesn't download the chat (markdown renderer, icons) and vice versa
const Login = lazy(() => import('./login/login').then((m) => ({ default: m.Login })))
const Chat = lazy(() => import('./chat/chat').then((m) => ({ default: m.Chat })))

const Admin = lazy(() => import('./admin/admin').then((m) => ({ default: m.Admin })))

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

  // Declarative routes keep the screens mounted when userInfo changes (e.g. a token renewal)
  return (
    <BrowserRouter>
      <Suspense fallback={null}>
        <Routes>
          <Route
            path="/"
            element={
              <PublicRoute isLoggedIn={!!userInfo.access_token}>
                <Login userInfo={userInfo} setUserInfo={setUserInfo} />
              </PublicRoute>
            }
          />
          <Route
            path="/chat"
            element={
              <ProtectedRoute isLoggedIn={!!userInfo.access_token}>
                <Chat userInfo={userInfo} setUserInfo={setUserInfo} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin"
            element={
              <ProtectedRoute isLoggedIn={!!userInfo.access_token}>
                <Admin userInfo={userInfo} />
              </ProtectedRoute>
            }
          />
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}

export default App
