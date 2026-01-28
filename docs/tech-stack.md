# Tech Stack

## MVP Scope (Mobile-First)

### Frontend
- **Flutter** (iOS + Android)
- Purpose: daily training, Woodpecker repetition, game upload, explanations
- Focus: native mobile UX for focused learning

### Backend
- **FastAPI** (Python)
- Purpose: API layer, orchestration, auth, business logic

### Chess Engine
- **Stockfish**
- Purpose: mistake detection and critical moment identification

### AI / LLM
- **Provider-agnostic** (Gemini / OpenAI / Claude)
- Purpose: natural-language explanations of chess concepts

### Data
- **PostgreSQL**: persistent storage (games, mistakes, user data)
- **Redis**: repetition scheduling and caching

### Infrastructure
- **Fly.io**: backend services + Stockfish
- **Docker**: engine + service isolation

---

## Future Scope (Post-MVP)

### Web Frontend
- **Next.js + TypeScript**
- **Tailwind CSS**
- Purpose: SEO, discovery, broader reach, desktop-optimized UX
- Infrastructure: Vercel hosting

### Rationale
- Mobile delivers core value (daily training, focused repetition)
- Web expands reach and improves discoverability
- Shared backend API serves both platforms
