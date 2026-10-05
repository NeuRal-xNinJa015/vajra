import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Feature, FeatureCollection, Position } from 'geojson'
import { CircleAlert, CloudOff, LoaderCircle, RefreshCw, ServerCrash, TrendingUp } from 'lucide-react'
import {
  Map as MapLibreMap,
  NavigationControl,
  ScaleControl,
  type Coordinates,
  type ExpressionSpecification,
  type GeoJSONSource,
  type ImageSource,
  type MapMouseEvent,
  type StyleSpecification,
  Marker,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { LayerPanel } from '@/components/LayerPanel'
import { Legend } from '@/components/Legend'
import { Button } from '@/components/ui/button'
import { backendRequestHeaders, type Bounds } from '@/lib/api'
import { utcTime } from '@/lib/format'
import { useTheme } from '@/lib/theme'
import {
  useAlerts,
  useConnectionStatus,
  useDisplayedLayer,
  useForecast,
  useHealth,
  useLightning,
  useObservation,
  useReplay,
  useStatus,
  useStormCells,
} from '@/lib/queries'
import { useUi } from '@/store/ui'

// Fully offline base map: a plain background, administrative boundaries from a
// file shipped with the app, and a lat/lon graticule drawn from the configured
// region. No tiles, fonts or network requests.
const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    graticule: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
    boundaries: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
    region: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
    cells: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
    alert: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
    'cell-track': { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
  },
  layers: [
    { id: 'background', type: 'background', paint: { 'background-color': '#090e19' } },
    { id: 'land', type: 'fill', source: 'boundaries', paint: { 'fill-color': '#111a2e' } },
    {
      id: 'graticule',
      type: 'line',
      source: 'graticule',
      paint: { 'line-color': '#1c2638', 'line-width': 0.7 },
    },
    {
      id: 'boundary-lines',
      type: 'line',
      source: 'boundaries',
      paint: { 'line-color': '#3b4a66', 'line-width': 1 },
    },
    {
      id: 'region-fill',
      type: 'fill',
      source: 'region',
      paint: { 'fill-color': '#38bdf8', 'fill-opacity': 0.03 },
    },
    {
      id: 'region-outline',
      type: 'line',
      source: 'region',
      paint: { 'line-color': '#38bdf8', 'line-width': 1.2, 'line-opacity': 0.7, 'line-dasharray': [3, 2] },
    },
    // Area of the alert under review.
    {
      id: 'alert-fill',
      type: 'fill',
      source: 'alert',
      paint: { 'fill-color': '#fbbf24', 'fill-opacity': 0.12 },
    },
    {
      id: 'alert-outline',
      type: 'line',
      source: 'alert',
      paint: { 'line-color': '#fbbf24', 'line-width': 2, 'line-dasharray': [4, 2] },
    },
    // Storm cells: a dark casing under a light outline stays readable over any radar colour.
    {
      id: 'cells-fill',
      type: 'fill',
      source: 'cells',
      paint: { 'fill-color': '#ffffff', 'fill-opacity': ['case', ['get', 'selected'], 0.2, 0.04] },
    },
    {
      id: 'cells-casing',
      type: 'line',
      source: 'cells',
      paint: { 'line-color': '#020617', 'line-opacity': 0.7, 'line-width': ['case', ['get', 'selected'], 5, 3.5] },
    },
    {
      id: 'cells-line',
      type: 'line',
      source: 'cells',
      filter: ['!', ['get', 'projected']],
      paint: {
        'line-color': ['case', ['get', 'selected'], '#67e8f9', '#ffffff'],
        'line-width': ['case', ['get', 'selected'], 2.5, 1.5],
      },
    },
    // A projected position is drawn dashed: it is a forecast, not an observation.
    {
      id: 'cells-line-projected',
      type: 'line',
      source: 'cells',
      filter: ['get', 'projected'],
      paint: {
        'line-color': ['case', ['get', 'selected'], '#67e8f9', '#ffffff'],
        'line-width': ['case', ['get', 'selected'], 2.5, 1.5],
        'line-dasharray': [2, 1.5],
      },
    },
    {
      id: 'track-past',
      type: 'line',
      source: 'cell-track',
      filter: ['==', ['get', 'kind'], 'past'],
      paint: { 'line-color': '#67e8f9', 'line-width': 2, 'line-opacity': 0.9 },
    },
    {
      id: 'track-future',
      type: 'line',
      source: 'cell-track',
      filter: ['==', ['get', 'kind'], 'future'],
      paint: { 'line-color': '#67e8f9', 'line-width': 2, 'line-dasharray': [1.5, 1.5] },
    },
    {
      id: 'track-points',
      type: 'circle',
      source: 'cell-track',
      filter: ['==', ['get', 'kind'], 'point'],
      paint: {
        'circle-radius': 3.5,
        'circle-color': '#67e8f9',
        'circle-stroke-color': '#020617',
        'circle-stroke-width': 1.5,
      },
    },
  ],
}

