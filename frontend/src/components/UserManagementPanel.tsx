/**
 * UserManagementPanel — the Settings → Users section (sysadmin only).
 *
 * Lists every platform user and lets the sysadmin add, edit and
 * deactivate them. Access is permission-based: a role preset seeds the
 * permissions checklist, but every permission is individually editable —
 * a user whose permissions diverge from their role's preset is shown as
 * "Custom".
 */
import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import { UserPlus, Loader2, AlertCircle, X, CheckCircle } from 'lucide-react'
import {
  PERMISSIONS, ROLE_PRESETS, ASSIGNABLE_ROLES, matchesPreset,
} from '../constants/permissions'

interface PlatformUser {
  id: number
  email: string
  display_name: string | null
  role: string
  permissions: string[]
  is_active: boolean
  created_at: string | null
  created_by: string | null
  last_login_at: string | null
  notes: string | null
  activity_count: number
  // Lifetime council-query allocation. council_queries_limit null = unlimited.
  council_queries_used: number
  council_queries_limit: number | null
}

const ROLE_BADGE: Record<string, string> = {
  sysadmin: 'bg-warning/15 text-warning border-warning/30',
  team_member: 'bg-electric/15 text-electric border-electric/30',
  viewer: 'bg-navy-700 text-muted border-border',
}

