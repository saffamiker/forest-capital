/**
 * Light-mode chart theming pins (bridge #64).
 *
 * The audit identified 7 chart surfaces that hardcoded dark colors in
 * Recharts contentStyle props or SVG fill/stroke attributes, bypassing
 * the CSS-variable fallback that PR #279 set up. The fix wires each
 * through useChartTheme() so the styling flips with the theme.
 *
 * These tests are deliberately READING THE SOURCE FILES rather than
 * rendering the components: most of the components in question pull
 * data via axios + zustand stores and require extensive mocking to
 * render at all, and the bug we are pinning is "the file still contains
 * a hardcoded value that wins over CSS." A grep-on-disk assertion is
 * the cheapest reliable guard against a future contributor reverting
 * one of these edits.
 *
 * If a file is renamed or moved the test will fail explicitly so it
 * can be re-pointed -- it never silently passes against the wrong file.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'

function source(rel: string): string {
  // The component path is taken relative to the frontend root; the
  // test runs from frontend/, so __dirname is frontend/src/__tests__.
  const root = resolve(__dirname, '..', '..')
  const path = resolve(root, rel)
  if (!existsSync(path)) {
    throw new Error(
      `Expected source file missing: ${rel}. `
      + 'A rename or move requires updating this test.',
    )
  }
  return readFileSync(path, 'utf8')
}

const HIGH_IMPACT = [
  'src/pages/PerformanceRecord.tsx',
  'src/components/ForwardConfidenceChart.tsx',
  'src/components/Dashboard.tsx',
  'src/components/AdvisorPanel.tsx',
]

const MEDIUM_IMPACT = [
  'src/components/charts/FactorExposureHeatmap.tsx',
  'src/components/charts/CPCVSharpePlot.tsx',
  'src/components/charts/ProbabilisticSharpeChart.tsx',
  'src/components/charts/RegimeTransitionMatrix.tsx',
  // bridge #67 -- the CV stability radar was the dark-grey-card
  // outlier still showing in light mode after PR #289. Same fix
  // pattern: import useChartTheme, swap inline SVG strokes + the
  // bg-navy-800/60 card class for chartTheme-driven values.
  'src/components/charts/CVStabilityRadar.tsx',
  // bridge #69 -- Performance Attribution Waterfall used the same
  // bg-navy-800/60 grid-card pattern + hardcoded SVG strokes and
  // text fills. Same migration as #67.
  'src/components/charts/PerformanceAttributionWaterfall.tsx',
]

describe('light-mode chart theming -- bridge #64 audit', () => {
  it.each([...HIGH_IMPACT, ...MEDIUM_IMPACT])(
    'imports useChartTheme: %s', (rel) => {
      const src = source(rel)
      expect(src).toMatch(
        /import\s*\{\s*useChartTheme\s*\}\s*from\s*['"](\.\.\/)+lib\/useChartTheme['"]/,
      )
    },
  )

  describe('high-impact tooltips no longer hardcode #1a2438 or #0d1424', () => {
    it.each(HIGH_IMPACT)('no hardcoded navy tooltip in %s', (rel) => {
      const src = source(rel)
      // The four offenders all used one of these two navy hexes in
      // an inline tooltip background. After the fix the value comes
      // from chartTheme.tooltipContentStyle or chartTheme.background.
      expect(src).not.toMatch(/background\s*:\s*['"]#1a2438['"]/i)
      expect(src).not.toMatch(/backgroundColor\s*:\s*['"]#1a2438['"]/i)
      expect(src).not.toMatch(/backgroundColor\s*:\s*['"]#0d1424['"]/i)
    })
  })

  describe('SVG annotations no longer hardcode #f9fafb stroke', () => {
    it('CPCVSharpePlot median line uses chartTheme.textPrimary', () => {
      const src = source('src/components/charts/CPCVSharpePlot.tsx')
      expect(src).toContain('stroke={chartTheme.textPrimary}')
      // The exact pattern that was broken before -- a hardcoded
      // stroke literal on the median line -- must be gone.
      expect(src).not.toMatch(/stroke="#f9fafb"\s+strokeWidth=\{2\}/)
    })

    it('ProbabilisticSharpeChart point estimate uses chartTheme.textPrimary', () => {
      const src = source(
        'src/components/charts/ProbabilisticSharpeChart.tsx')
      expect(src).toContain('stroke={chartTheme.textPrimary}')
      expect(src).not.toMatch(/stroke="#f9fafb"\s+strokeWidth=\{1\.5\}/)
    })
  })

  describe('heatmap cells use luminance-aware text colour', () => {
    it('FactorExposureHeatmap routes text through cellTextColour', () => {
      const src = source(
        'src/components/charts/FactorExposureHeatmap.tsx')
      expect(src).toContain('cellTextColour(')
      // The previous hardcoded white literal must be gone from the
      // inline style on the heatmap cell.
      expect(src).not.toMatch(
        /background:\s*cellColor\([^)]+\),\s*color:\s*'#f9fafb'/,
      )
    })

    it('RegimeTransitionMatrix routes text through cellTextColour', () => {
      const src = source(
        'src/components/charts/RegimeTransitionMatrix.tsx')
      expect(src).toContain('cellTextColour(')
      expect(src).not.toMatch(
        /background:\s*cellColor\([^)]+\),\s*\n\s*color:\s*'#f9fafb'/,
      )
    })
  })

  describe('CV stability radar -- bridge #67', () => {
    const path = 'src/components/charts/CVStabilityRadar.tsx'

    it('no longer carries bg-navy-800/60 on the radar card', () => {
      const src = source(path)
      expect(src).not.toMatch(/bg-navy-800\/60/)
    })

    it('SVG strokes use chartTheme.gridStroke not the #1e3a5c literal', () => {
      const src = source(path)
      expect(src).toContain('stroke={chartTheme.gridStroke}')
      expect(src).not.toMatch(/stroke="#1e3a5c"/)
    })

    it('SVG axis labels use chartTheme.textSecondary not the #64748b literal', () => {
      const src = source(path)
      expect(src).toContain('fill={chartTheme.textSecondary}')
      // The badge-color logic at the top of RadarSmall still
      // references '#64748b' as the STATIC strategy badge tint --
      // that is intentional (functional UI color, not theme-driven)
      // -- so we cannot just grep the file for absence of the hex.
      // The pin specifically targets the SVG axis-label fill line.
      expect(src).not.toMatch(/fill="#64748b"\s+fontSize/)
    })
  })

  describe('Performance Attribution Waterfall -- bridge #69', () => {
    const path = 'src/components/charts/PerformanceAttributionWaterfall.tsx'

    it('no longer carries bg-navy-800/60 on the waterfall card', () => {
      const src = source(path)
      expect(src).not.toMatch(/bg-navy-800\/60/)
    })

    it('SVG zero-line uses chartTheme.gridStroke', () => {
      const src = source(path)
      expect(src).toContain('stroke={chartTheme.gridStroke}')
      expect(src).not.toMatch(/stroke="#1e3a5c"/)
    })

    it('SVG axis labels + value labels use chartTheme.textSecondary / textPrimary', () => {
      const src = source(path)
      expect(src).toContain('fill={chartTheme.textSecondary}')
      expect(src).toContain('fill={chartTheme.textPrimary}')
      expect(src).not.toMatch(/fill="#64748b"\s+fontSize="9"/)
      expect(src).not.toMatch(/fill="#cbd5e1"\s+fontSize="8"/)
    })
  })

  it('uses theme.tooltipContentStyle on at least one tooltip per high-impact file', () => {
    // Sanity rollup -- every high-impact file should reference the
    // theme-driven tooltip style at least once after the migration.
    for (const rel of HIGH_IMPACT) {
      const src = source(rel)
      const hasTooltipStyle = src.includes('chartTheme.tooltipContentStyle')
      const hasBgFromTheme = src.includes(
        'chartTheme.tooltipContentStyle.backgroundColor')
      expect(
        hasTooltipStyle || hasBgFromTheme,
        `${rel} does not reference chartTheme.tooltipContentStyle`,
      ).toBe(true)
    }
  })
})
