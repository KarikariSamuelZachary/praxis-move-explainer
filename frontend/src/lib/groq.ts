import Groq from 'groq-sdk';
import { ExplanationRequest, ExplanationResponse, MoveClassification } from '@/types';

const groq = new Groq({
  apiKey: process.env.GROQ_API_KEY,
});

function getExplanationStyle(elo: number): string {
  if (elo < 800) return 'Explain using very simple language. Avoid chess jargon. Maximum 2 sentences.';
  if (elo < 1200) return 'Use simple chess terms. Explain the basic tactical idea clearly. Maximum 3 sentences.';
  if (elo < 1600) return 'Use standard chess terminology. Explain the tactical or positional idea and why it works. 3-4 sentences.';
  if (elo < 2000) return 'Use precise chess terminology. Discuss key variations and positional implications. 4-5 sentences.';
  return 'Be concise but deep. Focus on subtle nuances and precise variations. 3-4 sentences.';
}

function getPromptForClassification(
  classification: MoveClassification,
  moveHistory: string[],
  fen: string,
  sanMove: string
): string {
  switch (classification) {
    case 'book':
      return `You are Praxis, an expert chess coach.

Move history so far: ${moveHistory.join(' ')}
Current position (FEN): ${fen}
Move played: ${sanMove}

Explain the PURPOSE of this move in 2-3 sentences.
Use the move history to understand the position context.
Focus ONLY on:
- What square or piece does this move develop or control?
- What does it contribute to the position given what has been played?

STRICT RULES:
- Do NOT mention the opening name under any circumstances
- Do NOT say "this is the X opening" or "named after..."
- Do NOT start with the move notation
- Start directly with what the move DOES

Respond in this exact JSON format:
{
  "explanation": "...",
  "concept": "...",
  "tip": "A practical idea to keep in mind as the position develops."
}`;
    case 'best':
    case 'excellent':
    case 'good':
      return `You are Praxis, an expert chess coach. The player played a strong move.

Move history so far: ${moveHistory.join(' ')}
Position (FEN): ${fen}
Move: ${sanMove}

Explain WHY this move is strong (focus on threats, positional advantages, or tactics). Do NOT criticize it.

Respond in this exact JSON format:
{
  "explanation": "...",
  "concept": "...",
  "tip": "A pattern or concept to remember for future games."
}`;
    case 'inaccuracy':
      return `You are Praxis, an expert chess coach. The player played an inaccuracy.

Move history so far: ${moveHistory.join(' ')}
Position (FEN): ${fen}
Move: ${sanMove}

This move is slightly sub-optimal. Gently explain what a better plan or square would have been without giving away the exact best move.

Respond in this exact JSON format:
{
  "explanation": "...",
  "concept": "...",
  "tip": "What to look for instead."
}`;
    case 'mistake':
    case 'blunder':
      return `You are Praxis, an expert chess coach. The player played a severe mistake or blunder.

Move history so far: ${moveHistory.join(' ')}
Position (FEN): ${fen}
Move: ${sanMove}

Directly explain the immediate threat missed or the tactical flaw this move creates. Be urgent and clear.

Respond in this exact JSON format:
{
  "explanation": "...",
  "concept": "...",
  "tip": "The immediate threat you missed or the tactical vulnerability created."
}`;
  }
}

function getLegacyPuzzlePrompt(
  fen: string,
  sanMove: string,
  isCorrect: boolean,
  playerElo: number,
  puzzleThemes: string[],
  moveHistory: string[] = []
): string {
  const explanationStyle = getExplanationStyle(playerElo);
  const themesText = puzzleThemes.length > 0
    ? `Key themes in this puzzle: ${puzzleThemes.join(', ')}.`
    : '';

  return isCorrect
    ? `You are Praxis, an expert chess coach. A player just solved a puzzle correctly.

${explanationStyle}
${themesText}

Move history so far: ${moveHistory.join(' ')}
Position (FEN): ${fen}
The correct move was: ${sanMove}

Explain WHY this is the best move. Focus on the specific threat or tactic it creates.

IMPORTANT:
- Do NOT start your explanation with the move notation like "${sanMove}"
- Start with the IDEA or CONCEPT (e.g. "The knight fork wins material because..." or "Pushing to d4 challenges the center because...")
- Write naturally as a chess coach

Respond in this exact JSON format:
{
  "explanation": "Your explanation starting with the idea, not the move notation",
  "concept": "Key concept in 2-4 words",
  "tip": "A practical tip for recognizing this pattern in future games"
}`
    : `You are Praxis, an expert chess coach. A player just played an incorrect move.

${explanationStyle}
${themesText}

Move history so far: ${moveHistory.join(' ')}
Position (FEN): ${fen}
The move the player tried: ${sanMove}
This was NOT the best move.

Explain briefly why this move falls short. Be encouraging but honest.

IMPORTANT:
- Do NOT start with the move notation like "${sanMove}"
- Do NOT say this move is good or correct
- Do not reveal the exact solution
- Be constructive and brief

Respond in this exact JSON format:
{
  "explanation": "Brief explanation of why this move is suboptimal",
  "concept": "What to look for instead in 2-4 words",
  "tip": "A hint toward the right idea without giving the solution"
}`;
}