const LAYER_TITLES: Record<string, string> = {
  radar_reflectivity: 'Radar reflectivity',
  forecast_reflectivity: 'Forecast reflectivity',
  ml_storm_probability: 'Storm probability (ML)',
  ml_lightning_probability: 'Lightning probability (ML)',
}

const GRATICULE_PADDING_DEG = 5

function graticule([west, south, east, north]: Bounds): FeatureCollection {
  const w = Math.max(-180, Math.floor(west) - GRATICULE_PADDING_DEG)
  const e = Math.min(180, Math.ceil(east) + GRATICULE_PADDING_DEG)
  const s = Math.max(-85, Math.floor(south) - GRATICULE_PADDING_DEG)
  const n = Math.min(85, Math.ceil(north) + GRATICULE_PADDING_DEG)
  const lines: Position[][] = []
  for (let lon = w; lon <= e; lon++) lines.push([[lon, s], [lon, n]])
  for (let lat = s; lat <= n; lat++) lines.push([[w, lat], [e, lat]])
  return {
    type: 'FeatureCollection',
    features: lines.map((coordinates) => ({
      type: 'Feature',
      properties: {},
      geometry: { type: 'LineString', coordinates },
    })),
  }
}

function regionBox([west, south, east, north]: Bounds): Feature {
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'Polygon',
      coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
    },
  }
}

function formatCoordinate(value: number, positive: string, negative: string) {
  return `${Math.abs(value).toFixed(3)}° ${value >= 0 ? positive : negative}`
}

// A centred message over the map for states where there is nothing to draw.
function MapNotice({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-background/60 backdrop-blur-[1px]">
      <div className="bg-card w-80 rounded-xl border p-5 text-center shadow-xl">
        <div className="text-muted-foreground mb-3 flex justify-center">{icon}</div>
        <div className="text-sm font-medium">{title}</div>
        {children}
      </div>
    </div>
  )
}

// Map colours that follow the theme. The radar, lightning and probability images
// come from the backend with fixed colours, so only the base and overlays change.
// Where to write an area's name: the middle of its largest outline's bounding box.
function labelPoint(feature: Feature): [number, number] | null {
  const geometry = feature.geometry
  const rings: Position[][] =
    geometry.type === 'Polygon'
      ? [geometry.coordinates[0]]
      : geometry.type === 'MultiPolygon'
        ? geometry.coordinates.map((polygon) => polygon[0])
        : []
  let best: [number, number] | null = null
  let bestArea = 0
  for (const ring of rings) {
    const lons = ring.map((point) => point[0])
    const lats = ring.map((point) => point[1])
    const [w, e, sLat, n] = [Math.min(...lons), Math.max(...lons), Math.min(...lats), Math.max(...lats)]
    const area = (e - w) * (n - sLat)
    if (area > bestArea) {
      bestArea = area
      best = [(w + e) / 2, (sLat + n) / 2]
    }
  }
  return best
}

const MAP_THEME = {
  dark: { background: '#0a101c', land: '#121a2b', boundary: '#3d4c69', graticule: '#1b2538', region: '#7cb8ff', track: '#67e8f9' },
  light: { background: '#dde5ee', land: '#f6f7f9', boundary: '#9aa6b5', graticule: '#d3d8de', region: '#2563eb', track: '#0e7490' },
}

function applyMapTheme(map: MapLibreMap, dark: boolean) {
  const colors = dark ? MAP_THEME.dark : MAP_THEME.light
  const selectedOrWhite: ExpressionSpecification = ['case', ['get', 'selected'], colors.track, '#ffffff']
  map.setPaintProperty('background', 'background-color', colors.background)
  map.setPaintProperty('land', 'fill-color', colors.land)
  map.setPaintProperty('boundary-lines', 'line-color', colors.boundary)
  map.setPaintProperty('graticule', 'line-color', colors.graticule)
  map.setPaintProperty('region-fill', 'fill-color', colors.region)
  map.setPaintProperty('region-outline', 'line-color', colors.region)
  map.setPaintProperty('cells-line', 'line-color', selectedOrWhite)
  map.setPaintProperty('cells-line-projected', 'line-color', selectedOrWhite)
  map.setPaintProperty('track-past', 'line-color', colors.track)
  map.setPaintProperty('track-future', 'line-color', colors.track)
  map.setPaintProperty('track-points', 'circle-color', colors.track)
}

