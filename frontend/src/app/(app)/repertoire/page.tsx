'use client';

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

/**
 * My Repertoires list page.
 *
 * Renders the user's repertoires as a scrollable column of cards. The
 * page lives inside the (app) layout so it sits underneath TopNav and
 * inherits the body's wood-grain background.
 *
 * The whole page is one client component because every control is
 * interactive (search input, color checkboxes, sort dropdown, create
 * modal, delete confirmation). There is no useful server-rendered
 * phase - the initial list IS the first paint, and auth/user id flow
 * through the proxy middleware on the API side (see src/proxy.ts).
 *
 * Pattern parity with Train / Woodpecker pages:
 *   * Wood-grain background + CARD_CLASS (from train/page.tsx) used for
 *     the create modal; the cards on this page use a lighter variant
 *     because they're the primary content, not a one-off modal.
 *   * Chessboard thumbnail via dynamic import of react-chessboard
 *     (matches train/page.tsx's RecommendedPanel which renders a
 *     mini-board for an unrelated FEN).
 *   * Fetch + error pattern matches woodpecker/page.tsx: cache: 'no-store',
 *     try/catch, surface body.detail || body.error || status.
 *   * Modal overlay style matches train/page.tsx's OpponentPrepDialog.
 *
 * Scope intentionally excludes (left for later tasks):
 *   * Detail / training page (mounted at /repertoire/{id} as a
 *     placeholder for now, so /create can navigate there).
 *   * Session start / complete flow.
 *   * The "Add positions" flow on the detail page.
 *   * The board thumbnail here deliberately renders the STANDARD
 *     STARTING POSITION - fetching a real per-repertoire position is
 *     the detail page's job. Doing it here would couple the list to
 *     a per-row query we don't need yet.
 */

const Chessboard = dynamic(
  () => import('react-chessboard').then((module) => module.Chessboard),
  {
    ssr: false,
    loading: () => (
      <div
        className="h-full w-full animate-pulse rounded-md bg-black/35"
        aria-hidden="true"
      />
    ),
  }
);

// CARD_CLASS is duplicated from train/page.tsx / woodpecker/page.tsx
// (those files declare it locally too - there is no shared card
// module yet, and inventing one for a single page is out of scope).
// Keep in sync if any of those pages change.
const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

// Lighter card variant for list cards - the page background already
// carries the dark wood grain, so a card-on-card overlay reads as a
// "raised board" rather than another dark slab. Mirrors the card
// styling on the reference image (a subtly lighter rounded panel on
// the wooden backdrop), but uses actual tokens already in use across
// the app rather than a different palette.
const ROW_CARD_CLASS =
  'rounded-2xl border border-[#3a2410]/70 bg-[#1b120d]/55 backdrop-blur-sm [box-shadow:0_8px_24px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,200,100,0.08),inset_0_-1px_0_rgba(0,0,0,0.5)]';

const STARTING_FEN =
  'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

type RepertoireColor = 'white' | 'black';

type ApiRepertoire = {
  id: string;
  name: string;
  color: RepertoireColor;
  created_at: string;
  updated_at: string;
  last_trained_at: string | null;
  times_trained: number;
  last_score_percent: number | null;
};

type SortMode = 'az' | 'za';

type ApiErrorResponse = {
  detail?: string;
  error?: string;
};

