export type Classification =
  | 'book'
  | 'brilliant'
  | 'great'
  | 'best'
  | 'excellent'
  | 'good'
  | 'inaccuracy'
  | 'mistake'
  | 'miss'
  | 'blunder';

export const CLASSIFICATION_LABELS: Record<Classification, string> = {
  book: 'Book',
  brilliant: 'Brilliant',
  great: 'Great',
  best: 'Best',
  excellent: 'Excellent',
  good: 'Good',
  inaccuracy: 'Inaccuracy',
  mistake: 'Mistake',
  miss: 'Miss',
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
          <circle cx="22" cy="22" r="21" fill="#3a2412" stroke="#c1954f" strokeWidth="1" />
          <path
            d="M22 14.5c-2.6-1.7-5.7-2.1-8.8-1.6V28c3.1-.5 6.2-.1 8.8 1.6 2.6-1.7 5.7-2.1 8.8-1.6V12.9c-3.1-.5-6.2-.1-8.8 1.6Z"
            fill="#f3e7c3"
            stroke="#2a1a06"
            strokeWidth="1"
            strokeLinejoin="round"
          />
          <path d="M22 14.5V29.6" stroke="#8a6136" strokeWidth="1.2" />
          <path
            d="M15.6 17.6c1.5-.3 3-.2 4.4.3M15.6 21.1c1.5-.3 3-.2 4.4.3M24 17.9c1.4-.5 2.9-.6 4.4-.3M24 21.4c1.4-.5 2.9-.6 4.4-.3"
            fill="none"
            stroke="#b99a5e"
            strokeWidth="1"
            strokeLinecap="round"
          />
          <path
            d="M26.6 12.4h3.4V19l-1.7-1.2-1.7 1.2Z"
            fill="#eacb90"
            stroke="#2a1a06"
            strokeWidth="0.6"
            strokeLinejoin="round"
          />
        </svg>
      );
    case 'brilliant':
      return (
        <svg {...common}>
          <circle cx="22" cy="22" r="21" fill="#083D3D" stroke="#2BC4B4" strokeWidth="1" />
          <text x="22" y="29" textAnchor="middle" fontSize="17" fontWeight="700" fill="#A8F0E6" fontFamily="Georgia,serif">!!</text>
        </svg>
      );
    case 'great':
      return (
        <svg {...common}>
          <circle cx="22" cy="22" r="21" fill="#0B3B2A" stroke="#1D9E75" strokeWidth="0.5" />
          <text x="22" y="30" textAnchor="middle" fontSize="20" fontWeight="700" fill="#9FE1CB" fontFamily="Georgia,serif">!</text>
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
          <circle cx="22" cy="22" r="21" fill="#54400d" stroke="#d9a92e" strokeWidth="1" />
          <text x="22" y="30" textAnchor="middle" fontSize="17" fontWeight="700" fill="#ffe6a3" fontFamily="Georgia,serif">?!</text>
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
    case 'miss':
      return (
        <svg {...common}>
          <circle cx="22" cy="22" r="21" fill="#3B0A16" stroke="#E24B4A" strokeWidth="0.5" />
          <path d="M15 15l14 14M29 15L15 29" fill="none" stroke="#F7C1C1" strokeWidth="3" strokeLinecap="round" />
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