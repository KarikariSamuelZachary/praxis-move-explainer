'use client';

const PIECE_KEYS = ['q', 'r', 'b', 'n'] as const;
export type PromotionPiece = (typeof PIECE_KEYS)[number];

const PIECE_NAMES: Record<PromotionPiece, string> = {
  q: 'Queen',
  r: 'Rook',
  b: 'Bishop',
  n: 'Knight',
};

// The hollow white glyphs vs the solid black ones carry the side by shape,
// exactly like the board: both are painted in the same cream.
const GLYPHS: Record<'w' | 'b', Record<PromotionPiece, string>> = {
  w: { q: '♕', r: '♖', b: '♗', n: '♘' },
  b: { q: '♛', r: '♜', b: '♝', n: '♞' },
};

/**
 * The promotion chooser, in the app's walnut-and-gold language: a wood panel
 * over a blurred board, cream glyphs in gold-edged wells. Shared verbatim by
 * the puzzles, repertoire and endgame boards so a promotion looks the same
 * everywhere.
 */
export default function PromotionPicker({
  turn,
  onSelect,
  onCancel,
}: {
  turn: 'w' | 'b';
  onSelect: (piece: PromotionPiece) => void;
  onCancel: () => void;
}) {
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-black/70 backdrop-blur-sm">
      <div className="rounded-2xl border border-black/60 p-4 [background-image:linear-gradient(rgba(0,0,0,0.45),rgba(0,0,0,0.45)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_18px_44px_rgba(0,0,0,0.62),inset_0_1px_0_rgba(255,255,255,0.07),inset_0_-1px_0_rgba(0,0,0,0.55)]">
        <div className="text-center text-[10px] font-bold uppercase tracking-[0.25em] text-[#e8cfa0]/85">
          Promote to
        </div>
        <div className="mt-3 flex gap-2.5">
          {PIECE_KEYS.map((piece) => (
            <button
              key={piece}
              type="button"
              onClick={() => onSelect(piece)}
              aria-label={`Promote to ${PIECE_NAMES[piece]}`}
              className="flex h-14 w-14 items-center justify-center rounded-xl border border-[#d9b87c]/25 bg-black/35 text-4xl text-[#f7e5c6] transition duration-200 hover:border-[#eacb90]/75 hover:bg-[#d9b87c]/15 hover:text-[#ffedd0] active:scale-95"
            >
              <span className="pb-1 leading-none">{GLYPHS[turn][piece]}</span>
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="mt-3 w-full rounded-lg border border-white/15 bg-white/5 py-2 text-xs font-semibold text-white/70 transition hover:bg-white/10 hover:text-white"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
