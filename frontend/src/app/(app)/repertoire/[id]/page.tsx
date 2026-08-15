'use client';

/**
 * Repertoire detail / Build page — mounted at /repertoire/{id}.
 *
 * Replaces the prior "detail coming next" placeholder stub with the
 * real page: header (name + color + Train button), clickable SAN
 * breadcrumb of the currently-viewed line, a drag-to-add board, and
 * per-position "My saved moves" + "Other moves" panels drawn from the
 * gaps endpoint cache.
 *
 * State model (CLIENT-SIDE, no new endpoint for navigation):
 *   * `path: string[]`  — UCI moves replayed from the starting
 *     position. The board always shows the position at the end of
 *     `path`. Clicking breadcrumb ply N truncates `path` to [:N].
 *   * `clientKnownMoves: Map<normalizedFen, {uci, san, id}>` — moves
 *     the client knows about, keyed by the stored row's position id
 *     is threaded through so the trash icon can DELETE the row.
 *     Seeded ONCE on mount from `GET /api/repertoires/{id}/positions`
 *     (every stored row, unfiltered by FSRS due), and merged with
 *     additional entries from in-session POST responses as the user
 *     drags new moves. Used to populate "My saved moves" at the
 *     breadcrumb's current position. Fix for the prior gap where a
 *     position stored in a prior session showed the "+ Drag to add"
 *     empty state.
 *   * `cachedGaps: RepertoireGap[]` — full repertoire gap report
 *     fetched ONCE on mount; filtered client-side by current
 *     `parent_fen` to render "Other moves".
 *
 * Why fetch the full gap report once (rather than per position):
 * the upstream endpoint hits Lichess Explorer server-side per stored
 * position. Calling it on every breadcrumb navigation would be
 * expensive AND the reference explicitly asks for a single "Opening
 * book loading…" placeholder pattern, not a per-navigation flicker.
 *
 * Board reuse: there is NO existing browse-mode board component
 * (ChessBoard in src/components/board/ChessBoard.tsx is locked to
 * the Puzzle playback flow with callback hooks). The list page
 * mounted `<Chessboard>` directly via `dynamic` for thumbnails, and
 * `train/sparring/page.tsx:599` mounts it directly for interactive
 * play with `onPieceDrop`. Same pattern here — direct dynamic
 * import of `react-chessboard`'s `Chessboard` component with the
 * walnut square styling that every other Praxis board uses.
 *
 * Known backend gaps (REPORTED in this task's summary):
 *   * The header's name/color now come from GET /api/repertoires/{id}
 *     (the dedicated single-repertoire endpoint) — no list-page
 *     derivation anymore, so a direct link / cold refresh works.
 *   * The reference screenshot's "10 positions" subtree count
 *     under a saved move isn't computed (would require walking the
 *     position tree; out of scope here — see the count comment in
 *     `SavedMoveRow` below).
 *   * The reference screenshot's per-line comment box was removed
 *     entirely — there's no schema field or endpoint for it and it
 *     wasn't part of the original scope (it was cargo-culted from
 *     the screenshot layout).
 *
 * Inference / non-invention:
 *   * Drag-to-add: the task said "infer a reasonable UI (e.g.
 *     clicking a legal square/move on the board itself triggers
 *     POST". I implemented drag-and-drop on owner-turn positions
 *     because every stored row is an owner-turn row (per the
 *     upsert contract). At opponent-turn positions the piece is
 *     snapped back; only the breadcrumb is interactive there,
 *     matching what the schema can persist.
 *   * Sort / orientation: board flipped to the owner's color (the
 *     white repertoire shows white at the bottom; black flipped).
 *     Same as train/sparring/page.tsx (which does humanColor flips).
 */

import { use, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Chess, type Square } from 'chess.js';

import BoardShell from '@/components/board/BoardShell';

const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

const STARTING_FEN =
  'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

type DetailParams = Promise<{ id: string }>;

type RepertoireColor = 'white' | 'black';

// Shape returned by GET /api/repertoires/{id} — the plain Repertoire
// row (NOT RepertoireSummary; the detail page doesn't need the
// last_trained_at / times_trained / last_score_percent list-page
// aggregates).
type ApiRepertoire = {
  id: string;
  user_id: string;
  name: string;
  color: RepertoireColor;
  created_at: string;
  updated_at: string;
};

type RepertoireGap = {
  parent_position_id: string;
  parent_fen: string;
  opponent_move_uci: string;
  opponent_move_san: string;
  frequency_percent: number;
  resulting_fen: string;
};

