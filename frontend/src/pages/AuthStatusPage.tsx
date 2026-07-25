import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'

export function UnauthorizedPage() {
  const { clearFailure } = useAuth()
  return <main className="auth-page"><section className="auth-card">
    <p className="eyebrow">401</p><h1>Unauthorized</h1>
    <p>Your session is missing or expired. Sign in again to continue.</p>
    <Link className="button button-primary" to="/login" onClick={clearFailure}>Go to sign in</Link>
  </section></main>
}

export function ForbiddenPage() {
  const { clearFailure } = useAuth()
  return <main className="auth-page"><section className="auth-card">
    <p className="eyebrow">403</p><h1>Forbidden</h1>
    <p>Your account does not have permission for that operation. The backend remains authoritative.</p>
    <Link className="button button-ghost" to="/" onClick={clearFailure}>Return to dashboard</Link>
  </section></main>
}
