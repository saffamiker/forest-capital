interface AgentConfig {
  key: string
  label: string
  color: string
}

const AGENTS: AgentConfig[] = [
  { key: 'equity',   label: 'Equity',   color: '#60a5fa' },
  { key: 'fi',       label: 'Fixed Inc', color: '#34d399' },
  { key: 'risk',     label: 'Risk Mgr',  color: '#f59e0b' },
  { key: 'quant',    label: 'Quant',     color: '#a78bfa' },
  { key: 'gemini',   label: 'Gemini',    color: '#c084fc' },
  { key: 'cio',      label: 'CIO',       color: '#3b82f6' },
]

type Sentiment = 1 | 0 | -1
type SentimentData = Record<string, Sentiment>
type SentimentMap = Record<string, SentimentData>

const MOCK_SENTIMENTS: SentimentMap = {
  BENCHMARK:          { equity: 0,  fi: 0,  risk: 0,  quant: 0,  gemini: 0,  cio: 0  },
  CLASSIC_60_40:      { equity: 0,  fi: 1,  risk: 0,  quant: 0,  gemini: -1, cio: 0  },
  RISK_PARITY:        { equity: 1,  fi: 1,  risk: 1,  quant: 1,  gemini: 0,  cio: 1  },
  MIN_VARIANCE:       { equity: 0,  fi: 1,  risk: 1,  quant: 0,  gemini: 0,  cio: 0  },
  EQUAL_WEIGHT:       { equity: 0,  fi: 0,  risk: -1, quant: -1, gemini: -1, cio: -1 },
  MOMENTUM_ROTATION:  { equity: 1,  fi: 0,  risk: 0,  quant: 1,  gemini: -1, cio: 1  },
  REGIME_SWITCHING:   { equity: 1,  fi: 1,  risk: 1,  quant: 1,  gemini: 0,  cio: 1  },
  VOL_TARGETING:      { equity: 1,  fi: 1,  risk: 1,  quant: 1,  gemini: 1,  cio: 1  },
  BLACK_LITTERMAN:    { equity: 1,  fi: 1,  risk: 1,  quant: 1,  gemini: 0,  cio: 1  },
  MAX_SHARPE_ROLLING: { equity: 1,  fi: 0,  risk: 1,  quant: 1,  gemini: -1, cio: 1  },
}

interface CellConfig {
  bg: string
  border: string
  text: string
  label: string
}

const CELL_CONFIG: Record<string, CellConfig> = {
  '1':  { bg: 'bg-success/20',  border: 'border-success/30', text: 'text-success',  label: '▲' },
  '0':  { bg: 'bg-navy-700',    border: 'border-border',     text: 'text-muted',    label: '—' },
  '-1': { bg: 'bg-danger/20',   border: 'border-danger/30',  text: 'text-danger',   label: '▼' },
}

function Cell({ value }: { value: Sentiment }) {
  const c = CELL_CONFIG[String(value)] ?? CELL_CONFIG['0']!
  return (
    <div className={`w-8 h-8 rounded border ${c.bg} ${c.border} flex items-center justify-center`}>
      <span className={`text-xs font-semibold ${c.text}`}>{c.label}</span>
    </div>
  )
}

function DivergenceScore({ sentiments }: { sentiments: SentimentData }) {
  const vals = Object.values(sentiments)
  const cio = sentiments['cio'] ?? 0
  const disagreements = vals.filter((v) => v !== cio).length
  const total = vals.length - 1
  const score = total > 0 ? disagreements / total : 0
  return (
    <div className="flex items-center gap-1">
      <div className="w-12 h-1 bg-navy-700 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{
            width: `${score * 100}%`,
            backgroundColor: score > 0.4 ? '#ef4444' : score > 0.2 ? '#f59e0b' : '#22c55e',
          }}
        />
      </div>
      <span className="text-2xs font-mono text-muted">{Math.round(score * 100)}%</span>
    </div>
  )
}

interface DisagreementHeatmapProps {
  sentiments?: SentimentMap
}

export default function DisagreementHeatmap({ sentiments = MOCK_SENTIMENTS }: DisagreementHeatmapProps) {
  const strategies = Object.keys(sentiments)

  return (
    <div className="card p-4 overflow-x-auto">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-white font-semibold text-sm">Agent Disagreement Heatmap</h3>
          <p className="text-muted text-xs mt-0.5">
            <span className="text-success">▲ Bullish</span>
            <span className="mx-2 text-muted">—  Neutral</span>
            <span className="text-danger">▼ Bearish</span>
            <span className="text-muted ml-2">— Gemini divergence from Claude consensus highlighted</span>
          </p>
        </div>
      </div>

      <table className="w-full text-left border-collapse">
        <thead>
          <tr>
            <th className="text-muted text-2xs font-medium uppercase tracking-wide pr-4 pb-2 w-36">Strategy</th>
            {AGENTS.map((a) => (
              <th key={a.key} className="pb-2 text-center w-10">
                <span
                  className="text-2xs font-semibold"
                  style={{ color: a.key === 'gemini' ? '#c084fc' : a.color }}
                >
                  {a.label}
                </span>
              </th>
            ))}
            <th className="pb-2 pl-3 text-2xs text-muted font-medium uppercase tracking-wide">Divergence</th>
          </tr>
        </thead>
        <tbody>
          {strategies.map((strat) => {
            const row = sentiments[strat] ?? {}
            const isGeminiDivergent = (row['gemini'] ?? 0) !== (row['cio'] ?? 0)
            return (
              <tr
                key={strat}
                className={`border-t border-border/50 ${isGeminiDivergent ? 'bg-purple-500/5' : ''}`}
              >
                <td className="py-1.5 pr-4">
                  <div className="flex items-center gap-1.5">
                    <span className="text-white text-2xs font-medium">
                      {strat.replace(/_/g, ' ')}
                    </span>
                    {isGeminiDivergent && (
                      <span className="text-2xs text-purple-400 border border-purple-400/20 rounded px-1 bg-purple-400/10">
                        ≠
                      </span>
                    )}
                  </div>
                </td>
                {AGENTS.map((a) => (
                  <td key={a.key} className="py-1.5 text-center">
                    <div className="flex justify-center">
                      <Cell value={(row[a.key] ?? 0) as Sentiment} />
                    </div>
                  </td>
                ))}
                <td className="py-1.5 pl-3">
                  <DivergenceScore sentiments={row} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <div className="mt-3 pt-3 border-t border-border flex items-center gap-2">
        <div className="w-2 h-2 rounded-full bg-purple-400/50" />
        <span className="text-muted text-2xs">Purple highlight = Gemini diverges from CIO recommendation</span>
      </div>
    </div>
  )
}
