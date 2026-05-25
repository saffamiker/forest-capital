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
  // UAT 2026-05-24 (#119) — was missing from this list. Without it,
  // matchesPreset() flagged every team_member as "Custom" in the
  // User Management panel (the backend ROLE_PRESETS.team_member
  // ships with access_test_panel; the frontend preset omitted it),
  // and the sysadmin couldn't toggle the permission per-user. The
  // backend gate already existed and was correct; the frontend just
  // wasn't surfacing the permission to the UI.
  { key: 'access_test_panel',  label: 'Open Test Administration panel' },
  { key: 'manage_users',       label: 'Manage users', sysadminOnly: true },
]

export const ROLE_PRESETS: Record<string, string[]> = {
  viewer: ['view_analytics', 'ask_council'],
  team_member: [
    'view_analytics', 'ask_council', 'team_member',
    'generate_documents', 'export_package',
    // Mirrors backend config.ROLE_PRESETS["team_member"]. Without
    // this entry, matchesPreset() returned false for every
    // team_member user (their backend permissions array carries
    // access_test_panel but the frontend preset didn't expect it)
    // — so the UI rendered them as "Custom" and the sysadmin
    // couldn't return them to the canonical team_member preset
    // via the role picker.
    'access_test_panel',
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
