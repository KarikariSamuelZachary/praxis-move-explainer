'use client';

import { ReactNode } from 'react';

type ModalProps = {
  children: ReactNode;
  onClose: () => void;
};

export default function Modal({ children, onClose }: ModalProps) {
  return (
    <div
      aria-modal="true"
      role="dialog"
      className="fixed inset-0 z-50 flex min-h-screen items-center justify-center bg-zinc-950/80 px-4 py-8 backdrop-blur-sm"
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 cursor-default"
      />
      <div className="relative max-h-[calc(100vh-4rem)] w-full max-w-[430px] overflow-y-auto rounded-2xl border border-black/50 p-6 text-zinc-50 [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)] sm:p-8">
        <button
          type="button"
          aria-label="Close modal"
          onClick={onClose}
          className="absolute right-5 top-5 flex h-9 w-9 items-center justify-center rounded-md text-zinc-300 transition hover:bg-white/10 hover:text-zinc-50"
        >
          <svg
            aria-hidden="true"
            className="h-5 w-5"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.8"
            viewBox="0 0 24 24"
          >
            <path d="M6 6l12 12" />
            <path d="M18 6L6 18" />
          </svg>
        </button>
        {children}
      </div>
    </div>
  );
}
