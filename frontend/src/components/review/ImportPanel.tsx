'use client';

import { useId, useState } from 'react';

export type ImportSource = 'paste' | 'chesscom' | 'lichess';

type GamePlayer = {
  username?: string;
  rating?: number;
};

type GameSummary = {
  url: string;
  pgn: string;
  white: GamePlayer;
  black: GamePlayer;
  result: string;
  end_time: number;
  time_class: string;
};

type ImportPanelProps = {
  pgn: string;
  onPgnChange: (value: string) => void;
  source: ImportSource;
  onSourceChange: (source: ImportSource) => void;
  onImport: () => void;
  isAnalyzing: boolean;
  errorMessage: string | null;
  disabled?: boolean;
};

const SOURCE_TABS: { key: ImportSource; label: string }[] = [
  { key: 'paste', label: 'Paste PGN' },
  { key: 'chesscom', label: 'Chess.com' },
  { key: 'lichess', label: 'Lichess' },
];

const MAX_CLIENT_PGN_BYTES = 2 * 1024 * 1024;

const woodBoxStyle: React.CSSProperties = {
  borderRadius: '4px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.png)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
};

export default function ImportPanel({
  pgn,
  onPgnChange,
  source,
  onSourceChange,
  onImport,
  isAnalyzing,
  errorMessage,
  disabled,
}: ImportPanelProps) {
  const inputId = useId();
  const [fileName, setFileName] = useState<string | null>(null);
  const [hasFetchedGames, setHasFetchedGames] = useState(false);

  const canImport = !disabled && !isAnalyzing && pgn.trim().length > 0;
  const showImportButton = source === 'paste' || hasFetchedGames;

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    if (file.size > MAX_CLIENT_PGN_BYTES) {
      event.target.value = '';
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const text = typeof reader.result === 'string' ? reader.result : '';
      onPgnChange(text);
      setFileName(file.name);
    };
    reader.onerror = () => {
      event.target.value = '';
    };
    reader.readAsText(file);
    event.target.value = '';
  }

  return (
    <aside className="flex h-full flex-col gap-4 overflow-hidden rounded-[24px] border border-black/50 p-4 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]">
      <div role="tablist" aria-label="PGN import source" className="flex flex-wrap gap-1 rounded-xl border border-black/40 bg-black/40 p-1">
        {SOURCE_TABS.map((tab) => {
          const isActive = tab.key === source;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => {
              onSourceChange(tab.key);
              setHasFetchedGames(false);
            }}
              className={`flex-1 cursor-pointer rounded-lg px-2 py-1.5 text-xs font-medium transition ${
                isActive
                  ? 'bg-[#10b981]/20 text-[#10b981] ring-1 ring-[#10b981]/30'
                  : 'text-white/70 hover:text-white'
              }`}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {source === 'paste' && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-end gap-2">
            <label
              className={`inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-black/50 bg-black/30 px-2.5 py-1.5 text-xs font-semibold text-white transition hover:bg-white/5 ${
                isAnalyzing ? 'cursor-not-allowed opacity-50' : ''
              }`}
            >
              <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <path d="M17 8l-5-5-5 5" />
                <path d="M12 3v12" />
              </svg>
              Upload .pgn
              <input
                type="file"
                accept=".pgn,.txt,application/x-chess-pgn,text/plain"
                onChange={handleFileChange}
                disabled={isAnalyzing}
                className="hidden"
              />
            </label>
          </div>
          <textarea
            id={inputId}
            value={pgn}
            onChange={(event) => onPgnChange(event.target.value)}
            placeholder="Paste your PGN here, for example: [Event &quot;Casual Game&quot;] ..."
            disabled={isAnalyzing}
            className="min-h-[200px] w-full resize-none rounded-2xl border border-black/50 bg-black/60 px-3 py-3 font-mono text-xs leading-6 text-white outline-none transition placeholder:text-white/30 focus:border-[#10b981]/60 focus:ring-2 focus:ring-[#10b981]/20 disabled:opacity-60"
          />
          {fileName && pgn.length > 0 && (
            <p className="text-[11px] text-[#10b981]">Loaded {fileName}</p>
          )}
        </div>
      )}

      {source === 'chesscom' && (
        <UsernameImport
          platform="chess.com"
          apiPath="chesscom"
          pgn={pgn}
          onPgnChange={onPgnChange}
          isAnalyzing={isAnalyzing}
          onGamesFetched={() => setHasFetchedGames(true)}
        />
      )}

      {source === 'lichess' && (
        <UsernameImport
          platform="lichess"
          apiPath="lichess"
          pgn={pgn}
          onPgnChange={onPgnChange}
          isAnalyzing={isAnalyzing}
          onGamesFetched={() => setHasFetchedGames(true)}
        />
      )}

      {showImportButton && (
        <button
          type="button"
          onClick={onImport}
          disabled={!canImport}
          className="inline-flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold text-[#f0e0c0] transition-transform hover:scale-[1.02] active:scale-95 disabled:pointer-events-none disabled:opacity-40"
          style={{
            cursor: canImport ? 'pointer' : 'default',
            ...woodBoxStyle,
            borderRadius: '8px',
          }}
        >
          {isAnalyzing ? (
            <>
              <span className="h-4 w-4 rounded-full border-2 border-[#f0e0c0]/40 border-t-[#f0e0c0] animate-spin" />
              <span>Analyzing...</span>
            </>
          ) : (
            <>
              <span>Import Game</span>
              <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
                <path d="m9 18 6-6-6-6" />
              </svg>
            </>
          )}
        </button>
      )}

      {errorMessage && (
        <p role="alert" className="rounded-xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs text-red-400">
          {errorMessage}
        </p>
      )}
    </aside>
  );
}

