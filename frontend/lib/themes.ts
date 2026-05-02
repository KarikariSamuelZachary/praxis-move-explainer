export type PuzzleThemeOption = {
  key: string;
  label: string;
};

export type PuzzleThemeGroup = {
  name: string;
  accent: string;
  themes: PuzzleThemeOption[];
};

export const PUZZLE_THEME_GROUPS: PuzzleThemeGroup[] = [
  {
    name: 'Phases',
    accent: 'from-sky-500 to-cyan-300',
    themes: [
      { key: 'opening', label: 'Opening' },
      { key: 'middlegame', label: 'Middlegame' },
      { key: 'endgame', label: 'Endgame' },
      { key: 'rookEndgame', label: 'Rook endgame' },
      { key: 'bishopEndgame', label: 'Bishop endgame' },
      { key: 'pawnEndgame', label: 'Pawn endgame' },
      { key: 'knightEndgame', label: 'Knight endgame' },
      { key: 'queenEndgame', label: 'Queen endgame' },
      { key: 'queenRookEndgame', label: 'Queen and Rook' },
    ],
  },
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
  {
    name: 'Goals',
    accent: 'from-indigo-400 to-violet-300',
    themes: [
      { key: 'mix', label: 'Healthy mix' },
      { key: 'equality', label: 'Equality' },
      { key: 'advantage', label: 'Advantage' },
      { key: 'crushing', label: 'Crushing' },
    ],
  },
  {
    name: 'Length',
    accent: 'from-stone-300 to-zinc-500',
    themes: [
      { key: 'oneMove', label: 'One-move puzzle' },
      { key: 'short', label: 'Short puzzle' },
      { key: 'long', label: 'Long puzzle' },
      { key: 'veryLong', label: 'Very long puzzle' },
    ],
  },
  {
    name: 'Origin',
    accent: 'from-blue-400 to-slate-300',
    themes: [
      { key: 'master', label: 'Master games' },
      { key: 'masterVsMaster', label: 'Master vs Master' },
      { key: 'superGM', label: 'Super GM games' },
      { key: 'playerGames', label: 'Player games' },
    ],
  },
];
