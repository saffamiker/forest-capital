/**
 * useIsTeamMember — true when the signed-in user is a project team
 * member (their email is in PROJECT_TEAM_EMAILS).
 *
 * The platform is open to any authenticated user for exploration;
 * action features are reserved for the team. This hook is the single
 * frontend source of that distinction — TeamGate and the gated screens
 * all read it.
 */
import { useAuth } from '../App'
import { isTeamMember } from '../constants/team'

export function useIsTeamMember(): boolean {
  const { session } = useAuth()
  return isTeamMember(session?.email)
}