function relativeTimeFromIso(iso: string | null): string {
  if (!iso) return 'Never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'Never';
  const diffMs = Date.now() - then;
  const minutes = Math.round(diffMs / 60_000);
  if (minutes < 60) {
    if (minutes <= 1) return 'just now';
    return `${minutes}m ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.round(days / 7);
  if (weeks < 8) return `${weeks}w ago`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.round(days / 365);
  return `${years}y ago`;
}

function SearchIcon() {
  return (
    <svg
      className="h-5 w-5 text-[#efd9a7]/70"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

function ChevronDownIcon() {
  return (
    <svg
      className="h-4 w-4 text-[#efd9a7]/70"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      className="h-5 w-5"
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
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}

function CloudOffIcon() {
  return (
    <svg
      className="h-4 w-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M2.27 14.5A4.49 4.49 0 0 0 7 21h11a3.5 3.5 0 0 0 3.45-4.1" />
      <path d="M5.7 5.7A5 5 0 0 1 19 9.5" />
      <path d="M8.5 8.5a5 5 0 0 0 7 7" />
      <path d="m2 2 20 20" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg
      className="h-4 w-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

function BoardThumb() {
  // Static thumbnail: the STARTING POSITION, frozen, unclickable, no
  // coordinates. This is the per-card "what color do I play" hint
  // (we also render a small color dot next to the name).
  return (
    <div className="h-28 w-28 shrink-0 overflow-hidden rounded-lg shadow-lg shadow-black/50 ring-1 ring-black/60">
      <Chessboard
        options={{
          position: STARTING_FEN,
          allowDragging: false,
          showNotation: false,
          darkSquareStyle: {
            backgroundImage: 'url(/walnut-dark.webp)',
            backgroundSize: '140% 140%',
            backgroundPosition: 'center',
          },
          lightSquareStyle: {
            backgroundImage: 'url(/walnut-light.webp)',
            backgroundSize: '140% 140%',
            backgroundPosition: 'center',
          },
          boardStyle: { width: '100%', height: '100%' },
        }}
      />
    </div>
  );
}

function ColorDot({ color }: { color: RepertoireColor }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-3 w-3 rounded-full ring-1 ring-white/30 ${
        color === 'white' ? 'bg-[#ede3d0]' : 'bg-[#241206]'
      }`}
    />
  );
}

type SortMenuProps = {
  value: SortMode;
  onChange: (next: SortMode) => void;
};