function UsernameImport({
  platform,
  apiPath,
  pgn,
  onPgnChange,
  isAnalyzing,
  onGamesFetched,
}: {
  platform: string;
  apiPath: string;
  pgn: string;
  onPgnChange: (value: string) => void;
  isAnalyzing: boolean;
  onGamesFetched: () => void;
}) {
  const [username, setUsername] = useState('');
  const [isFetching, setIsFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [games, setGames] = useState<GameSummary[]>([]);

  const hasPgn = pgn.trim().length > 0;

  async function handleFetchGames() {
    const trimmed = username.trim();
    if (!trimmed || isFetching || isAnalyzing) {
      return;
    }

    setIsFetching(true);
    setFetchError(null);
    setGames([]);

    try {
      const res = await fetch(
        `/api/import/${apiPath}/${encodeURIComponent(trimmed)}?limit=10`
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          body.detail ?? body.error ?? `Request failed (${res.status})`
        );
      }
      const data = (await res.json()) as GameSummary[];
      if (data.length === 0) {
        setFetchError(`No recent games found for ${platform} username "${trimmed}".`);
        return;
      }
      setGames(data);
      onGamesFetched();
    } catch (err) {
      setFetchError(
        err instanceof Error ? err.message : 'Failed to fetch games.'
      );
    } finally {
      setIsFetching(false);
    }
  }

  function handleSelectGame(game: GameSummary) {
    onPgnChange(game.pgn);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <input
          type="text"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              handleFetchGames();
            }
          }}
          placeholder={`${platform} username`}
          disabled={isFetching || isAnalyzing}
          className="min-w-0 flex-1 rounded-xl border border-black/50 bg-black/60 px-3 py-2 text-xs text-white outline-none transition placeholder:text-white/30 focus:border-[#10b981]/60 focus:ring-2 focus:ring-[#10b981]/20 disabled:opacity-60"
        />
        <button
          type="button"
          onClick={handleFetchGames}
          disabled={!username.trim() || isFetching || isAnalyzing}
          aria-label="Fetch games"
          className="flex h-[34px] w-[34px] shrink-0 items-center justify-center transition-transform hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
          style={{
            cursor:
              !username.trim() || isFetching || isAnalyzing
                ? 'default'
                : 'pointer',
            ...woodBoxStyle,
          }}
        >
          {isFetching ? (
            <span className="h-3 w-3 rounded-full border-2 border-[#f0e0c0]/40 border-t-[#f0e0c0] animate-spin" />
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#f0e0c0" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="7" />
              <line x1="16.5" y1="16.5" x2="21" y2="21" />
            </svg>
          )}
        </button>
      </div>

      {fetchError && (
        <p role="alert" className="rounded-xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs text-red-400">
          {fetchError}
        </p>
      )}

      {games.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="wooden-scroll flex max-h-[318px] flex-col gap-1.5 overflow-y-auto">
            {games.map((game) => {
              const isSelected = hasPgn && game.pgn === pgn;
              return (
                <button
                  key={game.url}
                  type="button"
                  onClick={() => handleSelectGame(game)}
                  disabled={isAnalyzing}
                  className={`group flex w-full items-center justify-between gap-2 rounded-xl border px-3 py-2 text-left text-xs transition disabled:opacity-50 ${
                    isSelected
                      ? 'border-[#10b981]/50 bg-[#10b981]/15 text-[#10b981]'
                      : 'border-transparent bg-black/30 text-white hover:border-white/10 hover:bg-black/50'
                  }`}
                >
                  <span className="flex flex-col">
                    <span className="font-medium">
                      {game.white.username ?? '?'} vs. {game.black.username ?? '?'}
                    </span>
                    <span className="text-[11px] text-white/50">
                      {formatGameDate(game.end_time)} · {game.time_class} · {game.result}
                    </span>
                  </span>
                  {isSelected ? (
                    <svg
                      className="h-3.5 w-3.5 shrink-0 text-[#10b981]"
                      fill="none"
                      stroke="currentColor"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth="2.5"
                      viewBox="0 0 24 24"
                      aria-hidden
                    >
                      <path d="m5 12 4 4L19 6" />
                    </svg>
                  ) : (
                    <svg
                      className="h-3.5 w-3.5 shrink-0 text-white/50 transition group-hover:text-[#10b981]"
                      fill="none"
                      stroke="currentColor"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth="2"
                      viewBox="0 0 24 24"
                      aria-hidden
                    >
                      <path d="M5 12h14" />
                      <path d="m12 5 7 7-7 7" />
                    </svg>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function formatGameDate(unixSeconds: number): string {
  if (!unixSeconds) {
    return 'Unknown date';
  }
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}