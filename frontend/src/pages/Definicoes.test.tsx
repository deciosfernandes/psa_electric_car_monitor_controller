import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Definicoes from './Definicoes'

vi.mock('../components/PlacesEditor', () => ({ PlacesEditor: () => <div /> }))
vi.mock('../components/SetupWizard', () => ({ SetupWizard: () => <div /> }))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const settings = { discord_webhook_set: false, discord_info: false, refresh_min: 5, battery_capacity_kwh: 46 }

describe('Definicoes capacity', () => {
  it('sends battery_capacity_kwh with PUT /api/settings', async () => {
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.method === 'PUT' ? { ...settings, battery_capacity_kwh: 48 } : url === '/api/settings' ? settings : {}
      return new Response(JSON.stringify(body), { status: 200 })
    })
    vi.stubGlobal('fetch', f)
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Definicoes />
      </QueryClientProvider>,
    )
    const input = await screen.findByLabelText(/Capacidade útil da bateria/)
    fireEvent.change(input, { target: { value: '48' } })
    fireEvent.click(input.closest('div')!.parentElement!.querySelector('button')!)
    await waitFor(() => expect(f.mock.calls.some(([, i]) => i?.method === 'PUT')).toBe(true))
    const put = f.mock.calls.find(([, i]) => i?.method === 'PUT')!
    expect(JSON.parse(put[1]?.body as string).battery_capacity_kwh).toBe(48)
  })
})
