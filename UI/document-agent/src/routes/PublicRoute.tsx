import { Navigate } from 'react-router'

interface PublicRouteProps {
  isLoggedIn: boolean
  children: React.ReactNode
}

export const PublicRoute = ({ isLoggedIn, children }: PublicRouteProps) => {
  if (isLoggedIn) {
    return <Navigate to="/chat" replace />
  }
  return <>{children}</>
}
