/**
 * usePermissions — the frontend permission hooks.
 *
 * Access control is permission-based: each signed-in user carries an
 * authoritative `permissions` array (resolved server-side from
 * platform_users, delivered via GET /api/auth/me into AuthContext).
 * Roles are presets over those permissions.
 *
 * useHasPermission is the single primitive; the convenience hooks are
 * thin wrappers for the common checks. They all read false until
 * /api/auth/me has populated the session — a brief, safe window.
 */
import { useAuth } from '../App'

/** True when the signed-in user holds the given permission. */
export function useHasPermission(permission: string): boolean {
  const { session } = useAuth()
  return !!session?.permissions?.includes(permission)
}

/** Team-tier access — document upload, Academic Review, the test runner. */
export function useIsTeamMember(): boolean {
  return useHasPermission('team_member')
}

/** Sysadmin — user management and the admin views. */
export function useIsSysadmin(): boolean {
  return useHasPermission('manage_users')
}

/** Can generate the midpoint paper / executive brief / presentation deck. */
export function useCanGenerateDocuments(): boolean {
  return useHasPermission('generate_documents')
}

/** Can export the academic ZIP package. */
export function useCanExport(): boolean {
  return useHasPermission('export_package')
}

/** May 24 2026 (#275 follow-up) — narrow permission that opens the
 *  Test Administration settings section. Paired with view_uat_status
 *  (below) which controls whether the data tables actually populate
 *  for the signed-in user. */
export function useCanAccessTestPanel(): boolean {
  return useHasPermission('access_test_panel')
}

/** May 24 2026 (UAT #119) — read-only UAT status. team_member carries
 *  this so Bob and Molly see real-time UAT progress (failure reports,
 *  issue tracker, feedback backlog) without admin rights. Action
 *  buttons (resolve failure, resolve feedback, trigger triage, approve
 *  suggestion) are still sysadmin-only and gated on useIsSysadmin
 *  separately in the components — a team_member sees the data,
 *  never the controls.
 *
 *  This is the "view" side of the split that closes UAT #119; the
 *  "manage" side stays on useIsSysadmin (manage_users). */
export function useCanViewUatStatus(): boolean {
  return useHasPermission('view_uat_status')
}
