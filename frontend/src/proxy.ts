import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const isProtectedRoute = createRouteMatcher(["/puzzles(.*)", "/review(.*)", "/woodpecker(.*)", "/train(.*)", "/onboarding(.*)"]);
const isAppRoute = createRouteMatcher(["/puzzles(.*)", "/review(.*)", "/woodpecker(.*)", "/train(.*)"]);
const isOnboardingRoute = createRouteMatcher(["/onboarding(.*)"]);

async function getSkillLevel(userId: string): Promise<string | null> {
  const backendApiUrl = process.env.BACKEND_API_URL ?? "http://localhost:8000";
  const internalSecret = process.env.INTERNAL_SECRET ?? "";

  try {
    const res = await fetch(`${backendApiUrl}/onboarding/skill-level`, {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "X-Internal-Secret": internalSecret,
        "X-Clerk-User-Id": userId,
      },
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.skill_level ?? null;
  } catch {
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

  const skillLevel = await getSkillLevel(userId);

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
