import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  BarChart3,
  CandlestickChart,
  ChevronLeft,
  CircleGauge,
  FileClock,
  Gauge,
  LayoutDashboard,
  Menu,
  Network,
  ScrollText,
  Settings,
  ShieldCheck,
  Signal,
  TestTube2,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api, loadPreferences } from '../api/client'
import { ConnectionIndicator, StatusBadge } from './ui'

const navigation = [
  { path: '/', label: 'Overview', icon: LayoutDashboard },
  { path: '/market', label: 'Market', icon: CandlestickChart },
  { path: '/analysis', label: 'Analysis', icon: Activity },
  { path: '/signals', label: 'Signals', icon: Signal },
  { path: '/risk', label: 'Risk Management', icon: ShieldCheck },
  { path: '/trade-plans', label: 'Trade Plans', icon: FileClock },
  { path: '/demo', label: 'Demo Trading', icon: Gauge },
  { path: '/paper', label: 'Paper Trading', icon: TestTube2 },
  { path: '/backtesting', label: 'Backtesting', icon: BarChart3 },
  { path: '/mt5', label: 'MT5 Connection', icon: Network },
  { path: '/logs', label: 'Logs', icon: ScrollText },
  { path: '/settings', label: 'Settings', icon: Settings },
]

export function DashboardLayout() {
  const [open, setOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const location = useLocation()
  const title = navigation.find((item) => item.path === location.pathname)?.label || 'Dashboard'
  const refreshInterval = loadPreferences().refreshInterval
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: refreshInterval })
  const mt5 = useQuery({ queryKey: ['mt5-status'], queryFn: api.mt5Status, refetchInterval: refreshInterval })

  return (
    <div className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''}`}>
      {open && <button className="sidebar-scrim" aria-label="Close menu" onClick={() => setOpen(false)} />}
      <aside className={`sidebar ${open ? 'sidebar-open' : ''}`}>
        <div className="brand"><CircleGauge aria-hidden /><div><strong>Aurum</strong><span>Control Center</span></div><button aria-label="Close navigation" className="icon-button mobile-only" onClick={() => setOpen(false)}><X /></button></div>
        <div className="safety-label">DEMO ONLY · REAL ACCOUNTS BLOCKED</div>
        <nav aria-label="Main navigation">{navigation.map(({ path, label, icon: Icon }) => (
          <NavLink key={path} to={path} end={path === '/'} onClick={() => setOpen(false)} className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
            <Icon size={19} aria-hidden /><span>{label}</span>
          </NavLink>
        ))}</nav>
        <button className="collapse-button" onClick={() => setCollapsed((value) => !value)}><ChevronLeft className={collapsed ? 'rotate-180' : ''} /><span>Collapse</span></button>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <div className="topbar-title"><button aria-label="Open navigation" className="icon-button mobile-only" onClick={() => setOpen(true)}><Menu /></button><div><p>Trading operations</p><h1>{title}</h1></div></div>
          <div className="topbar-status">
            <ConnectionIndicator status={health.data ? 'connected' : health.isLoading ? 'connecting' : 'error'} />
            <StatusBadge value={mt5.data?.connected ? 'MT5 connected' : 'MT5 disconnected'} />
          </div>
        </header>
        <main className="content" id="main-content"><Outlet /></main>
      </div>
    </div>
  )
}
