'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';

type NavItem = {
  href: string;
  label: string;
  icon: string;
  disabled?: boolean;
};

const NAV_ITEMS: NavItem[] = [
  { href: '/puzzles', label: 'Puzzles', icon: '♟' },
  { href: '/review', label: 'Game Review', icon: '🎬' },
  { href: '/woodpecker', label: 'Woodpecker', icon: '🔁', disabled: true },
  { href: '/dashboard', label: 'Dashboard', icon: '📊', disabled: true },
];

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <div className="flex h-full flex-col bg-zinc-950">
      <div className="border-b border-zinc-800 px-5 py-5">
        <Link
          href="/puzzles"
          onClick={onNavigate}
          className="flex items-center gap-3 rounded-lg text-zinc-100 transition hover:text-emerald-400"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-lg text-emerald-400">
            ♟
          </span>
          <span className="text-lg font-semibold">Praxis</span>
        </Link>
      </div>

      <nav className="flex-1 space-y-2 px-3 py-4">
        {NAV_ITEMS.map((item) => {
          const isActive = !item.disabled && pathname.startsWith(item.href);

          if (item.disabled) {
            return (
              <div
                key={item.href}
                aria-disabled="true"
                className="flex items-center gap-3 rounded-lg border border-zinc-800 px-3 py-2.5 text-sm font-medium text-zinc-500"
              >
                <span className="w-5 text-center">{item.icon}</span>
                <span className="flex-1">{item.label}</span>
                <span className="rounded-md bg-zinc-700 px-2 py-0.5 text-[10px] font-semibold uppercase text-zinc-300">
                  Soon
                </span>
              </div>
            );
          }

          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                isActive
                  ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
                  : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100'
              }`}
            >
              <span className="w-5 text-center">{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-zinc-800 px-5 py-4">
        <p className="text-xs text-zinc-600">Praxis v0.1</p>
      </div>
    </div>
  );
}

export default function Sidebar() {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className="fixed left-4 top-4 z-50 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-lg leading-none text-zinc-100 shadow-lg shadow-zinc-950/40 transition hover:bg-zinc-800 md:hidden"
        aria-label="Open navigation"
      >
        ☰
      </button>

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[220px] border-r border-zinc-800 bg-zinc-950 md:block">
        <SidebarContent />
      </aside>

      {isOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            className="absolute inset-0 bg-zinc-950/80"
            onClick={() => setIsOpen(false)}
          />
          <aside className="relative h-full w-[220px] border-r border-zinc-800 bg-zinc-950 shadow-2xl shadow-zinc-950/60">
            <SidebarContent onNavigate={() => setIsOpen(false)} />
          </aside>
        </div>
      )}
    </>
  );
}
