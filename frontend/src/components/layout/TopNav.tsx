'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';
import { Show, SignInButton, SignUpButton, UserButton } from '@clerk/nextjs';

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

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <>
      {NAV_ITEMS.map((item) => {
        const isActive = !item.disabled && pathname.startsWith(item.href);

        if (item.disabled) {
          return (
            <span
              key={item.href}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-zinc-500"
            >
              <span className="w-4 text-center">{item.icon}</span>
              <span>{item.label}</span>
              <span className="rounded-md bg-zinc-700 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-zinc-300">
                Soon
              </span>
            </span>
          );
        }

        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={`inline-flex items-center gap-1.5 text-sm font-medium transition ${
              isActive
                ? 'text-emerald-400'
                : 'text-zinc-400 hover:text-zinc-100'
            }`}
          >
            <span className="w-4 text-center">{item.icon}</span>
            <span>{item.label}</span>
          </Link>
        );
      })}
    </>
  );
}

function AuthSection() {
  return (
    <div className="flex items-center gap-2">
      <Show when="signed-out">
        <SignInButton mode="modal">
          <button className="rounded-md border border-zinc-700 bg-zinc-900/90 px-3 py-1.5 text-sm font-medium text-zinc-100 transition hover:border-emerald-500/70 hover:bg-zinc-800">
            Sign in
          </button>
        </SignInButton>
        <SignUpButton mode="modal">
          <button className="rounded-md bg-emerald-500 px-3 py-1.5 text-sm font-semibold text-zinc-950 shadow-lg shadow-emerald-500/20 transition hover:bg-emerald-400">
            Sign up
          </button>
        </SignUpButton>
      </Show>
      <Show when="signed-in">
        <UserButton />
      </Show>
    </div>
  );
}

export default function TopNav() {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      {/* Desktop top nav */}
      <nav className="fixed inset-x-0 top-0 z-40 flex h-16 items-center justify-between border-b border-zinc-800 bg-zinc-950 px-5">
        <Link href="/puzzles" className="flex items-center gap-2 transition hover:opacity-80">
          <img src="/logo.svg" alt="Praxis" className="h-10 w-auto" />
        </Link>

        <div className="hidden items-center gap-6 md:flex">
          <NavLinks />
        </div>

        <div className="hidden items-center md:flex">
          <AuthSection />
        </div>

        {/* Mobile hamburger */}
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="rounded-md p-2 text-zinc-100 transition hover:bg-zinc-800 md:hidden"
          aria-label="Open navigation"
        >
          <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
      </nav>

      {/* Mobile dropdown */}
      {isOpen && (
        <div className="fixed inset-x-0 top-0 z-50 md:hidden">
          <div className="border-b border-zinc-800 bg-zinc-950 px-5 py-4">
            <div className="flex items-center justify-between">
              <Link href="/puzzles" onClick={() => setIsOpen(false)} className="flex items-center gap-2 transition hover:opacity-80">
                <img src="/logo.svg" alt="Praxis" className="h-10 w-auto" />
              </Link>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="rounded-md p-2 text-zinc-100 transition hover:bg-zinc-800"
                aria-label="Close navigation"
              >
                <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="mt-4 flex flex-col gap-3">
              <NavLinks onNavigate={() => setIsOpen(false)} />
            </div>

            <div className="mt-4 border-t border-zinc-800 pt-4">
              <AuthSection />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
