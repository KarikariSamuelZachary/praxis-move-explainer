import type { CSSProperties } from 'react';

/**
 * The app's dark wood-panel surface: the landing page's Woodpecker card, the
 * Endgame Rating / Reviews Due cards and the signed-in navbar are the same
 * material, so they share one surface instead of drifting apart.
 */
export const WOOD_PANEL_CLASS =
  'relative overflow-hidden rounded-2xl border border-white/10 shadow-[0_18px_50px_rgba(0,0,0,0.5)]';

export const WOOD_PANEL_STYLE: CSSProperties = {
  background:
    'linear-gradient(rgba(18,11,7,0.72), rgba(18,11,7,0.86)), url(/wood-panel-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
};
