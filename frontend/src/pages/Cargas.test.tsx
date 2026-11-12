import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Cargas from './Cargas'

vi.mock('../components/ChargeCurve', () => ({ ChargeCurve: ({ samples }: { samples: unknown[] }) => <div>curva {samples.length}</div> }))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const now = new Date().toISOString()
const base = { ended_at: now, soc_start: 31, soc_end: 80, price_override_eur_kwh: null, partial: false }
const charges = [
  { ...base, id: 1, started_at: now, place_kind: 'home', place_name: 'Casa', kwh: 23.4, cost_eur: 3.51 },
  { ...base, id: 2, started_at: now, place_kind: 'other', place_name: 'Lagoa', kwh: 22.8, cost_eur: null },
  { ...base, id: 3, started_at: now, place_kind: 'work', place_name: 'Trabalho', kwh: 15.2, cost_eur: 0, partial: true },
]
const summary = { kwh: 61.4, cost_eur: 3.51, work_pct: 25, home_pct: 38, avg_home_eur_kwh: 0.15 }

function setup() {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const body = url.startsWith('/api/charges/summary')
      ? summary
      : url.includes('/samples')
        ? [{ at: now, soc: 31, rate_kmh: 20 }]
        : init?.method === 'PUT'
          ? {}
          : charges
    return new Response(JSON.stringify(body), { status: 200 })
  })
  vi.stubGlobal('fetch', f)
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Cargas />
    </QueryClientProvider>,
  )
  return f
}

describe('Cargas', () => {
  it('renders rows, EUR formatting, definir preço and ≈ for partial', async () => {
    setup()
    expect(await screen.findByText('3,51 €')).toBeTruthy()
    expect(screen.getByText('definir preço')).toBeTruthy()
    expect(screen.getByText('≈ 0,00 €')).toBeTruthy()
    expect(screen.getByText('≈ 15,2')).toBeTruthy()
    expect(screen.getByText('Há cargas sem preço ou incompletas')).toBeTruthy()
  })

  it('shows the curve on row click and saves a price', async () => {
    const f = setup()
    fireEvent.click((await screen.findByText('Lagoa')).closest('tr')!)
    expect(await screen.findByText('curva 1')).toBeTruthy()
    fireEvent.click(screen.getByText('definir preço'))
    fireEvent.change(screen.getByLabelText('Preço (€/kWh)'), { target: { value: '0,25' } })
    fireEvent.click(screen.getByText('Guardar'))
    await waitFor(() => {
      const put = f.mock.calls.find((c) => c[1]?.method === 'PUT')
      expect(put?.[0]).toBe('/api/charges/2/price')
      expect(JSON.parse(put?.[1]?.body as string)).toEqual({ eur_kwh: 0.25 })
    })
  })
})