function relativeTime(iso: string | null): string {
  if (!iso) return 'Never'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'Never'
  const mins = Math.round((Date.now() - then) / 60000)
  if (mins < 1) return 'Just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

// A confirmation surfaced after a save — e.g. whether the welcome email
// sent on user creation actually went out.
interface SaveNotice {
  kind: 'success' | 'warning'
  text: string
}

// ── Add / Edit form modal ─────────────────────────────────────────────────────

function UserFormModal({ user, onClose, onSaved }: {
  user: PlatformUser | null   // null → Add; set → Edit
  onClose: () => void
  onSaved: (notice?: SaveNotice) => void
}) {
  const editing = user !== null
  const [email, setEmail] = useState(user?.email ?? '')
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [role, setRole] = useState(
    user && user.role !== 'sysadmin' ? user.role : 'viewer')
  const [perms, setPerms] = useState<string[]>(
    user?.permissions ?? ROLE_PRESETS.viewer)
  const [notes, setNotes] = useState(user?.notes ?? '')
  // Council query allocation — Unlimited (limit null), the limit value,
  // and a one-shot "reset usage to 0" applied on save.
  const [councilUnlimited, setCouncilUnlimited] = useState(
    user ? user.council_queries_limit == null : false)
  const [councilLimit, setCouncilLimit] = useState(
    String(user?.council_queries_limit ?? 5))
  const [resetUsage, setResetUsage] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // A sysadmin user is editable but the role preset list omits sysadmin.
  const effectiveRole = user?.role === 'sysadmin' ? 'sysadmin' : role
  const custom = !matchesPreset(effectiveRole, perms)

  const applyPreset = (r: string) => {
    setRole(r)
    setPerms([...(ROLE_PRESETS[r] ?? [])])
  }
  const togglePerm = (key: string) => {
    setPerms((p) => (p.includes(key) ? p.filter((x) => x !== key) : [...p, key]))
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      if (editing) {
        const patch: Record<string, unknown> = {
          display_name: displayName, role: effectiveRole,
          permissions: perms, notes,
        }
        // Council allocation — null when Unlimited, else the parsed limit.
        patch.council_queries_limit = councilUnlimited
          ? null : Math.max(0, parseInt(councilLimit, 10) || 0)
        if (resetUsage) patch.council_queries_used = 0
        await axios.patch(`/api/v1/admin/users/${user.id}`, patch)
        onSaved()
      } else {
        const res = await axios.post<{ welcome_email_sent?: boolean }>(
          '/api/v1/admin/users',
          { email, display_name: displayName, role, permissions: perms, notes },
        )
        // The welcome email is sent fail-open server-side — surface
        // whether it actually went out so the sysadmin knows.
        const notice: SaveNotice = res.data.welcome_email_sent
          ? { kind: 'success',
              text: `User added and welcome email sent to ${email}.` }
          : { kind: 'warning',
              text: 'User added. Welcome email could not be sent — '
                + 'check email configuration.' }
        onSaved(notice)
      }
    } catch (err) {
      setError(axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Save failed')
      setBusy(false)
    }
  }

  const emailValid = editing || /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())

  return (
    <div className="fixed inset-0 z-[95] flex items-center justify-center
                    bg-black/60 p-4" role="presentation" onClick={onClose}>
      <div role="dialog" aria-label={editing ? 'Edit user' : 'Add user'}
           onClick={(e) => e.stopPropagation()}
           className="w-full max-w-md max-h-[88vh] flex flex-col rounded-lg
                      border border-border bg-navy-800 shadow-2xl">
        <header className="flex items-center justify-between px-4 py-3
                           border-b border-border shrink-0">
          <h2 className="text-sm font-semibold text-white">
            {editing ? `Edit ${user.display_name || user.email}` : 'Add User'}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close"
                  className="text-muted hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          <Field label="Email address" required>
            <input value={email} disabled={editing}
              onChange={(e) => setEmail(e.target.value)}
              className={inputCls + (editing ? ' opacity-60' : '')} />
          </Field>
          <Field label="Display name">
            <input value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className={inputCls} />
          </Field>

          <Field label={`Role preset${custom ? ` — Custom (based on `
            + `${effectiveRole === 'sysadmin' ? 'Sysadmin'
              : effectiveRole === 'team_member' ? 'Team Member' : 'Viewer'})` : ''}`}>
            {user?.role === 'sysadmin' ? (
              <div className="text-xs text-warning">
                Sysadmin — role assigned via migration, not editable here.
              </div>
            ) : (
              <select value={role} onChange={(e) => applyPreset(e.target.value)}
                className={inputCls}>
                {ASSIGNABLE_ROLES.map((r) => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
            )}
          </Field>

          <Field label="Permissions">
            <div className="space-y-1">
              {PERMISSIONS.map((p) => (
                <label key={p.key}
                  className={`flex items-center gap-2 text-xs ${
                    p.sysadminOnly ? 'text-muted' : 'text-slate-200'}`}>
                  <input type="checkbox" checked={perms.includes(p.key)}
                    disabled={p.sysadminOnly}
                    onChange={() => togglePerm(p.key)} />
                  {p.label}
                  {p.sysadminOnly && (
                    <span className="text-2xs text-muted">
                      (sysadmin only)
                    </span>
                  )}
                </label>
              ))}
            </div>
          </Field>

          <Field label="Notes">
            <input value={notes} onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Forest Capital review access"
              className={inputCls} />
          </Field>

          {/* Council query allocation — viewers have a finite lifetime
              allowance; team members and sysadmins are unlimited. */}
          {editing && (
            <Field label="Council query allocation">
              {effectiveRole === 'viewer' && !councilUnlimited ? (
                <div className="space-y-2">
                  <div className="text-2xs text-muted">
                    {user.council_queries_used} of{' '}
                    {user.council_queries_limit ?? '∞'} used so far
                    {resetUsage && ' — will reset to 0 on save'}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-2xs text-muted">Query limit</span>
                    <input type="number" min={0} value={councilLimit}
                      onChange={(e) => setCouncilLimit(e.target.value)}
                      className={inputCls + ' w-24'} />
                  </div>
                  <label className="flex items-center gap-2 text-xs text-slate-200">
                    <input type="checkbox" checked={resetUsage}
                      onChange={(e) => setResetUsage(e.target.checked)} />
                    Reset usage count to 0
                  </label>
                  <label className="flex items-center gap-2 text-xs text-slate-200">
                    <input type="checkbox" checked={councilUnlimited}
                      onChange={(e) => setCouncilUnlimited(e.target.checked)} />
                    Grant unlimited council access
                  </label>
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="text-xs text-electric">
                    Unlimited council access — no query limit.
                  </div>
                  {effectiveRole === 'viewer' && (
                    <label className="flex items-center gap-2 text-xs text-slate-200">
                      <input type="checkbox" checked={councilUnlimited}
                        onChange={(e) => setCouncilUnlimited(e.target.checked)} />
                      Unlimited (uncheck to set a finite limit)
                    </label>
                  )}
                </div>
              )}
            </Field>
          )}

          <div className="rounded border border-border bg-navy-900 px-2.5 py-2
                          text-2xs text-muted leading-relaxed">
            <strong className="text-slate-300">Viewer</strong> — explore all
            analytics and ask the council.{' '}
            <strong className="text-slate-300">Team Member</strong> — full
            access including document upload, Academic Review, document
            generation and guided testing.
          </div>

          {error && <div className="text-2xs text-danger">{error}</div>}
        </div>

        <footer className="px-4 py-3 border-t border-border shrink-0
                           flex justify-end gap-2">
          <button type="button" onClick={onClose}
            className="px-3 py-1.5 text-xs text-muted hover:text-white">
            Cancel
          </button>
          <button type="button" onClick={() => void submit()}
            disabled={busy || !emailValid}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded text-xs
                       font-medium bg-electric text-white hover:bg-blue-500
                       disabled:opacity-50 disabled:cursor-not-allowed">
            {busy && <Loader2 className="w-3 h-3 animate-spin" />}
            {editing ? 'Save Changes' : 'Add User'}
          </button>
        </footer>
      </div>
    </div>
  )
}

const inputCls = 'w-full rounded border border-border bg-navy-900 px-2 py-1.5 '
  + 'text-xs text-white focus:border-electric focus:outline-none'

function Field({ label, required, children }: {
  label: string; required?: boolean; children: React.ReactNode
}) {
  return (
    <div>
      <label className="text-2xs uppercase tracking-wide text-muted">
        {label}{required && <span className="text-danger"> *</span>}
      </label>
      <div className="mt-1">{children}</div>
    </div>
  )
}

// ── Panel ─────────────────────────────────────────────────────────────────────

export default function UserManagementPanel() {
  const [users, setUsers] = useState<PlatformUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<SaveNotice | null>(null)
  const [adding, setAdding] = useState(false)
  const [editingUser, setEditingUser] = useState<PlatformUser | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    axios.get<{ users: PlatformUser[] }>('/api/v1/admin/users')
      .then((res) => { setUsers(res.data.users ?? []); setError(null) })
      .catch((err) => setError(axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message) : 'Failed to load users'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  const deactivate = async (u: PlatformUser) => {
    if (!window.confirm(
      `Deactivate ${u.display_name || u.email}? `
      + 'They will no longer be able to log in.')) return
    try {
      await axios.delete(`/api/v1/admin/users/${u.id}`)
      load()
    } catch (err) {
      setError(axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message) : 'Deactivate failed')
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs text-muted">
          Manage who can access this platform and their permission level.
        </p>
        <button type="button" onClick={() => setAdding(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs
                     font-medium bg-electric text-white hover:bg-blue-500
                     transition-colors shrink-0">
          <UserPlus className="w-3.5 h-3.5" />
          Add User
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 px-3 py-2 mb-2 rounded border
                        border-danger/30 bg-danger/5 text-danger text-xs">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {notice && (
        <div className={`flex items-start gap-2 px-3 py-2 mb-2 rounded border
                        text-xs ${notice.kind === 'success'
            ? 'border-success/30 bg-success/5 text-success'
            : 'border-warning/30 bg-warning/5 text-warning'}`}>
          {notice.kind === 'success'
            ? <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            : <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />}
          <span>{notice.text}</span>
        </div>
      )}

      {loading ? (
        <p className="text-xs text-muted flex items-center gap-1.5">
          <Loader2 className="w-3 h-3 animate-spin" /> Loading users…</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-2xs uppercase
                             tracking-wide text-muted text-left">
                <th className="py-1.5 pr-2 sticky left-0 z-10 bg-navy-900">Name / Email</th>
                <th className="py-1.5 px-2">Role</th>
                <th className="py-1.5 px-2">Status</th>
                <th className="py-1.5 px-2">Last login</th>
                <th className="py-1.5 px-2">Activity</th>
                <th className="py-1.5 px-2">Council</th>
                <th className="py-1.5 pl-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const custom = !matchesPreset(u.role, u.permissions)
                return (
                  <tr key={u.id} className="border-b border-border/50">
                    <td className="py-2 pr-2 sticky left-0 z-[5] bg-navy-900">
                      <div className="text-white">{u.display_name || u.email}</div>
                      <div className="text-2xs text-muted">{u.email}</div>
                    </td>
                    <td className="py-2 px-2">
                      <span className={`text-2xs px-1.5 py-0.5 rounded border ${
                        ROLE_BADGE[u.role] ?? ROLE_BADGE.viewer}`}>
                        {u.role}
                      </span>
                      {custom && (
                        <span className="ml-1 text-2xs text-warning"
                          title={`Permissions: ${u.permissions.join(', ')}`}>
                          Custom
                        </span>
                      )}
                    </td>
                    <td className="py-2 px-2">
                      <span className={u.is_active ? 'text-success' : 'text-danger'}>
                        {u.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="py-2 px-2 text-muted">
                      {relativeTime(u.last_login_at)}
                    </td>
                    <td className="py-2 px-2 text-muted"
                        title="Total interactions and page views">
                      {u.activity_count}
                    </td>
                    <td className="py-2 px-2"
                        title="Lifetime council query allocation">
                      {u.council_queries_limit == null ? (
                        <span className="text-electric">Unlimited</span>
                      ) : (
                        <span className={
                          u.council_queries_used >= u.council_queries_limit
                            ? 'text-warning' : 'text-muted'}>
                          {u.council_queries_used} / {u.council_queries_limit}
                        </span>
                      )}
                    </td>
                    <td className="py-2 pl-2">
                      <div className="flex items-center gap-2">
                        <button type="button" onClick={() => setEditingUser(u)}
                          className="text-electric hover:underline">
                          Edit
                        </button>
                        {u.is_active && (
                          <button type="button" onClick={() => void deactivate(u)}
                            className="text-danger hover:underline">
                            Deactivate
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {adding && (
        <UserFormModal user={null} onClose={() => setAdding(false)}
          onSaved={(n) => {
            setAdding(false)
            setNotice(n ?? null)
            load()
          }} />
      )}
      {editingUser && (
        <UserFormModal user={editingUser} onClose={() => setEditingUser(null)}
          onSaved={() => { setEditingUser(null); setNotice(null); load() }} />
      )}
    </div>
  )
}
