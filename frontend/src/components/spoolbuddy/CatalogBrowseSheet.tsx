import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Check, ChevronRight, Loader2, Search, X } from 'lucide-react';
import {
  api,
  type BrowseHue,
  type CatalogBrowseResponse,
  type CatalogHueGroup,
  type CatalogSearchRow,
} from '../../api/client';
import { spoolColorString } from '../../utils/colors';
import { ColorFilter, ColorSwatch, FilamentCard, TileButton } from '../color';
import { RefillBadge } from '../RefillBadge';

// The full-screen tap-first catalog browser (1024×600 kiosk sheet):
// brand → materials (jumbo tiles) → product lines → hue-sorted color grid,
// with a fixed 11-family ColorFilter rail at every depth (empty families dim,
// never disappear) and hue-filtered results as FilamentCards, yours-first.
// The on-screen keyboard survives only as the "Search by text instead"
// footer link. Leaf picks are CatalogSearchRow — the same confirm/create
// path as a Find pick. Replaces the earlier compact CatalogBrowsePanel.

export interface BrowseNav {
  brand?: string;
  material?: string;
  line?: string;
  hue?: BrowseHue | null;
}

interface CatalogBrowseSheetProps {
  nav: BrowseNav;
  /** Lifted to the parent modal so Back-from-confirm restores this exact screen. */
  onNavChange: (nav: BrowseNav) => void;
  onPick: (row: CatalogSearchRow) => void;
  /** Back pressed at the root level, or the ✕ — return to the opening screen. */
  onExit: () => void;
  /** The footer escape hatch into the restyled keyboard search. */
  onSearchInstead: () => void;
  tagUid: string | null;
  grossWeight: number | null;
}

/** A row's fill colors: real multi-color hexes when the catalog has them,
 *  else the single rgba. */
export function rowColors(row: CatalogSearchRow): string[] {
  if (row.hexes && row.hexes.length > 0) return row.hexes;
  return [spoolColorString(row.rgba)];
}

export function rowTempRange(row: CatalogSearchRow): [number, number] | null {
  return row.nozzle_temp_min != null && row.nozzle_temp_max != null
    ? [row.nozzle_temp_min, row.nozzle_temp_max]
    : null;
}

