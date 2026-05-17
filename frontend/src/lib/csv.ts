/**
 * csv.ts — one CSV serialiser, shared by the per-table export button and
 * the academic export package, so CSV-quoting logic is never duplicated.
 *
 * RFC-4180 quoting: a cell is wrapped in double quotes (and its own quotes
 * doubled) when it contains a comma, quote, or newline. Rows join with
 * CRLF. csvBlob() prepends a UTF-8 BOM so Excel opens it without mojibake.
 */

export type CsvCell = string | number | boolean | null | undefined

function escapeCell(v: CsvCell): string {
  const s = v == null ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** Serialise headers + rows to a CSV string (CRLF line endings). */
export function toCsv(headers: string[], rows: CsvCell[][]): string {
  return [headers, ...rows]
    .map((row) => row.map(escapeCell).join(','))
    .join('\r\n')
}

/** A UTF-8-BOM CSV Blob — Excel-safe. */
export function csvBlob(headers: string[], rows: CsvCell[][]): Blob {
  return new Blob(['﻿' + toCsv(headers, rows)], {
    type: 'text/csv;charset=utf-8;',
  })
}
