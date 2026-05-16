/**
 * Settings — a single scrollable page (no tabs) with five sections:
 *   1. Organisation            — reporting-context / brand switcher
 *   2. Data and Study Period   — read-only data-table status
 *   3. Analytics Configuration — the risk-free rate assumption
 *   4. Academic Documents      — agent-context document upload
 *   5. Account                 — signed-in email + sign out
 *
 * Reached from the nav-ribbon gear icon (route /settings). The Academic
 * Documents section carries id="academic-documents" so /settings#academic-documents
 * deep-links straight to it.
 */
import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

interface SettingsSectionProps {
  id: string
  title: string
  description: string
  children: React.ReactNode
}

function SettingsSection({ id, title, description, children }: SettingsSectionProps) {
  // scroll-mt keeps the heading clear of the fixed 56px nav bar when a
  // hash anchor scrolls the section to the top of the viewport.
  return (
    <section id={id} className="scroll-mt-20">
      <h2 className="text-base font-semibold text-white">{title}</h2>
      <p className="text-xs text-muted mt-0.5">{description}</p>
      <div className="border-t border-border mt-3 pt-4">{children}</div>
    </section>
  )
}

const Placeholder = ({ children }: { children: React.ReactNode }) => (
  <p className="text-xs text-muted italic">{children}</p>
)

export default function Settings() {
  const location = useLocation()

  // Deep-link support — /settings#academic-documents scrolls to that section.
  useEffect(() => {
    if (!location.hash) return
    const el = document.getElementById(location.hash.slice(1))
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [location.hash])

  return (
    <div className="p-4 md:p-6 max-w-screen-md mx-auto space-y-8">
      <div>
        <h1 className="text-xl font-semibold text-white">Settings</h1>
        <p className="text-sm text-muted mt-1">
          Reporting context, data status, analytics assumptions, agent-context
          documents, and account.
        </p>
      </div>

      <SettingsSection
        id="organisation"
        title="Organisation"
        description="Select the reporting context for this session."
      >
        <Placeholder>Organisation settings.</Placeholder>
      </SettingsSection>

      <SettingsSection
        id="data-study-period"
        title="Data and Study Period"
        description="Read-only status of the data tables feeding the analytics layer."
      >
        <Placeholder>Data status.</Placeholder>
      </SettingsSection>

      <SettingsSection
        id="analytics-configuration"
        title="Analytics Configuration"
        description="Assumptions applied across all analytics and the efficient frontier."
      >
        <Placeholder>Analytics configuration.</Placeholder>
      </SettingsSection>

      <SettingsSection
        id="academic-documents"
        title="Academic Documents"
        description="Documents uploaded here are injected into every AI agent session."
      >
        <Placeholder>Academic documents.</Placeholder>
      </SettingsSection>

      <SettingsSection
        id="account"
        title="Account"
        description="The account signed in to this session."
      >
        <Placeholder>Account.</Placeholder>
      </SettingsSection>
    </div>
  )
}
