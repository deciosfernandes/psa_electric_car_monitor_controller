import type { Where } from '../api'

const KIND_TEXT: Record<Where, string> = { home: 'Casa', work: 'Trabalho', other: 'Outro', unknown: 'Desconhecido', unset: 'Sem local' }

/** Place pill; an "other" place shows its own name, else rounded coordinates when given. */
export function PlaceBadge({ kind, name, lat, lon }: { kind: Where; name: string | null; lat?: number | null; lon?: number | null }) {
  const coords = (kind === 'other' || kind === 'unknown') && !name && lat != null && lon != null
  const text = kind === 'other' && name ? name : coords ? `${lat.toFixed(3)}, ${lon.toFixed(3)}` : KIND_TEXT[kind]
  return (
    <span className="rounded-full bg-line px-2 py-0.5 text-[11px] font-semibold">
      {text}
    </span>
  )
}
