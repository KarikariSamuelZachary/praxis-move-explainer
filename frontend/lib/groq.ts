import Groq from 'groq-sdk';
import { ExplanationRequest, ExplanationResponse } from '@/types';

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

export async function getChessMoveExplanation(
  request: ExplanationRequest
): Promise<ExplanationResponse> {
  const { fen, move, isCorrect, playerElo, puzzleThemes } = request;
  const explanationStyle = getExplanationStyle(playerElo);
  const sanMove = move.trim();
  const themesText = puzzleThemes.length > 0
    ? `Key themes in this puzzle: ${puzzleThemes.join(', ')}.`
    : '';

  const prompt = isCorrect
    ? `You are Praxis, an expert chess coach. A player just solved a puzzle correctly.

${explanationStyle}
${themesText}

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
      explanation: parsed.explanation || (isCorrect
        ? 'This move creates a decisive advantage.'
        : 'This move misses a stronger continuation.'),
      concept: parsed.concept || 'Tactics',
      tip: parsed.tip,
    };
  } catch (error) {
    console.error('Groq API error:', error);
    return {
      explanation: isCorrect
        ? 'This is the strongest move, creating an immediate threat.'
        : 'This move misses a stronger continuation. Look for more forcing moves.',
      concept: isCorrect ? 'Tactics' : 'Look again',
      tip: 'Always look for checks, captures, and threats first.',
    };
  }
}

const explanationCache = new Map<string, ExplanationResponse>();

export async function getCachedExplanation(
  request: ExplanationRequest
): Promise<ExplanationResponse> {
  const eloRange = Math.floor(request.playerElo / 400) * 400;
  const normalizedThemes = [...request.puzzleThemes].sort().join(',');
  const cacheKey = [
    request.fen,
    request.move,
    request.isCorrect ? 'correct' : 'incorrect',
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
