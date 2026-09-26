export type PuzzleThemeOption = {
  key: string;
  label: string;
};

export type PuzzleThemeGroup = {
  name: string;
  accent: string;
  themes: PuzzleThemeOption[];
};

export function puzzleThemeLabel(key: string): string {
  for (const group of PUZZLE_THEME_GROUPS) {
    const theme = group.themes.find((option) => option.key === key);
    if (theme) return theme.label;
  }
  return key;
}

export const PUZZLE_THEME_GROUPS: PuzzleThemeGroup[] = [
  {
    name: 'Motifs',
    accent: 'from-emerald-500 to-lime-300',
    themes: [
      { key: 'advancedPawn', label: 'Advanced pawn' },
      { key: 'attackingF2F7', label: 'Attacking f2/f7' },
      { key: 'capturingDefender', label: 'Capture the defender' },
      { key: 'discoveredAttack', label: 'Discovered attack' },
      { key: 'doubleCheck', label: 'Double check' },
      { key: 'exposedKing', label: 'Exposed king' },
      { key: 'fork', label: 'Fork' },
      { key: 'hangingPiece', label: 'Hanging piece' },
      { key: 'kingsideAttack', label: 'Kingside attack' },
      { key: 'pin', label: 'Pin' },
      { key: 'queensideAttack', label: 'Queenside attack' },
      { key: 'sacrifice', label: 'Sacrifice' },
      { key: 'skewer', label: 'Skewer' },
      { key: 'trappedPiece', label: 'Trapped piece' },
    ],
  },
  {
    name: 'Advanced',
    accent: 'from-amber-400 to-orange-500',
    themes: [
      { key: 'attraction', label: 'Attraction' },
      { key: 'clearance', label: 'Clearance' },
      { key: 'defensiveMove', label: 'Defensive move' },
      { key: 'deflection', label: 'Deflection' },
      { key: 'interference', label: 'Interference' },
      { key: 'intermezzo', label: 'Intermezzo' },
      { key: 'quietMove', label: 'Quiet move' },
      { key: 'xRayAttack', label: 'X-Ray attack' },
      { key: 'zugzwang', label: 'Zugzwang' },
    ],
  },
  {
    name: 'Mates',
    accent: 'from-red-500 to-rose-300',
    themes: [
      { key: 'mate', label: 'Checkmate' },
      { key: 'mateIn1', label: 'Mate in 1' },
      { key: 'mateIn2', label: 'Mate in 2' },
      { key: 'mateIn3', label: 'Mate in 3' },
      { key: 'mateIn4', label: 'Mate in 4' },
      { key: 'mateIn5', label: 'Mate in 5+' },
    ],
  },
  {
    name: 'Mate Patterns',
    accent: 'from-fuchsia-500 to-pink-300',
    themes: [
      { key: 'anastasiaMate', label: "Anastasia's mate" },
      { key: 'arabianMate', label: 'Arabian mate' },
      { key: 'backRankMate', label: 'Back rank mate' },
      { key: 'bodenMate', label: "Boden's mate" },
      { key: 'doubleBishopMate', label: 'Double bishop mate' },
      { key: 'dovetailMate', label: 'Dovetail mate' },
      { key: 'hookMate', label: 'Hook mate' },
      { key: 'killBoxMate', label: 'Kill box mate' },
      { key: 'smotheredMate', label: 'Smothered mate' },
      { key: 'vukovicMate', label: 'Vukovic mate' },
    ],
  },
  {
    name: 'Special Moves',
    accent: 'from-teal-400 to-emerald-300',
    themes: [
      { key: 'castling', label: 'Castling' },
      { key: 'enPassant', label: 'En passant' },
      { key: 'promotion', label: 'Promotion' },
      { key: 'underPromotion', label: 'Underpromotion' },
    ],
  },
];
