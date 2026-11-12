import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Viagens from './Viagens'

vi.mock('../components/MiniMap', () => ({
  MiniMap: ({ markers }: { markers: unknown[] }) => <div>marcadores {markers.length}</div>,
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const trip = {
  id: 1, departed_at: '2026-11-12T08:30:00+00:00', arrived_at: '2026-11-12T08:50:00+00:00',
  from_kind: 'home', from_name: 'Casa', to_kind: 'other', to_name: 'Lagoa',
  from_lat: 37.7, from_lon: -25.6, to_lat: 37.8, to_lon: -25.5,
  km: 14.2, soc_start: 80, soc_end: 76, kwh: 2, kwh_100km: 14.3,
}

describe('Viagens', () => {
  it('renders trips as approximate and shows both markers on selection', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify([trip]), { status: 200 })))
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Viagens />
      </QueryClientProvider>,
    )
    expect(await screen.findByText('Lagoa')).toBeTruthy()
    expect(screen.getByText('≈ 14,2 km')).toBeTruthy()
    expect(screen.getByText('-4 %')).toBeTruthy()
    expect(screen.getByText('14,3')).toBeTruthy()
    expect(screen.getByText(/aproximadas/)).toBeTruthy()
    expect(screen.getByText('marcadores 0')).toBeTruthy()
    fireEvent.click(screen.getByText('Lagoa').closest('tr')!)
    expect(screen.getByText('marcadores 2')).toBeTruthy()
  })
})
