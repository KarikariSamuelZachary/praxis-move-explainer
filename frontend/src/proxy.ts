import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import {
  SKILL_CACHE_COOKIE,
  SKILL_CACHE_MAX_AGE_SECONDS,
  readSkillCache,
  serializeSkillCache,
} from "@/lib/skill-cache";

const isProtectedRoute = createRouteMatcher(["/puzzles(.*)", "/review(.*)", "/woodpecker(.*)", "/train(.*)", "/repertoire(.*)", "/onboarding(.*)"]);
const isAppRoute = createRouteMatcher(["/puzzles(.*)", "/review(.*)", "/woodpecker(.*)", "/train(.*)", "/repertoire(.*)"]);
const isOnboardingRoute = createRouteMatcher(["/onboarding(.*)"]);

// Skill level is fixed once onboarding completes, so caching it in an
// HTTP-only cookie removes the per-navigation (and per-prefetch) backend
// round trip this middleware used to make.
const SKILL_FETCH_TIMEOUT_MS = 2000;

// A single app page mounts ~6 <Link>s whose prefetches all hit this
// middleware concurrently; they share one in-flight backend call per user
// instead of fanning out.
const skillFetches = new Map<string, Promise<SkillFetchResult>>();

type SkillFetchResult = { ok: true; skillLevel: string | null } | { ok: false };

async function fetchSkillLevel(userId: string): Promise<SkillFetchResult> {
  const backendApiUrl = process.env.BACKEND_API_URL ?? "http://localhost:8000";
  const internalSecret = process.env.INTERNAL_SECRET ?? "";

  try {
    const res = await fetch(`${backendApiUrl}/onboarding/skill-level`, {
      cache: "no-store",
      signal: AbortSignal.timeout(SKILL_FETCH_TIMEOUT_MS),
      headers: {
        Accept: "application/json",
        "X-Internal-Secret": internalSecret,
        "X-Clerk-User-Id": userId,
      },
    });
    if (!res.ok) return { ok: false };
    const data = await res.json();
    return { ok: true, skillLevel: data.skill_level ?? null };
  } catch (error) {
    console.warn(
      "[proxy] skill-level fetch failed:",
      error instanceof Error ? error.message : error,
    );
    return { ok: false };
  }
}

function getSkillLevel(userId: string): Promise<SkillFetchResult> {
  const inflight = skillFetches.get(userId);
  if (inflight) return inflight;

  const request = fetchSkillLevel(userId).finally(() => skillFetches.delete(userId));
  skillFetches.set(userId, request);
  return request;
}

function gate(req: NextRequest, skillLevel: string | null, onboardingRoute: boolean): NextResponse {
  if (onboardingRoute) {
    return skillLevel
      ? NextResponse.redirect(new URL("/puzzles", req.url))
      : NextResponse.next();
  }

  return skillLevel
    ? NextResponse.next()
    : NextResponse.redirect(new URL("/onboarding", req.url));
}

export default clerkMiddleware(async (auth, req) => {
  return NextResponse.next(); // TEMP-LOCAL-PREVIEW-BYPASS
  if (isProtectedRoute(req)) {
    await auth.protect();
  }

  const { userId } = await auth();
  if (!userId) return NextResponse.next();

  const onboardingRoute = isOnboardingRoute(req);
  if (!isAppRoute(req) && !onboardingRoute) return NextResponse.next();

  const cached = readSkillCache(req.cookies.get(SKILL_CACHE_COOKIE)?.value, userId);
  if (cached !== undefined) {
    return gate(req, cached, onboardingRoute);
  }

  const result = await getSkillLevel(userId);
  if (!result.ok) {
    // Backend unreachable or slow: fail open rather than bouncing a
    // returning user into onboarding on a transient blip. The next
    // navigation retries.
    return NextResponse.next();
  }

  const response = gate(req, result.skillLevel, onboardingRoute);
  if (result.skillLevel) {
    response.cookies.set(SKILL_CACHE_COOKIE, serializeSkillCache(userId, result.skillLevel), {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: SKILL_CACHE_MAX_AGE_SECONDS,
    });
  }
  return response;
});

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/__clerk/(.*)",
    "/(api|trpc)(.*)",
  ],
};
