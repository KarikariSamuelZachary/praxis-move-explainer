'use client';

import { GameReviewMove } from '@/types';

type MoveListProps = {
  moves: GameReviewMove[];
  activePly: number;
  onPlySelect: (ply: number) => void;
};

const CLASSIFICATION_DOT: Record<GameReviewMove['classification'], string> = {
  book: 'bg-sky-400/80',
  best: 'bg-emerald-400',
  excellent: 'bg-teal-400',
  good: 'bg-lime-400',
  inaccuracy: 'bg-amber-400',
  mistake: 'bg-orange-400',
  blunder: 'bg-rose-500',
};

export default function MoveList({ moves, activePly, onPlySelect }: MoveListProps) {
  if (moves.length === 0) {
    return null;
  }

  const pairs: { number: number; whitePly: number; blackPly: number }[] = [];
  for (let i = 0; i < moves.length; i += 2) {
    pairs.push({
      number: Math.floor(i / 2) + 1,
      whitePly: i,
      blackPly: i + 1 < moves.length ? i + 1 : -1,
    });
  }

  return (
    <div className="max-h-48 overflow-y-auto rounded-2xl border border-black/40 bg-black/40 p-3 text-sm [box-shadow:inset_0_1px_0_rgba(255,255,255,0.05)]">
      <div className="grid grid-cols-[2.25rem_1fr_1fr] gap-x-2 gap-y-1">
        {pairs.map((pair) => (
          <PairRow
            key={pair.number}
            pair={pair}
            moves={moves}
            activePly={activePly}
            onPlySelect={onPlySelect}
          />
        ))}
      </div>
    </div>
  );
}

type Pair = { number: number; whitePly: number; blackPly: number };

function PairRow({
  pair,
  moves,
  activePly,
  onPlySelect,
}: {
  pair: Pair;
  moves: GameReviewMove[];
  activePly: number;
  onPlySelect: (ply: number) => void;
}) {
  return (
    <>
      <span className="select-none self-center text-right text-xs font-medium text-white/50">
        {pair.number}.
      </span>
      <MoveButton
        move={pair.whitePly >= 0 ? moves[pair.whitePly] : undefined}
        ply={pair.whitePly}
        activePly={activePly}
        onPlySelect={onPlySelect}
      />
      <MoveButton
        move={pair.blackPly >= 0 ? moves[pair.blackPly] : undefined}
        ply={pair.blackPly}
        activePly={activePly}
        onPlySelect={onPlySelect}
      />
    </>
  );
}

function MoveButton({
  move,
  ply,
  activePly,
  onPlySelect,
}: {
  move?: GameReviewMove;
  ply: number;
  activePly: number;
  onPlySelect: (ply: number) => void;
}) {
  if (!move || ply < 0) {
    return <span />;
  }
  const isActive = ply === activePly;

  return (
    <button
      type="button"
      onClick={() => onPlySelect(ply)}
      className={`group inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-left text-sm transition ${
        isActive
          ? 'bg-[#10b981]/15 text-[#10b981] ring-1 ring-[#10b981]/40'
          : 'text-white/90 hover:bg-white/5 hover:text-white'
      }`}
    >
      <span
        aria-hidden
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${CLASSIFICATION_DOT[move.classification]}`}
      />
      <span className="font-mono text-sm">{move.san}</span>
    </button>
  );
}
