import { describe, expect, it } from 'vitest'
import type { Slot, Tariff } from '../api'
import { azoresNow, daySegments, daySegmentsFor, seasonAt } from './tariff'

const day = (...s: [number, number, Slot['period']][]): Slot[] => s.map(([start, end, period]) => ({ start, end, period }))
const sunday = day([0, 1440, 'vazio'])

const EDA: Tariff = {
  name: 'EDA tri-horário semanal',
  prices: [],
  seasons: [
    {
      name: 'Inverno',
      months: [11, 12, 1, 2, 3, 4, 5],
      days: {
        weekday: day([0, 420, 'vazio'], [420, 1110, 'cheias'], [1110, 1290, 'ponta'], [1290, 1440, 'cheias']),
        saturday: day([0, 690, 'vazio'], [690, 810, 'cheias'], [810, 1080, 'vazio'], [1080, 1380, 'cheias'], [1380, 1440, 'vazio']),
        sunday,
      },
    },
    {
      name: 'Verão',
      months: [6, 7, 8, 9, 10],
      days: { weekday: day([0, 1440, 'cheias']), saturday: day([0, 1440, 'cheias']), sunday },
    },
  ],
}

describe('daySegments', () => {
  it('Thursday 12/11/2026 (winter weekday) has four segments', () => {
    expect(daySegments(EDA, new Date('2026-11-12T12:00:00Z'))).toEqual(
      day([0, 420, 'vazio'], [420, 1110, 'cheias'], [1110, 1290, 'ponta'], [1290, 1440, 'cheias']),
    )
  })
  it('Sunday is a single vazio segment', () => {
    expect(daySegments(EDA, new Date('2026-11-15T12:00:00Z'))).toEqual(sunday)
  })
  it('uses the Azores calendar date, not the runtime one', () => {
    // 00:30 UTC on Monday 16/11 is still Sunday 15/11 in the Azores (UTC-1)
    expect(daySegments(EDA, new Date('2026-11-16T00:30:00Z'))).toEqual(sunday)
  })
  it('picks the season by Azores month', () => {
    // Azores is UTC+0 in summer: 23:30Z on 31 May is still May (winter); 00:30Z on 1 June is summer
    expect(seasonAt(EDA, new Date('2026-05-31T23:30:00Z'))?.name).toBe('Inverno')
    expect(daySegments(EDA, new Date('2026-06-01T00:30:00Z'))).toEqual(day([0, 1440, 'cheias']))
  })
})

describe('azoresNow', () => {
  it('gives minutes since midnight in Azores time', () => {
    expect(azoresNow(new Date('2026-11-12T07:50:00Z')).minute).toBe(6 * 60 + 50)
  })
})

describe('daySegmentsFor', () => {
  it('resolves a plain calendar date: Saturday 14/11/2026 in winter', () => {
    expect(daySegmentsFor(EDA, { year: 2026, month: 11, day: 14 })).toEqual(EDA.seasons[0].days.saturday)
  })
  it('uses the month to pick the season', () => {
    expect(daySegmentsFor(EDA, { year: 2026, month: 7, day: 8 })).toEqual(day([0, 1440, 'cheias']))
  })
})
