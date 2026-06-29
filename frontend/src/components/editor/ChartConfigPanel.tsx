/**
 * ChartConfigPanel — slide-editor side panel for editing a deck
 * slide's chart_config (for type='chart' elements) OR table_config
 * (for type='table' elements). Opens when the user clicks a
 * Configure button on a selected chart/table element in the
 * canvas; emits onChange whenever a field changes so the parent
 * patches contentJson immediately (auto-save picks the change up
 * on its next tick).
 *
 * Schema source of truth: frontend/src/types/editor.ts
 * (ChartConfig + TableConfig). All fields optional -- the panel
 * shows the prepopulated values from deck-generation time + lets
 * the user override.
 *
 * Non-destructive contract: opening the panel never resets the
 * element's existing config; closing without changes is a no-op.
 * The panel writes the element's full config back on every
 * change (not a diff) so the parent's patching logic stays a
 * simple replace.
 *
 * June 26 2026 -- first cut. Series visibility + color pickers
 * use lightweight native controls (checkbox + <input type=color>)
 * rather than a heavyweight color-picker library; matches the
 * project's "ship the panel, polish later" rule.
 */
import { useMemo, useState } from 'react'
import { X, RefreshCw } from 'lucide-react'

import type {
  CanvasChartElement, CanvasTableElement,
  ChartConfig, TableConfig,
} from '../../types/editor'


interface ChartPanelProps {
  element: CanvasChartElement
  onChange: (config: ChartConfig) => void
  onClose: () => void
}


interface TablePanelProps {
  element: CanvasTableElement
  onChange: (config: TableConfig) => void
  onClose: () => void
}


// ─── Chart variant ─────────────────────────────────────────────────────

