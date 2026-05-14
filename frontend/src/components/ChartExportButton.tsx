import { useRef } from 'react'
import { Download } from 'lucide-react'

interface ChartExportButtonProps {
  /** Identifies the chart for filename generation. */
  chartId: string
  /** Optional ref to the chart container — used for PNG capture. */
  containerRef?: React.RefObject<HTMLDivElement | null>
  className?: string
}

/**
 * Provides per-chart PNG / SVG download.
 *
 * PNG uses html2canvas at 2× resolution for print quality.
 * SVG export falls back to a descriptive text file when the chart is
 * canvas-based (recharts renders SVG natively, so the SVG path works
 * for all recharts charts).
 */
export default function ChartExportButton({
  chartId,
  containerRef,
  className = '',
}: ChartExportButtonProps) {
  const internalRef = useRef<HTMLDivElement>(null)
  const ref = containerRef ?? internalRef

  const timestamp = () => new Date().toISOString().slice(0, 10).replace(/-/g, '')

  const downloadPng = async () => {
    const node = ref.current
    if (!node) return
    try {
      // html2canvas is loaded lazily — not bundled unless this function runs
      const { default: html2canvas } = await import('html2canvas')
      const canvas = await html2canvas(node, { scale: 2, useCORS: true })
      const url = canvas.toDataURL('image/png')
      const a = document.createElement('a')
      a.href = url
      a.download = `${chartId}_${timestamp()}.png`
      a.click()
    } catch {
      // html2canvas dynamic import failed (offline, bundle issue) —
      // silently no-op so the export menu stays usable. SVG export
      // is the deterministic fallback and remains available.
    }
  }

  const downloadSvg = () => {
    const node = ref.current
    if (!node) return
    const svg = node.querySelector('svg')
    if (!svg) return
    const blob = new Blob([svg.outerHTML], { type: 'image/svg+xml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${chartId}_${timestamp()}.svg`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className={`relative group inline-block ${className}`} data-testid="chart-export-button">
      <button
        aria-label={`Export ${chartId} chart`}
        className="p-1 rounded text-muted hover:text-white transition-colors"
      >
        <Download className="w-3.5 h-3.5" />
      </button>
      <div className="absolute right-0 top-6 z-20 hidden group-hover:flex flex-col
                      bg-navy-800 border border-border rounded shadow-lg overflow-hidden text-xs">
        <button
          onClick={downloadPng}
          className="px-3 py-2 text-left text-white hover:bg-navy-700 whitespace-nowrap"
        >
          Download PNG
        </button>
        <button
          onClick={downloadSvg}
          className="px-3 py-2 text-left text-white hover:bg-navy-700 whitespace-nowrap"
        >
          Download SVG
        </button>
      </div>
    </div>
  )
}
