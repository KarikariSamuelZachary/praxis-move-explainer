'use client';

import Image from 'next/image';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';

type NavItem = {
  href: string;
  label: string;
};

const NAV_ITEMS: NavItem[] = [
  { href: '/puzzles', label: 'Puzzles' },
  { href: '/review', label: 'Game Review' },
  { href: '/woodpecker', label: 'Woodpecker' },
  { href: '/lessons', label: 'Lessons' },
  { href: '/openings', label: 'Openings' },
  { href: '/community', label: 'Community' },
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
                : 'text-white/80 hover:text-white'
            }`}
          >
            <span>{item.label}</span>
          </Link>
        );
      })}
    </>
  );
}

function SearchIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
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

export default function TopNav() {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      {/* Desktop top nav */}
      <nav className="fixed inset-x-0 top-0 z-40 flex h-14 items-center justify-between border-b border-black/40 px-10 text-white shadow-[0_4px_20px_rgba(0,0,0,0.45)] [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] xl:px-12 2xl:px-16">
        <Link href="/puzzles" className="flex items-center gap-3 transition hover:opacity-80">
          <Image src="/praxis-logo.png" alt="Praxis" width={28} height={28} className="h-7 w-7 object-contain" />
          <span className="text-xl font-bold tracking-[0.16em]">PRAXIS</span>
        </Link>

        <div className="hidden items-center gap-8 lg:flex">
          <NavLinks />
        </div>

        <div className="hidden items-center gap-5 md:flex">
          <button type="button" className="text-white/80 transition hover:text-white" aria-label="Search">
            <SearchIcon />
          </button>
          <button type="button" className="relative text-white/80 transition hover:text-white" aria-label="Notifications">
            <BellIcon />
            <span className="absolute -right-0.5 top-0 h-2 w-2 rounded-full bg-[#10b981] ring-2 ring-black" />
          </button>
          <button type="button" className="flex h-9 w-9 items-center justify-center rounded-full bg-[#c49a7a] text-sm font-bold text-black shadow-inner shadow-white/20" aria-label="User menu">
            S
          </button>
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
          <div className="border-b border-black/40 px-5 py-4 text-white [background-image:linear-gradient(rgba(0,0,0,0.7),rgba(0,0,0,0.7)),url(/walnut-dark.png)] [background-size:cover] [background-position:center]">
            <div className="flex items-center justify-between">
              <Link href="/puzzles" onClick={() => setIsOpen(false)} className="flex items-center gap-2 transition hover:opacity-80">
                <Image src="/praxis-logo.png" alt="Praxis" width={28} height={28} className="h-7 w-7 object-contain" />
                <span className="text-xl font-bold tracking-[0.16em]">PRAXIS</span>
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
              <SearchIcon />
              <BellIcon />
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#c49a7a] text-sm font-bold text-black">
                S
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
