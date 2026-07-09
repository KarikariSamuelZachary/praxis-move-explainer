'use client';

import { useId } from 'react';

export type ImportSource = 'paste' | 'chesscom' | 'lichess' | 'file';

export type ImportExample = {
  id: string;
  white: string;
  black: string;
  event: string;
  pgn: string;
};

export const SAMPLE_EXAMPLES: ImportExample[] = [
  {
    id: 'kasparov-topalov-1999',
    white: 'Kasparov',
    black: 'Topalov',
    event: 'Wijk aan Zee 1999',
    pgn: `[Event "Wijk aan Zee"]
[Site "Wijk aan Zee NED"]
[Date "1999.01.20"]
[Round "3"]
[White "Garry Kasparov"]
[Black "Veselin Topalov"]
[Result "1-0"]

1.e4 d6 2.d4 Nf6 3.Nc3 g6 3...Bg7 4.Be3 a6 5.Qd2 b5 6.f3`,
  },
  {
    id: 'carlsen-nepo-2021',
    white: 'Carlsen',
    black: 'Nepomniachtchi',
    event: 'World Championship 2021',
    pgn: `[Event "WCh 2021"]
[White "Magnus Carlsen"]
[Black "Ian Nepomniachtchi"]
[Result "1-0"]

1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 4.O-O Nf6 5.d3 d6 6.c3 O-O 7.Re1 a6 8.Bb3 Ba7 9.Nbd2 h6`,
  },
  {
    id: 'andersen-kieseritzky-1851',
    white: 'Anderssen',
    black: 'Kieseritzky',
    event: 'The Immortal Game, 1851',
    pgn: `[Event "London"]
[Date "1851.06.21"]
[White "Adolf Anderssen"]
[Black "Lionel Kieseritzky"]
[Result "1-0"]

1.e4 e5 2.f4 exf4 3.Bc4 Qh4+ 4.Kf1 b5 5.Bxb5 Nf6 6.Nf3 Qh6 7.d3 Nh5 8.Nh4 Qg5 9.Nf5 c6 10.g4 Nf6 11.Rg1 cxb5 12.h4 Qg6 13.h5 Qg5 14.Qf3 Ng8 15.Bxf4 Qf6 16.Nc3 Bc5 17.Nd5 Qxb2 18.Bd6 Bxg1 19.e5 Qxa1+ 20.Ke2 Na6 21.Nxg7+ Kd8 22.Qf6+ Nxf6 23.Be7# 1-0`,
  },
];

type ImportPanelProps = {
  pgn: string;
  onPgnChange: (value: string) => void;
  source: ImportSource;
  onSourceChange: (source: ImportSource) => void;
  onImport: () => void;
  onLoadExample: (example: ImportExample) => void;
  isAnalyzing: boolean;
  errorMessage: string | null;
  onClear: () => void;
  disabled?: boolean;
};

const SOURCE_TABS: { key: ImportSource; label: string; disabled?: boolean }[] = [
  { key: 'paste', label: 'Paste PGN' },
  { key: 'chesscom', label: 'Chess.com', disabled: true },
  { key: 'lichess', label: 'Lichess', disabled: true },
  { key: 'file', label: 'PGN File', disabled: true },
];

const MAX_CLIENT_PGN_BYTES = 2 * 1024 * 1024;

