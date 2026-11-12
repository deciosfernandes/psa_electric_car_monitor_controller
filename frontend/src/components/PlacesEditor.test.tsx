import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PlacesEditor } from './PlacesEditor'

vi.mock('./MiniMap', () => ({
  MiniMap: ({ onPick }: { onPick: (a: number, b: number) => void }) => <button type="button" onClick={() => onPick(38.5, -28.6)}>mapa</button>,
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function setup() {
  const f = vi.fn(async (_url: string, init?: RequestInit) => new Response(init?.method === 'GET' ? '[]' : '{}', { status: 200 }))
  vi.stubGlobal('fetch', f)
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PlacesEditor />
    </QueryClientProvider>,
  )
  return f
}

const bodyOf = (f: ReturnType<typeof setup>, url: string) => {
  const call = f.mock.calls.find((c) => c[0] === url && c[1]?.method === 'POST')
  return call ? JSON.parse(call[1]?.body as string) : undefined
}

describe('PlacesEditor', () => {
  it('posts from-car with name and kind', async () => {
    const f = setup()
    fireEvent.click(await screen.findByText('Adicionar local'))
    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'Casa' } })
    fireEvent.change(screen.getByLabelText('Tipo'), { target: { value: 'home' } })
    fireEvent.click(screen.getByText('Usar posição atual do carro'))
    await waitFor(() => expect(bodyOf(f, '/api/places/from-car')).toEqual({ name: 'Casa', kind: 'home' }))
  })

  it('saves a clicked map point with radius and price for kind other', async () => {
    const f = setup()
    fireEvent.click(await screen.findByText('Adicionar local'))
    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'Ginásio' } })
    fireEvent.change(screen.getByLabelText(/Preço/), { target: { value: '0.25' } })
    fireEvent.change(screen.getByLabelText(/Raio/), { target: { value: '300' } })
    fireEvent.click(screen.getByText('mapa'))
    fireEvent.click(screen.getByText('Guardar'))
    await waitFor(() => expect(bodyOf(f, '/api/places')).toMatchObject({ name: 'Ginásio', kind: 'other', lat: 38.5, lon: -28.6, radius_m: 300, price_eur_kwh: 0.25 }))
  })
})
