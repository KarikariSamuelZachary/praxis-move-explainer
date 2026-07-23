'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

type ImportStatus = 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'error';
type ProfileStatus = 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'error';

type StartImportResponse = {
  job_id: string;
  status: 'queued';
  lichess_username?: string | null;
  chesscom_username?: string | null;
  limit: number;
};

type JobStatusResponse = {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  imported_count: number;
  error_message?: string | null;
};

type AccuracyRow = {
  key: string;
  accuracy: number;
  total_moves: number;
  mistakes: number;
  blunders: number;
};

type MistakeTypeRow = {
  type: string;
  count: number;
};

type WorstPosition = {
  fen: string;
  game_url: string;
  move_number: number;
  san: string;
  color: string;
  classification: string;
  cp_loss: number;
  phase: string;
  move_bucket: string;
  mistake_type: string;
  best_move_san: string;
};

type WeaknessSummary = {
  accuracy_by_phase?: AccuracyRow[];
  accuracy_by_move_bucket?: AccuracyRow[];
  common_mistake_types?: MistakeTypeRow[];
  worst_positions?: WorstPosition[];
};

type WeaknessProfileResponse = {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  analyzed_games_count: number;
  analyzed_moves_count: number;
  mistake_count: number;
  blunder_count: number;
  summary: WeaknessSummary;
  error_message?: string | null;
};

type ApiErrorResponse = {
  detail?: string;
  error?: string;
};

const woodPanelClass =
  'border border-black/50 [background-image:linear-gradient(rgba(0,0,0,0.56),rgba(0,0,0,0.56)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.48),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

