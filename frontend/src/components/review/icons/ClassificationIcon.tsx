export type Classification =
  | 'book'
  | 'best'
  | 'excellent'
  | 'good'
  | 'inaccuracy'
  | 'mistake'
  | 'blunder';

export const CLASSIFICATION_LABELS: Record<Classification, string> = {
  book: 'Book',
  best: 'Best',
  excellent: 'Excellent',
  good: 'Good',
  inaccuracy: 'Inaccuracy',
  mistake: 'Mistake',
  blunder: 'Blunder',
};

type ClassificationIconProps = {
  classification: Classification;
  size?: number | string;
};

export function ClassificationIcon({
  classification,
  size = 24,
}: ClassificationIconProps) {
  const common = { width: size, height: size, viewBox: '0 0 44 44', xmlns: 'http://www.w3.org/2000/svg' };

  switch (classification) {
    case 'book':
      return (
        <svg {...common}>
          <circle cx="22" cy="22" r="21" fill="#5f5e5a" stroke="#888780" strokeWidth="0.5" />
          <path d="M14 15h8a3 3 0 013 3v11a2 2 0 00-2-2h-9z" fill="none" stroke="#f1efe8" strokeWidth="1.6" strokeLinejoin="round" />
          <path d="M30 15h-8a3 3 0 00-3 3v11a2 2 0 012-2h9z" fill="none" stroke="#f1efe8" strokeWidth="1.6" strokeLinejoin="round" />
        </svg>
      );
    case 'best':
      return (
        <svg {...common}>
          <circle cx="22" cy="22" r="21" fill="#412402" stroke="#EF9F27" strokeWidth="1" />
          <path d="M22 10l3.5 8.2 8.9.8-6.7 5.9 2 8.7L22 29l-7.7 4.6 2-8.7-6.7-5.9 8.9-.8z" fill="#FAC775" />
        </svg>
      );
    case 'excellent':
      return (
        <svg {...common}>
          <circle cx="22" cy="22" r="21" fill="#04342C" stroke="#1D9E75" strokeWidth="0.5" />
          <path d="M13 22l6 6 12-13" fill="none" stroke="#9FE1CB" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      );
    case 'good':
      return (
        <svg {...common}>
          <circle cx="22" cy="22" r="21" fill="#173404" stroke="#639922" strokeWidth="0.5" />
          <path d="M14 23l5.5 5.5L31 16" fill="none" stroke="#C0DD97" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      );
    case 'inaccuracy':
      return (
        <svg {...common}>
          <circle cx="22" cy="22" r="21" fill="#412402" stroke="#BA7517" strokeWidth="0.5" />
          <text x="22" y="30" textAnchor="middle" fontSize="22" fontWeight="500" fill="#FAC775" fontFamily="Georgia,serif">?</text>
        </svg>
      );
    case 'mistake':
      return (
        <svg {...common}>
          <circle cx="22" cy="22" r="21" fill="#4A1B0C" stroke="#D85A30" strokeWidth="0.5" />
          <rect x="20" y="12" width="4" height="14" rx="2" fill="#F0997B" />
          <circle cx="22" cy="31" r="2.4" fill="#F0997B" />
        </svg>
      );
    case 'blunder':
      return (
        <svg {...common}>
          <circle cx="22" cy="22" r="21" fill="#501313" stroke="#E24B4A" strokeWidth="1" />
          <rect x="16.5" y="12" width="3.6" height="14" rx="1.8" fill="#F7C1C1" />
          <circle cx="18.3" cy="31" r="2.1" fill="#F7C1C1" />
          <rect x="24" y="12" width="3.6" height="14" rx="1.8" fill="#F7C1C1" />
          <circle cx="25.8" cy="31" r="2.1" fill="#F7C1C1" />
        </svg>
      );
    default:
      return null;
  }
}