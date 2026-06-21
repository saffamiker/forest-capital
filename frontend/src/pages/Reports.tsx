/**
 * frontend/src/pages/Reports.tsx
 *
 * Reports & Deliverables screen. The page is composed of standalone
 * panels rather than a manifest-driven card grid:
 *
 *   - Submission Readiness   (ReportReadinessBanner)
 *   - Generate Documents      (DocumentGenerationPanel)
 *       The ONLY generation surface. Runs the two-pass story plan
 *       architecture for brief / deck / appendix with locked numeric
 *       anchors and the post-generation audit.
 *   - Team Activity           (TeamActivityPanel)
 *
 * The previous "Bob's Deliverables" / "Molly's Deliverables" card
 * panel was a shadow pipeline: each card called a different endpoint
 * from the canonical top panel (legacy template generators, the
 * storyboard PPTX path with no story plan, a Q&A doc that did not
 * read from story_plans.anticipated_questions). It was removed so
 * the page has exactly one generation path and no submission-time
 * confusion about which Generate button to click.
 *
 * The /reports/storyboard editor route remains -- Molly can open it
 * directly when she wants to edit a storyboard.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle, Info, FileArchive,
} from 'lucide-react'
import TeamActivityPanel from '../components/TeamActivityPanel'
import AcademicExportModal from '../components/AcademicExportModal'
import DocumentGenerationPanel from '../components/DocumentGenerationPanel'
import { ReportReadinessBanner } from '../components/ReportReadinessIndicator'
import SubmissionGuidePanel from '../components/SubmissionGuides'
import TeamGate from '../components/TeamGate'
import FloatingSectionNav from '../components/FloatingSectionNav'

export default function Reports() {
  const [error] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [guideOpen, setGuideOpen] = useState(false)

  return (
    <div className="p-4 md:p-6 space-y-6 max-w-screen-xl mx-auto">
      {/* Floating section navigator (May 25 2026) — same component
          the QA tab and Regime Analysis use. Auto-discovers the
          data-section-id markers on the major content panels below
          and surfaces a click-to-jump TOC on the right edge. */}
      <FloatingSectionNav pageKey="reports" />
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white">Reports & Deliverables</h1>
          <p className="text-sm text-muted mt-1">
            AI-drafted documents for the FNA 670 practicum. Every output is
            labelled <strong className="text-warning">AI DRAFT — REQUIRES HUMAN REVIEW</strong> —
            edit before submitting.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* Submission Guide — opens the role-relevant deliverable guide
              with its deadline countdown. */}
          <button
            type="button"
            onClick={() => setGuideOpen(true)}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm
                       font-semibold border border-electric/40 text-electric
                       hover:bg-electric/10 transition-colors"
          >
            📋 Submission Guide
          </button>
          {/* Academic Export Package — light-mode charts + CSV tables zipped
              for paper submission. A team action. */}
          <TeamGate permission="export_package"
            tooltip="Exporting the academic package is available to the project team">
            <button
              type="button"
              onClick={() => setExporting(true)}
              className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold
                         bg-electric text-white hover:bg-blue-500 transition-colors shrink-0"
            >
              <FileArchive className="w-4 h-4" />
              Export Academic Package
            </button>
          </TeamGate>
        </div>
      </div>

      {exporting && <AcademicExportModal onClose={() => setExporting(false)} />}
      {guideOpen && <SubmissionGuidePanel onClose={() => setGuideOpen(false)} />}

      {error && (
        <div className="flex items-start gap-2 px-3 py-2 rounded border border-danger/30 bg-danger/5 text-danger text-xs">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {/* Workstream C — Report readiness banner. Reads
          /api/v1/report/readiness and renders the verdict at the top
          of the generation surface so the team sees blocker state
          before clicking Generate. The DocumentGenerationPanel below
          surfaces the blocking modal when a click is intercepted by
          the gate. */}
      <div
        data-section-id="report-readiness"
        data-section-label="Submission Readiness">
        <ReportReadinessBanner />
      </div>

      {/* Generate Documents — one-click first-draft .docx / .pptx of the
          three graded deliverables, assembled server-side from real
          platform data via the two-pass story plan architecture. The
          ONLY generation surface on this page; the legacy manifest-
          driven card grid was removed because each card called a
          different endpoint from the canonical pipeline. */}
      <div
        data-section-id="document-generation"
        data-section-label="Generate Documents">
        <DocumentGenerationPanel />
      </div>

      {/* Team Activity — the evidence behind the Roles & Division-of-Labor
          deliverable and the AI-use narrative, so it leads the page.
          Independent of the deliverables manifest — renders regardless of
          whether the manifest loaded. */}
      <div
        data-section-id="team-activity"
        data-section-label="Team Activity">
        <TeamActivityPanel />
      </div>

      {/* Academic documents moved to Settings (commit 5/7). A muted info
          banner points there; the hash anchor scrolls to the section. */}
      <div className="flex items-start gap-2 px-3 py-2.5 rounded border border-border
                      bg-navy-800 text-muted text-xs">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          Project requirements and agent context documents are managed in{' '}
          <Link
            to="/settings#academic-documents"
            className="text-electric hover:underline"
          >
            Settings
          </Link>
          . Documents uploaded there are automatically injected into all AI
          agent sessions.
        </span>
      </div>
    </div>
  )
}
