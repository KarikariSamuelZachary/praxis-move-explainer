export type PersonaKey = 'attacker' | 'sacrificer' | 'defender' | 'positional' | 'gambiter';

export type Tone = 'amber' | 'purple' | 'blue' | 'emerald' | 'orange';

export type Persona = {
  key: PersonaKey;
  name: string;
  icon: string;
  tone: Tone;
  description: string;
};

export const PERSONAS: Persona[] = [
  {
    key: 'attacker',
    name: 'Attacker',
    icon: '/persona-attacker.webp',
    tone: 'amber',
    description: 'Hunts for king pressure, favoring checks, threats, and forcing lines.',
  },
  {
    key: 'sacrificer',
    name: 'Sacrificer',
    icon: '/persona-sacrificer.webp',
    tone: 'purple',
    description: 'Loves a good gamble, trading material for initiative and danger.',
  },
  {
    key: 'defender',
    name: 'Defender',
    icon: '/persona-defender.webp',
    tone: 'blue',
    description: 'Plays it safe, prioritizing king safety and solid consolidation.',
  },
  {
    key: 'positional',
    name: 'Positional',
    icon: '/persona-positional.webp',
    tone: 'emerald',
    description: 'Avoids complications, sticking to quiet, sound, controlled play.',
  },
  {
    key: 'gambiter',
    name: 'Gambiter',
    icon: '/persona-gambiter.webp',
    tone: 'orange',
    description: 'Opens with the book\'s gambit whenever one is available, then hunts king threats.',
  },
];

export type TierKey = 'club' | 'expert' | 'master' | 'grandmaster';

export type Tier = {
  key: TierKey;
  name: string;
  minElo: number;
  maxElo: number;
};

export const TIERS: Tier[] = [
  { key: 'club', name: 'Club Player', minElo: 1320, maxElo: 1600 },
  { key: 'expert', name: 'Expert', minElo: 1600, maxElo: 2000 },
  { key: 'master', name: 'Master', minElo: 2000, maxElo: 2500 },
  { key: 'grandmaster', name: 'Grandmaster', minElo: 2500, maxElo: 3190 },
];

export type PlayColor = 'white' | 'black';

// Planned personas, rendered as coming-soon placeholders. These are NOT in
// PERSONAS (and must not be): they have no backend PersonaType/weight vector
// yet, so starting a game with them would be rejected by the API. They only
// reserve their slot in the eight-tile persona row.
export type UpcomingPersona = {
  name: string;
  description: string;
};

export const UPCOMING_PERSONAS: UpcomingPersona[] = [
  {
    name: 'Endgamer',
    description: 'Trades into clean endgames and converts with precise technique.',
  },
  {
    name: 'Brilliant',
    description: 'Finds the brilliant lines: sound sacrifices the engine itself calls best.',
  },
  {
    name: 'Swindler',
    description: 'Refuses to lose, swindling lost positions with traps and counterplay.',
  },
];

export function isPersonaKey(value: unknown): value is PersonaKey {
  return PERSONAS.some((persona) => persona.key === value);
}

export function isTierKey(value: unknown): value is TierKey {
  return TIERS.some((tier) => tier.key === value);
}

export function isPlayColor(value: unknown): value is PlayColor {
  return value === 'white' || value === 'black';
}

export function randomEloInRange(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}
