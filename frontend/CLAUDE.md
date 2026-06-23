@AGENTS.md
# Praxis — Claude Code Reference

## Project Overview
Chess training web app. AI-powered puzzle training, game review, 
and coaching.

## Stack
- Frontend: Next.js (App Router), Tailwind CSS, chess.js, Clerk auth
- Backend: FastAPI, PostgreSQL, Groq API
- Infra: Vercel (frontend), DigitalOcean App Platform (backend)
- Auth: Clerk (headless hooks, no prebuilt components)
- Rate limiting: Upstash Redis
- Repo: GitHub, auto-deploy on both Vercel and DigitalOcean

## Directory Structure
frontend/          Next.js app
  src/
    app/
      (marketing)/ Landing page, no sidebar
      (app)/       Protected pages, has sidebar
      api/         Vercel proxy routes to FastAPI
    components/
      auth/        SignInModal.tsx, SignUpModal.tsx (headless Clerk)
      board/       ChessBoard.tsx (main board component)
    proxy.ts       clerkMiddleware + route protection
  public/          Static assets including hero-chess.png

src/               FastAPI backend
  core/
    database.py
    migrations.py
    game_analyzer.py
  routers/
    puzzles.py
    review.py
    explain.py     Puzzle explanations via Groq
    webhooks.py    Clerk webhook, user sync
  llms/
    groq_explainer.py
    mock_explainer.py
  main.py

## Key Rules
- Samuel handles all non-code steps himself (env vars, 
  dashboards, terminal commands, deployments)
- Only provide code changes or Codex prompts for coding tasks
- Never touch non-code config unless explicitly asked
- Frontend never calls Groq directly, all LLM calls go 
  through FastAPI
- Frontend never calls FastAPI directly, all calls go 
  through Vercel API proxy routes in src/app/api/
- Every proxy route passes X-Internal-Secret header

## Auth Flow
- Clerk handles auth via headless hooks in SignInModal and SignUpModal
- clerkMiddleware in proxy.ts protects /puzzles and /review
- Unauthenticated users redirect to /sign-in (modal on landing page)
- After sign in/up, users land on /puzzles
- Clerk webhook at /webhooks/clerk syncs new users to PostgreSQL users table
- /webhooks/clerk is excluded from X-Internal-Secret middleware

