import { useRef, useState } from 'react'
import type { DayType, PeriodType, Price, Slot, Tariff } from '../api'
import { fmtClock, fmtMinutes } from '../format'

const STEP = 15
const CELLS = 1440 / STEP
const PERIODS: PeriodType[] = ['vazio', 'cheias', 'ponta']
const ROWS: { type: DayType; label: string }[] = [
  { type: 'weekday', label: 'Dias úteis' },
  { type: 'saturday', label: 'Sábado' },
  { type: 'sunday', label: 'Domingo' },
]
const MONTHS = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
const COLOR: Record<PeriodType, string> = { vazio: 'var(--vazio)', cheias: 'var(--cheias)', ponta: 'var(--ponta)' }

export function slotsToCells(slots: Slot[]): PeriodType[] {
  const cells: PeriodType[] = Array<PeriodType>(CELLS).fill('vazio')
  for (const s of slots) cells.fill(s.period, s.start / STEP, s.end / STEP)
  return cells
}

export function cellsToSlots(cells: PeriodType[]): Slot[] {
  const slots: Slot[] = []
  cells.forEach((period, i) => {
    const last = slots[slots.length - 1]
    if (last && last.period === period) last.end = (i + 1) * STEP
    else slots.push({ start: i * STEP, end: (i + 1) * STEP, period })
  })
  return slots
}

/** Copy of `cells` with the inclusive range between `a` and `b` (either order) set to `period`. */
export function paintCells(cells: PeriodType[], a: number, b: number, period: PeriodType): PeriodType[] {
  const next = [...cells]
  next.fill(period, Math.min(a, b), Math.max(a, b) + 1)
  return next
}

const summary = (slots: Slot[]) => slots.map((s) => `${s.period} ${fmtMinutes(s.start)}–${fmtMinutes(s.end)}`).join(' · ')

const toMinutes = (clock: string) => {
  const [h, m] = clock.split(':').map(Number)
  return h * 60 + m
}

const timeOptions = (from: number, to: number) => {
  const out: number[] = []
  for (let m = from; m <= to; m += STEP) out.push(m)
  return out
}

function Swatch({ period }: { period: PeriodType }) {
  return (
    <span style={{ color: COLOR[period] }} aria-hidden="true">
      ■
    </span>
  )
}

interface DragState {
  row: DayType
  anchor: number
  base: PeriodType[]
}

interface Props {
  tariff: Tariff
  onChange: (t: Tariff) => void
}

