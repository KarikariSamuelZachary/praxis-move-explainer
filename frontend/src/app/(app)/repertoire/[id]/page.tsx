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
 *   * `clientKnownMoves: Map<normalizedFen, KnownMove[]>` — stored
 *     rows grouped by their row's FEN, with the row id threaded
 *     through so the trash icon can DELETE the row. A FEN can hold
 *     SEVERAL rows (one per saved move — a diverging repertoire).
 *     Seeded ONCE on mount from `GET /api/repertoires/{id}/positions`
 *     (every stored row, unfiltered by FSRS due), and merged with
 *     additional entries from in-session POST responses as the user
 *     drags new moves. Used to populate "My saved moves" at the
 *     current position (all saved continuations from here — see
 *     `savedContinuations`).
 *   * `suggestions` — Stockfish top-N for the CURRENT position,
 *     fetched per navigation; filtered against the saved set to
 *     render "Other moves" (unsaved replies only).
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
 * `train/opponent-prep/page.tsx:599` mounts it directly for interactive
 * play with `onPieceDrop`. Same pattern here — direct dynamic
 * import of `react-chessboard`'s `Chessboard` component with the
 * walnut square styling that every other Praxis board uses.
 *
 * Known backend gaps (REPORTED in this task's summary):
 *   * The header's name/color now come from GET /api/repertoires/{id}
 *     (the dedicated single-repertoire endpoint) — no list-page
 *     derivation anymore, so a direct link / cold refresh works.
 *   * The per-row subtree counts in "My saved moves" are computed
 *     CLIENT-SIDE by `countSubtreeRows` (a bounded walk over the
 *     already-fetched /positions map — owner nodes follow their one
 *     stored row, opponent nodes fan into every prepared reply). No
 *     server tree logic is duplicated; a 400-node guard caps corrupt
 *     data.
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
 *     Same as train/opponent-prep/page.tsx (which does humanColor flips).
 */

import { use, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Chess, type Square } from 'chess.js';

import BoardShell from '@/components/board/BoardShell';
import ReviewShell from '@/components/review/ReviewShell';

const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

// Wooden-box button style, lifted verbatim from the game-review page's
// movement controls (components/review/BoardPanel.tsx + AnalysisPanel.tsx)
// so the relocated navigation strip matches that card exactly.
const WOOD_BOX_STYLE: React.CSSProperties = {
  borderRadius: '4px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
};

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

// Shape returned by GET /api/repertoires/suggestions (Stockfish top-N).
type Suggestion = {
  uci: string;
  san: string;
  score_cp: number;
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

// One stored repertoire_positions row as tracked client-side. Keyed by
// the row's normalized 4-field pre-ply FEN in `clientKnownMoves`
// (a FEN can hold SEVERAL rows — one per saved move, i.e. one per
// prepared branch from that position).
type KnownMove = { uci: string; san: string; id: string; createdAt: string };

// Merge stored rows into a per-FEN KnownMove map (mutates `target`).
// Same-UCI rows are replaced in place (re-save is idempotent);
// different UCIs at the same FEN append — that's a diverging
// repertoire (e.g. both Nf3 and Be2 prepared from one position).
function mergeKnownRows(
  target: Map<string, KnownMove[]>,
  rows: RepertoirePositionRow[]
) {
  for (const row of rows) {
    const key = normalizeFen(row.fen);
    const entry: KnownMove = {
      uci: row.move,
      san: moveSanAtFen(row.fen, row.move),
      id: row.id,
      createdAt: row.created_at,
    };
    const list = target.get(key) ?? [];
    const idx = list.findIndex((e) => e.uci === entry.uci);
    if (idx >= 0) {
      list[idx] = entry;
    } else {
      list.push(entry);
    }
    target.set(key, list);
  }
}

// Count the stored rows in the branch subtree rooted at `rootFen`.
// Walk: every stored row at a node contributes 1 and follows its own
// move (a FEN can hold SEVERAL saved moves — each branch is walked);
// nodes with no rows are leaves. Bounded by a visited set
// (transpositions/cycles) and a node guard so a corrupt map can never
// spin the walk. All data is already client-side (the full /positions
// fetch), so no server tree logic is duplicated.
function countSubtreeRows(
  rootFen: string,
  known: Map<string, KnownMove[]>
): number {
  const visited = new Set<string>();
  const stack: string[] = [normalizeFen(rootFen)];
  let count = 0;
  let guard = 0;
  while (stack.length > 0 && guard++ < 400) {
    const key = stack.pop() as string;
    if (visited.has(key)) continue;
    visited.add(key);
    const rows = known.get(key) ?? [];
    if (rows.length > 0) {
      count += rows.length;
      for (const row of rows) {
        try {
          const next = new Chess(`${key} 0 1`);
          next.move({
            from: row.uci.slice(0, 2),
            to: row.uci.slice(2, 4),
            promotion: row.uci.length > 4 ? row.uci[4] : undefined,
          });
          stack.push(normalizeFen(next.fen()));
        } catch {
          // Stale/corrupt row — this branch is a leaf.
        }
      }
    }
  }
  return count;
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
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    </svg>
  );
}

// Format a Stockfish centipawn score (from the mover's perspective) into
// a compact human label: "+0.4", "0.0", "-0.3", or "M" for mate.
function formatScoreCp(cp: number): string {
  if (cp >= 9000) return 'M';
  if (cp <= -9000) return '−M';
  const sign = cp > 0 ? '+' : '';
  return `${sign}${(cp / 100).toFixed(1)}`;
}

type SuggestionRowProps = {
  moveSan: string;
  scoreCp: number;
  isBest: boolean;
  onSelect: () => void;
};

function SuggestionRow({ moveSan, scoreCp, isBest, onSelect }: SuggestionRowProps) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className="group/other flex w-full items-center gap-3 rounded-2xl border border-white/5 bg-black/25 px-3 py-2.5 text-left text-sm transition hover:border-[#d9b87c]/30 hover:bg-black/40"
    >
      <span className="font-mono text-base font-semibold text-[#efd9a7]/85">
        {moveSan}
      </span>
      <span
        className={`ml-auto shrink-0 font-mono text-xs tabular-nums ${
          scoreCp >= 0 ? 'text-emerald-300/90' : 'text-red-300/90'
        }`}
      >
        {formatScoreCp(scoreCp)}
      </span>
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-[#a79b8a]/55">
        {isBest ? 'Best' : 'Stockfish'}
      </span>
    </button>
  );
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
  const [path, setPath] = useState<string[]>([]);
  const [loadingPositions, setLoadingPositions] = useState(true);
  const [positionsError, setPositionsError] = useState<string | null>(null);
  const [clientKnownMoves, setClientKnownMoves] = useState<
    Map<string, KnownMove[]>
  >(new Map());
  const [savePending, setSavePending] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const [suggestionsError, setSuggestionsError] = useState<string | null>(null);

  // Derived: position we're currently viewing (end of `path`).
  const currentFen = useMemo(() => playToFen(STARTING_FEN, path), [path]);

  // Total stored rows (one per saved move — NOT the FEN count, which
  // would undercount diverging positions that hold several branches).
  const totalSavedRows = useMemo(
    () =>
      Array.from(clientKnownMoves.values()).reduce(
        (n, list) => n + list.length,
        0
      ),
    [clientKnownMoves]
  );

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
          // Merge into a copy (rows win on same-UCI collision — the
          // stored row is the source of truth; DIFFERENT UCIs at the
          // same FEN append as separate branches). POST-response
          // merges happen immediately on success, so a same-session
          // add can't be clobbered by an in-flight GET that lands
          // later: both paths produce identical entries.
          const next = new Map(prev);
          mergeKnownRows(next, rows);
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

  // Stockfish move suggestions for the CURRENT position. Fetched on
  // every position change (a ~0.4s multi-PV analysis), so the "Other
  // moves" panel always reflects the position on the board. Replaces
  // the previous Lichess Explorer gap feed, which was network-dependent
  // and returned nothing when the local machine couldn't reach Explorer.
  useEffect(() => {
    if (color === null) return;
    let cancelled = false;
    setLoadingSuggestions(true);
    setSuggestionsError(null);
    (async () => {
      try {
        const res = await fetch(
          `/api/repertoires/suggestions?fen=${encodeURIComponent(currentFen)}`,
          { cache: 'no-store' }
        );
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as ApiError;
          throw new Error(
            body.detail ?? body.error ?? `Suggestions failed (${res.status})`
          );
        }
        const data = (await res.json()) as { suggestions?: Suggestion[] };
        if (!cancelled) {
          setSuggestions(Array.isArray(data.suggestions) ? data.suggestions : []);
        }
      } catch (err) {
        if (!cancelled) {
          setSuggestionsError(
            err instanceof Error ? err.message : 'Failed to load suggestions'
          );
        }
      } finally {
        if (!cancelled) {
          setLoadingSuggestions(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [currentFen, color]);

  // The primary saved line — the ordered UCI move sequence reconstructed
  // by walking the stored rows from the start position. At EVERY node
  // (owner AND opponent alike) it follows the EARLIEST-CREATED saved
  // row at that FEN (the same main-line tiebreak the backend tree
  // classifier uses). Used by the step-controls' "Last" button.
  const savedLine = useMemo(() => {
    if (!color) return [];
    const game = new Chess(STARTING_FEN);
    const line: string[] = [];
    let guard = 0;
    while (guard++ < 400) {
      const fenKey = normalizeFen(game.fen());
      const rows = clientKnownMoves.get(fenKey) ?? [];
      if (rows.length === 0) break;
      const saved = [...rows].sort((a, b) =>
        a.createdAt < b.createdAt
          ? -1
          : a.createdAt > b.createdAt
            ? 1
            : a.id < b.id
              ? -1
              : 1
      )[0];
      try {
        game.move({
          from: saved.uci.slice(0, 2),
          to: saved.uci.slice(2, 4),
          promotion: saved.uci.length > 4 ? saved.uci[4] : undefined,
        });
      } catch {
        break;
      }
      line.push(saved.uci);
    }
    return line;
  }, [clientKnownMoves, color]);

  // "My saved moves" AT THE CURRENT POSITION — every saved
  // continuation from here. Under the save-every-ply model, the
  // current FEN holds rows for BOTH the owner AND the opponent (a
  // row is stored for every ply, regardless of which side is on
  // move). So the continuations are simply ALL rows at the current
  // FEN — no legal-move fan-out at opponent nodes anymore. Each row
  // carries the stored row's id (for DELETE) plus the subtree's
  // stored-row count.
  const savedContinuations = useMemo<
    {
      uci: string;
      san: string;
      rowId: string;
      rowFenKey: string;
      subtreeRows: number;
    }[]
  >(() => {
    if (!color) return [];
    const key = normalizeFen(currentFen);
    const results: {
      uci: string;
      san: string;
      rowId: string;
      rowFenKey: string;
      subtreeRows: number;
    }[] = [];

    // Every row at the current FEN is a continuation — owner moves
    // AND opponent moves. The subtree count is per-branch: the walk
    // starts AFTER this row's move, so sibling branches' rows don't
    // inflate the number.
    for (const row of clientKnownMoves.get(key) ?? []) {
      let subtreeRows = 0;
      try {
        const probe = new Chess(currentFen);
        probe.move({
          from: row.uci.slice(0, 2),
          to: row.uci.slice(2, 4),
          promotion: row.uci.length > 4 ? row.uci[4] : undefined,
        });
        subtreeRows = countSubtreeRows(probe.fen(), clientKnownMoves);
      } catch {
        subtreeRows = 0;
      }
      results.push({
        uci: row.uci,
        san: row.san,
        rowId: row.id,
        rowFenKey: key,
        subtreeRows,
      });
    }
    return results;
  }, [color, currentFen, clientKnownMoves]);

  // "Other moves" excludes anything already saved at this position —
  // a move the user built a subtree for belongs in "My saved moves",
  // never in both panels (the saved set is now potentially multi-row,
  // so every one of its UCIs must be checked against suggestions).
  const otherMoves = useMemo(
    () =>
      suggestions.filter(
        (s) => !savedContinuations.some((c) => c.uci === s.uci)
      ),
    [suggestions, savedContinuations]
  );

  // Board arrows — one arrow PER saved continuation from the current
  // position, so a diverged repertoire shows every branch at once
  // (e.g. a position with both Nf3 and Be2 saved draws the knight
  // arrow g1→f3 AND the bishop arrow c1/f1→e2). Owner moves render
  // gold, opponent replies rust. A single board position has one
  // side to move, so every continuation arrow starts from that side.
  const lineArrows = useMemo<
    { startSquare: string; endSquare: string; color: string }[]
  >(() => {
    if (!color) return [];
    const isOwnerMove = isOwnersTurn(currentFen, color);
    return savedContinuations.map((c) => ({
      startSquare: c.uci.slice(0, 2),
      endSquare: c.uci.slice(2, 4),
      color: isOwnerMove ? '#d9b87c' : '#a34a2a',
    }));
  }, [savedContinuations, currentFen, color]);

  // Unified move handler. Both colors play in turn during the build
  // flow — EVERY move (owner AND opponent) is POSTed so the backend
  // persists a row for every ply. This is the fix for "the last move
  // black played is not saved": when the line ends with an opponent
  // reply (e.g. d4 d5), d5 is POSTed and stored even though no owner
  // move follows.
  //
  // The path is updated OPTIMISTICALLY: we extend `path` synchronously
  // (so the dropped piece lands immediately and the board re-orients),
  // then POST in the background. On a failed POST we roll `path` back
  // so the user can retry from the position they left. This is the fix
  // for the "dragging my piece snaps it back while the save round-
  // trips" behaviour — previously the move only committed after the
  // POST resolved, which made every move appear to bounce back on a
  // slow/flaky backend.
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
      const nextUci = played.promotion
        ? `${sourceSquare}${targetSquare}${played.promotion}`
        : `${sourceSquare}${targetSquare}`;

      // Owner AND opponent moves both POST the full path. The backend
      // saves a row for EVERY ply in the path (the new save-every-ply
      // model), so the opponent's reply is persisted alongside the
      // owner's move — including terminal opponent moves with no
      // following owner move (e.g. d4 d5 stops at d5 and d5 IS saved).
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
          // keyed by the pre-ply normalized FEN (the row's `fen`).
          // The POST response re-returns EVERY row on the replayed
          // path (the backend upserts the whole line, not just the
          // new ply), so mergeKnownRows handles all of them.
          //
          // IMPORTANT: the SAN is computed PER ROW (`moveSanAtFen`),
          // not reused from the just-played move. Reusing one SAN
          // would stamp every saved move in the line with the latest
          // move's name (e.g. "e4" would read "Bxd5").
          setClientKnownMoves((prev) => {
            const next = new Map(prev);
            mergeKnownRows(next, persisted);
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

  // Navigation strip (first / prev / next / last) — relocated under the
  // "Other moves" panel and restyled to match the game-review movement
  // buttons. "Next" steps into the UNIQUE saved continuation from the
  // current position (the same move the board arrow points at) and is
  // disabled at branch points/leaves — that's what keeps arrow, Next
  // and the saved-moves list consistent with each other. "Last" jumps
  // to the end of the primary reconstructed line.
  const atStart = path.length === 0;
  const atEnd = path.length >= savedLine.length;
  const hasUniqueContinuation = savedContinuations.length === 1;

  const handleStart = useCallback(() => setPath([]), []);
  const handlePrev = useCallback(
    () => setPath((prev) => prev.slice(0, -1)),
    []
  );
  const handleNext = useCallback(() => {
    if (savedContinuations.length !== 1) return;
    setPath((prev) => [...prev, savedContinuations[0].uci]);
  }, [savedContinuations]);
  const handleEnd = useCallback(() => setPath(savedLine), [savedLine]);

  // Clicking a "My saved moves" row navigates the board forward into
  // that continuation by appending the row's move to `path` — the same
  // local path-append the opponent-reply branch of `handleMove` uses
  // (no POST: the move is already stored, this is pure navigation).
  const handleContinuationClick = useCallback((uci: string) => {
    setPath((prev) => [...prev, uci]);
  }, []);

  // Trash icon on a saved-moves row: DELETE the anchoring stored row
  // via the existing per-position endpoint (the backend 409s if the
  // row still has prepared responses beneath it — that detail is
  // surfaced verbatim through the board's save-error toast). On
  // success ONLY that row is dropped from the per-FEN list (a sibling
  // branch at the same position stays) so the panels and arrows
  // recompute immediately.
  const handleDeleteRow = useCallback(
    async (rowId: string, rowFenKey: string) => {
      try {
        const res = await fetch(
          `/api/repertoires/positions/${encodeURIComponent(rowId)}`,
          { method: 'DELETE' }
        );
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as ApiError;
          throw new Error(
            body.detail ??
              body.error ??
              `Delete failed (${res.status})`
          );
        }
        setClientKnownMoves((prev) => {
          const list = prev.get(rowFenKey);
          if (!list || !list.some((e) => e.id === rowId)) return prev;
          const next = new Map(prev);
          const filtered = list.filter((e) => e.id !== rowId);
          if (filtered.length === 0) {
            next.delete(rowFenKey);
          } else {
            next.set(rowFenKey, filtered);
          }
          return next;
        });
      } catch (err) {
        setSaveError(
          err instanceof Error ? err.message : 'Failed to delete move'
        );
      }
    },
    []
  );

  // Clicking a Stockfish suggestion plays that move on the board via
  // the same `handleMove` path a drag/click uses (owner moves are
  // saved + POSTed, opponent moves just extend the path). The move's
  // from/to/promotion are parsed out of its UCI.
  const handleSuggestionClick = useCallback(
    (uci: string) => {
      if (savePending) return;
      void handleMove(
        uci.slice(0, 2),
        uci.slice(2, 4),
        uci.length > 4 ? uci[4] : undefined
      );
    },
    [handleMove, savePending]
  );

  const boardOrientation: 'white' | 'black' = color === 'black' ? 'black' : 'white';

  // Side-to-move at the currently viewed position. Used by the board's
  // canDragPiece guard so the user can grab whichever color is on move
  // (owner pieces during their turn, opponent pieces during the
  // opponent's turn — the schema persists owner rows only, but the
  // user drives both sides to walk into lines they want to prepare).
  const currentSide = currentFen.split(/\s+/)[1] ?? 'w';

  return (
    <div className="relative -mt-2 h-[calc(100vh-2.5rem)] w-full overflow-y-auto px-6 pb-[10px] pt-6 text-white lg:overflow-hidden lg:px-10 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <ReviewShell
        importPanel={
          <aside className={`${CARD_CLASS} flex w-full flex-col p-5`}>
            <div className="flex items-center justify-between gap-2">
              <Link
                href="/repertoire"
                aria-label="Back to repertoires"
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-black/50 bg-black/40 text-[#efd9a7] transition hover:bg-black/60"
              >
                <SearchBackIcon />
              </Link>
              <button
                type="button"
                aria-label="About this repertoire"
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-black/50 bg-black/40 text-[#efd9a7]/70 transition hover:bg-black/60 hover:text-[#efd9a7]"
              >
                <InfoIcon />
              </button>
            </div>

            {/* Identity row — king glyph tile in the repertoire's
                color beside the name. Fit-content height: the card
                ends after the hint instead of stretching the rail. */}
            <div className="mt-4 flex items-center gap-3">
              <span
                aria-hidden="true"
                className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border text-[26px] leading-none ${
                  color === 'black'
                    ? 'border-[#3a2410]/60 bg-[#120c08]/80 text-[#a79b8a]'
                    : 'border-[#d9b87c]/40 bg-[#ede3d0]/10 text-[#ede3d0]'
                }`}
              >
                {color === 'black' ? '♚' : '♔'}
              </span>
              <div className="min-w-0 flex-1">
                <h1 className="truncate font-display text-xl font-semibold text-[#f7e5c6]">
                  {name ?? 'Repertoire'}
                </h1>
                <p className="mt-0.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-[#a79b8a]">
                  {color ? `${color} repertoire` : 'Loading…'}
                </p>
              </div>
            </div>

            {/* Saved-move counter — owner rows stored for this line. */}
            <div className="mt-4 flex items-center justify-between rounded-xl border border-black/40 bg-black/30 px-3 py-2">
              <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#a79b8a]/80">
                Saved moves
              </span>
              <span className="font-mono text-sm font-semibold tabular-nums text-[#efd9a7]">
                {totalSavedRows}
              </span>
            </div>

            <button
              type="button"
              onClick={() => router.push(`/repertoire/${id}/train`)}
              className="mt-4 flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-[#d9b87c] text-sm font-bold uppercase tracking-wider text-[#241206] shadow-lg shadow-orange-950/40 transition hover:bg-[#efd9a7]"
            >
              <TrainIcon />
              <span>Train</span>
            </button>

            <p className="mt-3 text-center text-[11px] leading-4 text-[#a79b8a]/70">
              Drag a piece or tap a suggestion to extend the line.
            </p>
          </aside>
        }
        boardPanel={
          <div className="relative mx-auto aspect-square w-full max-w-[calc(100vh-70px)]">
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
                arrows={lineArrows}
                canDragPiece={({ piece }) => piece.pieceType[0] === currentSide}
                onMove={(source, target, promotion) =>
                  handleMove(source, target, promotion)
                }
              />
            )}
            {saveError && (
              <div className="pointer-events-none absolute inset-x-0 top-3 z-20 flex justify-center px-4">
                <p
                  role="alert"
                  className="rounded-lg border border-red-400/20 bg-red-950/85 px-4 py-2 text-xs text-red-200 shadow-lg"
                >
                  {saveError}
                </p>
              </div>
            )}
          </div>
        }
        analysisPanel={
          <aside className="flex h-full flex-col gap-4 overflow-hidden">
            <div className="wood-scrollbar flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1">
              {positionsError && (
                <div className={`${CARD_CLASS} p-3 text-sm text-red-300`} role="alert">
                  {positionsError}
                </div>
              )}

              {/* My saved moves — every saved continuation FROM THE
                  CURRENT POSITION (there can be several at opponent
                  nodes). Rows are clickable to descend into their
                  branch; the trash icon deletes the anchoring row. */}
              <div className={`${CARD_CLASS} flex flex-col gap-2 p-4`}>
                <header className="flex items-center justify-between">
                  <h3 className="font-display text-sm font-semibold uppercase tracking-wider text-[#efd9a7]/85">
                    My saved moves
                  </h3>
                  {savedContinuations.length > 1 && (
                    <span className="text-[10px] uppercase tracking-wider text-[#a79b8a]/55">
                      {savedContinuations.length} branches
                    </span>
                  )}
                </header>
                {savedContinuations.length === 0 ? (
                  <div className="rounded-2xl border border-dashed border-[#efd9a7]/15 bg-black/25 px-3 py-3 text-center text-xs text-[#a79b8a]/85">
                    {loadingPositions && clientKnownMoves.size === 0
                      ? 'Loading saved moves…'
                      : 'Nothing saved from this position yet — drag a piece or tap a suggestion below.'}
                  </div>
                ) : (
                  savedContinuations.map((c) => (
                    <div
                      key={c.rowId}
                      className="flex items-stretch overflow-hidden rounded-2xl border border-white/5 bg-black/25 transition hover:border-[#d9b87c]/30 hover:bg-black/40"
                    >
                      <button
                        type="button"
                        onClick={() => handleContinuationClick(c.uci)}
                        title={`Step into ${c.san}`}
                        className="flex flex-1 items-center gap-3 px-3 py-2.5 text-left"
                      >
                        <span className="font-mono text-base font-semibold text-[#efd9a7]/85">
                          {c.san}
                        </span>
                        <span className="ml-auto shrink-0 text-[10px] uppercase tracking-wider text-[#a79b8a]/55">
                          {c.subtreeRows}{' '}
                          {c.subtreeRows === 1 ? 'position' : 'positions'}
                        </span>
                      </button>
                      <button
                        type="button"
                        aria-label={`Delete saved move ${c.san}`}
                        title={`Delete ${c.san}`}
                        onClick={() => void handleDeleteRow(c.rowId, c.rowFenKey)}
                        className="flex w-9 shrink-0 items-center justify-center border-l border-white/5 text-[#a79b8a]/60 transition hover:bg-red-500/15 hover:text-red-300"
                      >
                        <TrashIcon />
                      </button>
                    </div>
                  ))
                )}
              </div>

              {/* Other moves — suggestions for the current position
                  that are NOT already saved here, clickable to play
                  the move on the board. */}
              <div className={`${CARD_CLASS} flex flex-col gap-2 p-4`}>
                <header className="flex items-center justify-between">
                  <h3 className="font-display text-sm font-semibold uppercase tracking-wider text-[#efd9a7]/85">
                    Other moves
                  </h3>
                </header>
                {loadingSuggestions && otherMoves.length === 0 ? (
                  <div className="flex items-center justify-center gap-2 rounded-2xl border border-white/5 bg-black/25 px-3 py-3 text-xs text-[#a79b8a]">
                    <span className="h-3 w-3 animate-spin rounded-full border-2 border-[#a79b8a]/40 border-t-transparent" />
                    <span>Analysing position…</span>
                  </div>
                ) : suggestionsError ? (
                  <div className="rounded-2xl border border-red-400/20 bg-red-400/10 px-3 py-2 text-xs text-red-300">
                    {suggestionsError}
                  </div>
                ) : otherMoves.length === 0 ? (
                  <div className="rounded-2xl border border-dashed border-[#efd9a7]/15 bg-black/25 px-3 py-3 text-center text-xs text-[#a79b8a]/85">
                    No unsaved moves to suggest here.
                  </div>
                ) : (
                  otherMoves.map((s, i) => (
                    <SuggestionRow
                      key={s.uci}
                      moveSan={s.san}
                      scoreCp={s.score_cp}
                      isBest={i === 0}
                      onSelect={() => handleSuggestionClick(s.uci)}
                    />
                  ))
                )}
              </div>
            </div>

            {/* Navigation strip — first / prev / next / last. Styled as
                four wooden boxes to match the game-review movement
                controls, pinned to the bottom of the right panel. */}
            <div className="grid grid-cols-4 gap-1.5">
              <button
                type="button"
                onClick={handleStart}
                disabled={atStart}
                aria-label="First move"
                className="flex h-8 items-center justify-center transition-transform hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
                style={{
                  cursor: atStart ? 'default' : 'pointer',
                  ...WOOD_BOX_STYLE,
                  borderRadius: '4px 4px 4px 24px',
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0">
                  <rect x="3" y="4" width="2.5" height="16" rx="1" />
                  <path d="M21 4 L9 12 L21 20 Z" />
                </svg>
              </button>
              <button
                type="button"
                onClick={handlePrev}
                disabled={atStart}
                aria-label="Previous move"
                className="flex h-8 items-center justify-center transition-transform hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
                style={{
                  cursor: atStart ? 'default' : 'pointer',
                  ...WOOD_BOX_STYLE,
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0">
                  <path d="M18 4 L6 12 L18 20 Z" />
                </svg>
              </button>
              <button
                type="button"
                onClick={handleNext}
                disabled={!hasUniqueContinuation}
                aria-label="Next move"
                className="flex h-8 items-center justify-center transition-transform hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
                style={{
                  cursor: hasUniqueContinuation ? 'pointer' : 'default',
                  ...WOOD_BOX_STYLE,
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0">
                  <path d="M6 4 L18 12 L6 20 Z" />
                </svg>
              </button>
              <button
                type="button"
                onClick={handleEnd}
                disabled={atEnd}
                aria-label="Last move"
                className="flex h-8 items-center justify-center transition-transform hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
                style={{
                  cursor: atEnd ? 'default' : 'pointer',
                  ...WOOD_BOX_STYLE,
                  borderRadius: '4px 4px 24px 4px',
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0">
                  <path d="M3 4 L15 12 L3 20 Z" />
                  <rect x="18.5" y="4" width="2.5" height="16" rx="1" />
                </svg>
              </button>
            </div>
          </aside>
        }
      />
    </div>
  );
}
