import 'leaflet/dist/leaflet.css'
import { useEffect } from 'react'
import { Circle, CircleMarker, MapContainer, TileLayer, useMap, useMapEvents } from 'react-leaflet'

interface Props {
  lat: number
  lon: number
  radius_m: number
  onPick: (lat: number, lon: number) => void
}

function Clicks({ onPick }: Pick<Props, 'onPick'>) {
  useMapEvents({ click: (e) => onPick(e.latlng.lat, e.latlng.lng) })
  return null
}

function Recenter({ lat, lon }: Pick<Props, 'lat' | 'lon'>) {
  const map = useMap()
  useEffect(() => {
    map.setView([lat, lon])
  }, [map, lat, lon])
  return null
}

/** OSM map with the place circle; a click moves the point. */
export function MiniMap({ lat, lon, radius_m, onPick }: Props) {
  return (
    <MapContainer center={[lat, lon]} zoom={15} className="h-64 w-full rounded-[9px]">
      <TileLayer attribution="&copy; OpenStreetMap" url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
      <Circle center={[lat, lon]} radius={radius_m} />
      <CircleMarker center={[lat, lon]} radius={5} />
      <Recenter lat={lat} lon={lon} />
      <Clicks onPick={onPick} />
    </MapContainer>
  )
}
