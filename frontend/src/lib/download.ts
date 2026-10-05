// Save text the app already holds as a file, through the browser's own download.
export function downloadText(filename: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

// Rows of values as CSV text. Values with a comma, quote or line break are quoted.
export function toCsv(header: string[], rows: (string | number | null)[][]): string {
  const cell = (value: string | number | null) => {
    const text = value === null ? '' : String(value)
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  return [header, ...rows].map((row) => row.map(cell).join(',')).join('\r\n') + '\r\n'
}
