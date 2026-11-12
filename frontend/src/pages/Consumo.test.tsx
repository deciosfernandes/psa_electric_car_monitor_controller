import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Consumo from './Consumo'

vi.mock('recharts', async (orig) => ({
  ...(await orig<typeof import('recharts')>()),
  ResponsiveContainer: () => <div>barras</div>, // jsdom has no layout
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const data = {
  last30: { km: 612, kwh: 92.4, kwh_100km: 15.1 },
  months: [{ month: '2026-10', km: 500, kwh: 75, kwh_100km: 15 }],
}

describe('Consumo', () => {
  it('shows the last-30-days card and the monthly chart', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(data), { status: 200 })))
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Consumo />
      </QueryClientProvider>,
    )
    expect(await screen.findByText('15,1')).toBeTruthy()
    expect(screen.getByText('612 km · 92,4 kWh')).toBeTruthy()
    expect(screen.getByText('barras')).toBeTruthy()
  })
})
