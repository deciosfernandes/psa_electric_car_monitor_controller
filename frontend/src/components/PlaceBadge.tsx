const KIND_TEXT = { home: 'Casa', work: 'Trabalho', other: 'Outro' } as const

/** Place pill; an "other" place shows its own name when it has one. */
export function PlaceBadge({ kind, name }: { kind: keyof typeof KIND_TEXT; name: string | null }) {
  return (
    <span className="rounded-full bg-line px-2 py-0.5 text-[11px] font-semibold">
      {kind === 'other' && name ? name : KIND_TEXT[kind]}
    </span>
  )
}
