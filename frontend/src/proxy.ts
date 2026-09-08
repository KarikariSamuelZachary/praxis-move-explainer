import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const isProtectedRoute = createRouteMatcher(["/puzzles(.*)", "/review(.*)", "/woodpecker(.*)", "/train(.*)", "/repertoire(.*)", "/onboarding(.*)"]);
const isAppRoute = createRouteMatcher(["/puzzles(.*)", "/review(.*)", "/woodpecker(.*)", "/train(.*)", "/repertoire(.*)"]);
const isOnboardingRoute = createRouteMatcher(["/onboarding(.*)"]);

async function getSkillLevel(
  userId: string,
  route: "onboarding" | "puzzles",
): Promise<string | null> {
  const backendApiUrl = process.env.BACKEND_API_URL ?? "http://localhost:8000";
  const internalSecret = process.env.INTERNAL_SECRET ?? "";
  const startedAt = Date.now();

  console.log("[TIMING] proxy skill-level fetch start", {
    timestamp: new Date(startedAt).toISOString(),
    route,
  });

  try {
    const res = await fetch(`${backendApiUrl}/onboarding/skill-level`, {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "X-Internal-Secret": internalSecret,
        "X-Clerk-User-Id": userId,
      },
    });

    console.log("[TIMING] proxy skill-level fetch resolved", {
      timestamp: new Date().toISOString(),
      elapsed_ms: Date.now() - startedAt,
      route,
      status: res.status,
    });

    if (!res.ok) return null;
    const data = await res.json();
    return data.skill_level ?? null;
  } catch (error) {
    console.log("[TIMING] proxy skill-level fetch failed", {
      timestamp: new Date().toISOString(),
      elapsed_ms: Date.now() - startedAt,
      route,
      error: error instanceof Error ? error.message : String(error),
    });
    return null;
  }
}

export default clerkMiddleware(async (auth, req) => {
  if (isProtectedRoute(req)) {
    await auth.protect();
  }

  const { userId } = await auth();
  if (!userId) return NextResponse.next();

  if (!isAppRoute(req) && !isOnboardingRoute(req)) {
    return NextResponse.next();
  }

  const route = isOnboardingRoute(req) ? "onboarding" : "puzzles";
  const skillLevel = await getSkillLevel(userId, route);

  if (isAppRoute(req) && !skillLevel) {
    return NextResponse.redirect(new URL("/onboarding", req.url));
  }

  if (isOnboardingRoute(req) && skillLevel) {
    return NextResponse.redirect(new URL("/puzzles", req.url));
  }

  return NextResponse.next();
});

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/__clerk/(.*)",
    "/(api|trpc)(.*)",
  ],
};
