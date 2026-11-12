import { useQuery } from '@tanstack/react-query'
import { Suspense } from 'react'
import { Link, Navigate, Outlet, useLocation } from 'react-router'
import { apiGet, type AuthState } from '../api'
import { useStatus } from '../useStatus'
import { Sidebar } from './Sidebar'

function NoCarNotice() {
  const { data } = useStatus()
  const location = useLocation()
  if (!data || data.psa_connected || data.car || location.pathname === '/definicoes') return null
  return (
    <div role="status" className="mb-4 rounded-[12px] bg-primary-soft px-4 py-3 text-primary-ink">
      A conta PSA ainda não está configurada.{' '}
      <Link to="/definicoes" className="font-semibold underline">
        Configurar em Definições
      </Link>
    </div>
  )
}

function FakeBanner() {
  const { data } = useQuery({ queryKey: ['health'], queryFn: () => apiGet<{ fake: boolean }>('/api/health') })
  if (!data?.fake) return null
  return (
    <div role="status" className="mb-4 rounded-[12px] bg-warn-soft px-4 py-3 font-semibold text-warn-ink">
      Modo de demonstração — os comandos não chegam ao carro
    </div>
  )
}

export function Shell() {
  const auth = useQuery({ queryKey: ['auth'], queryFn: () => apiGet<AuthState>('/api/auth/state') })
  if (auth.isPending) return null
  if (auth.isError || !auth.data.logged_in) return <Navigate to="/login" replace />
  return (
    <div className="flex min-h-full">
      <Sidebar />
      <main className="min-w-0 flex-1 p-6">
        <FakeBanner />
        <NoCarNotice />
        <Suspense fallback={<p>A carregar…</p>}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  )
}
