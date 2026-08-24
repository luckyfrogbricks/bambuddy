import { useTranslation } from 'react-i18next';
import { FAMILY_LABELS, type HueFamily } from './families';
import { groupFill, type FillGroup } from './colorFill';
import { ColorDot } from './ColorDot';
import type { IndicatorPlacement } from './ActiveIndicator';

// ColorFilter — molecule. A rail of ColorDots over the hue groups the backend
// ColorManager computed for the current scope (GET /inventory/barcode/
// catalog-hues). The component only RENDERS: classification and partitioning
// are server-side, so the client never reasons about color.
//
// Group labels are always family words ("Brown", "Purple · Pink") — never a
// specific catalog color. Selection is keyed by the FAMILY SET, not dot
// index, so a scope change can never silently move the active selection onto
// different colors. A family with zero colors in scope DIMS — it never
// disappears, preserving muscle memory (fixed mode).

export interface HueGroupData extends FillGroup {
  count: number;
}

export interface ColorFilterProps {
  groups: HueGroupData[];
  /** Active group's family set (order-insensitive), or null. */
  value: string[] | null;
  /** Tapping the active dot clears (families = null). */
  onChange: (families: string[] | null, group?: HueGroupData) => void;
  orientation?: 'horizontal' | 'vertical';
  showLabels?: boolean;
  /** Defaults by orientation: horizontal → 'below', vertical → 'trailing'. */
  indicatorPlacement?: IndicatorPlacement;
}

function sameFamilySet(a: string[] | null, b: string[]): boolean {
  if (!a || a.length !== b.length) return false;
  const set = new Set(a);
  return b.every((f) => set.has(f));
}

export function ColorFilter({
  groups,
  value,
  onChange,
  orientation = 'horizontal',
  showLabels = false,
  indicatorPlacement,
}: ColorFilterProps) {
  const { t } = useTranslation();
  const placement = indicatorPlacement ?? (orientation === 'vertical' ? 'trailing' : 'below');

  const labelFor = (group: HueGroupData): string => {
    if (group.kind === 'grayscale') return t('spoolbuddy.barcode.hueGrayscale', 'Grayscale');
    if (group.kind === 'earth-tones') return t('spoolbuddy.barcode.hueEarthTones', 'Earth tones');
    if (group.kind === 'multicolor') return t('spoolbuddy.barcode.hueMulticolor', 'Multicolor');
    return group.families
      .map((f) => {
        const entry = FAMILY_LABELS[f as HueFamily];
        return entry ? t(entry.key, entry.fallback) : f;
      })
      .join(' · ');
  };

  return (
    <div
      role="group"
      className={
        orientation === 'vertical'
          ? 'flex flex-col items-start gap-0.5'
          : 'flex flex-wrap items-start gap-0.5'
      }
    >
      {groups.map((group) => {
        const active = sameFamilySet(value, group.families);
        return (
          <ColorDot
            key={group.families.join('+')}
            colors={[]}
            fillStyle={groupFill(group)}
            label={labelFor(group)}
            showLabel={showLabels}
            active={active}
            dimmed={group.count === 0}
            indicatorPlacement={placement}
            onClick={() => onChange(active ? null : [...group.families], group)}
          />
        );
      })}
    </div>
  );
}
