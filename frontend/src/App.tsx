import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Navigate, Outlet, RouterProvider, createBrowserRouter, useLocation } from 'react-router'
import { AppLayout } from './components/Layout'
import { ToastProvider } from './components/Toast'
import { ErrorNote, Skeleton } from './components/ui'
import { ApiError } from './lib/api'
import { meKey, useMe } from './lib/session'
import Landing from './pages/Landing'

// The landing page loads eagerly; app screens are split out so first paint stays light.
const page = (load: () => Promise<{ default: () => ReactNode }>) => async () => ({
  Component: (await load()).default,
})

const queryClient: QueryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // A 401 won't change on a retry: the session is gone.
      retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 1,
    },
  },
  // Any request answered 401 means the session ended (expired, logged out elsewhere): treat the
  // user as signed out everywhere, so the app goes to the login page instead of showing an error
  // inside a page that still looks signed in.
  queryCache: new QueryCache({
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) queryClient.setQueryData(meKey, null)
    },
  }),
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
  // Reached from email links, signed in or not.
  { path: '/forgot-password', lazy: page(() => import('./pages/ForgotPassword')) },
  { path: '/reset-password', lazy: page(() => import('./pages/ResetPassword')) },
  { path: '/unsubscribe', lazy: page(() => import('./pages/Unsubscribe')) },
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
          { path: '/applications', lazy: page(() => import('./pages/Applications')) },
          { path: '/profile', lazy: page(() => import('./pages/Profile')) },
          { path: '/plans', lazy: page(() => import('./pages/Plans')) },
          { path: '/activity', lazy: page(() => import('./pages/Activity')) },
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
