import type { CSSProperties } from 'react';
import { colorFill, type FillPresentation } from './colorFill';

// ColorPill — atom · CATEGORY. A chip: small dot + family label + optional
// scope-aware count ("how many results this filter would return here" —
// counts come from the backend partition response, never a client tally).

export interface ColorPillProps {
  colors: string | string[];
  presentation?: FillPresentation;
  fillStyle?: CSSProperties;
  label: string;
  count?: number;
  active?: boolean;
  dimmed?: boolean;
  onClick?: () => void;
}

export function ColorPill({
  colors,
  presentation = 'slash-striped',
  fillStyle,
  label,
  count,
  active = false,
  dimmed = false,
  onClick,
}: ColorPillProps) {
  const fill = fillStyle ?? colorFill(colors, presentation);
  // Active keeps the green tint on the CHROME (fill + border) but the label
  // stays neutral, brightened — a color-name label never renders in a
  // non-neutral color ("Brown" in green would lie about its subject).
  const stateClass = active
    ? 'bg-green-500/10 border-green-500/60 text-zinc-100 font-semibold'
    : 'bg-zinc-700 border-zinc-600 text-zinc-300';
  const body = (
    <>
      <span aria-hidden className="w-4 h-4 rounded-full border border-white/15 shrink-0" style={fill} />
      <span className="truncate">{label}</span>
      {count !== undefined && <span className="text-zinc-500 tabular-nums">{count}</span>}
    </>
  );
  if (!onClick) {
    return (
      <span
        className={`inline-flex items-center gap-1.5 min-h-[38px] px-3 rounded-full text-sm font-medium border ${stateClass} ${dimmed ? 'opacity-40' : ''}`}
      >
        {body}
      </span>
    );
  }
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-disabled={dimmed || undefined}
      disabled={dimmed}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 min-h-[44px] px-3.5 rounded-full text-sm font-medium border transition-colors ${stateClass} ${
        dimmed ? 'opacity-40 cursor-not-allowed' : 'hover:bg-zinc-600'
      } focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-white focus-visible:outline-offset-2`}
    >
      {body}
    </button>
  );
}
