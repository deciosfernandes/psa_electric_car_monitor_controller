import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CarSnapshot, Status } from '../api'
import { AutomationCard, HealthCard, HomeBanner, MileageCard } from './PainelCards'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const car = (over: Partial<CarSnapshot> = {}): CarSnapshot => ({
  vin: 'V', at: '2026-11-12T10:00:00Z', soc: 80, range_km: 200, plugged: true, charging: false, charge_status: null,
  charge_rate_kmh: null, next_delayed_time: null, latitude: null, longitude: null, locked: true, precond_on: false,
  mileage_km: 37256.4, battery_health: 95, ...over,
})
const status = (over: Partial<Status> = {}): Status => ({
  car: car(), where: 'home', place_name: 'Casa', paused_reason: null, mileage_km: 37256.4, position_age_min: 3, period: 'vazio', next_event: null,
  automation: { enabled: true, offset_min: 10, battery_limit: 80 }, override: null, last_command: null,
  mqtt_connected: true, psa_connected: true, updated_at: '2026-11-12T10:00:00Z', ...over,
})
const wrap = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)
const stubFetch = () => {
  const f = vi.fn(async (_url: string, _init?: RequestInit) => new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', f)
  return f
}
const deferred = { mode: 'defer' } as Status['last_command']

describe('Painel cards', () => {
  it('toggle sends PUT /api/automation with enabled flipped and current offset/limit', async () => {
    const f = stubFetch()
    wrap(<AutomationCard status={status()} run={vi.fn()} pending={null} />)
    fireEvent.click(screen.getByRole('switch', { name: 'Automação' }))
    await waitFor(() => expect(f).toHaveBeenCalled())
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/automation')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(init?.body as string)).toEqual({ enabled: false, offset_min: 10, battery_limit: 80 })
  })

  it.each([
    ['unset', 'Defina o local Casa'],
    ['unknown', 'Posição do carro desconhecida'],
  ] as const)('shows "Em pausa" instead of the next event when where=%s', (where, reason) => {
    const next_event = { at: '2026-11-12T23:00:00Z', action: 'charge' } as Status['next_event']
    wrap(<AutomationCard status={status({ where, paused_reason: reason, next_event })} run={vi.fn()} pending={null} />)
    expect(screen.getByText(`Em pausa · ${reason}`)).toBeTruthy()
    expect(screen.queryByText(/Próximo:/)).toBeNull()
  })

  it('shows "só em Casa" and the deferred hint only when away with a defer command', () => {
    wrap(<AutomationCard status={status({ where: 'work', last_command: deferred })} run={vi.fn()} pending={null} />)
    expect(screen.getByText(/só em Casa/)).toBeTruthy()
    expect(screen.getByText('Programa diferido ativo no carro')).toBeTruthy()
    cleanup()
    wrap(<AutomationCard status={status({ last_command: deferred })} run={vi.fn()} pending={null} />)
    expect(screen.queryByText('Programa diferido ativo no carro')).toBeNull()
  })

  it('health null shows "não disponível"; a value shows the PSA caption', () => {
    wrap(<HealthCard status={status({ car: car({ battery_health: null }) })} />)
    expect(screen.getByText('não disponível')).toBeTruthy()
    cleanup()
    wrap(<HealthCard status={status()} />)
    expect(screen.getByText('95 %')).toBeTruthy()
    expect(screen.getByText('valor reportado pela PSA')).toBeTruthy()
  })

  it('formats the mileage in pt-PT', () => {
    wrap(<MileageCard status={status()} />)
    expect(screen.getByText(/37\s256 km/)).toBeTruthy()
  })

  it('null mileage shows a dash', () => {
    wrap(<MileageCard status={status({ mileage_km: null, car: car({ mileage_km: null }) })} />)
    expect(screen.getByText('—')).toBeTruthy()
  })

  it('home banner posts from-car as Casa', async () => {
    const f = stubFetch()
    wrap(<HomeBanner />)
    expect(screen.getByText('Defina o local Casa')).toBeTruthy()
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(f).toHaveBeenCalled())
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/places/from-car')
    expect(JSON.parse(init?.body as string)).toEqual({ name: 'Casa', kind: 'home' })
  })
})