type RepertoireGapReport = {
  gaps: RepertoireGap[];
  unchecked_positions: { position_id: string; fen: string; reason: string }[];
};

type ApiError = { detail?: string; error?: string };

type RepertoirePositionRow = {
  id: string;
  repertoire_id: string;
  fen: string;
  move: string;
  due: string;
  stability: number | null;
  difficulty: number | null;
  state: string;
  step: number | null;
  reps: number;
  lapses: number;
  last_review: string | null;
  created_at: string;
  updated_at: string;
};

// 4-field FEN (matches `_normalize_fen` in
// services/repertoire_service.py:82) so the client's keys line up
// with the 4-field values stored in repertoire_positions + produced
// by find_repertoire_gaps.
function normalizeFen(fen: string): string {
  return fen.split(/\s+/).slice(0, 4).join(' ');
}

function playToFen(startFen: string, moves: string[]): string {
  const game = new Chess(startFen);
  for (const uci of moves) {
    try {
      game.move({
        from: uci.slice(0, 2),
        to: uci.slice(2, 4),
        promotion: uci.length > 4 ? uci[4] : undefined,
      });
    } catch {
      // Best-effort replay — an illegal move simply truncates the
      // reconstruction at the last legal ply.
      break;
    }
  }
  return game.fen();
}

// Apply `uci` to the board at `fen` and return the resulting SAN. Used
// to convert stored UCIs (returned from /positions) into the SAN
// strings the UI shows in "My saved moves". Falls back to the raw UCI
// if the move is unpushable (stale/corrupt row).
function moveSanAtFen(fen: string, uci: string): string {
  try {
    const game = new Chess(fen);
    const m = game.move({
      from: uci.slice(0, 2),
      to: uci.slice(2, 4),
      promotion: uci.length > 4 ? uci[4] : undefined,
    });
    return m.san;
  } catch {
    return uci;
  }
}

function isOwnersTurn(fen: string, ownerColor: RepertoireColor): boolean {
  const side = fen.split(/\s+/)[1];
  return side === (ownerColor === 'white' ? 'w' : 'b');
}

function SearchBackIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4" />
      <path d="M12 8h.01" />
    </svg>
  );
}

function TrainIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M5 12h14" />
      <path d="m13 5 7 7-7 7" />
      <path d="M9 5 3 12l6 7" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6 18 20a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    </svg>
  );
}

function StepIcon({ kind }: { kind: 'home' | 'start' | 'prev' | 'next' | 'end' }) {
  // Square renderings of the reference's < < < > > > jump controls.
  switch (kind) {
    case 'home':
      return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 12 12 4l9 8" />
          <path d="M5 10v10h14V10" />
        </svg>
      );
    case 'start':
      return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M19 5 8 12l11 7z" />
          <path d="M5 5v14" />
        </svg>
      );
    case 'prev':
      return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m15 18-6-6 6-6" />
        </svg>
      );
    case 'next':
      return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m9 6 6 6-6 6" />
        </svg>
      );
    case 'end':
      return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M5 5v14" />
          <path d="M5 5 16 12 5 19z" />
        </svg>
      );
  }
}

type BreadcrumbProps = {
  path: string[];
  onJump: (index: number) => void;
};