export default function ImportPanel({
  pgn,
  onPgnChange,
  source,
  onSourceChange,
  onImport,
  onLoadExample,
  isAnalyzing,
  errorMessage,
  onClear,
  disabled,
}: ImportPanelProps) {
  const inputId = useId();

  const canImport = !disabled && !isAnalyzing && pgn.trim().length > 0;

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
    };
    reader.onerror = () => {
      event.target.value = '';
    };
    reader.readAsText(file);
    event.target.value = '';
  }

  return (
    <aside className="flex h-full flex-col gap-4 overflow-y-auto rounded-[24px] border border-black/50 p-4 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]">
      <header className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold tracking-wide text-zinc-100">Import Game</h2>
        {pgn.length > 0 && (
          <button
            type="button"
            onClick={onClear}
            disabled={isAnalyzing}
            className="rounded-md px-2 py-1 text-xs text-zinc-400 transition hover:text-white disabled:opacity-50"
          >
            Clear
          </button>
        )}
      </header>

      <div role="tablist" aria-label="PGN import source" className="flex flex-wrap gap-1 rounded-xl border border-black/40 bg-black/40 p-1">
        {SOURCE_TABS.map((tab) => {
          const isActive = tab.key === source;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              disabled={tab.disabled}
              onClick={() => !tab.disabled && onSourceChange(tab.key)}
              className={`flex-1 rounded-lg px-2 py-1.5 text-xs font-medium transition ${
                isActive
                  ? 'bg-[#10b981]/20 text-emerald-200 ring-1 ring-emerald-400/30'
                  : 'text-zinc-300 hover:text-white'
              } ${tab.disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'}`}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {source === 'paste' && (
        <div className="flex flex-col gap-2">
          <label htmlFor={inputId} className="text-xs font-medium text-zinc-300">
            Paste your PGN text below to import your game.
          </label>
          <textarea
            id={inputId}
            value={pgn}
            onChange={(event) => onPgnChange(event.target.value)}
            placeholder="Paste your PGN here, for example: [Event &quot;Casual Game&quot;] ..."
            disabled={isAnalyzing}
            className="min-h-[200px] w-full resize-none rounded-2xl border border-black/50 bg-black/60 px-3 py-3 font-mono text-xs leading-6 text-zinc-100 outline-none transition placeholder:text-zinc-600 focus:border-emerald-500/60 focus:ring-2 focus:ring-emerald-500/20 disabled:opacity-60"
          />
          <p className="text-[11px] text-zinc-500">{pgn.length} characters</p>
        </div>
      )}

      {source === 'chesscom' && (
        <DisabledSource platform="chess.com" />
      )}

      {source === 'lichess' && (
        <DisabledSource platform="lichess" />
      )}

      {source === 'file' && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-zinc-400">
            Upload a .pgn file from your computer.
          </p>
          <input
            type="file"
            accept=".pgn,.txt,application/x-chess-pgn,text/plain"
            onChange={handleFileChange}
            disabled={isAnalyzing}
            className="block w-full text-xs text-zinc-300 file:mr-3 file:rounded-lg file:border-0 file:bg-emerald-500/20 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-emerald-200 hover:file:bg-emerald-500/30"
          />
        </div>
      )}

      <button
        type="button"
        onClick={onImport}
        disabled={!canImport}
        className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-emerald-500/40 bg-emerald-500/15 px-4 py-2.5 text-sm font-medium text-emerald-200 transition hover:border-emerald-400 hover:bg-emerald-500/25 disabled:cursor-not-allowed disabled:border-black/40 disabled:bg-black/30 disabled:text-zinc-600"
      >
        {isAnalyzing ? (
          <>
            <span className="h-4 w-4 rounded-full border-2 border-emerald-300/40 border-t-emerald-200 animate-spin" />
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

      {errorMessage && (
        <p role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
          {errorMessage}
        </p>
      )}

      <div className="mt-2 border-t border-white/10 pt-3">
        <p className="mb-2 text-center text-[10px] uppercase tracking-[0.24em] text-zinc-500">
          Examples
        </p>
        <ul className="flex flex-col gap-1.5">
          {SAMPLE_EXAMPLES.map((example) => (
            <li key={example.id}>
              <button
                type="button"
                onClick={() => onLoadExample(example)}
                disabled={isAnalyzing}
                className="group flex w-full items-center justify-between gap-2 rounded-xl border border-transparent bg-black/30 px-3 py-2 text-left text-xs transition hover:border-amber-900/40 hover:bg-black/50 disabled:opacity-50"
              >
                <span className="flex flex-col">
                  <span className="font-medium text-zinc-100">
                    {example.white} vs. {example.black}
                  </span>
                  <span className="text-[11px] text-zinc-500">{example.event}</span>
                </span>
                <svg
                  className="h-3.5 w-3.5 shrink-0 text-zinc-500 transition group-hover:text-emerald-300"
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
              </button>
            </li>
          ))}
        </ul>
      </div>

      <p className="mt-auto flex items-start gap-2 rounded-xl border border-amber-900/30 bg-black/30 px-3 py-2 text-[11px] leading-5 text-zinc-400">
        <svg className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
          <circle cx="12" cy="12" r="10" />
          <path d="M12 16v-4" />
          <path d="M12 8h.01" />
        </svg>
        <span>
          <span className="font-medium text-amber-200">Tip:</span> Make sure the PGN includes headers and all moves for best results.
        </span>
      </p>
    </aside>
  );
}

function DisabledSource({ platform }: { platform: string }) {
  return (
    <div className="flex flex-col gap-2 rounded-2xl border border-dashed border-amber-900/40 bg-black/30 p-4">
      <p className="text-xs text-zinc-400">
        {platform} game sync is coming soon. Paste a PGN for now to get started.
      </p>
      <input
        type="text"
        placeholder={`${platform} username`}
        disabled
        className="w-full rounded-xl border border-black/40 bg-black/40 px-3 py-2 text-xs text-zinc-500 placeholder:text-zinc-600"
      />
    </div>
  );
}
