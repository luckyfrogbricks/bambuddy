import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ChevronRight, Loader2, X } from 'lucide-react';
import { api, type BrowseHue, type CatalogBrowseResponse, type CatalogSearchRow } from '../../api/client';
import { spoolColorString } from '../../utils/colors';

// Tap-first catalog browsing for the kiosk: brand → material → product line →
// color, with a hue filter that can be pressed at any depth. Backed by
// GET /inventory/barcode/catalog-browse; the leaf rows it returns are
// CatalogSearchRow — the same shape the Find picker produces — so a browsed
// pick flows through BarcodeAddModal's existing confirm/create path.

export interface BrowseNav {
  brand?: string;
  material?: string;
  line?: string;
  hue?: BrowseHue | null;
}

interface CatalogBrowsePanelProps {
  nav: BrowseNav;
  /** Lifted to the parent modal so Back-from-confirm can restore this exact screen. */
  onNavChange: (nav: BrowseNav) => void;
  onPick: (row: CatalogSearchRow) => void;
  /** Back pressed at the root level — return to the screen that opened Browse. */
  onExit: () => void;
}

// One representative swatch per hue family for the filter rail.
const HUE_SWATCHES: { hue: BrowseHue; color: string }[] = [
  { hue: 'red', color: '#c12e1f' },
  { hue: 'orange', color: '#ff9016' },
  { hue: 'yellow', color: '#fec600' },
  { hue: 'green', color: '#00ae42' },
  { hue: 'blue', color: '#0056b8' },
  { hue: 'purple', color: '#7e3f98' },
  { hue: 'pink', color: '#ec4899' },
  { hue: 'neutral', color: 'linear-gradient(135deg, #18181b 0% 50%, #e4e4e7 50% 100%)' },
];