function Breadcrumb({ path, onJump }: BreadcrumbProps) {
  // Format the path as SAN history in pairs (number.moves) so it
  // matches the reference render ("1.e4 e5 2.Nf3 Nc6"). Each rendered
  // unit is one PLAIN MOVE button that jumps the board to that ply
  // when clicked (truncating `path` to [0..ply]).
  const items: { ply: number; san: string; isClickable: boolean }[] = [];
  const game = new Chess();
  for (let i = 0; i < path.length; i++) {
    try {
      const m = game.move({
        from: path[i].slice(0, 2),
        to: path[i].slice(2, 4),
        promotion: path[i].length > 4 ? path[i][4] : undefined,
      });
      items.push({ ply: i + 1, san: m.san, isClickable: true });
    } catch {
      // Corrupt UCI in the path — render it as a non-clickable
      // stub so we never crash the breadcrumb.
      items.push({ ply: i + 1, san: path[i] ?? '?', isClickable: false });
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-white/5 px-4 py-2 text-sm">
      {items.length === 0 ? (
        <span className="text-[#a79b8a]">Starting position — drag a piece to begin</span>
      ) : (
        items.map((item, idx) => (
          <span key={`ply-${item.ply}`} className="flex items-center gap-2">
            <span
              className={
                Math.floor((item.ply - 1) / 2) % 1 === 0
                  ? 'text-[#a79b8a]'
                  : 'text-[#a79b8a]/40'
              }
            >
              {Math.floor((item.ply - 1) / 2) + 1}.
            </span>
            <button
              type="button"
              disabled={!item.isClickable}
              onClick={() => onJump(item.ply - 1)}
              className="rounded-md px-1.5 py-0.5 font-mono text-sm font-semibold text-[#efd9a7] transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {item.san}
            </button>
            {idx < items.length - 1 && <span className="text-[#a79b8a]/30">·</span>}
          </span>
        ))
      )}
    </div>
  );
}

type SavedMoveRowProps = {
  prefix: string;
  moveSan: string;
  count: number | null;
  onDelete: () => void;
};

function SavedMoveRow({ prefix, moveSan, count, onDelete }: SavedMoveRowProps) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-[#d9b87c]/35 bg-black/30 px-3 py-2.5 text-sm shadow-inner shadow-black/40">
      <div className="flex min-w-0 items-center gap-2">
        <span className="font-mono text-xs text-[#a79b8a]">{prefix}</span>
        <span className="font-mono text-base font-bold text-[#efd9a7]">{moveSan}</span>
      </div>
      <span className="ml-1 text-xs text-[#a79b8a]">
        {count === null ? 'In this line' : `${count} position${count === 1 ? '' : 's'}`}
      </span>
      <button
        type="button"
        onClick={onDelete}
        aria-label={`Remove ${prefix}${moveSan}`}
        className="ml-auto flex h-8 w-8 items-center justify-center rounded-lg text-[#d97757] transition hover:bg-[#d97757]/10"
      >
        <TrashIcon />
      </button>
    </div>
  );
}

type OtherMoveRowProps = {
  prefix: string;
  moveSan: string;
  frequencyPercent: number;
  loading?: boolean;
  onSelect: () => void;
};

function OtherMoveRow({
  prefix,
  moveSan,
  frequencyPercent,
  loading,
  onSelect,
}: OtherMoveRowProps) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className="group/other flex w-full items-center gap-3 rounded-2xl border border-white/5 bg-black/25 px-3 py-2.5 text-left text-sm transition hover:border-[#d9b87c]/30 hover:bg-black/40"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="font-mono text-xs text-[#a79b8a]">{prefix}</span>
        <span className="font-mono text-base font-semibold text-[#efd9a7]/85">
          {moveSan}
        </span>
      </div>
      <div className="relative ml-1 h-2 min-w-[80px] max-w-[160px] flex-1 overflow-hidden rounded-full bg-black/45">
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-[#d9b87c]/80 transition-[width] duration-300 group-hover/other:bg-[#efd9a7]/90"
          style={{ width: `${Math.max(2, Math.min(100, frequencyPercent))}%` }}
        />
      </div>
      <span className="shrink-0 text-xs tabular-nums text-[#a79b8a]">
        {frequencyPercent.toFixed(1)}%
      </span>
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-[#a79b8a]/55">
        {loading ? 'Opening book loading…' : 'Suggested'}
      </span>
    </button>
  );
}

type MovePlaceholderRowProps = {
  prefix: string;
  loading?: boolean;
};

function MovePlaceholderRow({ prefix, loading }: MovePlaceholderRowProps) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-white/5 bg-black/25 px-3 py-2.5 text-sm text-[#a79b8a]">
      <div className="flex min-w-0 items-center gap-2">
        <span className="font-mono text-xs text-[#a79b8a]/70">{prefix}</span>
        <span className="font-mono text-base font-semibold text-[#a79b8a]/40">
          —
        </span>
      </div>
      <div className="relative ml-1 h-2 min-w-[80px] max-w-[160px] flex-1 overflow-hidden rounded-full bg-black/45">
        <div className="absolute inset-y-0 left-0 w-1/2 rounded-full bg-[#a79b8a]/30 animate-pulse" />
      </div>
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-[#a79b8a]/55">
        {loading ? 'Opening book loading…' : 'No suggestion'}
      </span>
    </div>
  );
}

function breadcrumbPrefix(path: string[]): string {
  // Returns the "n.m..." short hint shown in My saved / Other rows
  // so the user can see at a glance which ply these correspond to.
  if (path.length === 0) return 'start';
  const lastIdx = path.length - 1;
  const moveNumber = Math.floor(lastIdx / 2) + 1;
  const isWhiteMove = lastIdx % 2 === 0;
  return `${moveNumber}.${isWhiteMove ? '' : '..'}`;
}

