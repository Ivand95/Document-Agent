import './App.css'
import { RouterProvider, createBrowserRouter } from 'react-router'
import { Chat } from './chat/chat'
import { Login } from './login/login'
import { ProtectedRoute } from './routes/ProtectedRoute'
import { PublicRoute } from './routes/PublicRoute'
import { useEffect, useState } from 'react'
import { encrypt, decrypt } from './utils/crypto'

const STORAGE_KEY = 'document-agent-user'

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
    return { access_token: '', name: '', email: '', department: '' }
  })

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