export function CatalogBrowseSheet({
  nav,
  onNavChange,
  onPick,
  onExit,
  onSearchInstead,
  tagUid,
  grossWeight,
}: CatalogBrowseSheetProps) {
  const { t } = useTranslation();
  const [data, setData] = useState<CatalogBrowseResponse | null>(null);
  const [hueGroups, setHueGroups] = useState<CatalogHueGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  // Level data (or hue-filtered rows) for the current position.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    setExpandedIdx(null);
    api
      .browseBarcodeCatalog({
        brand: nav.brand,
        material: nav.material,
        line: nav.line,
        hue: nav.hue ?? undefined,
      })
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nav.brand, nav.material, nav.line, nav.hue, attempt]);

  // Per-family counts for the rail (fixed mode: zeros dim their dots).
  useEffect(() => {
    let cancelled = false;
    api
      .browseCatalogHues({ brand: nav.brand, material: nav.material, line: nav.line })
      .then((res) => {
        if (!cancelled) setHueGroups(res.groups);
      })
      .catch(() => {
        if (!cancelled) setHueGroups([]);
      });
    return () => {
      cancelled = true;
    };
  }, [nav.brand, nav.material, nav.line, attempt]);

  const goBack = () => {
    if (nav.hue) onNavChange({ ...nav, hue: null });
    else if (nav.line) onNavChange({ brand: nav.brand, material: nav.material });
    else if (nav.material) onNavChange({ brand: nav.brand });
    else if (nav.brand) onNavChange({});
    else onExit();
  };

  const crumbs: { label: string; target: BrowseNav }[] = [
    { label: t('spoolbuddy.barcode.browseTitle', 'Browse Catalog'), target: {} },
    ...(nav.brand ? [{ label: nav.brand, target: { brand: nav.brand } }] : []),
    ...(nav.material ? [{ label: nav.material, target: { brand: nav.brand, material: nav.material } }] : []),
    ...(nav.line
      ? [{ label: nav.line, target: { brand: nav.brand, material: nav.material, line: nav.line } }]
      : []),
  ];

  const showResultCards = !!nav.hue;

  return (
    <div className="fixed inset-0 z-50 bg-bambu-dark flex flex-col" data-testid="browse-sheet">
      {/* ── Header: crumbs + context chips + close ─────────────────────── */}
      <div className="shrink-0 flex items-center flex-wrap gap-1 px-4 pt-3 pb-1">
        {/* Keyed by depth — a bare line can share its material's label. */}
        {crumbs.map((crumb, i) => (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="w-3.5 h-3.5 text-zinc-600" />}
            {i < crumbs.length - 1 || nav.hue ? (
              <button
                type="button"
                onClick={() => onNavChange(crumb.target)}
                className="min-h-[36px] px-2 rounded-md text-sm text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700 transition-colors"
              >
                {crumb.label}
              </button>
            ) : (
              <span className="px-2 text-sm font-semibold text-zinc-100">{crumb.label}</span>
            )}
          </span>
        ))}
        <span className="flex-1" />
        <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs border border-zinc-600 text-zinc-300">
          {tagUid ? <Check className="w-3 h-3 text-green-500" /> : <span className="text-amber-500">◌</span>}
          <span className="text-zinc-500">{t('spoolbuddy.barcode.chipTag', 'Tag')}</span>
          <span className="font-mono">{tagUid ?? t('spoolbuddy.barcode.chipWaiting', 'waiting…')}</span>
        </span>
        <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs border border-zinc-600 text-zinc-300">
          {grossWeight !== null ? (
            <Check className="w-3 h-3 text-green-500" />
          ) : (
            <span className="text-amber-500">◌</span>
          )}
          <span className="text-zinc-500">{t('spoolbuddy.barcode.chipScale', 'Scale')}</span>
          <span className="font-mono">
            {grossWeight !== null ? `${grossWeight} g` : t('spoolbuddy.barcode.chipEmpty', 'empty')}
          </span>
        </span>
        <button
          type="button"
          aria-label={t('common.close', 'Close')}
          onClick={onExit}
          className="ml-1 w-9 h-9 rounded-full bg-zinc-800 border border-zinc-600 text-zinc-400 hover:text-zinc-100 flex items-center justify-center transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* ── The fixed hue rail — every family, empties dimmed ──────────── */}
      <div className="shrink-0 px-4 pb-1.5">
        <ColorFilter
          groups={hueGroups}
          value={nav.hue ? [nav.hue] : null}
          onChange={(families) =>
            onNavChange({ ...nav, hue: families ? (families[0] as BrowseHue) : null })
          }
          showLabels
        />
      </div>

      {/* ── Body ───────────────────────────────────────────────────────── */}
      <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
        {loading && (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-7 h-7 text-green-500 animate-spin" />
          </div>
        )}

        {!loading && error && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-sm text-zinc-400">
            <AlertTriangle className="w-6 h-6 text-amber-500" />
            {t('spoolbuddy.barcode.browseLoadFailed', "Couldn't load the catalog")}
            <button
              type="button"
              onClick={() => setAttempt((n) => n + 1)}
              className="min-h-[44px] px-4 rounded-lg bg-zinc-700 text-zinc-200 hover:bg-zinc-600 transition-colors"
            >
              {t('common.retry', 'Retry')}
            </button>
          </div>
        )}

        {!loading && !error && data && !data.enabled && (
          <div className="flex items-start gap-3 p-4 mt-2 rounded-lg bg-amber-500/10 border border-amber-500/25 text-amber-200 text-sm">
            <AlertTriangle className="w-5 h-5 shrink-0 text-amber-500" />
            {t(
              'spoolbuddy.barcode.browseDisabled',
              'Community catalog lookups are turned off. Turn them on in the web app under Settings → Filament to browse the catalog.',
            )}
          </div>
        )}

        {/* Brands */}
        {!loading && !error && data?.enabled && !showResultCards && data.level === 'brands' && (
          <div className="flex flex-col gap-3 pt-1">
            {data.brands.some((b) => b.owned) && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-green-500 mb-1.5">
                  {t('spoolbuddy.barcode.browseYourBrands', 'Your brands')}
                </p>
                <div className="flex flex-wrap gap-2">
                  {data.brands
                    .filter((b) => b.owned)
                    .map((b) => (
                      <button
                        key={b.name}
                        type="button"
                        onClick={() => onNavChange({ ...nav, brand: b.name })}
                        className="min-h-[44px] px-4 rounded-lg text-sm font-medium bg-green-500/10 border border-green-500/40 text-green-300 hover:bg-green-500/20 transition-colors"
                      >
                        {b.name}
                      </button>
                    ))}
                </div>
              </div>
            )}
            <div>
              {data.brands.some((b) => b.owned) && (
                <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-1.5">
                  {t('spoolbuddy.barcode.browseAllBrands', 'All brands')}
                </p>
              )}
              <div className="grid grid-cols-4 gap-2">
                {data.brands
                  .filter((b) => !b.owned)
                  .map((b) => (
                    <button
                      key={b.name}
                      type="button"
                      onClick={() => onNavChange({ ...nav, brand: b.name })}
                      className="flex items-center justify-between gap-2 min-h-[44px] px-3 rounded-lg bg-zinc-900/60 border border-zinc-700 hover:border-green-500/50 text-left transition-colors"
                    >
                      <span className="text-sm text-zinc-200 truncate">{b.name}</span>
                      <span className="text-xs text-zinc-600 shrink-0 tabular-nums">{b.variant_count}</span>
                    </button>
                  ))}
              </div>
            </div>
          </div>
        )}

        {/* Materials & lines — jumbo tiles */}
        {!loading &&
          !error &&
          data?.enabled &&
          !showResultCards &&
          (data.level === 'materials' || data.level === 'lines') && (
            <div className={`grid gap-2.5 pt-1 ${data.level === 'materials' ? 'grid-cols-4' : 'grid-cols-3'}`}>
              {data.groups.map((group) => (
                <TileButton
                  key={group.name}
                  label={group.name}
                  sublabel={t('spoolbuddy.barcode.browseColorsCount', '{{count}} colors', {
                    count: group.variant_count,
                  })}
                  onClick={() =>
                    onNavChange(
                      data.level === 'materials'
                        ? { ...nav, material: group.name }
                        : { ...nav, line: group.name },
                    )
                  }
                >
                  <span className="flex gap-1 pt-0.5">
                    {group.preview_rgbas.slice(0, 5).map((rgba, i) => (
                      <ColorSwatch key={`${rgba}-${i}`} colors={spoolColorString(rgba)} size="mini" />
                    ))}
                  </span>
                </TileButton>
              ))}
              {data.groups.length === 0 && <EmptyNote />}
            </div>
          )}

        {/* One line's palette — hue-sorted swatch grid */}
        {!loading && !error && data?.enabled && !showResultCards && data.level === 'colors' && (
          <div className="grid grid-cols-8 gap-1.5 pt-1">
            {data.colors.map((row, i) => (
              <ColorSwatch
                key={`${row.color_name}-${i}`}
                colors={rowColors(row)}
                size="small"
                label={row.color_name ?? t('spoolbuddy.barcode.unknownColor', 'Unknown color')}
                onClick={() => onPick(row)}
              />
            ))}
            {data.colors.length === 0 && <EmptyNote />}
          </div>
        )}

        {/* Hue-filtered results — FilamentCards, yours first */}
        {!loading && !error && data?.enabled && showResultCards && (
          <>
            {data.total !== null && data.total > data.colors.length && (
              <p className="text-xs text-zinc-500 pt-1 mb-2">
                {t('spoolbuddy.barcode.browseShowingTop', 'Showing {{count}} of {{total}} — pick a brand to narrow down', {
                  count: data.colors.length,
                  total: data.total,
                })}
              </p>
            )}
            <div className="grid grid-cols-4 gap-2 pt-1">
              {data.colors.map((row, i) => (
                <FilamentCard
                  key={`${row.brand}-${row.subtype}-${row.color_name}-${i}`}
                  colors={rowColors(row)}
                  title={row.color_name ?? t('spoolbuddy.barcode.unknownColor', 'Unknown color')}
                  subtitle={[row.brand, row.subtype || row.material].filter(Boolean).join(' · ')}
                  source={row.source}
                  weight={row.label_weight}
                  tempRange={rowTempRange(row)}
                  badge={
                    row.codes.length > 0 && row.codes.every((c) => c.is_refill) ? <RefillBadge /> : undefined
                  }
                  codes={row.codes}
                  expanded={expandedIdx === i}
                  onToggleExpand={() => setExpandedIdx((v) => (v === i ? null : i))}
                  onClick={() => onPick(row)}
                />
              ))}
              {data.colors.length === 0 && <EmptyNote />}
            </div>
          </>
        )}
      </div>

      {/* ── Footer: Back + the keyboard escape hatch ───────────────────── */}
      <div className="shrink-0 flex items-center gap-2 px-4 py-2 border-t border-zinc-700/60">
        <button
          type="button"
          onClick={goBack}
          className="min-h-[44px] px-5 rounded-lg text-sm font-medium bg-transparent border border-zinc-600 text-zinc-400 hover:bg-zinc-700 transition-colors"
        >
          {t('common.back', 'Back')}
        </button>
        <span className="flex-1" />
        <button
          type="button"
          onClick={onSearchInstead}
          className="min-h-[44px] px-4 rounded-lg text-sm text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700 transition-colors flex items-center gap-2"
        >
          <Search className="w-4 h-4" />
          {t('spoolbuddy.barcode.browseSearchInstead', 'Search by text instead')}
        </button>
      </div>
    </div>
  );
}

function EmptyNote() {
  const { t } = useTranslation();
  return (
    <p className="col-span-full text-sm text-zinc-500 text-center py-6">
      {t('spoolbuddy.barcode.browseEmpty', 'Nothing here')}
    </p>
  );
}
