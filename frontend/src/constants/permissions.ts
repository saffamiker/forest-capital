/**
 * permissions.ts — the frontend mirror of config.PERMISSIONS and
 * config.ROLE_PRESETS.
 *
 * Used by the user-management UI: the permissions checklist, the role
 * presets, and the "Custom" detection (a user whose permissions diverge
 * from their role's preset). Keep in lockstep with backend/config.py.
 */

export interface PermissionDef {
  key: string
  label: string
  /** manage_users is sysadmin-only — shown disabled in the UI. */
  sysadminOnly?: boolean
}

export const PERMISSIONS: PermissionDef[] = [
  { key: 'view_analytics',     label: 'View analytics and dashboard' },
  { key: 'ask_council',        label: 'Ask council questions' },
  { key: 'team_member',        label: 'Upload documents / Academic Review' },
  { key: 'generate_documents', label: 'Generate documents' },
  { key: 'export_package',     label: 'Export academic package' },
  { key: 'view_admin',         label: 'View admin reports' },
  { key: 'manage_users',       label: 'Manage users', sysadminOnly: true },
]

export const ROLE_PRESETS: Record<string, string[]> = {
  viewer: ['view_analytics', 'ask_council'],
  team_member: [
    'view_analytics', 'ask_council', 'team_member',
    'generate_documents', 'export_package',
  ],
  sysadmin: PERMISSIONS.map((p) => p.key),
}

/** Roles a sysadmin may assign via the UI — sysadmin is migration-only. */
export const ASSIGNABLE_ROLES: { value: string; label: string }[] = [
  { value: 'viewer', label: 'Viewer' },
  { value: 'team_member', label: 'Team Member' },
]

/** True when a permission set matches its role's preset exactly. */
export function matchesPreset(role: string, permissions: string[]): boolean {
  const preset = ROLE_PRESETS[role]
  if (!preset) return false
  const a = [...preset].sort()
  const b = [...permissions].sort()
  return a.length === b.length && a.every((p, i) => p === b[i])
}