export function CatalogBrowsePanel({ nav, onNavChange, onPick, onExit }: CatalogBrowsePanelProps) {
  const { t } = useTranslation();
  const [data, setData] = useState<CatalogBrowseResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  // Re-fires the fetch effect after a failed load without changing nav.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
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

  const goBack = () => {
    if (nav.hue) onNavChange({ ...nav, hue: null });
    else if (nav.line) onNavChange({ brand: nav.brand, material: nav.material });
    else if (nav.material) onNavChange({ brand: nav.brand });
    else if (nav.brand) onNavChange({});
    else onExit();
  };

  const hueName = (hue: BrowseHue) =>
    ({
      red: t('spoolbuddy.barcode.hueRed', 'Red'),
      orange: t('spoolbuddy.barcode.hueOrange', 'Orange'),
      yellow: t('spoolbuddy.barcode.hueYellow', 'Yellow'),
      green: t('spoolbuddy.barcode.hueGreen', 'Green'),
      blue: t('spoolbuddy.barcode.hueBlue', 'Blue'),
      purple: t('spoolbuddy.barcode.huePurple', 'Purple'),
      pink: t('spoolbuddy.barcode.huePink', 'Pink'),
      neutral: t('spoolbuddy.barcode.hueNeutral', 'Black / White / Gray'),
    })[hue];

  // Crumbs jump straight back to their level with everything after them (and
  // any hue filter) cleared.
  const crumbs: { label: string; target: BrowseNav }[] = [
    { label: t('spoolbuddy.barcode.browseTitle', 'Browse Catalog'), target: {} },
    ...(nav.brand ? [{ label: nav.brand, target: { brand: nav.brand } }] : []),
    ...(nav.material ? [{ label: nav.material, target: { brand: nav.brand, material: nav.material } }] : []),
    ...(nav.line
      ? [{ label: nav.line, target: { brand: nav.brand, material: nav.material, line: nav.line } }]
      : []),
  ];

  const showBrandContext = !!nav.hue && !nav.line;

  return (
    <>
      {/* Breadcrumb trail */}
      <div className="flex items-center flex-wrap gap-1 mb-3 text-sm">
        {/* Keyed by depth — a bare line can share its material's label (e.g. PLA › PLA). */}
        {crumbs.map((crumb, i) => (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="w-3.5 h-3.5 text-zinc-600" />}
            {i < crumbs.length - 1 || nav.hue ? (
              <button
                type="button"
                onClick={() => onNavChange(crumb.target)}
                className="min-h-[36px] px-2 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700 transition-colors"
              >
                {crumb.label}
              </button>
            ) : (
              <span className="px-2 font-semibold text-zinc-100">{crumb.label}</span>
            )}
          </span>
        ))}
        {nav.hue && (
          <span className="flex items-center gap-1">
            <ChevronRight className="w-3.5 h-3.5 text-zinc-600" />
            <span className="inline-flex items-center gap-1.5 min-h-[36px] px-3 rounded-full text-sm bg-zinc-700 border border-zinc-600 text-zinc-100">
              <span
                className="w-3.5 h-3.5 rounded-full border border-zinc-500"
                style={{ background: HUE_SWATCHES.find((h) => h.hue === nav.hue)?.color }}
              />
              {hueName(nav.hue)}
              <button
                type="button"
                aria-label={t('spoolbuddy.barcode.browseClearColor', 'Clear color filter')}
                onClick={() => onNavChange({ ...nav, hue: null })}
                className="p-0.5 rounded-full text-zinc-400 hover:text-zinc-100"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </span>
          </span>
        )}
      </div>

      {/* Hue filter rail — available at every depth */}
      <div className="flex items-center gap-2 mb-4">
        <span className="text-xs text-zinc-500 shrink-0">
          {t('spoolbuddy.barcode.browseByColor', 'Color')}
        </span>
        <div className="flex gap-1.5 flex-wrap">
          {HUE_SWATCHES.map(({ hue, color }) => (
            <button
              key={hue}
              type="button"
              aria-label={hueName(hue)}
              aria-pressed={nav.hue === hue}
              onClick={() => onNavChange({ ...nav, hue: nav.hue === hue ? null : hue })}
              className={`w-9 h-9 rounded-full border-2 transition-transform ${
                nav.hue === hue
                  ? 'border-green-500 scale-110'
                  : 'border-zinc-600 hover:border-zinc-400'
              }`}
              style={{ background: color }}
            />
          ))}
        </div>
      </div>

      {/* Content area */}
      <div className="h-72 overflow-y-auto mb-4">
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
              className="min-h-[40px] px-4 rounded-lg bg-zinc-700 text-zinc-200 hover:bg-zinc-600 transition-colors"
            >
              {t('common.retry', 'Retry')}
            </button>
          </div>
        )}

        {!loading && !error && data && !data.enabled && (
          <div className="flex items-start gap-3 p-4 rounded-lg bg-amber-500/10 border border-amber-500/25 text-amber-200 text-sm">
            <AlertTriangle className="w-5 h-5 shrink-0 text-amber-500" />
            {t(
              'spoolbuddy.barcode.browseDisabled',
              'Community catalog lookups are turned off. Turn them on in the web app under Settings → Filament to browse the catalog.',
            )}
          </div>
        )}

        {!loading && !error && data?.enabled && data.level === 'brands' && (
          <BrandList data={data} onPickBrand={(brand) => onNavChange({ ...nav, brand })} />
        )}

        {!loading && !error && data?.enabled && (data.level === 'materials' || data.level === 'lines') && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {data.groups.map((group) => (
              <button
                key={group.name}
                type="button"
                onClick={() =>
                  onNavChange(
                    data.level === 'materials' ? { ...nav, material: group.name } : { ...nav, line: group.name },
                  )
                }
                className="p-3 rounded-lg bg-zinc-900/60 border border-zinc-700 hover:border-green-500/50 text-left transition-colors"
              >
                <div className="text-sm font-semibold text-zinc-100 truncate">{group.name}</div>
                <div className="text-xs text-zinc-500 mb-2">
                  {t('spoolbuddy.barcode.browseColorsCount', '{{count}} colors', { count: group.variant_count })}
                </div>
                <div className="flex gap-1">
                  {group.preview_rgbas.map((rgba, i) => (
                    <span
                      key={`${rgba}-${i}`}
                      className="w-4 h-4 rounded border border-zinc-600"
                      style={{ backgroundColor: spoolColorString(rgba) }}
                    />
                  ))}
                </div>
              </button>
            ))}
            {data.groups.length === 0 && <EmptyNote />}
          </div>
        )}

        {!loading && !error && data?.enabled && data.level === 'colors' && (
          <>
            {data.total !== null && data.total > data.colors.length && (
              <p className="text-xs text-zinc-500 mb-2">
                {t('spoolbuddy.barcode.browseShowingTop', 'Showing {{count}} of {{total}} — pick a brand to narrow down', {
                  count: data.colors.length,
                  total: data.total,
                })}
              </p>
            )}
            {showBrandContext ? (
              // Hue results across a wide scope — rows with brand/line context.
              <div className="flex flex-col gap-2">
                {data.colors.map((row, i) => (
                  <button
                    key={`${row.brand}-${row.subtype}-${row.color_name}-${i}`}
                    type="button"
                    onClick={() => onPick(row)}
                    className="flex items-center gap-3 p-3 rounded-lg bg-zinc-900/60 border border-zinc-700 hover:border-green-500/50 text-left transition-colors"
                  >
                    <span
                      className="w-8 h-8 rounded-full border-2 border-zinc-600 shrink-0"
                      style={{ backgroundColor: spoolColorString(row.rgba) }}
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-zinc-100 truncate">{row.color_name}</span>
                      <span className="block text-xs text-zinc-500 truncate">
                        {[row.brand, row.subtype || row.material].filter(Boolean).join(' • ')}
                      </span>
                    </span>
                  </button>
                ))}
                {data.colors.length === 0 && <EmptyNote />}
              </div>
            ) : (
              // A single line's palette — hue-sorted swatch grid.
              <div className="grid grid-cols-4 sm:grid-cols-6 gap-2">
                {data.colors.map((row, i) => (
                  <button
                    key={`${row.color_name}-${i}`}
                    type="button"
                    onClick={() => onPick(row)}
                    className="p-2 rounded-lg bg-zinc-900/60 border border-zinc-700 hover:border-green-500/50 text-center transition-colors"
                  >
                    <span
                      className="block w-full h-10 rounded-md border border-zinc-600 mb-1.5"
                      style={{ backgroundColor: spoolColorString(row.rgba) }}
                    />
                    <span className="block text-xs text-zinc-300 truncate">{row.color_name}</span>
                  </button>
                ))}
                {data.colors.length === 0 && <EmptyNote />}
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={goBack}
          className="flex-1 min-h-[44px] px-4 rounded-lg text-sm font-medium bg-transparent border border-zinc-600 text-zinc-400 hover:bg-zinc-700 transition-colors"
        >
          {t('common.back', 'Back')}
        </button>
      </div>
    </>
  );
}

function BrandList({
  data,
  onPickBrand,
}: {
  data: CatalogBrowseResponse;
  onPickBrand: (brand: string) => void;
}) {
  const { t } = useTranslation();
  const owned = data.brands.filter((b) => b.owned);
  const rest = data.brands.filter((b) => !b.owned);
  return (
    <div className="flex flex-col gap-3">
      {owned.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-green-500 mb-1.5">
            {t('spoolbuddy.barcode.browseYourBrands', 'Your brands')}
          </p>
          <div className="flex flex-wrap gap-2">
            {owned.map((b) => (
              <button
                key={b.name}
                type="button"
                onClick={() => onPickBrand(b.name)}
                className="min-h-[44px] px-4 rounded-lg text-sm font-medium bg-green-500/10 border border-green-500/40 text-green-300 hover:bg-green-500/20 transition-colors"
              >
                {b.name}
              </button>
            ))}
          </div>
        </div>
      )}
      <div>
        {owned.length > 0 && (
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-1.5">
            {t('spoolbuddy.barcode.browseAllBrands', 'All brands')}
          </p>
        )}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {rest.map((b) => (
            <button
              key={b.name}
              type="button"
              onClick={() => onPickBrand(b.name)}
              className="flex items-center justify-between gap-2 min-h-[44px] px-3 rounded-lg bg-zinc-900/60 border border-zinc-700 hover:border-green-500/50 text-left transition-colors"
            >
              <span className="text-sm text-zinc-200 truncate">{b.name}</span>
              <span className="text-xs text-zinc-600 shrink-0">{b.variant_count}</span>
            </button>
          ))}
        </div>
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