function buildPrompt(request: ExplanationRequest): string {
  const sanMove = request.move.trim();

  if (request.classification) {
    return getPromptForClassification(
      request.classification,
      request.moveHistory ?? [],
      request.fen,
      sanMove
    );
  }

  return getLegacyPuzzlePrompt(
    request.fen,
    sanMove,
    request.isCorrect ?? true,
    request.playerElo ?? 1200,
    request.puzzleThemes ?? [],
    request.moveHistory ?? []
  );
}

function getFallbackExplanation(request: ExplanationRequest): ExplanationResponse {
  switch (request.classification) {
    case 'book':
      return {
        explanation: 'This is a standard opening move that develops the position according to known opening principles.',
        concept: 'Opening Theory',
        tip: 'A practical idea to keep in mind as the position develops.',
      };
    case 'best':
    case 'excellent':
    case 'good':
      return {
        explanation: 'This is a strong move that improves your position and supports your overall plan.',
        concept: 'Strong Move',
        tip: 'A pattern or concept to remember for future games.',
      };
    case 'inaccuracy':
      return {
        explanation: 'This move is playable, but a better plan would improve your position more efficiently.',
        concept: 'Move Order',
        tip: 'What to look for instead.',
      };
    case 'mistake':
    case 'blunder':
      return {
        explanation: 'This move misses an immediate danger and creates a tactical problem in the position.',
        concept: 'Tactical Oversight',
        tip: 'The immediate threat you missed or the tactical vulnerability created.',
      };
    default:
      return {
        explanation: request.isCorrect
          ? 'This is the strongest continuation in the position.'
          : 'This move misses the most forcing continuation in the puzzle.',
        concept: 'Tactics',
        tip: 'Look for checks, captures, and threats before quieter moves.',
      };
  }
}

export async function getChessMoveExplanation(
  request: ExplanationRequest
): Promise<ExplanationResponse> {
  const prompt = buildPrompt(request);
  const fallback = getFallbackExplanation(request);

  try {
    const completion = await groq.chat.completions.create({
      messages: [{ role: 'user', content: prompt }],
      model: 'llama-3.3-70b-versatile',
      temperature: 0.3,
      max_tokens: 400,
      response_format: { type: 'json_object' },
    });

    const content = completion.choices[0]?.message?.content || '{}';
    const parsed = JSON.parse(content);

    return {
      explanation: parsed.explanation || fallback.explanation,
      concept: parsed.concept || fallback.concept,
      tip: parsed.tip || fallback.tip,
    };
  } catch (error) {
    console.error('Groq API error:', error);
    return fallback;
  }
}

const explanationCache = new Map<string, ExplanationResponse>();

export async function getCachedExplanation(
  request: ExplanationRequest
): Promise<ExplanationResponse> {
  const playerElo = request.playerElo ?? 1200;
  const puzzleThemes = request.puzzleThemes ?? [];
  const eloRange = Math.floor(playerElo / 400) * 400;
  const normalizedThemes = [...puzzleThemes].sort().join(',');
  const cacheKey = [
    request.fen,
    request.move,
    request.classification ?? (request.isCorrect ? 'correct' : 'incorrect'),
    eloRange,
    normalizedThemes,
  ].join('-');

  if (explanationCache.has(cacheKey)) {
    return explanationCache.get(cacheKey)!;
  }

  const explanation = await getChessMoveExplanation(request);
  explanationCache.set(cacheKey, explanation);
  return explanation;
}