export default function TrainPage() {
  const [lichessUsername, setLichessUsername] = useState('');
  const [chesscomUsername, setChesscomUsername] = useState('');
  const [limit, setLimit] = useState(100);
  const [status, setStatus] = useState<ImportStatus>('idle');
  const [jobId, setJobId] = useState<string | null>(null);
  const [importedCount, setImportedCount] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [profileStatus, setProfileStatus] = useState<ProfileStatus>('idle');
  const [profileJobId, setProfileJobId] = useState<string | null>(null);
  const [profileMessage, setProfileMessage] = useState<string | null>(null);
  const [profile, setProfile] = useState<WeaknessProfileResponse | null>(null);

  const canSubmit = useMemo(
    () =>
      status !== 'queued' &&
      status !== 'running' &&
      (lichessUsername.trim().length > 0 || chesscomUsername.trim().length > 0),
    [chesscomUsername, lichessUsername, status]
  );

  useEffect(() => {
    if (!jobId || (status !== 'queued' && status !== 'running')) {
      return;
    }

    let isCancelled = false;
    const interval = window.setInterval(async () => {
      try {
        const response = await fetch(`/api/train/opponent-import/${jobId}`);
        if (!response.ok) {
          const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
          throw new Error(body.detail ?? body.error ?? `Status request failed (${response.status})`);
        }

        const data = (await response.json()) as JobStatusResponse;
        if (isCancelled) {
          return;
        }

        setStatus(data.status);
        setImportedCount(data.imported_count);
        if (data.status === 'completed') {
          setMessage(`Imported ${data.imported_count} public games.`);
        }
        if (data.status === 'failed') {
          setMessage(data.error_message ?? 'Opponent import failed.');
        }
      } catch (error) {
        if (!isCancelled) {
          setStatus('error');
          setMessage(error instanceof Error ? error.message : 'Failed to check import status.');
        }
      }
    }, 2000);

    return () => {
      isCancelled = true;
      window.clearInterval(interval);
    };
  }, [jobId, status]);

  useEffect(() => {
    if (!profileJobId || (profileStatus !== 'queued' && profileStatus !== 'running')) {
      return;
    }

    let isCancelled = false;
    const interval = window.setInterval(async () => {
      try {
        const response = await fetch(`/api/train/weakness-profile/${profileJobId}`);
        if (!response.ok) {
          const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
          throw new Error(body.detail ?? body.error ?? `Profile request failed (${response.status})`);
        }

        const data = (await response.json()) as WeaknessProfileResponse;
        if (isCancelled) {
          return;
        }

        setProfileStatus(data.status);
        setProfile(data);
        if (data.status === 'completed') {
          setProfileMessage(`Analyzed ${data.analyzed_games_count} games and ${data.analyzed_moves_count} moves.`);
        }
        if (data.status === 'failed') {
          setProfileMessage(data.error_message ?? 'Weakness profile failed.');
        }
      } catch (error) {
        if (!isCancelled) {
          setProfileStatus('error');
          setProfileMessage(error instanceof Error ? error.message : 'Failed to check profile status.');
        }
      }
    }, 2500);

    return () => {
      isCancelled = true;
      window.clearInterval(interval);
    };
  }, [profileJobId, profileStatus]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    setStatus('queued');
    setMessage(null);
    setImportedCount(0);
    setJobId(null);

    try {
      const response = await fetch('/api/train/opponent-import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lichess_username: lichessUsername.trim() || null,
          chesscom_username: chesscomUsername.trim() || null,
          limit,
        }),
      });

      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
        throw new Error(body.detail ?? body.error ?? `Import request failed (${response.status})`);
      }

      const data = (await response.json()) as StartImportResponse;
      setJobId(data.job_id);
      setStatus(data.status);
      setMessage('Import job queued.');
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Failed to start import.');
    }
  }

  async function handleBuildProfile() {
    const provider =
      lichessUsername.trim().length > 0 && chesscomUsername.trim().length === 0
        ? 'lichess'
        : chesscomUsername.trim().length > 0 && lichessUsername.trim().length === 0
          ? 'chesscom'
          : null;
    const opponentUsername =
      provider === 'lichess'
        ? lichessUsername.trim()
        : provider === 'chesscom'
          ? chesscomUsername.trim()
          : null;

    setProfileStatus('queued');
    setProfileMessage(null);
    setProfile(null);
    setProfileJobId(null);

    try {
      const response = await fetch('/api/train/weakness-profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_type: 'opponent',
          provider,
          opponent_username: opponentUsername,
          limit,
        }),
      });

      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
        throw new Error(body.detail ?? body.error ?? `Profile request failed (${response.status})`);
      }

      const data = (await response.json()) as { job_id: string; status: 'queued' };
      setProfileJobId(data.job_id);
      setProfileStatus(data.status);
      setProfileMessage('Weakness profile queued.');
    } catch (error) {
      setProfileStatus('error');
      setProfileMessage(error instanceof Error ? error.message : 'Failed to build profile.');
    }
  }

  const isWorking = status === 'queued' || status === 'running';
  const isProfileWorking = profileStatus === 'queued' || profileStatus === 'running';

  return (
    <div className="relative -mt-2 h-[calc(100vh-2.25rem)] w-full overflow-y-auto px-6 py-8 text-white lg:px-10 [background-image:url(/walnut-dark.png)] [background-size:cover] [background-position:center]">
      <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <form
          onSubmit={handleSubmit}
          className={`${woodPanelClass} flex flex-col gap-5 rounded-[8px] p-5`}
        >
          <div>
            <h1 className="text-2xl font-semibold text-[#f7e5c6]">Train</h1>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="flex flex-col gap-2 text-sm font-medium text-[#f7e5c6]/85">
              Lichess username
              <input
                value={lichessUsername}
                onChange={(event) => setLichessUsername(event.target.value)}
                disabled={isWorking}
                className="h-11 rounded-xl border border-black/50 bg-black/60 px-3 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-[#10b981]/60 focus:ring-2 focus:ring-[#10b981]/20 disabled:opacity-60"
                placeholder="Opponent on Lichess"
              />
            </label>

            <label className="flex flex-col gap-2 text-sm font-medium text-[#f7e5c6]/85">
              Chess.com username
              <input
                value={chesscomUsername}
                onChange={(event) => setChesscomUsername(event.target.value)}
                disabled={isWorking}
                className="h-11 rounded-xl border border-black/50 bg-black/60 px-3 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-[#10b981]/60 focus:ring-2 focus:ring-[#10b981]/20 disabled:opacity-60"
                placeholder="Opponent on Chess.com"
              />
            </label>
          </div>

          <label className="flex max-w-48 flex-col gap-2 text-sm font-medium text-[#f7e5c6]/85">
            Games per site
            <input
              type="number"
              min={1}
              max={500}
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}
              disabled={isWorking}
              className="h-11 rounded-xl border border-black/50 bg-black/60 px-3 text-sm text-white outline-none transition focus:border-[#10b981]/60 focus:ring-2 focus:ring-[#10b981]/20 disabled:opacity-60"
            />
          </label>

          <button
            type="submit"
            disabled={!canSubmit}
            className="inline-flex h-11 w-full max-w-56 items-center justify-center gap-2 rounded-lg border border-[#f7e5c6]/25 bg-[#f7e5c6]/12 px-4 text-sm font-semibold text-[#f7e5c6] transition hover:bg-[#f7e5c6]/18 disabled:pointer-events-none disabled:opacity-40"
          >
            {isWorking && (
              <span className="h-4 w-4 rounded-full border-2 border-[#f7e5c6]/40 border-t-[#f7e5c6] animate-spin" />
            )}
            {isWorking ? 'Importing' : 'Import opponent games'}
          </button>
        </form>

        <section className={`${woodPanelClass} flex min-h-48 flex-col gap-4 rounded-[8px] p-5`}>
          <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-[#f7e5c6]/70">
            Import Status
          </h2>
          <div className="rounded-xl border border-black/50 bg-black/45 p-4">
            <div className="text-2xl font-semibold text-[#f7e5c6]">
              {formatStatus(status)}
            </div>
            <div className="mt-1 text-sm text-[#f7e5c6]/65">
              {importedCount} games stored
            </div>
          </div>
          {jobId && (
            <div className="break-all rounded-xl border border-black/40 bg-black/30 px-3 py-2 text-xs text-[#f7e5c6]/60">
              {jobId}
            </div>
          )}
          {message && (
            <p
              role="status"
              className={`rounded-xl border px-3 py-2 text-sm ${
                status === 'failed' || status === 'error'
                  ? 'border-red-400/30 bg-red-400/10 text-red-300'
                  : 'border-[#10b981]/30 bg-[#10b981]/10 text-[#a7f3d0]'
              }`}
            >
              {message}
            </p>
          )}

          <button
            type="button"
            onClick={handleBuildProfile}
            disabled={isProfileWorking || status !== 'completed'}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-[#10b981]/35 bg-[#10b981]/12 px-4 text-sm font-semibold text-[#a7f3d0] transition hover:bg-[#10b981]/18 disabled:pointer-events-none disabled:opacity-40"
          >
            {isProfileWorking && (
              <span className="h-4 w-4 rounded-full border-2 border-[#a7f3d0]/40 border-t-[#a7f3d0] animate-spin" />
            )}
            {isProfileWorking ? 'Profiling' : 'Build weakness profile'}
          </button>
          <Link
            href="/train/sparring"
            className="inline-flex h-11 items-center justify-center rounded-lg border border-[#f7e5c6]/25 bg-[#f7e5c6]/12 px-4 text-sm font-semibold text-[#f7e5c6] transition hover:bg-[#f7e5c6]/18"
          >
            Open sparring board
          </Link>
        </section>

        <section className={`${woodPanelClass} flex flex-col gap-5 rounded-[8px] p-5 lg:col-span-2`}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold text-[#f7e5c6]">Weakness Profile</h2>
            <div className="text-sm text-[#f7e5c6]/65">
              {profile ? `${profile.mistake_count} mistakes · ${profile.blunder_count} blunders` : formatStatus(profileStatus)}
            </div>
          </div>

          {profileMessage && (
            <p
              role="status"
              className={`rounded-xl border px-3 py-2 text-sm ${
                profileStatus === 'failed' || profileStatus === 'error'
                  ? 'border-red-400/30 bg-red-400/10 text-red-300'
                  : 'border-[#10b981]/30 bg-[#10b981]/10 text-[#a7f3d0]'
              }`}
            >
              {profileMessage}
            </p>
          )}

          {profile?.status === 'completed' ? (
            <div className="grid gap-5 xl:grid-cols-[1fr_1fr_1.4fr]">
              <SummaryTable
                title="Accuracy by Phase"
                rows={profile.summary.accuracy_by_phase ?? []}
              />
              <MistakeTypes rows={profile.summary.common_mistake_types ?? []} />
              <WorstPositions rows={profile.summary.worst_positions ?? []} />
            </div>
          ) : (
            <div className="rounded-xl border border-black/40 bg-black/30 px-4 py-8 text-center text-sm text-[#f7e5c6]/60">
              Build a profile after importing games to see phase accuracy, mistake patterns, and critical positions.
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function formatStatus(status: ImportStatus | ProfileStatus): string {
  if (status === 'idle') {
    return 'Ready';
  }
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function SummaryTable({ title, rows }: { title: string; rows: AccuracyRow[] }) {
  return (
    <div className="rounded-xl border border-black/45 bg-black/35 p-4">
      <h3 className="text-sm font-semibold text-[#f7e5c6]">{title}</h3>
      <div className="mt-3 flex flex-col gap-2">
        {rows.map((row) => (
          <div key={row.key} className="grid grid-cols-[1fr_auto] gap-3 text-sm">
            <span className="capitalize text-[#f7e5c6]/70">{row.key}</span>
            <span className="font-semibold text-[#a7f3d0]">{row.accuracy}%</span>
            <div className="col-span-2 h-2 rounded-full bg-black/50">
              <div
                className="h-full rounded-full bg-[#10b981]"
                style={{ width: `${Math.max(0, Math.min(100, row.accuracy))}%` }}
              />
            </div>
            <span className="col-span-2 text-xs text-[#f7e5c6]/45">
              {row.total_moves} moves · {row.mistakes} mistakes · {row.blunders} blunders
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MistakeTypes({ rows }: { rows: MistakeTypeRow[] }) {
  return (
    <div className="rounded-xl border border-black/45 bg-black/35 p-4">
      <h3 className="text-sm font-semibold text-[#f7e5c6]">Common Mistake Types</h3>
      <div className="mt-3 flex flex-col gap-2">
        {rows.map((row) => (
          <div key={row.type} className="flex items-center justify-between gap-3 rounded-lg bg-black/30 px-3 py-2 text-sm">
            <span className="capitalize text-[#f7e5c6]/75">{row.type}</span>
            <span className="font-semibold text-[#f7e5c6]">{row.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function WorstPositions({ rows }: { rows: WorstPosition[] }) {
  return (
    <div className="rounded-xl border border-black/45 bg-black/35 p-4">
      <h3 className="text-sm font-semibold text-[#f7e5c6]">Worst Positions</h3>
      <div className="mt-3 flex max-h-80 flex-col gap-2 overflow-y-auto pr-1">
        {rows.map((row, index) => (
          <a
            key={`${row.game_url}-${row.move_number}-${index}`}
            href={row.game_url}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg border border-transparent bg-black/30 px-3 py-2 text-sm transition hover:border-[#f7e5c6]/25 hover:bg-black/45"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="font-semibold text-[#f7e5c6]">
                {row.move_number}. {row.san}
              </span>
              <span className="text-rose-300">{row.cp_loss} cp</span>
            </div>
            <div className="mt-1 text-xs capitalize text-[#f7e5c6]/55">
              {row.phase} · {row.mistake_type} · best {row.best_move_san || 'unknown'}
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
