/**
 * Shared connection config for the Next.js proxies to the FastAPI backend.
 * Deliberately a function (not module constants) so env vars resolve on
 * every call, matching the route handlers' previous request-time reads.
 */
export function getBackendConfig() {
  return {
    backendApiUrl: process.env.BACKEND_API_URL ?? 'http://localhost:8000',
    internalSecret: process.env.INTERNAL_SECRET ?? '',
  };
}
