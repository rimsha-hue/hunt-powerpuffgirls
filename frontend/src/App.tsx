import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Navigate, Outlet, RouterProvider, createBrowserRouter, useLocation } from 'react-router'
import { AppLayout } from './components/Layout'
import { ToastProvider } from './components/Toast'
import { ErrorNote, Skeleton } from './components/ui'
import { useMe } from './lib/session'
import Landing from './pages/Landing'

// The landing page loads eagerly; app screens are split out so first paint stays light.
const page = (load: () => Promise<{ default: () => ReactNode }>) => async () => ({
  Component: (await load()).default,
})

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

function RequireAuth(): ReactNode {
  const { data: me, isPending, error } = useMe()
  const location = useLocation()
  if (isPending) {
    return (
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-6">
        <Skeleton className="h-28" />
        <Skeleton className="h-64" />
      </div>
    )
  }
  if (error) {
    return (
      <div className="mx-auto max-w-xl px-4 py-10">
        <ErrorNote>{error.message}</ErrorNote>
      </div>
    )
  }
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  // A session opened by a digest Apply link covers that one job only.
  const scoped = me.scope_job_id !== null ? `/jobs/${me.scope_job_id}` : null
  const allowed =
    scoped && [scoped, `${scoped}/workspace`, `${scoped}/pack`].includes(location.pathname)
  if (scoped && !allowed) return <Navigate to={scoped} replace />
  return <Outlet />
}

const router = createBrowserRouter([
  { path: '/', element: <Landing /> },
  { path: '/login', lazy: page(() => import('./pages/Login')) },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { path: '/onboarding', lazy: page(() => import('./pages/Onboarding')) },
          { path: '/ledger', lazy: page(() => import('./pages/Ledger')) },
          { path: '/jobs', lazy: page(() => import('./pages/Jobs')) },
          { path: '/jobs/:id', lazy: page(() => import('./pages/JobDetail')) },
          { path: '/jobs/:id/workspace', lazy: page(() => import('./pages/Workspace')) },
          { path: '/jobs/:id/pack', lazy: page(() => import('./pages/ApplyPack')) },
        ],
      },
    ],
  },
  { path: '*', element: <Navigate to="/" replace /> },
])

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>
  )
}
