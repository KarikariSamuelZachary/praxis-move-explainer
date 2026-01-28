# Architecture Overview

## High-Level System Design

```
┌─────────────────┐
│  Flutter App    │
│  (iOS/Android)  │
└────────┬────────┘
         │
         │ REST API
         ▼
┌─────────────────┐
│   FastAPI       │
│   Backend       │
└────┬──────┬─────┘
     │      │
     │      └──────► Stockfish (analysis)
     │
     ▼
┌─────────────────┐
│  PostgreSQL     │
│  + Redis        │
└─────────────────┘
     │
     └──────► LLM (Gemini/Claude)
```

## Core Components

### 1. Mobile App (Flutter)
- Game upload (PGN import)
- Mistake review UI
- Woodpecker training interface
- Board visualization

### 2. Backend (FastAPI)
- `/upload` - Accepts PGN files
- `/analyze` - Triggers Stockfish analysis
- `/explain` - Generates LLM explanations
- `/mistakes` - Retrieves user mistakes
- `/training` - Manages repetition schedule

### 3. Chess Engine (Stockfish)
- Runs analysis on uploaded games
- Detects critical moments (eval drops > threshold)
- Returns top 3 mistakes per game

### 4. LLM Layer
- Receives position (FEN) + context
- Generates natural-language explanation
- Returns mistake category + explanation text

### 5. Data Layer
- **PostgreSQL**: Games, mistakes, users
- **Redis**: Repetition queue, caching

## Data Flow (Upload → Explanation)

1. User uploads PGN via Flutter app
2. Backend stores game in PostgreSQL
3. Stockfish analyzes game, identifies mistakes
4. For each mistake:
   - Extract FEN + move context
   - Send to LLM for explanation
   - Store mistake card in DB
5. Schedule first repetition (1 day later)
6. Return mistakes to mobile app

## Security & Auth
- JWT-based authentication
- Rate limiting on API endpoints
- PGN validation before processing

---

**Note**: This is a simplified MVP architecture. Future iterations will add web frontend, caching layers, and analytics.
