import type { CSSProperties, ReactNode } from 'react';
import { colorFill, type FillPresentation } from './colorFill';

// TileButton — the big embossed button, extracted so 3D exists in exactly one
// place. It owns the inset highlight/shade, the sheen overlay, the inset
// label, the pressed depression, and the selected ring — and takes any FACE:
// a color fill (Pick-by-Color tiles) or arbitrary content like a big material
// name (Pick-by-Material tiles). Nothing else in the system may emboss.

export interface TileButtonProps {
  label: string;
  sublabel?: string;
  /** Color face: fill props. Omit for a text/content face on the dark ground. */
  colors?: string | string[];
  presentation?: FillPresentation;
  fillStyle?: CSSProperties;
  /** Free face content rendered above the inset label (e.g. a big material name). */
  children?: ReactNode;
  selected?: boolean;
  dimmed?: boolean;
  onClick?: () => void;
  className?: string;
}

export function TileButton({
  label,
  sublabel,
  colors,
  presentation = 'slash-striped',
  fillStyle,
  children,
  selected = false,
  dimmed = false,
  onClick,
  className = '',
}: TileButtonProps) {
  const fill: CSSProperties =
    fillStyle ?? (colors !== undefined ? colorFill(colors, presentation) : { background: '#2b2b30' });

  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-disabled={dimmed || undefined}
      disabled={dimmed || !onClick}
      onClick={onClick}
      className={`relative overflow-hidden rounded-xl border border-white/10 text-left min-h-[96px] p-3 flex flex-col justify-end transition-transform active:scale-[0.985] disabled:cursor-not-allowed focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-white focus-visible:outline-offset-2 ${
        selected ? 'outline outline-2 outline-green-500 outline-offset-2' : ''
      } ${dimmed ? 'opacity-40' : ''} ${className}`}
      style={{
        ...fill,
        boxShadow:
          'inset 0 1px 0 rgba(255,255,255,0.35), inset 0 -12px 18px rgba(0,0,0,0.28), 0 6px 16px -8px rgba(0,0,0,0.5)',
      }}
    >
      {/* The sheen overlay — the "embossed" 3D read, no images needed. */}
      <span
        aria-hidden
        className="absolute inset-0 rounded-xl pointer-events-none bg-gradient-to-b from-white/15 via-transparent to-black/10"
      />
      {children && <span className="relative flex-1 w-full">{children}</span>}
      <span
        className="relative text-base font-semibold text-white"
        style={{ textShadow: '0 1px 6px rgba(0,0,0,0.55)' }}
      >
        {label}
      </span>
      {sublabel && (
        <span
          className="relative text-xs text-white/80"
          style={{ textShadow: '0 1px 4px rgba(0,0,0,0.5)' }}
        >
          {sublabel}
        </span>
      )}
    </button>
  );
}
