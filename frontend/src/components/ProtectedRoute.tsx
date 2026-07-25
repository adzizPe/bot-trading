import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { LoadingSkeleton } from './ui'

export function ProtectedRoute() {
  const { user, loading, failureStatus } = useAuth()
  const location = useLocation()
  if (loading) return <main className="auth-page"><div className="auth-card"><LoadingSkeleton rows={3} /></div></main>
  if (failureStatus === 403) return <Navigate to="/forbidden" replace />
  if (!user) return <Navigate to={failureStatus === 401 ? '/unauthorized' : '/login'} replace state={{ from: `${location.pathname}${location.search}` }} />
  return <Outlet />
}
