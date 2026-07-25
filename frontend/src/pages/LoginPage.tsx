import { LogIn } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { sanitizeMessage } from '../api/client'
import { useAuth } from '../auth/AuthProvider'

export function LoginPage() {
  const { user, login, clearFailure } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const destination = (location.state as { from?: string } | null)?.from || '/'

  if (user) return <Navigate to={destination} replace />

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await login(username, password)
      clearFailure()
      navigate(destination, { replace: true })
    } catch (reason) {
      setError(sanitizeMessage(reason))
    } finally {
      setBusy(false)
    }
  }

  return <main className="auth-page"><section className="auth-card" aria-labelledby="login-title">
    <div className="brand auth-brand"><strong>Aurum</strong><span>Control Center</span></div>
    <div><p className="eyebrow">Secure access</p><h1 id="login-title">Sign in</h1><p>Use your assigned dashboard account. Session credentials remain in secure cookies.</p></div>
    <form className="form-grid" onSubmit={submit}>
      <label className="field">Username<input autoFocus autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
      <label className="field">Password<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
      {error && <div className="form-error" role="alert">{error}</div>}
      <button className="button button-primary" disabled={busy || !username || !password}><LogIn size={16} />{busy ? 'Signing in…' : 'Sign in'}</button>
    </form>
  </section></main>
}
