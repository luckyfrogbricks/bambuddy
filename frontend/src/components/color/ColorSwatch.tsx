import { colorFill, type FillPresentation } from './colorFill';
import {
  ActiveIndicator,
  indicatorWrapperClass,
  type IndicatorPlacement,
} from './ActiveIndicator';

// ColorSwatch — atom · FILAMENT. The rectangular unit for one particular
// filament's colors, never a category. Three sizes, ALL FLAT — embossing
// lives exclusively in TileButton. Defaults to the vertical-striped fill so
// dual-color products match how upstream FilamentSwatch (and the reel)
// splits them; diagonals are the category components' look.

export interface ColorSwatchProps {
  colors: string | string[];
  presentation?: FillPresentation;
  size: 'mini' | 'small' | 'large';
  /** mini: tooltip/aria only · small: visible below · large: none (the card provides text). */
  label?: string;
  selected?: boolean;
  indicatorPlacement?: IndicatorPlacement;
  onClick?: () => void;
}

export function ColorSwatch({
  colors,
  presentation = 'vertical-striped',
  size,
  label,
  selected = false,
  indicatorPlacement = 'below',
  onClick,
}: ColorSwatchProps) {
  const fill = colorFill(colors, presentation);

  if (size === 'mini') {
    return (
      <span
        aria-label={label}
        title={label}
        className="w-4 h-4 rounded border border-white/15 shrink-0 inline-block"
        style={fill}
      />
    );
  }

  if (size === 'large') {
    return (
      <span
        aria-label={label}
        className="block w-full aspect-[2.6] rounded-lg border border-white/15"
        style={fill}
      />
    );
  }

  // small — chiclet with its label underneath (the color-grid unit).
  const body = (
    <span className={`${indicatorWrapperClass(indicatorPlacement)} w-full`}>
      <span className="flex flex-col gap-1 w-full min-w-0">
        <span aria-hidden className="block w-full h-11 rounded-lg border border-white/15" style={fill} />
        {label && (
          <span
            className={`block text-[11px] leading-tight truncate text-center ${
              selected ? 'text-green-400 font-semibold' : 'text-zinc-300'
            }`}
          >
            {label}
          </span>
        )}
      </span>
      <ActiveIndicator active={selected} placement={indicatorPlacement} />
    </span>
  );

  if (!onClick) return <span aria-label={label}>{body}</span>;
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={selected}
      onClick={onClick}
      className="w-full min-w-0 p-1 rounded-lg transition-colors hover:bg-zinc-800 focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-white focus-visible:outline-offset-2"
    >
      {body}
    </button>
  );
}
