import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { ColorSwatch } from './ColorSwatch';
import type { FillPresentation } from './colorFill';
import { RefillBadge } from '../RefillBadge';

// FilamentCard — organism. A purchasable filament as a tappable card: the
// result unit of the catalog picker's hue-filtered and search screens. NOT a
// color component — it composes ColorSwatch (large) as its banner and knows
// everything about one product: names, provenance, weight/temps, codes.

export interface FilamentCardCode {
  code: string;
  kind: string;
  is_refill: boolean;
}

export interface FilamentCardProps {
  colors: string | string[];
  presentation?: FillPresentation;
  title: string;
  subtitle?: string;
  source?: 'inventory' | 'ofd' | 'spoolmandb-community';
  weight?: number | null;
  tempRange?: [number, number] | null;
  /** Shown mono in the meta line while collapsed, so near-identical twins
   *  (same color, different package code) stay tellable apart at a glance. */
  primaryCode?: string | null;
  badge?: React.ReactNode;
  codes?: FilamentCardCode[];
  expanded?: boolean;
  onToggleExpand?: () => void;
  selected?: boolean;
  onClick?: () => void;
}

export function FilamentCard({
  colors,
  presentation = 'vertical-striped',
  title,
  subtitle,
  source,
  weight,
  tempRange,
  primaryCode,
  badge,
  codes,
  expanded = false,
  onToggleExpand,
  selected = false,
  onClick,
}: FilamentCardProps) {
  const { t } = useTranslation();
  const sourceLabel =
    source === 'inventory'
      ? t('spoolbuddy.barcode.pillInventory', 'Your inventory')
      : source === 'ofd'
        ? t('spoolbuddy.barcode.pillOfd', 'Open Filament DB')
        : source === 'spoolmandb-community'
          ? t('spoolbuddy.barcode.pillSpoolmandb', 'SpoolmanDB')
          : null;

  const banner = (
    <span className="relative block">
      <ColorSwatch colors={colors} presentation={presentation} size="large" />
      {sourceLabel && (
        // Dark translucent scrim so the pill reads over any fill — orange
        // on orange included.
        <span className="absolute top-1.5 right-1.5 text-[10px] font-bold px-2 py-0.5 rounded-full border border-white/25 text-zinc-200 bg-black/60">
          {sourceLabel}
        </span>
      )}
    </span>
  );

  const meta: string[] = [];
  if (weight != null) meta.push(`${weight >= 1000 ? `${weight / 1000} kg` : `${weight} g`}`);
  if (tempRange) meta.push(`${tempRange[0]}–${tempRange[1]} °C`);

  const inner = (
    <>
      {banner}
      <span className="block mt-2 text-sm font-semibold text-zinc-100 truncate">{title}</span>
      {subtitle && <span className="block text-xs text-zinc-500 truncate">{subtitle}</span>}
      {(meta.length > 0 || primaryCode) && (
        <span className="block mt-1 text-[11px] text-zinc-500 tabular-nums truncate">
          {meta.join(' · ')}
          {primaryCode && <span className="font-mono">{meta.length > 0 ? ' · ' : ''}{primaryCode}</span>}
        </span>
      )}
      {badge && <span className="block mt-1.5">{badge}</span>}
    </>
  );

  const cardClass = `w-full text-left rounded-xl border p-3 transition-colors ${
    selected
      ? 'border-green-500 ring-1 ring-green-500 bg-zinc-900/60'
      : 'border-zinc-700 bg-zinc-900/60 hover:border-green-500/50'
  }`;

  return (
    <div className={cardClass}>
      {onClick ? (
        <button
          type="button"
          onClick={onClick}
          aria-pressed={selected}
          className="w-full text-left focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-white focus-visible:outline-offset-2"
        >
          {inner}
        </button>
      ) : (
        inner
      )}
      {codes && codes.length > 0 && onToggleExpand && (
        <>
          <button
            type="button"
            aria-label={t('spoolbuddy.barcode.details', 'Details')}
            aria-expanded={expanded}
            onClick={onToggleExpand}
            className="mt-2 w-full flex items-center justify-center gap-1 min-h-[32px] text-xs text-zinc-500 hover:text-zinc-200 border-t border-zinc-700/60 pt-1.5 transition-colors"
          >
            {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            {t('spoolbuddy.barcode.details', 'Details')}
          </button>
          {expanded && (
            <div className="mt-1.5 flex flex-col gap-1">
              {codes.map((c) => (
                <span key={c.code} className="flex items-center gap-2 text-xs text-zinc-300">
                  <span className="font-mono">{c.code}</span>
                  <span className="uppercase text-[10px] text-zinc-500">{c.kind}</span>
                  {c.is_refill && <RefillBadge />}
                </span>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
