import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Slot, Tariff } from '../api'
import { cellsToSlots, paintCells, slotsToCells, TariffEditor } from './TariffEditor'

afterEach(cleanup)

const day = (...s: [number, number, Slot['period']][]): Slot[] => s.map(([start, end, period]) => ({ start, end, period }))
const edaWinterWeekday = day([0, 420, 'vazio'], [420, 1110, 'cheias'], [1110, 1290, 'ponta'], [1290, 1440, 'cheias'])
const edaWinterSaturday = day([0, 690, 'vazio'], [690, 810, 'cheias'], [810, 1080, 'vazio'], [1080, 1380, 'cheias'], [1380, 1440, 'vazio'])

describe('grid conversion', () => {
  it('slots -> cells -> slots round-trips the EDA winter Saturday', () => {
    expect(cellsToSlots(slotsToCells(edaWinterSaturday))).toEqual(edaWinterSaturday)
  })
  it('has 96 cells of 15 minutes', () => {
    const cells = slotsToCells(edaWinterWeekday)
    expect(cells).toHaveLength(96)
    expect(cells[0]).toBe('vazio')
    expect(cells[28]).toBe('cheias') // 07:00
  })
  it('painting cells 44-53 as cheias on a weekday gives a cheias block 660-810', () => {
    const slots = cellsToSlots(paintCells(slotsToCells(edaWinterWeekday), 44, 53, 'cheias'))
    expect(slots).toEqual(edaWinterWeekday) // already cheias there: merged into the 420-1110 block
    const vazioMorning = cellsToSlots(paintCells(slotsToCells(edaWinterSaturday), 44, 53, 'vazio'))
    expect(vazioMorning.find((s) => s.start === 0)).toEqual({ start: 0, end: 1080, period: 'vazio' })
  })
  it('paints a cheias island inside a vazio day', () => {
    const slots = cellsToSlots(paintCells(slotsToCells(day([0, 1440, 'vazio'])), 44, 53, 'cheias'))
    expect(slots).toEqual(day([0, 660, 'vazio'], [660, 810, 'cheias'], [810, 1440, 'vazio']))
  })
  it('paints regardless of drag direction', () => {
    expect(paintCells(slotsToCells(day([0, 1440, 'vazio'])), 53, 44, 'ponta')).toEqual(
      paintCells(slotsToCells(day([0, 1440, 'vazio'])), 44, 53, 'ponta'),
    )
  })
})

const TARIFF: Tariff = {
  name: 'EDA',
  prices: [],
  seasons: [
    { name: 'Inverno', months: [11, 12, 1, 2, 3, 4, 5], days: { weekday: edaWinterWeekday, saturday: edaWinterSaturday, sunday: day([0, 1440, 'vazio']) } },
    { name: 'Verão', months: [6, 7, 8, 9, 10], days: { weekday: day([0, 1440, 'cheias']), saturday: day([0, 1440, 'cheias']), sunday: day([0, 1440, 'vazio']) } },
  ],
}

describe('TariffEditor keyboard fallback', () => {
  it('applies a period to a time range on the chosen row', () => {
    const onChange = vi.fn()
    render(<TariffEditor tariff={TARIFF} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('Linha'), { target: { value: 'sunday' } })
    fireEvent.change(screen.getByLabelText('Início'), { target: { value: '11:00' } })
    fireEvent.change(screen.getByLabelText('Fim'), { target: { value: '13:30' } })
    fireEvent.change(screen.getByLabelText('Período'), { target: { value: 'ponta' } })
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar' }))
    const next: Tariff = onChange.mock.calls[0][0]
    expect(next.seasons[0].days.sunday).toEqual(day([0, 660, 'vazio'], [660, 810, 'ponta'], [810, 1440, 'vazio']))
    expect(next.seasons[0].days.weekday).toEqual(edaWinterWeekday)
  })
  it('moving a month to this season takes it from the other one', () => {
    const onChange = vi.fn()
    render(<TariffEditor tariff={TARIFF} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Jun' }))
    const next: Tariff = onChange.mock.calls[0][0]
    expect(next.seasons[0].months).toContain(6)
    expect(next.seasons[1].months).not.toContain(6)
  })
})
