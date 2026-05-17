import { Download } from 'lucide-react'
import { trackExport } from '../lib/activityLogger'
import { toCsv } from '../lib/csv'

interface TableExportButtonProps {
  /** Used for the downloaded filename. */
  tableId: string
  /** Column headers, in order. */
  headers: string[]
  /** Row data — each row is an array of values matching headers. */
  rows: (string | number | boolean | null | undefined)[][]
  className?: string
}

/**
 * Exports a data table to CSV or Excel-compatible format.
 *
 * Values are coerced to strings; nulls and undefineds become empty cells.
 * CSV uses UTF-8 with BOM so Excel opens it correctly without encoding
 * issues on Windows.
 */
export default function TableExportButton({
  tableId,
  headers,
  rows,
  className = '',
}: TableExportButtonProps) {
  const timestamp = () => new Date().toISOString().slice(0, 10).replace(/-/g, '')

  const downloadCsv = () => {
    // UTF-8 BOM ensures Excel reads the file correctly
    const blob = new Blob(['﻿' + toCsv(headers, rows)], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${tableId}_${timestamp()}.csv`
    a.click()
    URL.revokeObjectURL(url)
    trackExport(`table:${tableId}`, { format: 'csv', rows: rows.length })
  }

  return (
    <button
      onClick={downloadCsv}
      aria-label={`Export ${tableId} table as CSV`}
      className={`flex items-center gap-1 text-xs text-muted hover:text-white
                  transition-colors p-1 rounded ${className}`}
      data-testid="table-export-button"
    >
      <Download className="w-3.5 h-3.5" />
      <span>CSV</span>
    </button>
  )
}
