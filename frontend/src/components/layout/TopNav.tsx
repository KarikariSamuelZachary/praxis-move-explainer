'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useClerk } from '@clerk/nextjs';
import { useEffect, useRef, useState } from 'react';

import { KnightMark } from '@/components/layout/KnightMark';

type NavItem = {
  href: string;
  label: string;
};

const NAV_ITEMS: NavItem[] = [
  { href: '/puzzles', label: 'Puzzles' },
  { href: '/train', label: 'Train' },
  { href: '/review', label: 'Game Review' },
  { href: '/woodpecker', label: 'Woodpecker' },
  { href: '/repertoire', label: 'Repertoire' },
  { href: '/openings', label: 'Openings' },
  { href: '/community', label: 'Endgames' },
];

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <>
      {NAV_ITEMS.map((item) => {
        const isActive = pathname.startsWith(item.href);

        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={`relative inline-flex h-12 items-center px-2 text-sm font-semibold transition ${
              isActive
                ? 'text-white after:absolute after:bottom-0 after:left-2 after:right-2 after:h-0.5 after:bg-[#10b981]'
                : 'text-wood-mute hover:text-gold-bright'
            }`}
          >
            <span>{item.label}</span>
          </Link>
        );
      })}
    </>
  );
}

function BellIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  );
}

function ProfileMenu() {
  const { signOut } = useClerk();
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsOpen(false);
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        className={`group flex h-9 w-9 items-center justify-center rounded-full border text-sm font-bold text-black shadow-[inset_0_1px_2px_rgba(255,255,255,0.45),0_3px_10px_rgba(0,0,0,0.28)] transition duration-200 ${
          isOpen
            ? 'border-gold-bright/80 bg-[#d7ae87] ring-2 ring-gold/25 ring-offset-2 ring-offset-walnut-950'
            : 'border-[#e4c197]/45 bg-[#c49a7a] hover:border-[#f0d5a4]/80 hover:bg-[#d0a780]'
        }`}
        aria-label="User menu"
        aria-haspopup="menu"
        aria-expanded={isOpen}
      >
        S
      </button>
      {isOpen && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-3 w-44 overflow-hidden rounded-xl border border-[#d9b87c]/30 bg-[#1b120d]/95 p-1.5 shadow-[0_16px_38px_rgba(0,0,0,0.48),inset_0_1px_0_rgba(255,255,255,0.08)] backdrop-blur-xl [animation:profile-menu-in_160ms_ease-out]"
        >
          <div className="h-px rounded-full bg-gradient-to-r from-transparent via-[#d9b87c]/80 to-transparent" />
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setIsOpen(false);
              void signOut();
            }}
            className="group mt-1 flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-medium text-cream/90 transition hover:bg-white/10 hover:text-white"
          >
            <svg
              aria-hidden
              className="h-4 w-4 text-gold/80 transition group-hover:text-gold-bright"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="1.8"
              viewBox="0 0 24 24"
            >
              <path d="M10 17l5-5-5-5" />
              <path d="M15 12H3" />
              <path d="M21 4v16" />
            </svg>
            Logout
          </button>
        </div>
      )}
    </div>
  );
}

export default function TopNav() {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      {/* Desktop top nav */}
      <nav className="fixed inset-x-0 top-0 z-40 border-b border-black/40 text-white shadow-[0_4px_20px_rgba(0,0,0,0.45)] [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
        {/* Inner container matches LandingNav.tsx:55 - mx-auto max-w-[1400px]
            px-5 sm:px-8 - so the logo and links sit at the exact same
            horizontal position on every route and on the marketing page. */}
        <div className="mx-auto flex h-12 w-full max-w-[1400px] items-center justify-between px-5 sm:px-8">
          <Link href="/dashboard" className="flex items-center gap-3 transition hover:opacity-80">
            <KnightMark className="h-7 w-6" />
            <span className="font-display text-xl font-bold tracking-[0.16em] text-gold">PRAXIS</span>
          </Link>

          <div className="hidden items-center gap-8 lg:flex">
            <NavLinks />
          </div>

          <div className="hidden items-center gap-5 md:flex">
            <button type="button" className="relative text-white/80 transition hover:text-white" aria-label="Notifications">
              <BellIcon />
              <span className="absolute -right-0.5 top-0 h-2 w-2 rounded-full bg-[#10b981] ring-2 ring-black" />
            </button>
            <ProfileMenu />
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
        </div>
      </nav>

      {/* Mobile dropdown */}
      {isOpen && (
        <div className="fixed inset-x-0 top-0 z-50 md:hidden">
          <div className="border-b border-black/40 px-5 py-4 text-white [background-image:linear-gradient(rgba(0,0,0,0.7),rgba(0,0,0,0.7)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
            <div className="flex items-center justify-between">
              <Link href="/dashboard" onClick={() => setIsOpen(false)} className="flex items-center gap-2 transition hover:opacity-80">
                <KnightMark className="h-7 w-6" />
                <span className="font-display text-xl font-bold tracking-[0.16em] text-gold">PRAXIS</span>
              </Link>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="rounded-md p-2 text-white transition hover:bg-white/10"
                aria-label="Close navigation"
              >
                <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="mt-4 flex flex-col gap-1">
              <NavLinks onNavigate={() => setIsOpen(false)} />
            </div>

            <div className="mt-4 flex items-center gap-5 border-t border-white/10 pt-4">
              <BellIcon />
              <ProfileMenu />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
