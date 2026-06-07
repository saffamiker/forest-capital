/**
 * Light-mode warning/success/danger semantic-token overrides
 * (bridge #72, addressing the #70 contrast audit).
 *
 * The audit identified 13 components where text-warning on
 * bg-warning/X (and the same-family success / danger / chat-agent
 * tints) produced near-invisible content on a white background.
 * PR #72 fixes the pattern at the CSS layer -- a single block of
 * html:not(.dark) overrides in index.css catches every
 * Tailwind-class consumer in one shot, without touching the 13
 * components. DisagreementHeatmap's inline backgroundColor literal
 * (not class-based) was migrated to useChartTheme separately.
 *
 * These tests are grep-on-disk pins (same pattern as the chart
 * theming tests). They guard the index.css override block + the
 * one component edit so a future contributor cannot silently
 * regress the contrast fix.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'

function source(rel: string): string {
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

describe('light-mode warning token overrides -- bridge #72', () => {
  const css = source('src/index.css')

  describe('index.css foreground accent overrides land on the right hex', () => {
    it('text-warning -> amber-700 (#b45309)', () => {
      expect(css).toContain('.text-warning')
      expect(css).toMatch(/text-warning\s*\{\s*color:\s*#b45309/i)
    })
    it('text-success -> green-800 (#166534)', () => {
      expect(css).toMatch(/text-success\s*\{\s*color:\s*#166534/i)
    })
    it('text-danger -> red-800 (#991b1b)', () => {
      expect(css).toMatch(/text-danger\s*\{\s*color:\s*#991b1b/i)
    })
    it('text-positive aliases text-success in light mode', () => {
      expect(css).toMatch(/text-positive\s*\{\s*color:\s*#166534/i)
    })
    it('text-negative aliases text-danger in light mode', () => {
      expect(css).toMatch(/text-negative\s*\{\s*color:\s*#991b1b/i)
    })
  })

  describe('index.css contains the warning background opacity variants', () => {
    it('bg-warning/5 flips to amber-50', () => {
      expect(css).toContain(String.raw`.bg-warning\/5`)
      expect(css).toMatch(/bg-warning\\\/5\s*\{\s*background-color:\s*#fffbeb/i)
    })
    it('bg-warning/10 flips to amber-100', () => {
      expect(css).toMatch(/bg-warning\\\/10\s*\{\s*background-color:\s*#fef3c7/i)
    })
    it('bg-warning/15 flips to amber-200', () => {
      expect(css).toMatch(/bg-warning\\\/15\s*\{\s*background-color:\s*#fde68a/i)
    })
    it('bg-warning/20 flips to amber-300', () => {
      expect(css).toMatch(/bg-warning\\\/20\s*\{\s*background-color:\s*#fcd34d/i)
    })
  })

  describe('index.css contains the success / danger background variants', () => {
    it('bg-success/10 flips to green-100', () => {
      expect(css).toMatch(/bg-success\\\/10\s*\{\s*background-color:\s*#dcfce7/i)
    })
    it('bg-danger/10 flips to red-100', () => {
      expect(css).toMatch(/bg-danger\\\/10\s*\{\s*background-color:\s*#fee2e2/i)
    })
  })

  describe('index.css contains direct shade overrides for audit findings', () => {
    it('text-amber-200 flips to amber-700', () => {
      expect(css).toContain('.text-amber-200')
      // The amber-100/200/300 share a single declaration block.
      expect(css).toMatch(
        /text-amber-(100|200|300)[^}]*color:\s*#b45309/i,
      )
    })
    it('text-orange-300 flips to orange-700 (#c2410c)', () => {
      expect(css).toMatch(/text-orange-300[^}]*color:\s*#c2410c/i)
    })
    it('text-emerald-400 flips to green-800', () => {
      expect(css).toMatch(/text-emerald-400\s*\{\s*color:\s*#166534/i)
    })
  })

  describe('index.css contains the chat agent chip overrides', () => {
    it('bg-amber-400/5 -> amber-50 (Risk Manager chip)', () => {
      expect(css).toContain(String.raw`.bg-amber-400\/5`)
      expect(css).toMatch(/bg-amber-400\\\/5[^}]*background-color:\s*#fffbeb/i)
    })
    it('bg-blue-400/5 -> blue-50 (CIO chip)', () => {
      expect(css).toMatch(/bg-blue-400\\\/5\s*\{\s*background-color:\s*#eff6ff/i)
    })
    it('bg-emerald-400/5 -> green-50 (Fixed Income chip)', () => {
      expect(css).toMatch(/bg-emerald-400\\\/5\s*\{\s*background-color:\s*#f0fdf4/i)
    })
    it('bg-violet-400/5 -> violet-50 (Quant chip)', () => {
      expect(css).toMatch(/bg-violet-400\\\/5\s*\{\s*background-color:\s*#faf5ff/i)
    })
  })

  describe('index.css contains the warning/success border overrides', () => {
    it('border-warning/30 keeps the alert tile outlined', () => {
      expect(css).toContain(String.raw`.border-warning\/30`)
    })
    it('border-warning/40 keeps the alert tile outlined', () => {
      expect(css).toContain(String.raw`.border-warning\/40`)
    })
    it('border-success/30 keeps the success tile outlined', () => {
      expect(css).toContain(String.raw`.border-success\/30`)
    })
  })

  describe('DisagreementHeatmap inline divergence colors are theme-aware', () => {
    const heatmap = source('src/components/DisagreementHeatmap.tsx')

    it('imports useChartTheme', () => {
      expect(heatmap).toMatch(
        /import\s*\{\s*useChartTheme\s*\}\s*from\s*['"]\.\.\/lib\/useChartTheme['"]/,
      )
    })

    it('switches the divergence-score bar color by chartTheme.mode', () => {
      // Light branch uses red-700 / amber-700 / green-800; dark uses
      // the saturated -500 family.
      expect(heatmap).toContain('#b91c1c') // red-700 in light
      expect(heatmap).toContain('#b45309') // amber-700 in light
      expect(heatmap).toContain('#166534') // green-800 in light
      // The dark-mode fallbacks remain so the bar still pops on navy.
      expect(heatmap).toContain('#ef4444') // red-500
      expect(heatmap).toContain('#f59e0b') // amber-500
      expect(heatmap).toContain('#22c55e') // green-500
    })

    it('the pre-fix single-literal bar style is gone', () => {
      // The previous code wrote
      //   backgroundColor: score > 0.4 ? '#ef4444' : ...
      // -- on a single line with no theme switch. After the fix the
      // bar color comes from a variable computed from chartTheme.mode
      // so this exact ternary should NOT appear.
      expect(heatmap).not.toMatch(
        /backgroundColor:\s*score\s*>\s*0\.4\s*\?\s*'#ef4444'/,
      )
    })
  })
})


// ── Bridge #83: warning banner subtext + Report Writer card body ─────

describe('light-mode banner subtext + text-text-* alias overrides -- bridge #83', () => {
  const css = source('src/index.css')

  describe('amber opacity variants flip to amber-700 (banner subtext)', () => {
    it('text-amber-200/80 reads dark on light banner backgrounds', () => {
      expect(css).toContain(String.raw`.text-amber-200\/80`)
      expect(css).toMatch(
        /text-amber-200\\\/80[^}]*color:\s*#b45309/i,
      )
    })

    it('the other amber-200 opacity variants share the override', () => {
      expect(css).toContain(String.raw`.text-amber-200\/70`)
      expect(css).toContain(String.raw`.text-amber-200\/60`)
    })
  })

  describe('text-text-* aliases route through the CSS variable system', () => {
    it('text-text-secondary uses --text-secondary on light', () => {
      expect(css).toContain('.text-text-secondary')
      expect(css).toMatch(
        /text-text-secondary\s*\{\s*color:\s*var\(--text-secondary\)/i,
      )
    })

    it('text-text-primary uses --text-primary on light', () => {
      expect(css).toMatch(
        /text-text-primary\s*\{\s*color:\s*var\(--text-primary\)/i,
      )
    })

    it('text-text-muted uses --text-muted on light', () => {
      expect(css).toMatch(
        /text-text-muted\s*\{\s*color:\s*var\(--text-muted\)/i,
      )
    })
  })

  describe('Reports.tsx Report Writer card body has no BOB callout copy', () => {
    const reports = source('src/pages/Reports.tsx')

    it('removes the [BOB] callout phrase from the description', () => {
      expect(reports).not.toMatch(/resolve every \[BOB\] callout/i)
    })

    it('still describes the eleven-step verified-data flow', () => {
      // The copy moves to "outstanding placeholder" -- generic phrasing
      // that survives the BOB callout removal without losing the user's
      // sense of what the eleven-step flow does.
      expect(reports).toMatch(/eleven-step verified-data flow/i)
      expect(reports).toMatch(/outstanding placeholder/i)
    })

    it('keeps the description on the text-text-secondary token', () => {
      // After bridge #83's index.css override, the token reads dark on
      // light AND light on dark -- single source of truth, no per-page
      // class swap needed.
      expect(reports).toMatch(
        /text-text-secondary[^>]*>\s*\n?\s*The eleven-step/,
      )
    })
  })

  describe('ReportReadinessIndicator banner subtext is on text-amber-200/80', () => {
    const indicator = source('src/components/ReportReadinessIndicator.tsx')

    it('still uses the text-amber-200/80 class (override-based fix)', () => {
      // The fix lives in CSS, not in this component -- the class itself
      // is the right semantic choice (amber subtext on amber banner).
      // The override forces the colour to amber-700 on light.
      expect(indicator).toContain('text-amber-200/80')
    })

    it('keeps the "Acknowledge, mark intentional, or revoke" wording', () => {
      expect(indicator).toMatch(/Acknowledge, mark intentional, or revoke/)
    })
  })
})
