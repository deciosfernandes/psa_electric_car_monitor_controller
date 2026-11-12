import { describe, expect, it } from 'vitest'
import { fmtDate, fmtEur, fmtHour, fmtKm, fmtPct } from './format'

describe('format (pt-PT, Atlantic/Azores)', () => {
  it('formats the hour in Azores time', () => {
    expect(fmtHour('2026-11-12T07:50:00Z')).toBe('06h50')
    expect(fmtHour(new Date('2026-07-12T07:05:00Z'))).toBe('07h05') // summer: UTC+0
  })
  it('formats the date as DD/MM/AAAA', () => {
    expect(fmtDate('2026-11-12T12:00:00Z')).toBe('12/11/2026')
    expect(fmtDate('2026-11-12T00:30:00Z')).toBe('11/11/2026') // still the 11th in Azores
  })
  it('formats percent, km and EUR', () => {
    expect(fmtPct(78)).toBe('78 %')
    expect(fmtKm(312.4)).toBe('312 km')
    expect(fmtEur(0.1234)).toBe('0,12 €')
  })
})
