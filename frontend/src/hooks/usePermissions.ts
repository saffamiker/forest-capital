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
