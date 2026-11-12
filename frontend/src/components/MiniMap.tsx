import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef } from 'react'

export interface LatLon {
  lat: number
  lon: number
}

interface Props {
  markers: LatLon[]
  radiusM?: number // circle around the first marker
  onPick?: (lat: number, lon: number) => void
}

/** OSM map: markers, an optional radius circle around the first one, and click-to-pick. Plain leaflet (no wrapper). */
export function MiniMap({ markers, radiusM, onPick }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layer = useRef<L.LayerGroup | null>(null)
  const pick = useRef(onPick)
  useEffect(() => {
    pick.current = onPick
  })

  useEffect(() => {
    const m = L.map(el.current!, { center: [0, 0], zoom: 15 })
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; OpenStreetMap' }).addTo(m)
    layer.current = L.layerGroup().addTo(m)
    m.on('click', (e: L.LeafletMouseEvent) => pick.current?.(e.latlng.lat, e.latlng.lng))
    map.current = m
    return () => {
      m.remove()
      map.current = null
      layer.current = null
    }
  }, [])

  const key = markers.map((p) => `${p.lat},${p.lon}`).join(';') // new array each render: depend on the values
  const latest = useRef(markers)
  useEffect(() => {
    latest.current = markers
  })

  useEffect(() => {
    const m = map.current
    const g = layer.current
    const list = latest.current
    if (!m || !g || list.length === 0) return
    g.clearLayers()
    const points = list.map((p) => L.latLng(p.lat, p.lon))
    points.forEach((p) => L.circleMarker(p, { radius: 5 }).addTo(g))
    if (radiusM !== undefined) {
      const circle = L.circle(points[0], { radius: radiusM }).addTo(g)
      m.fitBounds(circle.getBounds())
    } else if (points.length > 1) {
      m.fitBounds(L.latLngBounds(points).pad(0.2))
    } else {
      m.setView(points[0], 15)
    }
  }, [key, radiusM])

  return <div ref={el} className="h-64 w-full rounded-[9px]" aria-label="Mapa" />
}