/** Season tabs, month chips and the weekly 3 x 96 grid; edits are reported through onChange. */
export function TariffEditor({ tariff, onChange }: Props) {
  const [seasonIdx, setSeasonIdx] = useState(0)
  const [brush, setBrush] = useState<PeriodType>('vazio')
  const drag = useRef<DragState | null>(null)
  const [form, setForm] = useState<{ row: DayType; start: number; end: number; period: PeriodType }>({
    row: 'weekday',
    start: 0,
    end: 420,
    period: 'vazio',
  })
  const [formError, setFormError] = useState<string | null>(null)

  const idx = Math.min(seasonIdx, tariff.seasons.length - 1)
  const season = tariff.seasons[idx]
  if (!season) return null

  function setDay(type: DayType, slots: Slot[]) {
    onChange({
      ...tariff,
      seasons: tariff.seasons.map((s, i) => (i === idx ? { ...s, days: { ...s.days, [type]: slots } } : s)),
    })
  }

  function moveMonth(month: number) {
    onChange({
      ...tariff,
      seasons: tariff.seasons.map((s, i) => ({
        ...s,
        months: i === idx ? [...s.months, month].sort((a, b) => a - b) : s.months.filter((m) => m !== month),
      })),
    })
  }

  const cellAt = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    return Math.max(0, Math.min(CELLS - 1, Math.floor(((e.clientX - r.left) / r.width) * CELLS)))
  }

  function down(row: DayType, e: React.PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return
    const anchor = cellAt(e)
    const base = slotsToCells(season.days[row])
    drag.current = { row, anchor, base }
    e.currentTarget.setPointerCapture(e.pointerId)
    setDay(row, cellsToSlots(paintCells(base, anchor, anchor, brush)))
  }

  function move(e: React.PointerEvent<HTMLDivElement>) {
    const d = drag.current
    if (d) setDay(d.row, cellsToSlots(paintCells(d.base, d.anchor, cellAt(e), brush)))
  }

  function apply() {
    if (form.end <= form.start) {
      setFormError('O fim tem de ser depois do início.')
      return
    }
    setFormError(null)
    setDay(form.row, cellsToSlots(paintCells(slotsToCells(season.days[form.row]), form.start / STEP, form.end / STEP - 1, form.period)))
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Estação" className="flex gap-1">
          {tariff.seasons.map((s, i) => (
            <button key={s.name} role="tab" aria-selected={i === idx} className={`btn !py-1.5 ${i === idx ? 'btn-primary' : ''}`} onClick={() => setSeasonIdx(i)}>
              {s.name}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1" role="group" aria-label={`Meses de ${season.name}`}>
          {MONTHS.map((name, i) => {
            const month = i + 1
            const mine = season.months.includes(month)
            return (
              <button
                key={name}
                aria-pressed={mine}
                disabled={mine}
                title={mine ? `${name} pertence a ${season.name}` : `Passar ${name} para ${season.name}`}
                className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${
                  mine ? 'border-primary bg-primary-soft text-primary-ink' : 'border-line bg-card text-muted hover:border-primary'
                }`}
                onClick={() => moveMonth(month)}
              >
                {name}
              </button>
            )
          })}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <span className="label">Pincel</span>
        <div className="flex gap-1" role="group" aria-label="Período a pintar">
          {PERIODS.map((p) => (
            <button key={p} aria-pressed={brush === p} className={`btn !py-1.5 ${brush === p ? 'ring-2 ring-primary' : ''}`} onClick={() => setBrush(p)}>
              <Swatch period={p} /> {p}
            </button>
          ))}
        </div>
        <span className="text-xs text-muted">Clique ou arraste sobre a grelha para pintar.</span>
      </div>

      <div className="mt-3 overflow-x-auto">
        <div className="min-w-[720px]">
          {ROWS.map(({ type, label }) => (
            <div key={type} className="mb-2 grid grid-cols-[84px_1fr] items-center gap-2">
              <div className="text-xs font-semibold">{label}</div>
              <div>
                <div
                  role="group"
                  aria-label={`${label}: ${summary(season.days[type])}`}
                  className="grid h-7 cursor-crosshair touch-none select-none overflow-hidden rounded-[7px]"
                  style={{ gridTemplateColumns: `repeat(${CELLS}, 1fr)` }}
                  onPointerDown={(e) => down(type, e)}
                  onPointerMove={move}
                  onPointerUp={() => (drag.current = null)}
                  onPointerCancel={() => (drag.current = null)}
                >
                  {slotsToCells(season.days[type]).map((p, i) => (
                    <span
                      key={i}
                      aria-hidden="true"
                      title={`${fmtMinutes(i * STEP)} · ${p}`}
                      className={i % 4 === 0 && i > 0 ? 'border-l-2 border-card' : 'border-l border-card/50'}
                      style={{ background: COLOR[p] }}
                    />
                  ))}
                </div>
                <div className="mt-0.5 text-[10px] text-muted">{summary(season.days[type])}</div>
              </div>
            </div>
          ))}
          <div className="grid grid-cols-[84px_1fr] gap-2" aria-hidden="true">
            <span />
            <div className="relative h-4 text-[10px] text-muted">
              {Array.from({ length: 13 }, (_, k) => k * 2).map((h) => (
                <span key={h} className={`absolute ${h === 0 ? '' : h === 24 ? '-translate-x-full' : '-translate-x-1/2'}`} style={{ left: `${(h / 24) * 100}%` }}>
                  {String(h).padStart(2, '0')}h
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="mt-1 text-[11px] text-muted">
        {PERIODS.map((p) => (
          <span key={p} className="mr-3">
            <Swatch period={p} /> {p}
          </span>
        ))}
      </div>

      <fieldset className="mt-4 rounded-[10px] border border-line p-3">
        <legend className="label px-1">Editar por horas (teclado)</legend>
        <div className="flex flex-wrap items-end gap-3 text-xs">
          <label className="flex flex-col gap-1">
            Linha
            <select className="field !w-auto" value={form.row} onChange={(e) => setForm({ ...form, row: e.target.value as DayType })}>
              {ROWS.map((r) => (
                <option key={r.type} value={r.type}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            Início
            <select className="field !w-auto" value={fmtClock(form.start)} onChange={(e) => setForm({ ...form, start: toMinutes(e.target.value) })}>
              {timeOptions(0, 1440 - STEP).map((m) => (
                <option key={m}>{fmtClock(m)}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            Fim
            <select className="field !w-auto" value={fmtClock(form.end)} onChange={(e) => setForm({ ...form, end: toMinutes(e.target.value) })}>
              {timeOptions(STEP, 1440).map((m) => (
                <option key={m}>{fmtClock(m)}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            Período
            <select className="field !w-auto" value={form.period} onChange={(e) => setForm({ ...form, period: e.target.value as PeriodType })}>
              {PERIODS.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          <button className="btn" onClick={apply}>
            Aplicar
          </button>
        </div>
        {formError && (
          <div role="alert" className="mt-2 text-xs text-danger-ink">
            {formError}
          </div>
        )}
      </fieldset>
    </div>
  )
}

// ---- prices -------------------------------------------------------------------------------------------------------

const isoToPt = (iso: string) => iso.split('-').reverse().join('/')

function ptToIso(text: string): string | null {
  const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(text.trim())
  if (!m) return null
  const [, d, mo, y] = m
  const date = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d)))
  const real = date.getUTCFullYear() === Number(y) && date.getUTCMonth() === Number(mo) - 1 && date.getUTCDate() === Number(d)
  return real ? `${y}-${mo}-${d}` : null
}

/** DD/MM/AAAA text input that reports an ISO date once the text is a real calendar date. */
function DateField({ value, onChange, label }: { value: string; onChange: (iso: string) => void; label: string }) {
  const [text, setText] = useState(isoToPt(value))
  const valid = ptToIso(text) !== null
  return (
    <input
      aria-label={label}
      aria-invalid={!valid}
      inputMode="numeric"
      placeholder="DD/MM/AAAA"
      className={`field !w-28 ${valid ? '' : '!border-danger-ink'}`}
      value={text}
      onChange={(e) => {
        setText(e.target.value)
        const iso = ptToIso(e.target.value)
        if (iso) onChange(iso)
      }}
    />
  )
}

interface PriceProps {
  prices: Price[]
  today: string // ISO date (Azores)
  onChange: (prices: Price[]) => void
}

/** Prices per period, newest first; the latest one already valid is marked "em vigor". */
export function PriceTable({ prices, today, onChange }: PriceProps) {
  const update = (i: number, patch: Partial<Price>) => onChange(prices.map((p, k) => (k === i ? { ...p, ...patch } : p)))
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {PERIODS.map((period) => {
        const rows = prices
          .map((p, i) => ({ p, i }))
          .filter((r) => r.p.period === period)
          .sort((a, b) => b.p.valid_from.localeCompare(a.p.valid_from))
        const current = rows.find((r) => r.p.valid_from <= today)?.i
        return (
          <div key={period}>
            <div className="mb-1 text-xs font-semibold">
              <Swatch period={period} /> {period}
            </div>
            {rows.length === 0 && <div className="mb-1.5 text-xs text-muted">Sem preços.</div>}
            {rows.map(({ p, i }) => (
              <div key={i} className="mb-1.5 flex flex-wrap items-center gap-2">
                <input
                  aria-label={`Preço ${period} (€/kWh)`}
                  type="number"
                  min={0}
                  step={0.0001}
                  className="field !w-24"
                  value={Number.isFinite(p.eur_kwh) ? p.eur_kwh : ''}
                  onChange={(e) => update(i, { eur_kwh: e.target.value === '' ? NaN : Number(e.target.value) })}
                />
                <span className="text-xs text-muted">€/kWh desde</span>
                <DateField label={`Em vigor desde (${period})`} value={p.valid_from} onChange={(iso) => update(i, { valid_from: iso })} />
                {i === current && <span className="rounded-full bg-ok-soft px-2 py-0.5 text-[10px] font-semibold text-ok-ink">em vigor</span>}
                <button className="btn !px-2 !py-1 !text-xs" aria-label={`Remover preço ${period}`} onClick={() => onChange(prices.filter((_, k) => k !== i))}>
                  ✕
                </button>
              </div>
            ))}
            <button className="btn !py-1 !text-xs" onClick={() => onChange([...prices, { period, eur_kwh: rows[0]?.p.eur_kwh ?? 0, valid_from: today }])}>
              Adicionar preço
            </button>
          </div>
        )
      })}
    </div>
  )
}
