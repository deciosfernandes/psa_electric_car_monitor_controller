import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router'
import { Shell } from './layout/Shell'
import ComingSoon from './pages/ComingSoon'
import Automacao from './pages/Automacao'
import Login from './pages/Login'
import Painel from './pages/Painel'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })

const WIP = 'Em construção'

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<Shell />}>
            <Route index element={<Painel />} />
            <Route path="automacao" element={<Automacao />} />
            <Route path="cargas" element={<ComingSoon title="Cargas" />} />
            <Route path="viagens" element={<ComingSoon title="Viagens" />} />
            <Route path="consumo" element={<ComingSoon title="Consumo" />} />
            <Route path="controlo" element={<ComingSoon title="Controlo" message={WIP} />} />
            <Route path="definicoes" element={<ComingSoon title="Definições" message={WIP} />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
