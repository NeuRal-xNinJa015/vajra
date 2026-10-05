// All times in VAJRA are UTC; these read straight from the ISO string so the
// viewer's local timezone never shifts them.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function utcTime(iso: string): string {
  return iso.slice(11, 16)
}

export function utcTimeSeconds(iso: string): string {
  return iso.slice(11, 19)
}

export function utcDate(iso: string): string {
  return `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`
}

const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']

export function compass(bearingDeg: number): string {
  return COMPASS[Math.round(bearingDeg / 22.5) % 16]
}

// "T007" is stored; "T7" is easier to read on the map and in lists.
export function trackLabel(trackId: string): string {
  return `T${Number(trackId.slice(1))}`
}

export function duration(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  const rest = Math.round(minutes % 60)
  return hours > 0 ? `${hours} h ${rest} min` : `${rest} min`
}

export function titleCase(name: string): string {
  return name
    .toLowerCase()
    .split(/[_\s]+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}
