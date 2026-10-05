import { useQuery } from '@tanstack/react-query'
import type { Feature, FeatureCollection, Position } from 'geojson'

// Boundary files shipped with the app, so the map needs no network.
const FILES = ['us-states.geojson', 'india-districts.geojson']

// An administrative area: a state, or a district within one.
export interface Area {
  name: string
  district?: string
}

export interface Boundaries {
  collection: FeatureCollection
  // One name per state, placed near its middle.
  labels: { name: string; at: [number, number] }[]
  // The area containing a point, or null when it lies outside every mapped area.
  locate: (lon: number, lat: number) => Area | null
}

type Box = [west: number, south: number, east: number, north: number]

function polygonsOf(feature: Feature): Position[][][] {
  const geometry = feature.geometry
  if (geometry.type === 'Polygon') return [geometry.coordinates]
  if (geometry.type === 'MultiPolygon') return geometry.coordinates
  return []
}

function boxOf(ring: Position[]): Box {
  let [west, south, east, north] = [Infinity, Infinity, -Infinity, -Infinity]
  for (const [lon, lat] of ring) {
    if (lon < west) west = lon
    if (lon > east) east = lon
    if (lat < south) south = lat
    if (lat > north) north = lat
  }
  return [west, south, east, north]
}

// Ray casting: a point is inside when a ray from it crosses the outline an odd number of times.
function inRing(lon: number, lat: number, ring: Position[]): boolean {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

async function load(): Promise<Boundaries> {
  const loaded = await Promise.allSettled(
    FILES.map((file) =>
      fetch(`${import.meta.env.BASE_URL}basemap/${file}`).then((response) =>
        response.ok ? (response.json() as Promise<FeatureCollection>) : Promise.reject(new Error(file)),
      ),
    ),
  )
  const features = loaded.flatMap((result) => (result.status === 'fulfilled' ? result.value.features : []))

  // Each polygon with its bounding box, so a lookup tests only the few outlines near the point.
  const index: { area: Area; box: Box; rings: Position[][] }[] = []
  const centres = new Map<string, { lon: number; lat: number; weight: number }>()
  for (const feature of features) {
    const name = feature.properties?.name
    if (typeof name !== 'string') continue
    const district = feature.properties?.district
    const area: Area = typeof district === 'string' ? { name, district } : { name }
    let largest: Box | null = null
    let largestSize = 0
    for (const rings of polygonsOf(feature)) {
      const box = boxOf(rings[0])
      index.push({ area, box, rings })
      const size = (box[2] - box[0]) * (box[3] - box[1])
      if (size > largestSize) {
        largestSize = size
        largest = box
      }
    }
    // A state's label sits at the size-weighted middle of its parts.
    if (largest) {
      const centre = centres.get(name) ?? { lon: 0, lat: 0, weight: 0 }
      centre.lon += ((largest[0] + largest[2]) / 2) * largestSize
      centre.lat += ((largest[1] + largest[3]) / 2) * largestSize
      centre.weight += largestSize
      centres.set(name, centre)
    }
  }

  return {
    collection: { type: 'FeatureCollection', features },
    labels: [...centres].map(([name, c]) => ({ name, at: [c.lon / c.weight, c.lat / c.weight] })),
    locate: (lon, lat) => {
      for (const { area, box, rings } of index) {
        if (lon < box[0] || lon > box[2] || lat < box[1] || lat > box[3]) continue
        if (inRing(lon, lat, rings[0]) && !rings.slice(1).some((hole) => inRing(lon, lat, hole))) return area
      }
      return null
    },
  }
}

// Loaded once and kept for the life of the app.
export function useBoundaries() {
  return useQuery({ queryKey: ['boundaries'], queryFn: load, staleTime: Infinity, gcTime: Infinity, retry: false })
}

export function areaLabel(area: Area | null): string | null {
  if (!area) return null
  return area.district ? `${area.district}, ${area.name}` : area.name
}
