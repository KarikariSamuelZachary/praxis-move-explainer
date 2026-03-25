import Groq from 'groq-sdk';
import { ExplanationRequest, ExplanationResponse } from '@/types';

const groq = new Groq({
  apiKey: process.env.GROQ_API_KEY,
});

// Map ELO to explanation complexity
function getExplanationStyle(elo: number): string {
  if (elo < 800) {
    return 'You are explaining to a complete beginner. Use very simple language, avoid chess jargon, and focus on basic concepts like piece safety and simple threats. Keep it under 3 sentences.';
  }
  if (elo < 1200) {
    return 'You are explaining to a casual player. Use simple chess terms and explain tactics clearly. Mention the basic pattern being used. Keep it concise, 3-4 sentences.';
  }
  if (elo < 1600) {
    return 'You are explaining to an intermediate player. Use standard chess terminology. Explain the tactical or positional idea, why it works, and what would happen on alternative moves. 4-5 sentences.';
  }
  if (elo < 2000) {
    return 'You are explaining to an advanced player. Use precise chess terminology. Discuss the key variations, positional implications, and deeper strategic ideas. 5-6 sentences.';
  }
  return 'You are explaining to an expert player. Be concise but deep. Focus on subtle nuances, precise variations, and high-level strategic concepts. 4-5 sentences.';
}

export async function getChessMoveExplanation(
  request: ExplanationRequest
): Promise<ExplanationResponse> {
  const { fen, move, isCorrect, playerElo, puzzleThemes } = request;

  const explanationStyle = getExplanationStyle(playerElo);
  const themesText = puzzleThemes.length > 0
    ? `The key themes in this puzzle are: ${puzzleThemes.join(', ')}.`
    : '';

  const prompt = `You are Praxis, an expert chess coach helping a player improve through tactical training.

${explanationStyle}

Current position (FEN): ${fen}
The best move played: ${move}
Player's ELO: ${playerElo}
${themesText}
The player ${isCorrect ? 'found the correct move' : 'struggled with this move'}.

Provide:
1. A clear explanation of WHY this move is the best (explain the idea, threat, or tactic)
2. The key chess concept being demonstrated (e.g., "fork", "pin", "back rank weakness")
3. A brief tip for recognizing similar patterns in the future

Respond in this exact JSON format:
{
  "explanation": "Your main explanation here",
  "concept": "The key concept in 2-4 words",
  "tip": "A practical tip for future games"
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
      explanation: parsed.explanation || 'This move creates a decisive advantage.',
      concept: parsed.concept || 'Tactics',
      tip: parsed.tip,
    };
  } catch (error) {
    console.error('Groq API error:', error);
    return {
      explanation: 'This is the strongest move in the position, creating an immediate threat your opponent cannot handle.',
      concept: 'Tactics',
      tip: 'Always look for moves that create multiple threats simultaneously.',
    };
  }
}

// Cache layer - store explanations to avoid redundant API calls
const explanationCache = new Map<string, ExplanationResponse>();

export async function getCachedExplanation(
  request: ExplanationRequest
): Promise<ExplanationResponse> {
  // Create a cache key from position + move + elo range
  const eloRange = Math.floor(request.playerElo / 400) * 400; // Group by 400 elo brackets
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