export function ChartConfigPanelInner({
  element, onChange, onClose,
}: ChartPanelProps): JSX.Element {
  // Working copy of the element's chart_config. We seed from the
  // prepopulated config or an empty object; every field-edit
  // patches this local state and bubbles the full config out
  // via onChange so the parent persists immediately.
  const initial = element.chart_config ?? {}
  const [cfg, setCfg] = useState<ChartConfig>(initial)

  const patch = (mut: Partial<ChartConfig>): void => {
    const next = { ...cfg, ...mut }
    setCfg(next)
    onChange(next)
  }

  const patchAxis = (
    mut: Partial<NonNullable<ChartConfig['axis']>>,
  ): void => {
    patch({ axis: { ...(cfg.axis ?? {}), ...mut } })
  }

  const patchColor = (
    mut: Partial<NonNullable<ChartConfig['color_scheme']>>,
  ): void => {
    patch({
      color_scheme: { ...(cfg.color_scheme ?? {}), ...mut },
    })
  }

  const series = cfg.series ?? []
  const setSeries = (
    idx: number, mut: Partial<NonNullable<ChartConfig['series']>[number]>,
  ): void => {
    const next = series.map(
      (s, i) => (i === idx ? { ...s, ...mut } : s))
    patch({ series: next })
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <ConfigHeader title="Chart Configuration" onClose={onClose}
        subtitle={`Element ${element.id}  ·  ${element.chartKey}`} />
      <div className="flex-1 overflow-y-auto p-3 space-y-4 text-xs">

        <Section label="Chart type">
          <select value={cfg.chart_type ?? ''}
            onChange={(e) => {
              const v = e.target.value
              const next = { ...cfg }
              if (v) {
                next.chart_type =
                  v as NonNullable<ChartConfig['chart_type']>
              } else {
                delete next.chart_type
              }
              setCfg(next); onChange(next)
            }}
            className={INPUT_CLASS}>
            <option value="">(default)</option>
            <option value="line">Line</option>
            <option value="bar">Bar</option>
            <option value="scatter">Scatter</option>
            <option value="waterfall">Waterfall</option>
            <option value="table">Table</option>
          </select>
        </Section>

        <Section label="Title">
          <input type="text" value={cfg.title ?? ''}
            onChange={(e) => {
              const v = e.target.value
              const next = { ...cfg }
              if (v) next.title = v
              else delete next.title
              setCfg(next); onChange(next)
            }}
            placeholder="Default title from renderer"
            className={INPUT_CLASS} />
        </Section>

        <Section label="Caption">
          <textarea value={cfg.caption ?? ''}
            onChange={(e) => {
              const v = e.target.value
              const next = { ...cfg }
              if (v) next.caption = v
              else delete next.caption
              setCfg(next); onChange(next)
            }}
            placeholder="Optional caption below the chart"
            rows={2}
            className={INPUT_CLASS} />
        </Section>

        <Section label="Color scheme">
          <ColorRow label="Primary"
            value={cfg.color_scheme?.primary ?? '#1D4ED8'}
            onChange={(v) => patchColor({ primary: v })} />
          <ColorRow label="Secondary"
            value={cfg.color_scheme?.secondary ?? '#059669'}
            onChange={(v) => patchColor({ secondary: v })} />
          <ColorRow label="Benchmark"
            value={cfg.color_scheme?.benchmark ?? '#B45309'}
            onChange={(v) => patchColor({ benchmark: v })} />
          <ColorRow label="Accent"
            value={cfg.color_scheme?.accent ?? '#7C3AED'}
            onChange={(v) => patchColor({ accent: v })} />
        </Section>

        <Section label="Axis">
          <LabeledInput label="X label"
            value={cfg.axis?.x_label ?? ''}
            onChange={(v) => {
              const a = { ...(cfg.axis ?? {}) }
              if (v) a.x_label = v; else delete a.x_label
              patch({ axis: a })
            }} />
          <LabeledInput label="Y label"
            value={cfg.axis?.y_label ?? ''}
            onChange={(v) => {
              const a = { ...(cfg.axis ?? {}) }
              if (v) a.y_label = v; else delete a.y_label
              patch({ axis: a })
            }} />
          <div className="grid grid-cols-2 gap-2 mt-1.5">
            <NumberInput label="X min"
              value={cfg.axis?.x_min ?? null}
              onChange={(v) => patchAxis({ x_min: v })} />
            <NumberInput label="X max"
              value={cfg.axis?.x_max ?? null}
              onChange={(v) => patchAxis({ x_max: v })} />
            <NumberInput label="Y min"
              value={cfg.axis?.y_min ?? null}
              onChange={(v) => patchAxis({ y_min: v })} />
            <NumberInput label="Y max"
              value={cfg.axis?.y_max ?? null}
              onChange={(v) => patchAxis({ y_max: v })} />
          </div>
        </Section>

        <Section label="Date range">
          <select value={cfg.date_range?.preset ?? 'full'}
            onChange={(e) => {
              const preset = e.target.value as
                'full' | 'post_2022' | 'oos_only' | 'custom'
              const dr: NonNullable<ChartConfig['date_range']> = {
                ...(cfg.date_range ?? {}), preset,
              }
              patch({ date_range: dr })
            }}
            className={INPUT_CLASS}>
            <option value="full">Full history</option>
            <option value="post_2022">Post-2022 only</option>
            <option value="oos_only">OOS window only</option>
            <option value="custom">Custom</option>
          </select>
          {cfg.date_range?.preset === 'custom' && (
            <div className="grid grid-cols-2 gap-2 mt-1.5">
              <LabeledInput label="Start"
                value={cfg.date_range?.start ?? ''}
                placeholder="YYYY-MM-DD"
                onChange={(v) => {
                  const dr = { ...(cfg.date_range ?? {}) }
                  dr.start = v || null
                  patch({ date_range: dr })
                }} />
              <LabeledInput label="End"
                value={cfg.date_range?.end ?? ''}
                placeholder="YYYY-MM-DD"
                onChange={(v) => {
                  const dr = { ...(cfg.date_range ?? {}) }
                  dr.end = v || null
                  patch({ date_range: dr })
                }} />
            </div>
          )}
        </Section>

        <Section label="Toggles">
          <Toggle label="Highlight regime breaks"
            value={cfg.highlight_regime_breaks ?? false}
            onChange={(v) =>
              patch({ highlight_regime_breaks: v })} />
          <Toggle label="Show benchmark"
            value={cfg.show_benchmark ?? false}
            onChange={(v) => patch({ show_benchmark: v })} />
        </Section>

        {series.length > 0 && (
          <Section label={`Series (${series.length})`}>
            <div className="space-y-1.5">
              {series.map((s, i) => (
                <div key={s.key} className="flex items-center
                                            gap-2 min-w-0">
                  <input type="checkbox" checked={s.visible}
                    onChange={(e) => setSeries(i, {
                      visible: e.target.checked,
                    })}
                    className="shrink-0" />
                  <input type="color"
                    value={s.color ?? '#1D4ED8'}
                    onChange={(e) => setSeries(i, {
                      color: e.target.value,
                    })}
                    className="w-6 h-6 rounded cursor-pointer
                               shrink-0" />
                  <input type="text" value={s.label}
                    onChange={(e) => setSeries(i, {
                      label: e.target.value,
                    })}
                    className={`${INPUT_CLASS} flex-1 min-w-0`}
                    title={s.key} />
                </div>
              ))}
            </div>
          </Section>
        )}
      </div>
    </div>
  )
}


