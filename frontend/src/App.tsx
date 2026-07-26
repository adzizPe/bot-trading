import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthProvider'
import { DashboardLayout } from './components/layout'
import { ProtectedRoute } from './components/ProtectedRoute'
import { EmptyState, LoadingSkeleton, ToastProvider } from './components/ui'
import { ForbiddenPage, UnauthorizedPage } from './pages/AuthStatusPage'
import { LoginPage } from './pages/LoginPage'

const AnalysisPage = lazy(() => import('./pages/AnalysisPage').then((module) => ({ default: module.AnalysisPage })))
const BacktestingPage = lazy(() => import('./pages/BacktestingPage').then((module) => ({ default: module.BacktestingPage })))
const DemoTradingPage = lazy(() => import('./pages/DemoTradingPage').then((module) => ({ default: module.DemoTradingPage })))
const LogsPage = lazy(() => import('./pages/LogsPage').then((module) => ({ default: module.LogsPage })))
const MarketPage = lazy(() => import('./pages/MarketPage').then((module) => ({ default: module.MarketPage })))
const MT5Page = lazy(() => import('./pages/MT5Page').then((module) => ({ default: module.MT5Page })))
const OverviewPage = lazy(() => import('./pages/OverviewPage').then((module) => ({ default: module.OverviewPage })))
const PaperPage = lazy(() => import('./pages/PaperPage').then((module) => ({ default: module.PaperPage })))
const RiskPage = lazy(() => import('./pages/RiskPage').then((module) => ({ default: module.RiskPage })))
const RiskFeasibilityPage = lazy(() => import('./pages/RiskFeasibilityPage').then((module) => ({ default: module.RiskFeasibilityPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })))
const SignalsPage = lazy(() => import('./pages/SignalsPage').then((module) => ({ default: module.SignalsPage })))
const TradePlansPage = lazy(() => import('./pages/TradePlansPage').then((module) => ({ default: module.TradePlansPage })))
const SystemMetricsPage = lazy(() => import('./pages/SystemMetricsPage').then((module) => ({ default: module.SystemMetricsPage })))
const AlertsPage = lazy(() => import('./pages/AlertsPage').then((module) => ({ default: module.AlertsPage })))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 5000, refetchOnWindowFocus: false },
    mutations: { retry: 0 },
  },
})

export function AppRoutes() {
  return <Suspense fallback={<LoadingSkeleton rows={4} />}><Routes>
    <Route path="login" element={<LoginPage />} />
    <Route path="unauthorized" element={<UnauthorizedPage />} />
    <Route path="forbidden" element={<ForbiddenPage />} />
    <Route element={<ProtectedRoute />}>
      <Route element={<DashboardLayout />}>
        <Route index element={<OverviewPage />} />
        <Route path="market" element={<MarketPage />} />
        <Route path="analysis" element={<AnalysisPage />} />
        <Route path="signals" element={<SignalsPage />} />
        <Route path="risk" element={<RiskPage />} />
        <Route path="risk-feasibility" element={<RiskFeasibilityPage />} />
        <Route path="trade-plans" element={<TradePlansPage />} />
        <Route path="demo" element={<DemoTradingPage />} />
        <Route path="paper" element={<PaperPage />} />
        <Route path="backtesting" element={<BacktestingPage />} />
        <Route path="mt5" element={<MT5Page />} />
        <Route path="logs" element={<LogsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="system-metrics" element={<SystemMetricsPage />} />
        <Route path="alerts" element={<AlertsPage />} />
        <Route path="*" element={<EmptyState title="Halaman tidak ditemukan" description="Pilih menu dashboard dari sidebar." />} />
      </Route>
    </Route>
  </Routes></Suspense>
}

export default function App() {
  return <QueryClientProvider client={queryClient}>
    <ToastProvider>
      <BrowserRouter><AuthProvider><AppRoutes /></AuthProvider></BrowserRouter>
    </ToastProvider>
  </QueryClientProvider>
}
