import { Navigate } from 'react-router'

interface ProtectedRouteProps {
  isLoggedIn: boolean
  children: React.ReactNode
}

export const ProtectedRoute = ({ isLoggedIn, children }: ProtectedRouteProps) => {
  if (!isLoggedIn) {
    return <Navigate to="/" replace />
  }
  return <>{children}</>
}