// ─── Table variant ─────────────────────────────────────────────────────

export function TableConfigPanelInner({
  element, onChange, onClose,
}: TablePanelProps): JSX.Element {
  const initial = element.table_config ?? {}
  const [cfg, setCfg] = useState<TableConfig>(initial)

  const patch = (mut: Partial<TableConfig>): void => {
    const next = { ...cfg, ...mut }
    setCfg(next)
    onChange(next)
  }

  const rowsText = useMemo(
    () => (cfg.rows ?? []).map((r) =>
      Array.isArray(r) ? r.join(' | ') : String(r)).join('\n'),
    [cfg.rows])
  const columnsText = useMemo(
    () => (cfg.columns ?? []).join(', '),
    [cfg.columns])

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <ConfigHeader title="Table Configuration" onClose={onClose}
        subtitle={`Element ${element.id}`} />
      <div className="flex-1 overflow-y-auto p-3 space-y-4 text-xs">

        <Section label="Table type">
          <select value={cfg.table_type ?? 'performance'}
            onChange={(e) => {
              const next = { ...cfg }
              next.table_type =
                e.target.value as
                  NonNullable<TableConfig['table_type']>
              setCfg(next); onChange(next)
            }}
            className={INPUT_CLASS}>
            <option value="performance">Performance</option>
            <option value="correlation">Correlation</option>
            <option value="factor_loadings">Factor loadings</option>
            <option value="drawdown">Drawdown</option>
          </select>
        </Section>

        <Section label="Title">
          <input type="text" value={cfg.title ?? ''}
            onChange={(e) => {
              const v = e.target.value
              const next = { ...cfg }
              if (v) next.title = v
              else delete next.title
              setCfg(next); onChange(next)
            }}
            className={INPUT_CLASS} />
        </Section>

        <Section label="Caption">
          <textarea value={cfg.caption ?? ''}
            onChange={(e) => {
              const v = e.target.value
              const next = { ...cfg }
              if (v) next.caption = v
              else delete next.caption
              setCfg(next); onChange(next)
            }}
            rows={2}
            className={INPUT_CLASS} />
        </Section>

        <Section label={
          `Rows (${(cfg.rows ?? []).length})`
        }>
          {/* June 26 2026 -- rows preview is read-only here;
              full row selection lands in a follow-up (needs a
              strategy-cache fetch the editor doesn't have yet).
              Direct edits to the cell data still flow through
              the rows list as one-per-line. */}
          <textarea value={rowsText}
            onChange={(e) => {
              const lines = e.target.value.split('\n')
              const parsed = lines.map(
                (l) => l.split('|').map((c) => c.trim()))
              patch({ rows: parsed.filter(
                (r) => r.some((c) => c.length > 0)) })
            }}
            rows={6}
            placeholder="One row per line; cells separated by |"
            className={`${INPUT_CLASS} font-mono`} />
        </Section>

        <Section label="Columns">
          <input type="text" value={columnsText}
            onChange={(e) => {
              const cols = e.target.value
                .split(',').map((c) => c.trim())
                .filter((c) => c.length > 0)
              patch({ columns: cols })
            }}
            placeholder="comma-separated metric ids"
            className={INPUT_CLASS} />
        </Section>

        <Section label="Display">
          <Toggle label="Highlight best per column"
            value={cfg.highlight_best ?? false}
            onChange={(v) => patch({ highlight_best: v })} />
          <Toggle label="Highlight worst per column"
            value={cfg.highlight_worst ?? false}
            onChange={(v) => patch({ highlight_worst: v })} />
          <div className="flex items-center gap-2 mt-1">
            <label className="text-2xs text-muted shrink-0">
              Decimal places
            </label>
            <input type="number" min={0} max={6}
              value={cfg.decimal_places ?? 2}
              onChange={(e) => patch({
                decimal_places: Number(e.target.value),
              })}
              className={`${INPUT_CLASS} w-16`} />
          </div>
        </Section>
      </div>
    </div>
  )
}


