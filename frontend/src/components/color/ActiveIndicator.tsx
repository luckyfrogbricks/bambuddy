// The kit's active/selected marker: a small green bar OUTSIDE the fill, so it
// keeps contrast on any color — including a green dot, where a green ring
// would vanish. Placement is a parameter because filter rails can run
// vertically as well as horizontally. The bar always occupies its footprint
// (transparent when inactive) so activation never shifts layout.

export type IndicatorPlacement = 'above' | 'below' | 'leading' | 'trailing';

export function isVerticalIndicator(placement: IndicatorPlacement): boolean {
  return placement === 'leading' || placement === 'trailing';
}

/** Wrapper flex classes that put the indicator on the requested side. */
export function indicatorWrapperClass(placement: IndicatorPlacement): string {
  switch (placement) {
    case 'above':
      return 'flex flex-col-reverse items-center gap-1';
    case 'leading':
      return 'flex flex-row-reverse items-center gap-1';
    case 'trailing':
      return 'flex flex-row items-center gap-1';
    default:
      return 'flex flex-col items-center gap-1';
  }
}

export function ActiveIndicator({
  active,
  placement,
  compact = false,
}: {
  active: boolean;
  placement: IndicatorPlacement;
  compact?: boolean;
}) {
  const vertical = isVerticalIndicator(placement);
  const length = compact ? (vertical ? 'h-3' : 'w-3') : vertical ? 'h-6' : 'w-6';
  const thickness = vertical ? 'w-1' : 'h-1';
  return (
    <span
      aria-hidden
      data-testid="active-indicator"
      className={`rounded-full shrink-0 ${thickness} ${length} ${active ? 'bg-green-500' : 'bg-transparent'}`}
    />
  );
}