## Design System
- Background: zinc-900 (#18181b)
- Cards: zinc-800 (#27272a)
- Accent: emerald-500 (#10b981)
- Border: zinc-700 (#3f3f46)
- Text: zinc-100 (#f4f4f5)
- Muted text: zinc-400 (#a1a1aa)

## Environment Variables
### Vercel (frontend)
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
CLERK_SECRET_KEY
INTERNAL_SECRET
BACKEND_API_URL
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL=/puzzles
NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL=/puzzles

### DigitalOcean (backend)
DATABASE_URL
INTERNAL_SECRET
CLERK_WEBHOOK_SECRET
GROQ_API_KEY
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN

## Planned Features (Not Yet Built)
- Woodpecker Method training mode
- Progress Dashboard
- Chess.com and Lichess game import
- Mistake DNA Profile
- Tilt Detection
- Improvement ROI Dashboard
- Personal Pattern Vault
- Conversion Coach
- Lottie animations and Framer Motion polish
- Animated coach character (Rive)
- Custom Praxis logo (Figma)
- Clerk session token forwarding to FastAPI
- Model finetuning for chess explanations

## Known Issues
- Game review explanations occasionally hallucinate board
  details. Deferred until model finetuning.
- Node 18 locally, needs upgrade to 20 for npm run build.
  Vercel and DigitalOcean already run Node 20.
- Clerk session token not yet forwarded to FastAPI.
  Needed for per-user data tracking.

### 2026-06-23 — Onboarding "Saving…" hang + missing user rows (Clerk webhook misrouted)

**Symptom:** After sign-up, user lands on `/onboarding`, selects a
skill level, clicks **Start Training**. Button switches to "Saving…"
and never progresses. Browser console shows no errors. `psql` query
`SELECT * FROM users;` returns no rows for the new user.

**Root cause:** Clerk Dashboard webhook Endpoint URL points at the
backend root (`https://<backend>/`) instead of
`https://<backend>/webhooks/clerk`. The internal-secret middleware
in `src/main.py:55-70` only exempts `/webhooks/clerk`, so the
webhook hits 401, `user.created` never fires, and the user is never
synced to PostgreSQL. The onboarding POST handler hits the
`else` branch in `src/routers/onboarding.py:53-67` (INSERT path)
which can fail silently if the row state is unexpected, leaving the
frontend stuck on "Saving…".

**Fix (no code changes — Clerk dashboard only):**
1. Clerk Dashboard → Webhooks → set Endpoint URL to
   `https://<your-backend-domain>/webhooks/clerk`
2. Confirm `user.created` is in the subscribed events list.
3. Copy the Signing Secret (`whsec_…`) and confirm it matches
   `CLERK_WEBHOOK_SECRET` in DigitalOcean backend env vars.
4. Save. New sign-ups will now create the row.

**Why the frontend button gets stuck:** `handleSubmit` in
`frontend/src/app/(app)/onboarding/page.tsx:59-74` never calls
`setSubmitting(false)` on the success path — only on `catch`. If
the navigation fails or the middleware redirects back, the page
unmount doesn't clear `submitting=true` and the button stays on
"Saving…". (Independent of the webhook bug; worth fixing later.)

**Verification query:** `SELECT COUNT(*) FROM users WHERE
skill_level IS NULL;` — should grow by 1 per new sign-up after the
webhook is fixed.

**Diagnostic path used (for future reference):**
- Read backend logs (`Jun 22 01:36:56 POST / 0.44ms → 401`) →
  identified webhook POST hitting `/` instead of `/webhooks/clerk`.
- Cross-checked by querying the `users` table directly via
  `psql "$DATABASE_URL"`.

### 2026-06-23 — DB connection mismatch (local `.env` ≠ DigitalOcean)

**Symptom:** After fixing the webhook URL, backend logs showed
`POST /webhooks/clerk 200 OK` and `POST /onboarding/skill-level
200 OK`, but `SELECT * FROM users;` (via local `psql`) returned
0 rows. Frontend still stuck on /onboarding.

**Root cause:** `frontend/CLAUDE.md` says Samuel handles env vars
himself — and the **deployed** FastAPI backend on DigitalOcean has
its own `DATABASE_URL` set in the DO dashboard's environment
variables. The local `/home/iaminspiredbro/my_projects/praxis-move-explainer/.env`
file has a **different** `DATABASE_URL` (likely local dev or
staging). The two never get reconciled automatically because
`src/main.py` calls `load_dotenv()` only on the *deployed*
machine's env vars when running there.

Result: `TRUNCATE` and `SELECT` queries run against the local DB
have no effect on the prod DB that the deployed backend writes to.
Everything looks empty locally even though writes are succeeding
on prod.

**How to verify:**
```bash
# Show host portion of local DATABASE_URL
grep DATABASE_URL /home/iaminspiredbro/my_projects/praxis-move-explainer/.env

# Check DigitalOcean's DATABASE_URL host (DO Dashboard → App →
# Settings → Environment Variables → DATABASE_URL)
# If the hosts differ, you're querying a different DB.
```

**Diagnostic query to confirm which DB you're on:**
```sql
SELECT current_database(), inet_server_addr(), current_user;
```

**Fix:** Either
1. Query the production DB directly using the DO DATABASE_URL:
   `psql "<the-prod-DATABASE_URL-from-DO-dashboard>"`
2. Or align the local `.env` `DATABASE_URL` with prod for
   debugging (but never commit this — keep prod secrets in DO
   only).

**Open question still pending (as of 2026-06-23 end of session):**
Even after aligning the DB, the page is still stuck on /onboarding
and `GET /api/puzzles` is not firing after `POST
/onboarding/skill-level`. Three possibilities to check tomorrow:
1. Webhook event type might not be `user.created` — handler
   returns `{"status": "ignored"}` (200) for non-creation events.
   Check Clerk Dashboard → Webhooks → Messages → event type.
2. The `frontend/src/app/(app)/onboarding/page.tsx:59-74`
   `handleSubmit` doesn't call `setSubmitting(false)` on success,
   so if the post-POST middleware redirect doesn't fully unmount
   the page, the button stays disabled with "Saving…".
3. Clerk might be re-issuing the same `clerk_id` for re-signups
   with the same email, and `INSERT ... ON CONFLICT (email)` keeps
   the existing `skill_level` (only updates `clerk_id`).

  