// ─── Shared subcomponents ──────────────────────────────────────────────

const INPUT_CLASS = (
  'w-full bg-navy-800 border border-border rounded '
  + 'text-xs text-white px-1.5 py-1 '
  + 'focus:border-electric focus:outline-none'
)


function ConfigHeader({ title, subtitle, onClose }: {
  title: string
  subtitle?: string
  onClose: () => void
}): JSX.Element {
  return (
    <div className="flex items-start justify-between gap-2
                    border-b border-border px-3 py-2">
      <div className="min-w-0">
        <div className="text-white font-medium flex items-center
                        gap-1.5">
          <RefreshCw className="w-3.5 h-3.5 text-electric" />
          <span>{title}</span>
        </div>
        {subtitle && (
          <div className="text-2xs text-muted truncate mt-0.5">
            {subtitle}
          </div>
        )}
      </div>
      <button type="button" onClick={onClose}
        aria-label="Close configure panel"
        className="text-muted hover:text-white shrink-0">
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}


function Section({ label, children }: {
  label: string
  children: React.ReactNode
}): JSX.Element {
  return (
    <div>
      <div className="text-2xs text-muted uppercase
                      tracking-wide mb-1">
        {label}
      </div>
      {children}
    </div>
  )
}


function ColorRow({ label, value, onChange }: {
  label: string
  value: string
  onChange: (v: string) => void
}): JSX.Element {
  return (
    <div className="flex items-center gap-2 mb-1">
      <input type="color" value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-6 h-6 rounded cursor-pointer shrink-0" />
      <span className="text-2xs text-slate-300 w-20 shrink-0">
        {label}
      </span>
      <input type="text" value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`${INPUT_CLASS} flex-1 font-mono`} />
    </div>
  )
}


function LabeledInput({ label, value, onChange, placeholder }: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
}): JSX.Element {
  return (
    <div className="mb-1.5">
      <label className="text-2xs text-muted block mb-0.5">
        {label}
      </label>
      <input type="text" value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={INPUT_CLASS} />
    </div>
  )
}


function NumberInput({ label, value, onChange }: {
  label: string
  value: number | null
  onChange: (v: number | null) => void
}): JSX.Element {
  return (
    <div>
      <label className="text-2xs text-muted block mb-0.5">
        {label}
      </label>
      <input type="number" value={value ?? ''}
        placeholder="auto"
        onChange={(e) => {
          const v = e.target.value
          onChange(v === '' ? null : Number(v))
        }}
        className={INPUT_CLASS} />
    </div>
  )
}


function Toggle({ label, value, onChange }: {
  label: string
  value: boolean
  onChange: (v: boolean) => void
}): JSX.Element {
  return (
    <label className="flex items-center gap-2 mb-1
                      cursor-pointer">
      <input type="checkbox" checked={value}
        onChange={(e) => onChange(e.target.checked)} />
      <span className="text-2xs text-slate-300">{label}</span>
    </label>
  )
}
