/* eslint-disable react-refresh/only-export-components */
import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, subscribeAuthFailure, type AuthUser } from '../api/client'

interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  failureStatus: 401 | 403 | null
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  hasPermission: (permission: string) => boolean
  clearFailure: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children, initialUser }: { children: ReactNode; initialUser?: AuthUser | null }) {
  const queryClient = useQueryClient()
  const [user, setUser] = useState<AuthUser | null>(initialUser ?? null)
  const [loading, setLoading] = useState(initialUser === undefined)
  const [failureStatus, setFailureStatus] = useState<401 | 403 | null>(null)

  const loadCurrentUser = useCallback(async () => {
    try {
      setUser(await api.me())
      setFailureStatus(null)
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (initialUser === undefined) queueMicrotask(() => void loadCurrentUser())
  }, [initialUser, loadCurrentUser])

  useEffect(() => subscribeAuthFailure((status) => {
    setFailureStatus(status)
    if (status === 401) {
      setUser(null)
      queryClient.clear()
    }
  }), [queryClient])

  useEffect(() => {
    if (!user?.access_expires_at) return
    const expiresAt = Date.parse(user.access_expires_at)
    if (!Number.isFinite(expiresAt)) return
    const delay = Math.max(0, expiresAt - Date.now() - 60_000)
    const timer = window.setTimeout(async () => {
      try {
        setUser(await api.refreshAuth())
      } catch {
        setUser(null)
        setFailureStatus(401)
        queryClient.clear()
      }
    }, Math.min(delay, 2_147_483_647))
    return () => window.clearTimeout(timer)
  }, [queryClient, user?.access_expires_at])

  const login = useCallback(async (username: string, password: string) => {
    const current = await api.login(username, password)
    queryClient.clear()
    setUser(current)
    setFailureStatus(null)
  }, [queryClient])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      setUser(null)
      setFailureStatus(null)
      queryClient.clear()
    }
  }, [queryClient])

  const value = useMemo<AuthContextValue>(() => ({
    user,
    loading,
    failureStatus,
    login,
    logout,
    hasPermission: (permission) => user?.permissions.includes(permission) === true,
    clearFailure: () => setFailureStatus(null),
  }), [failureStatus, loading, login, logout, user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}

export function usePermission(permission: string): boolean {
  return useAuth().hasPermission(permission)
}
