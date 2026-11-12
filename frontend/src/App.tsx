import { lazy, Suspense } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router'
import { Shell } from './layout/Shell'
import Automacao from './pages/Automacao'
import Controlo from './pages/Controlo'
import Definicoes from './pages/Definicoes'
import Login from './pages/Login'
import Painel from './pages/Painel'

const Cargas = lazy(() => import('./pages/Cargas'))
const Consumo = lazy(() => import('./pages/Consumo'))
const Viagens = lazy(() => import('./pages/Viagens'))

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Suspense fallback={<p className="p-6">A carregar…</p>}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<Shell />}>
            <Route index element={<Painel />} />
            <Route path="automacao" element={<Automacao />} />
            <Route path="cargas" element={<Cargas />} />
            <Route path="viagens" element={<Viagens />} />
            <Route path="consumo" element={<Consumo />} />
            <Route path="controlo" element={<Controlo />} />
            <Route path="definicoes" element={<Definicoes />} />
          </Route>
        </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
