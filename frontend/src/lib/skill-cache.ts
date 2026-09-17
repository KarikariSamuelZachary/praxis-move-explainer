/**
 * Skill-level cache cookie shared by the middleware (frontend/src/proxy.ts).
 *
 * The value is bound to the Clerk user id so one browser switching accounts
 * can't reuse another user's cached onboarding state. Not a security
 * boundary: the backend remains the source of truth for skill level.
 */
export const SKILL_CACHE_COOKIE = 'praxis_skill';
export const SKILL_CACHE_MAX_AGE_SECONDS = 600;

export function serializeSkillCache(userId: string, skillLevel: string): string {
  return `${userId}:${encodeURIComponent(skillLevel)}`;
}

export function readSkillCache(
  cookieValue: string | undefined,
  userId: string,
): string | undefined {
  if (!cookieValue) return undefined;

  const separator = cookieValue.indexOf(':');
  if (separator <= 0 || cookieValue.slice(0, separator) !== userId) return undefined;

  const encoded = cookieValue.slice(separator + 1);
  if (!encoded) return undefined;

  try {
    return decodeURIComponent(encoded);
  } catch {
    return undefined;
  }
}
