import type { CSSProperties } from 'react';
import { colorFill, type FillPresentation } from './colorFill';
import {
  ActiveIndicator,
  indicatorWrapperClass,
  type IndicatorPlacement,
} from './ActiveIndicator';

// ColorDot — atom · CATEGORY. The round unit for a hue family (or a merged
// group of families), never a specific filament — that's ColorSwatch. The
// label prop is always required for the accessible name and is always a
// family label ("Brown", "Purple · Pink"), never a product name.

export interface ColorDotProps {
  colors: string | string[];
  /** Category components default to the diagonal stripes. */
  presentation?: FillPresentation;
  /** Override the fill entirely (reserved-group ramps, rainbow conic). */
  fillStyle?: CSSProperties;
  size?: 'sm' | 'md';
  label: string;
  showLabel?: boolean;
  active?: boolean;
  /** Family empty in the current scope: visible, not tappable, position kept. */
  dimmed?: boolean;
  indicatorPlacement?: IndicatorPlacement;
  onClick?: () => void;
  title?: string;
}

export function ColorDot({
  colors,
  presentation = 'slash-striped',
  fillStyle,
  size = 'md',
  label,
  showLabel = false,
  active = false,
  dimmed = false,
  indicatorPlacement = 'below',
  onClick,
  title,
}: ColorDotProps) {
  const fill = fillStyle ?? colorFill(colors, presentation);
  const dotSize = size === 'sm' ? 'w-4 h-4' : 'w-9 h-9';

  const face = (
    <span className="flex flex-col items-center gap-0.5">
      <span
        aria-hidden
        className={`${dotSize} rounded-full border border-white/15 shrink-0 ${dimmed ? 'opacity-30' : ''}`}
        style={fill}
      />
      {showLabel && (
        <span
          className={`text-[10px] leading-tight text-center max-w-[64px] truncate ${
            active ? 'text-green-400 font-semibold' : dimmed ? 'text-zinc-600' : 'text-zinc-400'
          }`}
        >
          {label}
        </span>
      )}
    </span>
  );

  const body = (
    <span className={indicatorWrapperClass(indicatorPlacement)}>
      {face}
      <ActiveIndicator active={active} placement={indicatorPlacement} compact={size === 'sm'} />
    </span>
  );

  if (!onClick) {
    return (
      <span title={title ?? label} aria-label={label}>
        {body}
      </span>
    );
  }
  return (
    <button
      type="button"
      // The dot stays 36px visually; padding keeps the hit area at ~44px.
      className="p-1 rounded-lg focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-white focus-visible:outline-offset-2 disabled:cursor-not-allowed"
      aria-label={label}
      aria-pressed={active}
      aria-disabled={dimmed || undefined}
      disabled={dimmed}
      title={title ?? label}
      onClick={onClick}
    >
      {body}
    </button>
  );
}
