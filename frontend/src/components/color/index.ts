// The Color Kit — see the Color Kit style guide (bambuddy-aash-docs/color-kit.html).
// Boundary rule: category components (ColorDot, ColorPill, ColorFilter,
// TileButton faces) speak hue-family labels; filament components (ColorSwatch,
// FilamentCard) speak product colors. Color *reasoning* (classify, partition)
// is backend-only: services/color_manager.py.

export { ColorDot, type ColorDotProps } from './ColorDot';
export { ColorPill, type ColorPillProps } from './ColorPill';
export { ColorSwatch, type ColorSwatchProps } from './ColorSwatch';
export { TileButton, type TileButtonProps } from './TileButton';
export { ColorFilter, type ColorFilterProps, type HueGroupData } from './ColorFilter';
export { FilamentCard, type FilamentCardProps, type FilamentCardCode } from './FilamentCard';
export { colorFill, groupFill, type FillPresentation, type FillGroup } from './colorFill';
export { FAMILY_ORDER, FAMILY_DOT_HEX, FAMILY_LABELS, type HueFamily } from './families';
export { type IndicatorPlacement } from './ActiveIndicator';
