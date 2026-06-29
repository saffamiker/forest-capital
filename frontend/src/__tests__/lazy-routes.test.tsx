/**
 * lazy-routes.test.tsx — Item 6 (May 23 2026 — performance audit).
 *
 * The heavy editor routes + secondary analytics routes are lazy-
 * loaded so the initial bundle does not carry their dependencies.
 * This test pins the lazy import contract — a regression that
 * eagerly imports any of these would balloon the initial bundle.
 *
 * Vitest cannot directly observe webpack/Vite chunk boundaries,
 * but it CAN verify that the imports return a Promise (the React.
 * lazy contract) by reading App.tsx as text and asserting on the
 * lazy() call sites.
 */
import { describe, it, expect } from 'vitest'
import * as fs from 'node:fs'
import * as path from 'node:path'


const APP_TSX = fs.readFileSync(
  path.resolve(__dirname, '..', 'App.tsx'),
  'utf-8')


describe('App.tsx — lazy-route contract', () => {
  const lazyRoutes = [
    'StatisticalEvidence',
    'RegimeAnalysis',
    'StoryboardEditor',
    'SectionEditor',
    'DocumentEditor',
  ]

  lazyRoutes.forEach((route) => {
    it(`${route} is lazy-imported`, () => {
      // The lazy() call site is: const X = lazy(() => import('./pages/X'))
      const pattern = new RegExp(
        `const ${route} = lazy\\(\\s*\\(\\)\\s*=>\\s*`
        + `import\\(['"][^'"]+/pages/${route}['"]\\)`)
      expect(pattern.test(APP_TSX)).toBe(true)
    })

    it(`${route} is NOT eagerly imported`, () => {
      // No top-of-file `import X from './pages/X'` line.
      const pattern = new RegExp(
        `^import ${route} from ['"]\\./pages/${route}['"]`,
        'm')
      expect(pattern.test(APP_TSX)).toBe(false)
    })
  })

  it('Suspense fallback wraps every lazy route element', () => {
    // Every <Route> that renders a lazy component must wrap its
    // element in <Suspense> — without this, React 18 throws on
    // the suspended render. A simple count check: the file has
    // one Suspense per lazy route.
    const suspenseCount = (APP_TSX.match(/<Suspense /g) || []).length
    // PR #338 retired the ReportWriter and PeerReview lazy routes;
    // five lazy-loaded pages remain (StatisticalEvidence,
    // RegimeAnalysis, StoryboardEditor, SectionEditor,
    // DocumentEditor).
    expect(suspenseCount).toBeGreaterThanOrEqual(5)
  })

  it('the cached page-load fallback exists', () => {
    expect(APP_TSX).toContain('_PageLoadingFallback')
  })
})