export default function RepertoireDetailPage({
  params,
}: {
  params: DetailParams;
}) {
  const { id } = use(params);
  const router = useRouter();

  const [name, setName] = useState<string | null>(null);
  const [color, setColor] = useState<RepertoireColor | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);

  const [path, setPath] = useState<string[]>([]);
  const [cachedGaps, setCachedGaps] = useState<RepertoireGap[]>([]);
  const [loadingGaps, setLoadingGaps] = useState(true);
  const [loadingPositions, setLoadingPositions] = useState(true);
  const [positionsError, setPositionsError] = useState<string | null>(null);
  const [clientKnownMoves, setClientKnownMoves] = useState<
    Map<string, { uci: string; san: string; id: string }>
  >(new Map());
  const [savePending, setSavePending] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [deletePending, setDeletePending] = useState(false);

  // Derived: position we're currently viewing (end of `path`).
  const currentFen = useMemo(() => playToFen(STARTING_FEN, path), [path]);
  const currentFenKey = useMemo(() => normalizeFen(currentFen), [currentFen]);

  // Load repertoire metadata via GET /api/repertoires/{id}. A dedicated
  // single-repertoire endpoint exists now, so we fetch it directly on
  // mount instead of deriving name/color from the LIST endpoint's
  // cached data — the latter only worked when the page was reached
  // via list-page navigation and broke on a direct link or a cold
  // refresh (the list fetch wasn't guaranteed to have run).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/repertoires/${encodeURIComponent(id)}`,
          { cache: 'no-store' }
        );
        if (!res.ok) return;
        const item = (await res.json()) as ApiRepertoire;
        if (!cancelled && item) {
          setName(item.name);
          setColor(item.color);
        }
      } catch {
        // Silent — board still renders from the STARTING_FEN. Header
        // falls back to "Repertoire".
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Seed `clientKnownMoves` from EVERY stored position for this
  // repertoire (NOT due-filtered — see routers/repertoire.py:520's
  // /queue for the contrast). One fetch on mount; subsequent
  // breadcrumb navigation reads from this map locally. Subsequent
  // drag-and-drop POSTs MERGE additional rows into the same map
  // (the POST response still hydrates `clientKnownMoves` for the
  // just-persisted positions) so we don't double-count or lose the
  // prior-session data.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoadingPositions(true);
      try {
        const res = await fetch(
          `/api/repertoires/${encodeURIComponent(id)}/positions`,
          { cache: 'no-store' }
        );
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as ApiError;
          throw new Error(
            body.detail ?? body.error ?? `Positions load failed (${res.status})`
          );
        }
        const rows = (await res.json()) as RepertoirePositionRow[];
        if (cancelled || !Array.isArray(rows)) return;
        setClientKnownMoves((prev) => {
          // Merge (rows win over `prev` on collision — the stored row
          // is the source of truth). POST-response merges happen
          // immediately on success, so a same-session add can't be
          // clobbered by an in-flight GET that lands later: both
          // paths produce identical entries for the same FEN.
          const next = new Map(prev);
          for (const row of rows) {
            next.set(normalizeFen(row.fen), {
              uci: row.move,
              san: moveSanAtFen(row.fen, row.move),
              id: row.id,
            });
          }
          return next;
        });
      } catch (err) {
        if (!cancelled) {
          setPositionsError(
            err instanceof Error ? err.message : 'Failed to load saved positions'
          );
        }
      } finally {
        if (!cancelled) {
          setLoadingPositions(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Load gaps ONCE on mount. The upstream endpoint is the heaviest
  // thing on this page (it walks every stored position and queries
  // Lichess Explorer per position) — we deliberately don't refetch
  // on breadcrumb navigation, even though the visible "Other moves"
  // row set changes. The local filter on `cachedGaps` is O(n) and
  // trivially fast.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoadingGaps(true);
      try {
        const res = await fetch(`/api/repertoires/${encodeURIComponent(id)}/gaps`, {
          cache: 'no-store',
        });
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as ApiError;
          throw new Error(body.detail ?? body.error ?? `Gaps load failed (${res.status})`);
        }
        const report = (await res.json()) as RepertoireGapReport;
        if (!cancelled) {
          setCachedGaps(Array.isArray(report.gaps) ? report.gaps : []);
        }
      } catch (err) {
        if (!cancelled) {
          setPageError(err instanceof Error ? err.message : 'Failed to load gaps');
        }
      } finally {
        if (!cancelled) {
          setLoadingGaps(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Filter gaps at the current position. `parent_fen` is the normalized
  // FEN of the user's stored position (see RepertoireGap schema).
  const otherMovesAtCurrent = useMemo(() => {
    return cachedGaps
      .filter((gap) => gap.parent_fen === currentFenKey)
      .sort((a, b) => b.frequency_percent - a.frequency_percent);
  }, [cachedGaps, currentFenKey]);

  // Saved move at the current position: pulled from the client-known
  // map (seeded by GET /positions on mount, merged with same-session
  // POST responses). The "10 positions" deeper count from the
  // reference isn't computed here — it would need a tree-walk over
  // all stored positions to measure subtree size, which is real work
  // deferred to a follow-up task per scope.
  const savedMoveAtCurrent = clientKnownMoves.get(currentFenKey) ?? null;

  // Unified move handler. Both colors play in turn during the build
  // flow — owner moves persist a repertoire_positions row via POST,
  // opponent moves just extend the path locally (the backend's upsert
  // replays opponent plies but writes no row for them, so POSTing an
  // opponent-only move would be a redundant round-trip; the row gets
  // persisted on the NEXT owner POST when the user plays their reply).
  //
  // The path is updated OPTIMISTICALLY for owner moves too: we extend
  // `path` synchronously (so the dropped piece lands immediately and
  // the board re-orients to the resulting position), then POST in the
  // background. On a failed POST we roll `path` back so the user can
  // retry from the position they left. This is the fix for the
  // "dragging my piece snaps it back while the save round-trips"
  // behaviour — previously the owner move only committed after the
  // POST resolved, which made every owner move (black in a black
  // repertoire) appear to bounce back and, on a slow/flaky backend,
  // look like the drag didn't take.
  const handleMove = useCallback(
    (sourceSquare: string, targetSquare: string, promotion?: string): boolean => {
      if (!color || savePending) return false;
      const beforeFen = playToFen(STARTING_FEN, path);
      const game = new Chess(beforeFen);
      let played;
      try {
        played = game.move({
          from: sourceSquare as Square,
          to: targetSquare as Square,
          promotion: promotion ?? 'q',
        });
      } catch {
        return false;
      }
      // UCI: source + target + promotion letter (only for actual
      // pawn promotions; chess.js auto-fills 'q' if the dialog path
      // wasn't used, but the resulting UCI still carries it so the
      // backend's replay produces the same position).
      const nextUci = played.promotion
        ? `${sourceSquare}${targetSquare}${played.promotion}`
        : `${sourceSquare}${targetSquare}`;
      const san = played.san;

      const isOwnerTurn = isOwnersTurn(beforeFen, color);

      if (!isOwnerTurn) {
        // Opponent reply: just extend `path`. No POST — the backend
        // replays opponent plies inside its upsert and writes nothing
        // for them, and we'd be paying a round-trip to learn nothing.
        // The user's NEXT owner move will POST the whole path
        // including this opponent ply, which is when the row lands.
        setPath((prev) => [...prev, nextUci]);
        return true;
      }

      // Owner move: extend the path optimistically, then POST the full
      // path so the backend persists a row for THIS owner-to-move
      // position (the row's `fen` is the pre-ply normalized FEN).
      const pathBefore = path; // snapshot for rollback on failure
      setSavePending(true);
      setSaveError(null);
      setPath((prev) => [...prev, nextUci]);
      (async () => {
        try {
          const res = await fetch(
            `/api/repertoires/${encodeURIComponent(id)}/positions`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ uci_moves: [...pathBefore, nextUci] }),
            }
          );
          if (!res.ok) {
            const body = (await res.json().catch(() => ({}))) as ApiError;
            throw new Error(
              body.detail ?? body.error ?? `Save failed (${res.status})`
            );
          }
          const persisted = (await res.json()) as RepertoirePositionRow[];
          // Merge persisted rows into the client-known map. Rows are
          // keyed by the OWNER-to-move pre-ply FEN (the row's `fen`),
          // which `clientKnownMoves.get(currentFenKey)` will hit when
          // the user navigates back to the position they just played
          // from. The resulting position (after this move) is opponent
          // turn, so no row gets stored there — and the panel lookup
          // at opponent-turn FENs returns null, matching the schema.
          setClientKnownMoves((prev) => {
            const next = new Map(prev);
            for (const row of persisted) {
              next.set(normalizeFen(row.fen), {
                uci: row.move,
                san,
                id: row.id,
              });
            }
            return next;
          });
        } catch (err) {
          setSaveError(err instanceof Error ? err.message : 'Failed to save move');
          // Roll back the optimistic path so the board returns to the
          // position before the failed save — the user can retry the
          // drag. Only roll back if the path is still what we set it
          // to (it should be — savePending blocks concurrent moves).
          setPath(pathBefore);
        } finally {
          setSavePending(false);
        }
      })();
      return true; // accept the drop immediately — the board is already updated
    },
    [color, path, id, savePending]
  );

  // Breadcrumb jump: truncate `path` to the given ply index.
  const handleJump = useCallback((idx: number) => {
    setPath((prev) => prev.slice(0, idx + 1));
  }, []);

  // Step controls (home/start/prev/next/end). "Next" beyond the
  // current path isn't meaningful without a tree view of the full
  // repertoire — it's disabled when there's nothing to advance to.
  const handleHome = useCallback(() => setPath([]), []);
  const handleStart = useCallback(() => setPath([]), []);
  const handlePrev = useCallback(
    () => setPath((prev) => prev.slice(0, -1)),
    []
  );
  const lastPly = path.length - 1;

  // Clicking an "Other move" suggestion navigates INTO the line the
  // gap describes — the gap's `parent_fen` is the OWNER-to-move row
  // the user has stored, the owner's stored UCI for that row is in the
  // client-known map (keyed by parent_fen), and the gap's
  // `opponent_move_uci` is the opponent reply we want to drive in.
  // Extending the path by [owner_uci, opponent_uci] lands the board
  // at the gap's `resulting_fen` (owner-to-move), where the user can
  // immediately drag their next prepared move.
  const handleOtherMoveClick = useCallback(
    (gap: RepertoireGap) => {
      if (savePending) return;
      const ownerRow = clientKnownMoves.get(normalizeFen(gap.parent_fen));
      if (!ownerRow) return;
      setPath((prev) => [...prev, ownerRow.uci, gap.opponent_move_uci]);
    },
    [clientKnownMoves, savePending]
  );

  const boardOrientation: 'white' | 'black' = color === 'black' ? 'black' : 'white';

  // Side-to-move at the currently viewed position. Used by the board's
  // canDragPiece guard so the user can grab whichever color is on move
  // (owner pieces during their turn, opponent pieces during the
  // opponent's turn — the schema persists owner rows only, but the
  // user drives both sides to walk into lines they want to prepare).
  const currentSide = currentFen.split(/\s+/)[1] ?? 'w';

  return (
    <div className="relative h-[calc(100vh-3rem)] w-full overflow-y-auto px-6 py-4 text-white lg:px-10 [background-image:url(/walnut-dark.png)] [background-size:cover] [background-position:center]">
      <div className="mx-auto flex min-h-full max-w-[1760px] flex-col gap-4">
        {/* Header — name top-left, Train top-right. The board below is
            horizontally centered between these two anchors. */}
        <header className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              href="/repertoire"
              aria-label="Back to repertoires"
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-black/50 bg-black/40 text-[#efd9a7] transition hover:bg-black/60"
            >
              <SearchBackIcon />
            </Link>
            <h1 className="min-w-0 truncate font-display text-2xl font-semibold text-[#f7e5c6] sm:text-3xl">
              {name ?? 'Repertoire'}
            </h1>
            {color && (
              <span
                className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider ${
                  color === 'white'
                    ? 'border border-[#d9b87c]/40 bg-[#ede3d0]/10 text-[#ede3d0]'
                    : 'border border-[#3a2410]/60 bg-[#120c08]/70 text-[#a79b8a]'
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`inline-block h-2.5 w-2.5 rounded-full ${
                    color === 'white' ? 'bg-[#ede3d0]' : 'bg-[#3a2410]'
                  }`}
                />
                {color}
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              aria-label="About this repertoire"
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-black/50 bg-black/40 text-[#efd9a7]/70 transition hover:bg-black/60 hover:text-[#efd9a7]"
            >
              <InfoIcon />
            </button>
            <button
              type="button"
              onClick={() => router.push(`/repertoire/${id}/train`)}
              className="group flex h-10 items-center gap-2 rounded-xl bg-[#d9b87c] px-4 text-sm font-bold uppercase tracking-wider text-[#241206] shadow-lg shadow-orange-950/40 transition hover:bg-[#efd9a7]"
            >
              <TrainIcon />
              <span>Train</span>
            </button>
          </div>
        </header>

        {(pageError || positionsError) && (
          <div className={`${CARD_CLASS} p-3 text-sm text-red-300`} role="alert">
            {[pageError, positionsError].filter(Boolean).join(' · ')}
          </div>
        )}

        {/*
          Main row — board centered, panels on the right.

          The chessboard is the SAME size as the puzzles page: that
          page mounts its board inside `max-w-[calc(100vh-70px)]`
          (puzzles/page.tsx), so we reuse the exact same cap here. The
          70px accounts for the 3rem TopNav + ~24px top margin, i.e.
          the board is sized so its square fills the viewport height
          on the puzzles page — and it renders identically here.

          Centering: a symmetric 3-column grid. The left column is an
          empty spacer with the same width (22rem) as the right panel
          column, so the board track sits dead-center under the header
          (equidistant from the name on the left and the Train button
          on the right). On narrower-than-xl viewports the grid
          collapses to one column and the spacer hides; the board is
          centered and the panels stack beneath it.
        */}
        <div className="grid grid-cols-1 items-start justify-center gap-6 xl:grid-cols-[22rem_minmax(0,calc(100vh-70px))_22rem]">
          {/* Left spacer — invisible, mirrors the right panel width to
              keep the board horizontally centered. */}
          <div className="hidden xl:block" aria-hidden="true" />

          {/* ============== BOARD (centered) ============== */}
          <section className="mx-auto flex w-full max-w-[calc(100vh-70px)] flex-col gap-2">
            {/* Breadcrumb bar */}
            <div className="rounded-lg border border-black/50 bg-black/40 backdrop-blur-sm">
              <Breadcrumb path={path} onJump={handleJump} />
            </div>

            {/* Board — aspect-square so it stays square; width is the
                section width (capped at calc(100vh-70px), matching
                the puzzles page board size). The board is gated on
                `color` so a black repertoire renders directly in the
                owner's (black) orientation instead of flashing the
                white orientation for a frame before the metadata
                fetch resolves. */}
            <div className="aspect-square w-full">
              {color === null ? (
                <div
                  className="h-full w-full animate-pulse rounded-md bg-black/35"
                  aria-label="Loading board"
                />
              ) : (
                <BoardShell
                  position={currentFen}
                  orientation={boardOrientation}
                  allowDragging
                  canDragPiece={({ piece }) => {
                    // Whichever side is on move is draggable — the
                    // schema persists owner rows only, but the user
                    // drives both colors during the build flow to walk
                    // into lines they want to prepare. (`savePending`
                    // is intentionally NOT checked here: the drag is
                    // brief-gated inside handleMove instead, so the
                    // click-to-move hint dots never get suppressed
                    // during an in-flight save.)
                    return piece.pieceType[0] === currentSide;
                  }}
                  onMove={(source, target, promotion) =>
                    handleMove(source, target, promotion)
                  }
                />
              )}
            </div>

            {/* Step controls bar */}
            <div className="flex items-center justify-center gap-3 rounded-lg border border-black/50 bg-black/40 px-3 py-2 backdrop-blur-sm">
              <button
                type="button"
                onClick={handleHome}
                aria-label="Jump to start position"
                className="rounded-md p-2 text-[#a79b8a] transition hover:bg-white/10 hover:text-[#efd9a7]"
              >
                <StepIcon kind="home" />
              </button>
              <button
                type="button"
                onClick={handleStart}
                disabled={path.length === 0}
                aria-label="First move"
                className="rounded-md p-2 text-[#a79b8a] transition hover:bg-white/10 hover:text-[#efd9a7] disabled:opacity-30"
              >
                <StepIcon kind="start" />
              </button>
              <button
                type="button"
                onClick={handlePrev}
                disabled={path.length === 0}
                aria-label="Previous move"
                className="rounded-md p-2 text-[#a79b8a] transition hover:bg-white/10 hover:text-[#efd9a7] disabled:opacity-30"
              >
                <StepIcon kind="prev" />
              </button>
              <span className="rounded-md p-1.5 text-[#d9b87c]">
                <span className="block h-0.5 w-5 rounded-full bg-[#d9b87c]" />
              </span>
              <button
                type="button"
                disabled={lastPly < 0}
                aria-label="Next move (no tree data — use breadcrumb)"
                className="rounded-md p-2 text-[#a79b8a] transition hover:bg-white/10 hover:text-[#efd9a7] disabled:opacity-30"
              >
                <StepIcon kind="next" />
              </button>
              <button
                type="button"
                disabled={lastPly < 0}
                aria-label="End of line"
                className="rounded-md p-2 text-[#a79b8a] transition hover:bg-white/10 hover:text-[#efd9a7] disabled:opacity-30"
              >
                <StepIcon kind="end" />
              </button>
              <button
                type="button"
                onClick={() =>
                  // No-op today: rotates to the next "view configuration"
                  // when one is selected. The reference shows the icon
                  // as a future affordance; we surface it disabled in
                  // the meantime so the layout matches. Wired up to a
                  // no-op handler to avoid an ever-disabled puzzle.
                  void 0
                }
                aria-label="View options"
                className="rounded-md border border-[#d9b87c]/30 p-2 text-[#d9b87c] transition hover:bg-[#d9b87c]/10"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                </svg>
              </button>
            </div>

            {saveError && (
              <p
                role="alert"
                className="rounded-lg border border-red-400/20 bg-red-400/10 px-4 py-2 text-xs text-red-300"
              >
                {saveError}
              </p>
            )}
          </section>

          {/* ============== PANELS (right) ============== */}
          <aside className="mx-auto flex w-full max-w-[22rem] flex-col gap-4">
            {/* My saved moves — compact card sized to its content */}
            <div className={`${CARD_CLASS} flex flex-col gap-2 p-4`}>
              <header className="flex items-center justify-between">
                <h3 className="font-display text-sm font-semibold uppercase tracking-wider text-[#efd9a7]/85">
                  My saved moves
                </h3>
              </header>
              {savedMoveAtCurrent ? (
                <SavedMoveRow
                  prefix={breadcrumbPrefix(path)}
                  moveSan={savedMoveAtCurrent.san}
                  count={null}
                  onDelete={() => {
                    if (deletePending) return;
                    const targetId = savedMoveAtCurrent.id;
                    setDeletePending(true);
                    setSaveError(null);
                    (async () => {
                      try {
                        const res = await fetch(
                          `/api/repertoires/positions/${encodeURIComponent(targetId)}`,
                          { method: 'DELETE' }
                        );
                        if (!res.ok) {
                          const body = (await res.json().catch(() => ({}))) as ApiError;
                          throw new Error(
                            body.detail ?? body.error ?? `Delete failed (${res.status})`
                          );
                        }
                        setClientKnownMoves((prev) => {
                          const next = new Map(prev);
                          next.delete(currentFenKey);
                          return next;
                        });
                        setPath([]);
                      } catch (err) {
                        setSaveError(
                          err instanceof Error ? err.message : 'Failed to delete move'
                        );
                      } finally {
                        setDeletePending(false);
                      }
                    })();
                  }}
                />
              ) : loadingPositions && clientKnownMoves.size === 0 ? (
                <MovePlaceholderRow
                  prefix={breadcrumbPrefix(path)}
                  loading
                />
              ) : otherMovesAtCurrent.length > 0 || path.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-[#efd9a7]/15 bg-black/25 px-3 py-3 text-center text-xs text-[#a79b8a]/85">
                  {path.length === 0
                    ? 'Drag a piece on the board to add your first move.'
                    : 'No saved move at this position yet — drag a piece to add one.'}
                </div>
              ) : (
                <MovePlaceholderRow prefix={breadcrumbPrefix(path)} />
              )}
            </div>

            {/* Other moves — lists the Lichess-sourced opponent replies
                for the current position. */}
            <div className={`${CARD_CLASS} flex flex-col gap-2 p-4`}>
              <header className="flex items-center justify-between">
                <h3 className="font-display text-sm font-semibold uppercase tracking-wider text-[#efd9a7]/85">
                  Other moves
                </h3>
              </header>
              {loadingGaps && cachedGaps.length === 0 ? (
                <>
                  {Array.from({ length: 3 }).map((_, i) => (
                    <MovePlaceholderRow
                      key={`ph-${i}`}
                      prefix={breadcrumbPrefix(path)}
                      loading
                    />
                  ))}
                </>
              ) : otherMovesAtCurrent.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-[#efd9a7]/15 bg-black/25 px-3 py-3 text-center text-xs text-[#a79b8a]/85">
                  No opponent replies catalogued here yet.
                </div>
              ) : (
                otherMovesAtCurrent.map((gap) => (
                  <OtherMoveRow
                    key={`${gap.resulting_fen}-${gap.opponent_move_uci}`}
                    prefix={breadcrumbPrefix(path)}
                    moveSan={gap.opponent_move_san}
                    frequencyPercent={gap.frequency_percent}
                    loading={loadingGaps}
                    onSelect={() => handleOtherMoveClick(gap)}
                  />
                ))
              )}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