function SortMenu({ value, onChange }: SortMenuProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (!containerRef.current) return;
      if (!containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
    return undefined;
  }, [open]);

  const label = value === 'az' ? 'A-Z' : 'Z-A';

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-lg border border-transparent px-2 py-2 text-sm font-semibold text-[#efd9a7] transition hover:bg-white/5"
      >
        <span>{label}</span>
        <ChevronDownIcon />
      </button>
      {open && (
        <ul
          role="listbox"
          className="absolute right-0 z-10 mt-1 min-w-[5rem] overflow-hidden rounded-lg border border-black/50 bg-[#1b120d]/95 py-1 shadow-2xl shadow-black/60 backdrop-blur"
        >
          {(['az', 'za'] as const).map((option) => (
            <li key={option}>
              <button
                type="button"
                role="option"
                aria-selected={value === option}
                onClick={() => {
                  onChange(option);
                  setOpen(false);
                }}
                className={`flex w-full items-center justify-end gap-2 px-3 py-1.5 text-sm font-semibold transition ${
                  value === option
                    ? 'bg-[#d9b87c]/15 text-[#efd9a7]'
                    : 'text-[#a79b8a] hover:bg-white/5 hover:text-[#efd9a7]'
                }`}
              >
                {option === 'az' ? 'A-Z' : 'Z-A'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type RepertoireCardProps = {
  repertoire: ApiRepertoire;
  onDelete: (repertoire: ApiRepertoire) => void;
};

function RepertoireCard({ repertoire, onDelete }: RepertoireCardProps) {
  return (
    <article className={`${ROW_CARD_CLASS} relative flex items-stretch gap-4 p-4`}>
      <Link
        href={`/repertoire/${repertoire.id}`}
        aria-label={`Open ${repertoire.name}`}
        className="absolute inset-0 z-0 rounded-2xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#d9b87c]"
      />
      <div className="relative z-10 pointer-events-none shrink-0">
        <BoardThumb />
      </div>

      <div className="relative z-10 pointer-events-none flex min-w-0 flex-1 flex-col justify-between py-1 pr-12">
        <div className="min-w-0">
          <h3 className="truncate font-display text-xl font-semibold text-[#f7e5c6]">
            {repertoire.name}
          </h3>
          <div className="mt-1 flex items-center gap-2 text-xs text-[#efd9a7]/80">
            <ColorDot color={repertoire.color} />
            <span className="capitalize">{repertoire.color}</span>
          </div>
        </div>

        <dl className="mt-3 space-y-1 text-xs">
          <div className="flex items-baseline gap-2">
            <dt className="text-[#a79b8a]">Last Trained:</dt>
            <dd className="font-medium text-[#efd9a7]">
              {relativeTimeFromIso(repertoire.last_trained_at)}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-[#a79b8a]">Times Trained:</dt>
            <dd className="font-medium text-[#efd9a7]">
              <BarChartIcon /> {repertoire.times_trained}
            </dd>
          </div>
          {repertoire.last_score_percent !== null && (
            <div className="flex items-baseline gap-2">
              <dt className="text-[#a79b8a]">Last Score:</dt>
              <dd className="font-medium text-[#efd9a7]">
                <TargetIcon /> {repertoire.last_score_percent.toFixed(1)}%
              </dd>
            </div>
          )}
        </dl>

        <div className="mt-3 flex items-center gap-1.5 text-xs text-[#a79b8a]/90">
          <CloudOffIcon />
          <span>Not backed up</span>
        </div>
      </div>

      <button
        type="button"
        onClick={() => onDelete(repertoire)}
        aria-label={`Delete ${repertoire.name}`}
        className="absolute right-3 top-1/2 z-20 -translate-y-1/2 flex h-9 w-9 items-center justify-center rounded-lg text-[#d97757] transition hover:bg-[#d97757]/10 hover:text-[#f5b39a] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#d97757]"
      >
        <TrashIcon />
      </button>
    </article>
  );
}

function BarChartIcon() {
  return (
    <svg
      className="mr-1 inline-block h-3.5 w-3.5 -translate-y-px align-middle text-[#d9b87c]"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 21h18" />
      <path d="M6 17V9" />
      <path d="M11 17V5" />
      <path d="M16 17v-4" />
      <path d="M21 17v-7" />
    </svg>
  );
}

function TargetIcon() {
  return (
    <svg
      className="mr-1 inline-block h-3.5 w-3.5 -translate-y-px align-middle text-[#d9b87c]"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" />
    </svg>
  );
}

type ColorCheckboxesProps = {
  whiteChecked: boolean;
  blackChecked: boolean;
  onToggleWhite: () => void;
  onToggleBlack: () => void;
};

function ColorCheckboxes({
  whiteChecked,
  blackChecked,
  onToggleWhite,
  onToggleBlack,
}: ColorCheckboxesProps) {
  return (
    <div className="flex items-center gap-6">
      <label className="flex cursor-pointer items-center gap-2 select-none">
        <input
          type="checkbox"
          checked={whiteChecked}
          onChange={onToggleWhite}
          className="peer sr-only"
        />
        <span
          aria-hidden="true"
          className={`flex h-5 w-5 items-center justify-center rounded border-2 transition ${
            whiteChecked
              ? 'border-[#d9b87c] bg-[#d9b87c]/20 text-[#efd9a7]'
              : 'border-[#a79b8a]/60 bg-transparent text-transparent'
          }`}
        >
          <svg
            className="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="m5 12 5 5 9-11" />
          </svg>
        </span>
        <span className="text-sm font-semibold text-[#efd9a7]">White</span>
      </label>
      <label className="flex cursor-pointer items-center gap-2 select-none">
        <input
          type="checkbox"
          checked={blackChecked}
          onChange={onToggleBlack}
          className="peer sr-only"
        />
        <span
          aria-hidden="true"
          className={`flex h-5 w-5 items-center justify-center rounded border-2 transition ${
            blackChecked
              ? 'border-[#d9b87c] bg-[#d9b87c]/20 text-[#efd9a7]'
              : 'border-[#a79b8a]/60 bg-transparent text-transparent'
          }`}
        >
          <svg
            className="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="m5 12 5 5 9-11" />
          </svg>
        </span>
        <span className="text-sm font-semibold text-[#efd9a7]">Black</span>
      </label>
    </div>
  );
}

type CreateDialogProps = {
  onClose: () => void;
  onCreated: (created: { id: string }) => void;
};

function CreateDialog({ onClose, onCreated }: CreateDialogProps) {
  const [name, setName] = useState('');
  const [color, setColor] = useState<RepertoireColor>('white');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = name.trim();
  const canSubmit = trimmed.length > 0 && !submitting;

  async function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch('/api/repertoires', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: trimmed, color }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
        throw new Error(body.detail ?? body.error ?? `Create failed (${response.status})`);
      }
      const created = (await response.json()) as { id: string; name: string; color: RepertoireColor };
      onCreated({ id: created.id });
    } catch (err) {
      setSubmitting(false);
      setError(err instanceof Error ? err.message : 'Failed to create repertoire');
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4"
      onClick={submitting ? undefined : onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Create a repertoire"
    >
      <div
        className={`${CARD_CLASS} relative w-full max-w-md rounded-2xl p-5`}
        onClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          disabled={submitting}
          aria-label="Close"
          className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-lg text-[#f7e5c6]/70 transition hover:bg-white/10 hover:text-[#f7e5c6] disabled:opacity-40 disabled:pointer-events-none"
        >
          <CloseIcon />
        </button>

        <h2 className="font-display text-xl font-semibold text-[#f7e5c6]">
          Create a repertoire
        </h2>
        <p className="mt-1 text-sm text-[#f7e5c6]/60">
          Pick a name and which color you&apos;ll be playing. You can add positions
          and start training on the next screen.
        </p>

        <label className="mt-4 block">
          <span className="text-[11px] font-bold uppercase tracking-[0.25em] text-[#f7e5c6]/60">
            Name
          </span>
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                handleSubmit();
              }
            }}
            placeholder="Italian Game"
            disabled={submitting}
            autoFocus
            maxLength={120}
            className="mt-1.5 w-full rounded-xl border border-black/50 bg-black/60 px-3 py-2 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-[#d9b87c]/60 focus:ring-2 focus:ring-[#d9b87c]/20 disabled:opacity-60"
          />
        </label>

        <div className="mt-3">
          <span className="text-[11px] font-bold uppercase tracking-[0.25em] text-[#f7e5c6]/60">
            Color
          </span>
          <div className="mt-1.5 grid grid-cols-2 overflow-hidden rounded-[8px] border border-black/50 bg-black/50 p-1">
            {(['white', 'black'] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setColor(option)}
                disabled={submitting}
                className={`h-10 rounded-[6px] text-sm font-semibold capitalize transition disabled:opacity-60 ${
                  color === option
                    ? 'bg-[#f7e5c6] text-[#241206]'
                    : 'text-[#f7e5c6]/70 hover:bg-white/10'
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <p
            role="alert"
            className="mt-3 rounded-xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs text-red-300"
          >
            {error}
          </p>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="h-9 rounded-lg border border-black/50 bg-black/40 px-4 text-sm font-semibold text-[#f7e5c6]/70 transition hover:bg-black/60 hover:text-[#f7e5c6] disabled:opacity-40 disabled:pointer-events-none"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="h-9 rounded-lg bg-[#10b981]/90 px-4 text-sm font-semibold text-white shadow-lg shadow-emerald-950/40 transition hover:bg-emerald-400 disabled:pointer-events-none disabled:opacity-40"
          >
            {submitting ? 'Creating…' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}

type ConfirmDeleteDialogProps = {
  repertoire: ApiRepertoire;
  onCancel: () => void;
  onConfirm: () => void;
  pending: boolean;
};

function ConfirmDeleteDialog({
  repertoire,
  onCancel,
  onConfirm,
  pending,
}: ConfirmDeleteDialogProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4"
      onClick={pending ? undefined : onCancel}
      role="dialog"
      aria-modal="true"
      aria-label={`Delete ${repertoire.name}`}
    >
      <div
        className={`${CARD_CLASS} relative w-full max-w-sm rounded-2xl p-5`}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="font-display text-lg font-semibold text-[#f7e5c6]">
          Delete &ldquo;{repertoire.name}&rdquo;?
        </h2>
        <p className="mt-2 text-sm text-[#f7e5c6]/60">
          This permanently removes the repertoire and every position and
          training session saved against it.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            className="h-9 rounded-lg border border-black/50 bg-black/40 px-4 text-sm font-semibold text-[#f7e5c6]/70 transition hover:bg-black/60 hover:text-[#f7e5c6] disabled:opacity-40 disabled:pointer-events-none"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            className="h-9 rounded-lg bg-[#d97757] px-4 text-sm font-semibold text-white shadow-lg shadow-orange-950/40 transition hover:bg-[#c6654a] disabled:pointer-events-none disabled:opacity-40"
          >
            {pending ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function RepertoireListPage() {
  const router = useRouter();

  const [repertoires, setRepertoires] = useState<ApiRepertoire[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState('');
  const [whiteOn, setWhiteOn] = useState(true);
  const [blackOn, setBlackOn] = useState(true);
  const [sort, setSort] = useState<SortMode>('az');

  const [createOpen, setCreateOpen] = useState(false);
  const [deleting, setDeleting] = useState<ApiRepertoire | null>(null);
  const [deletePending, setDeletePending] = useState(false);

  const titleId = useId();

  const load = useCallback(async () => {
    setError(null);
    try {
      const response = await fetch('/api/repertoires', { cache: 'no-store' });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
        throw new Error(body.detail ?? body.error ?? `Failed to load (${response.status})`);
      }
      const data = (await response.json()) as ApiRepertoire[];
      setRepertoires(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load repertoires');
      setRepertoires([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const visible = useMemo(() => {
    if (!repertoires) return null;
    const term = search.trim().toLowerCase();
    const allowedColors = new Set<RepertoireColor>();
    if (whiteOn) allowedColors.add('white');
    if (blackOn) allowedColors.add('black');

    const filtered = repertoires.filter((r) => {
      if (!allowedColors.has(r.color)) return false;
      if (term && !r.name.toLowerCase().includes(term)) return false;
      return true;
    });

    const sorted = [...filtered];
    sorted.sort((a, b) => {
      const cmp = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
      return sort === 'az' ? cmp : -cmp;
    });
    return sorted;
  }, [repertoires, search, whiteOn, blackOn, sort]);

  function handleCreateClick() {
    setCreateOpen(true);
  }

  function handleCreated(created: { id: string }) {
    setCreateOpen(false);
    // Navigate to the placeholder detail page; it will be replaced by
    // the real detail page in a later task.
    router.push(`/repertoire/${created.id}`);
  }

  function handleDeleteClick(repertoire: ApiRepertoire) {
    setDeleting(repertoire);
  }

  async function handleDeleteConfirm() {
    if (!deleting) return;
    setDeletePending(true);
    try {
      const response = await fetch(`/api/repertoires/${encodeURIComponent(deleting.id)}`, {
        method: 'DELETE',
      });
      if (!response.ok && response.status !== 404) {
        const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
        throw new Error(body.detail ?? body.error ?? `Delete failed (${response.status})`);
      }
      setDeleting(null);
      setDeletePending(false);
      // Optimistic local removal, then a background reload to stay
      // honest with the server's view (timestamps, future sessions).
      setRepertoires((prev) => (prev ? prev.filter((r) => r.id !== deleting.id) : prev));
    } catch (err) {
      setDeletePending(false);
      setError(err instanceof Error ? err.message : 'Failed to delete repertoire');
    }
  }

  return (
    <div className="relative h-[calc(100vh-3rem)] w-full overflow-y-auto px-6 py-6 text-white lg:px-10 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="mx-auto flex max-w-3xl flex-col gap-5">
        <header
          className="flex items-center justify-between pt-2"
          aria-labelledby={titleId}
        >
          <h1
            id={titleId}
            className="font-display text-2xl font-semibold tracking-tight text-[#f7e5c6]"
          >
            My Repertoires
          </h1>
        </header>

        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <div className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-black/50 bg-black/40 px-3 py-2.5 transition focus-within:border-[#d9b87c]/60">
              <SearchIcon />
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search"
                aria-label="Search repertoires"
                className="min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-white/30"
              />
            </div>
            <SortMenu value={sort} onChange={setSort} />
          </div>

          <div className="flex items-center justify-between">
            <ColorCheckboxes
              whiteChecked={whiteOn}
              blackChecked={blackOn}
              onToggleWhite={() => setWhiteOn((v) => !v)}
              onToggleBlack={() => setBlackOn((v) => !v)}
            />
            <button
              type="button"
              onClick={handleCreateClick}
              className="rounded-xl border border-[#d9b87c] px-5 py-2 text-sm font-semibold text-[#efd9a7] transition hover:bg-[#d9b87c]/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7]"
            >
              + Create
            </button>
          </div>
        </div>

        {error && repertoires === null && (
          <div className={`${ROW_CARD_CLASS} p-5 text-sm text-red-300`} role="alert">
            <p>{error}</p>
            <button
              type="button"
              onClick={load}
              className="mt-3 rounded-lg border border-white/15 px-3 py-1.5 text-xs font-semibold text-white/80 transition hover:bg-white/5"
            >
              Retry
            </button>
          </div>
        )}

        {!repertoires ? (
          <div className="flex justify-center py-12">
            <div className="h-9 w-9 animate-spin rounded-full border-2 border-[#d9b87c] border-t-transparent" />
          </div>
        ) : visible && visible.length === 0 ? (
          <div className={`${ROW_CARD_CLASS} flex flex-col items-center gap-3 px-6 py-12 text-center`}>
            <p className="font-display text-lg font-semibold text-[#f7e5c6]">
              {repertoires.length === 0
                ? 'No repertoires yet'
                : 'No repertoires match your filters'}
            </p>
            <p className="max-w-sm text-sm text-[#a79b8a]">
              {repertoires.length === 0
                ? 'Create your first repertoire to start training openings you actually play.'
                : 'Try a different search term, or turn on White / Black if you have both.'}
            </p>
            {repertoires.length === 0 ? (
              <button
                type="button"
                onClick={handleCreateClick}
                className="mt-1 rounded-xl border border-[#d9b87c] px-5 py-2 text-sm font-semibold text-[#efd9a7] transition hover:bg-[#d9b87c]/10"
              >
                + Create your first repertoire
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setSearch('');
                  setWhiteOn(true);
                  setBlackOn(true);
                }}
                className="mt-1 rounded-lg border border-white/15 px-3 py-1.5 text-xs font-semibold text-white/80 transition hover:bg-white/5"
              >
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-4 pb-8">
            {visible!.map((repertoire) => (
              <RepertoireCard
                key={repertoire.id}
                repertoire={repertoire}
                onDelete={handleDeleteClick}
              />
            ))}
          </div>
        )}
      </div>

      {createOpen && <CreateDialog onClose={() => setCreateOpen(false)} onCreated={handleCreated} />}
      {deleting && (
        <ConfirmDeleteDialog
          repertoire={deleting}
          onCancel={() => (deletePending ? undefined : setDeleting(null))}
          onConfirm={handleDeleteConfirm}
          pending={deletePending}
        />
      )}
    </div>
  );
}
