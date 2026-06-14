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

  ## Recent Session Work                                                                                                      
      133 +                                                                                                                            
      134 +### 2026-06-14 — Frontend src/ consolidation                                                                                
      135 +                                                                                                                            
      136 +**Goal:** Move all frontend source code under `src/` to follow                                                              
      137 +the standard Next.js App Router layout.                                                                                     
      138 +                                                                                                                            
      139 +**What was done:**                                                                                                          
      140 +- `git mv` (preserves history) for 8 files:                                                                                 
      141 +  - `components/board/ChessBoard.tsx` → `src/components/board/`                                                             
      142 +  - `components/layout/Sidebar.tsx` → `src/components/layout/`                                                              
      143 +  - `components/review/GameReview.tsx` → `src/components/review/`                                                           
      144 +  - `lib/{groq,lichess,redis,themes}.ts` → `src/lib/`                                                                       
      145 +  - `types/index.ts` → `src/types/`                                                                                         
      146 +- `tsconfig.json` `paths` changed from `{"@/*": ["./*"]}` to                                                                
      147 +  `{"@/*": ["./src/*"]}`. Required: the old alias mapped                                                                    
      148 +  `@/components/...` to the root-level dir, not `src/`.                                                                     
      149 +- Deleted now-empty root-level dirs: `frontend/components/`,                                                                
      150 +  `frontend/lib/`, `frontend/types/`.                                                                                       
      151 +- Deleted stale build cache: `.next/`, `tsconfig.tsbuildinfo`.                                                              
      152 +  The `.next/types/validator.ts` file referenced the old layout                                                             
      153 +  and produced the only remaining tsc error. Both regenerate on                                                             
      154 +  next build/dev.                                                                                                           
      155 +                                                                                                                            
      156 +**Decisions:**                                                                                                              
      157 +- **Scope expansion to lib/ and types/**: The user's task                                                                   
      158 +  explicitly named `components/`, but the old tsconfig had                                                                  
      159 +  `@/*` mapping to the root, so `lib/` and `types/` had to                                                                  
      160 +  move too for the new alias to work. Confirmed with user                                                                   
      161 +  before expanding.                                                                                                         
      162 +- **No code edits to source files**: All existing                                                                           
      163 +  `@/components/...`, `@/lib/...`, `@/types` imports keep                                                                   
      164 +  working as-is — just resolving to new paths.                                                                              
      165 +- **Used `git mv` over `mv + git add`**: Preserves file                                                                     
      166 +  history through renames in git.                                                                                           
      167 +                                                                                                                            
      168 +**Verification:**                                                                                                           
      169 +- `npx tsc --noEmit` passes with zero errors.                                                                               
      170 +- `git status` shows clean `R` (rename) entries for all 8 files                                                             
      171 +  and a single `M` for `tsconfig.json`.                                                                                     
      172 +                                                                                                                            
      173 +**What was NOT done (out of scope):**                                                                                       
      174 +- Did not touch the `AGENTS.md` / Next.js 16 doc note.                                                                      
      175 +- Did not update the backend FastAPI layout.                                                                                
      176 +- Did not move or rename `proxy.ts` (already at `src/proxy.ts`).                                                            
      177 +                                                                                                                            
      178 +**Next:**                                                                                                                   
      179 +- Run `npm run dev` or `npm run build` locally to regenerate                                                                
      180 +  `.next/` and confirm the app boots.                                                                                       
      181 +- Confirm the Vercel build still succeeds (it builds from a                                                                 
      182 +  clean state, so should be fine).                                                                                          
      183 +- Consider whether `Woodpecker Method training mode` should be                                                              
      184 +  removed from "Planned Features" — UI for it is already                                                                    
      185 +  present in `src/app/(app)/puzzles/page.tsx` (cycle                                                                        
      186 +  indicator, session stats, start-new-session flow).