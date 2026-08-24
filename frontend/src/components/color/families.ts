// Hue-family constants for the Color Kit — the client-side mirror of the
// backend's HUE_FAMILIES/CHROMATIC_WHEEL (services/color_manager.py). The
// client never CLASSIFIES colors (that stays server-side so the HSL
// thresholds live in one place); it only needs the family roster, canonical
// swatch colors, and label keys to RENDER what the backend returns.

export type HueFamily =
  | 'red'
  | 'orange'
  | 'brown'
  | 'yellow'
  | 'green'
  | 'blue'
  | 'purple'
  | 'pink'
  | 'black'
  | 'gray'
  | 'white'
  | 'multicolor';

/** Canonical rail order: the chromatic wheel (brown between orange and
 *  yellow — browns are dark, muted orange-yellows), then the achromatic run,
 *  then the synthetic multicolor family. */
export const FAMILY_ORDER: HueFamily[] = [
  'red', 'orange', 'brown', 'yellow', 'green', 'blue', 'purple', 'pink',
  'black', 'gray', 'white', 'multicolor',
];

/** One representative swatch color per family (the filter-rail dots). */
export const FAMILY_DOT_HEX: Record<Exclude<HueFamily, 'multicolor'>, string> = {
  red: '#c12e1f',
  orange: '#ff9016',
  brown: '#8a5a34',
  yellow: '#fec600',
  green: '#00ae42',
  blue: '#0056b8',
  purple: '#7e3f98',
  pink: '#ec4899',
  black: '#131316',
  gray: '#8e9089',
  white: '#f6f6f4',
};

/** i18n key + fallback per family (keys live in spoolbuddy.barcode.*). */
export const FAMILY_LABELS: Record<HueFamily, { key: string; fallback: string }> = {
  red: { key: 'spoolbuddy.barcode.hueRed', fallback: 'Red' },
  orange: { key: 'spoolbuddy.barcode.hueOrange', fallback: 'Orange' },
  brown: { key: 'spoolbuddy.barcode.hueBrown', fallback: 'Brown' },
  yellow: { key: 'spoolbuddy.barcode.hueYellow', fallback: 'Yellow' },
  green: { key: 'spoolbuddy.barcode.hueGreen', fallback: 'Green' },
  blue: { key: 'spoolbuddy.barcode.hueBlue', fallback: 'Blue' },
  purple: { key: 'spoolbuddy.barcode.huePurple', fallback: 'Purple' },
  pink: { key: 'spoolbuddy.barcode.huePink', fallback: 'Pink' },
  black: { key: 'spoolbuddy.barcode.hueBlack', fallback: 'Black' },
  gray: { key: 'spoolbuddy.barcode.hueGray', fallback: 'Gray' },
  white: { key: 'spoolbuddy.barcode.hueWhite', fallback: 'White' },
  multicolor: { key: 'spoolbuddy.barcode.hueMulticolor', fallback: 'Multicolor' },
};