export function MapView({ children }: { children?: ReactNode }) {
  const container = useRef<HTMLDivElement>(null)
  const [map, setMap] = useState<MapLibreMap | null>(null)
  const [cursor, setCursor] = useState<{ lng: number; lat: number } | null>(null)
  const connection = useConnectionStatus()
  const health = useHealth()
  const status = useStatus().data
  const replay = useReplay().data
  const bounds = status?.bounds
  const dark = useTheme((s) => s.dark)

  useEffect(() => {
    if (map) applyMapTheme(map, dark)
  }, [map, dark])

  useEffect(() => {
    const instance = new MapLibreMap({
      container: container.current!,
      style: STYLE,
      center: [0, 0],
      zoom: 1,
      attributionControl: false,
      dragRotate: false,
      // Backend images need the sign-in header when sign-in is on.
      transformRequest: (url) => ({ url, headers: backendRequestHeaders(url) }),
    })
    instance.addControl(new NavigationControl({ showCompass: false }), 'top-left')
    instance.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-right')
    instance.on('mousemove', (event) => setCursor(event.lngLat))
    instance.on('mouseout', () => setCursor(null))
    instance.once('load', () => setMap(instance))
    return () => instance.remove()
  }, [])

  // Administrative boundaries and their names, from a file shipped with the app.
  // The map works without them: a failed load leaves the plain background.
  useEffect(() => {
    if (!map) return
    let cancelled = false
    const labels: Marker[] = []
    fetch(`${import.meta.env.BASE_URL}basemap/us-states.geojson`)
      .then((response) => (response.ok ? (response.json() as Promise<FeatureCollection>) : Promise.reject(response.status)))
      .then((data) => {
        if (cancelled) return
        map.getSource<GeoJSONSource>('boundaries')?.setData(data)
        for (const feature of data.features) {
          const centre = labelPoint(feature)
          const name = feature.properties?.name
          if (!centre || typeof name !== 'string') continue
          const element = document.createElement('div')
          element.className = 'map-area-label'
          element.textContent = name
          labels.push(new Marker({ element }).setLngLat(centre).addTo(map))
        }
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
      labels.forEach((label) => label.remove())
    }
  }, [map])

  const [west, south, east, north] = bounds ?? []
  useEffect(() => {
    if (!map || west === undefined || south === undefined || east === undefined || north === undefined) return
    const region: Bounds = [west, south, east, north]
    map.getSource<GeoJSONSource>('graticule')?.setData(graticule(region))
    map.getSource<GeoJSONSource>('region')?.setData(regionBox(region))
    // Extra room at the bottom keeps the region clear of the legend.
    let fitted = ''
    // Once the user has panned or zoomed, the view is theirs: a resize (the storm
    // panel opening, the window changing) no longer moves it.
    let userMoved = false
    const onMoveStart = (event: { originalEvent?: unknown }) => {
      if (event.originalEvent) userMoved = true
    }
    const fit = () => {
      const { clientWidth, clientHeight } = map.getContainer()
      const size = `${clientWidth}x${clientHeight}`
      // Refit only for a real change of size. Hiding the map (another page is open)
      // and showing it again must not reset where the user had zoomed to.
      if (clientWidth === 0 || clientHeight === 0 || size === fitted || (fitted && userMoved)) return
      fitted = size
      map.fitBounds([[west, south], [east, north]], {
        padding: { top: 24, bottom: 124, left: 24, right: 24 },
        duration: 0,
      })
    }
    fit()
    map.on('resize', fit)
    map.on('movestart', onMoveStart)
    return () => {
      map.off('resize', fit)
      map.off('movestart', onMoveStart)
    }
  }, [map, west, south, east, north])

  // Observed or forecast field: an image rendered by the backend, drawn under the region outline.
  const observation = useObservation().data
  const { layer, isForecast, forecastMissing, productMissing } = useDisplayedLayer()
  const forecast = useForecast().data
  const radarVisible = useUi((s) => s.radarVisible)
  const radarOpacity = useUi((s) => s.radarOpacity)
  const imageUrl = layer?.image_url
  const [imgWest, imgSouth, imgEast, imgNorth] = layer?.bounds ?? []
  useEffect(() => {
    if (!map || !imageUrl || imgWest === undefined || imgSouth === undefined || imgEast === undefined || imgNorth === undefined) return
    const coordinates: Coordinates = [
      [imgWest, imgNorth],
      [imgEast, imgNorth],
      [imgEast, imgSouth],
      [imgWest, imgSouth],
    ]
    const source = map.getSource<ImageSource>('observation')
    if (source) {
      source.updateImage({ url: imageUrl, coordinates })
      return
    }
    map.addSource('observation', { type: 'image', url: imageUrl, coordinates })
    map.addLayer(
      {
        id: 'observation',
        type: 'raster',
        source: 'observation',
        // Nearest-neighbour keeps grid cells crisp instead of implying finer detail.
        paint: { 'raster-resampling': 'nearest', 'raster-fade-duration': 0 },
      },
      'region-fill',
    )
  }, [map, imageUrl, imgWest, imgSouth, imgEast, imgNorth])

  const hasLayer = imageUrl !== undefined
  useEffect(() => {
    if (!map || !hasLayer || !map.getLayer('observation')) return
    map.setLayoutProperty('observation', 'visibility', radarVisible ? 'visible' : 'none')
    map.setPaintProperty('observation', 'raster-opacity', radarOpacity)
  }, [map, hasLayer, imageUrl, radarVisible, radarOpacity])

  // Observed lightning, drawn over the reflectivity. It is an observation, so it is
  // hidden while a forecast lead is on screen.
  const lightning = useLightning().data
  const lightningVisible = useUi((s) => s.lightningVisible)
  const lightningUrl = lightning?.image_url
  const showLightning = lightningVisible && !isForecast
  const [ltgWest, ltgSouth, ltgEast, ltgNorth] = lightning?.bounds ?? []
  useEffect(() => {
    if (!map || !lightningUrl || ltgWest === undefined || ltgSouth === undefined || ltgEast === undefined || ltgNorth === undefined) return
    const coordinates: Coordinates = [
      [ltgWest, ltgNorth],
      [ltgEast, ltgNorth],
      [ltgEast, ltgSouth],
      [ltgWest, ltgSouth],
    ]
    const source = map.getSource<ImageSource>('lightning')
    if (source) source.updateImage({ url: lightningUrl, coordinates })
    else {
      map.addSource('lightning', { type: 'image', url: lightningUrl, coordinates })
      map.addLayer(
        {
          id: 'lightning',
          type: 'raster',
          source: 'lightning',
          paint: { 'raster-resampling': 'nearest', 'raster-fade-duration': 0 },
        },
        'region-fill',
      )
    }
    map.setLayoutProperty('lightning', 'visibility', showLightning ? 'visible' : 'none')
  }, [map, lightningUrl, ltgWest, ltgSouth, ltgEast, ltgNorth, showLightning])

  // Storm cells: outlines at the lead time on screen, plus the selected cell's track.
  const cells = useStormCells().data?.cells
  const cellsVisible = useUi((s) => s.cellsVisible)
  const leadTime = useUi((s) => s.leadTime)
  const selectedTrackId = useUi((s) => s.selectedTrackId)
  useEffect(() => {
    if (!map) return
    const shown = (cellsVisible && cells ? cells : []).flatMap((track) => {
      const at = leadTime === 0 ? track.cell : track.projections.find((p) => p.lead_time_min === leadTime)
      return at ? [{ track, at }] : []
    })
    map.getSource<GeoJSONSource>('cells')?.setData({
      type: 'FeatureCollection',
      features: shown.map(({ track, at }) => ({
        type: 'Feature',
        geometry: at.polygon,
        properties: {
          track_id: track.track_id,
          selected: track.track_id === selectedTrackId,
          projected: at.lead_time_min > 0,
        },
      })),
    })

    const selected = shown.find(({ track }) => track.track_id === selectedTrackId)?.track
    const path: Feature[] = []
    if (selected) {
      const past = selected.history.map((point) => point.centroid)
      if (past.length > 1) {
        path.push({ type: 'Feature', properties: { kind: 'past' }, geometry: { type: 'LineString', coordinates: past } })
      }
      const ahead = selected.projections.map((p) => p.centroid)
      if (ahead.length > 0) {
        path.push({
          type: 'Feature',
          properties: { kind: 'future' },
          geometry: { type: 'LineString', coordinates: [selected.cell.centroid, ...ahead] },
        })
        ahead.forEach((coordinates) =>
          path.push({ type: 'Feature', properties: { kind: 'point' }, geometry: { type: 'Point', coordinates } }),
        )
      }
    }
    map.getSource<GeoJSONSource>('cell-track')?.setData({ type: 'FeatureCollection', features: path })
  }, [map, cells, cellsVisible, leadTime, selectedTrackId])

  // The area of the alert being reviewed.
  const alerts = useAlerts().data?.alerts
  const selectedAlertId = useUi((s) => s.selectedAlertId)
  useEffect(() => {
    if (!map) return
    const alert = alerts?.find((a) => a.alert_id === selectedAlertId)
    map.getSource<GeoJSONSource>('alert')?.setData({
      type: 'FeatureCollection',
      features: alert ? [{ type: 'Feature', properties: {}, geometry: alert.polygon }] : [],
    })
  }, [map, alerts, selectedAlertId])

  // Click a cell to select it; click elsewhere to clear the selection.
  useEffect(() => {
    if (!map) return
    const onClick = (event: MapMouseEvent) => {
      const hit = map.queryRenderedFeatures(event.point, { layers: ['cells-fill'] })[0]
      useUi.getState().selectTrack((hit?.properties.track_id as string | undefined) ?? null)
    }
    const pointer = () => (map.getCanvas().style.cursor = 'pointer')
    const normal = () => (map.getCanvas().style.cursor = '')
    map.on('click', onClick)
    map.on('mouseenter', 'cells-fill', pointer)
    map.on('mouseleave', 'cells-fill', normal)
    return () => {
      map.off('click', onClick)
      map.off('mouseenter', 'cells-fill', pointer)
      map.off('mouseleave', 'cells-fill', normal)
    }
  }, [map])

  const noObservation = observation !== undefined && observation.obs_time === null
  const notice = noObservation
    ? 'No radar observation for this cycle'
    : forecastMissing
      ? `No forecast for this cycle. ${forecast?.reason ?? ''} Showing the latest observation.`
      : productMissing
        ? 'The ML model has not yet seen outcomes for this lead time. Showing forecast reflectivity.'
        : null

  return (
    <div className="relative min-h-0 min-w-0 flex-1">
      {/* Inline style: MapLibre's stylesheet sets position on this element and would
          otherwise override a utility class, collapsing the map to zero height. */}
      <div ref={container} style={{ position: 'absolute', inset: 0 }} />

      {layer && <LayerPanel />}
      {layer && radarVisible && (
        <Legend layer={layer} title={LAYER_TITLES[layer.variable] ?? layer.variable} />
      )}
      {children}

      {notice ? (
        <div className="border-warn/40 bg-card/90 text-warn absolute top-3.5 left-14 flex max-w-[26rem] items-center gap-2 rounded-full border px-3 py-1 text-xs backdrop-blur">
          <CircleAlert className="size-3.5 shrink-0" />
          {notice}
        </div>
      ) : (
        isForecast &&
        layer && (
          // A forecast must never be mistaken for an observation.
          <div className="border-primary/40 bg-card/90 text-primary absolute top-3.5 left-14 flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium backdrop-blur">
            <TrendingUp className="size-3.5" />
            Forecast +{layer.lead_time_min} min · valid {utcTime(layer.valid_time)} UTC
          </div>
        )
      )}

      {cursor && (
        <div className="bg-card/90 text-muted-foreground absolute right-3 bottom-9 rounded-md border px-2 py-1 font-mono text-[0.68rem] tabular-nums backdrop-blur">
          {formatCoordinate(cursor.lat, 'N', 'S')}&nbsp;&nbsp;{formatCoordinate(cursor.lng, 'E', 'W')}
        </div>
      )}

      {connection === 'connecting' && (
        <MapNotice icon={<LoaderCircle className="size-6 animate-spin" />} title="Starting the forecast engine">
          <p className="text-muted-foreground mt-1.5 text-xs">This usually takes a few seconds.</p>
        </MapNotice>
      )}
      {connection === 'disconnected' && (
        <MapNotice icon={<ServerCrash className="size-6" />} title="Forecast engine unavailable">
          <p className="text-muted-foreground mt-1.5 text-xs leading-relaxed">
            The engine is not responding. VAJRA keeps trying to reconnect; if this persists, restart the
            application.
          </p>
          <Button variant="outline" size="sm" className="mt-4" onClick={() => health.refetch()}>
            <RefreshCw />
            Retry now
          </Button>
        </MapNotice>
      )}
      {connection === 'connected' && replay?.status === 'no_event' && (
        <MapNotice icon={<CloudOff className="size-6" />} title="No storm event loaded">
          <p className="text-muted-foreground mt-1.5 text-xs leading-relaxed">
            Add radar files to the data folder and run ingestion to build an event for replay.
          </p>
        </MapNotice>
      )}
    </div>
  )
}
