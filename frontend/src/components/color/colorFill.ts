import type { CSSProperties } from 'react';
import { FAMILY_DOT_HEX, type HueFamily } from './families';

// The Color Kit's shared fill model. Every kit component takes
// `colors: string | string[]` (6-char hex, `#` optional) plus a presentation.
//
// Presentation semantics (per the style-guide rulings):
// - 'vertical-striped' — hard vertical bands; the FILAMENT-identity default,
//   matching upstream FilamentSwatch's deliberate "matches the reel" look for
//   dual/tri-color products.
// - 'slash-striped' / 'backslash-striped' — hard diagonal bands (/ and \);
//   the CATEGORY default, used for merged filter dots where no product
//   identity is at stake.
// - 'gradient' — smooth 135° blend (the look of Gradient-line products).
// One color renders solid; presentation is ignored.

export type FillPresentation = 'gradient' | 'slash-striped' | 'backslash-striped' | 'vertical-striped';

// The transparency checkerboard shown UNDER any fill with a non-opaque stop,
// so translucent filament (Clear at #FFFFFF40) reads as glass instead of the
// surface behind it — same convention as the web app's FilamentSwatch.
const CHECKERBOARD = 'repeating-conic-gradient(#979797 0% 25%, #f5f5f5 0% 50%)';
const CHECKERBOARD_TILE = '12px 12px';

function normalize(colors: string | string[]): string[] {
  const list = Array.isArray(colors) ? colors : [colors];
  return list
    .map((c) => c.replace(/^#/, ''))
    .map((c) => (/^[0-9a-fA-F]{8}$/.test(c) ? c : c.slice(0, 6)))
    .filter((c) => /^[0-9a-fA-F]{6}$|^[0-9a-fA-F]{8}$/.test(c))
    .map((c) => `#${c}`);
}

function hasTranslucentStop(stops: string[]): boolean {
  return stops.some((s) => s.length === 9 && s.slice(7, 9).toLowerCase() !== 'ff');
}

/** Compose the color layer over the checkerboard when any stop is translucent. */
function layered(colorLayer: string, translucent: boolean): CSSProperties {
  if (!translucent) return { background: colorLayer };
  return {
    backgroundImage: `${colorLayer}, ${CHECKERBOARD}`,
    backgroundSize: `cover, ${CHECKERBOARD_TILE}`,
  };
}

export function colorFill(
  colors: string | string[],
  presentation: FillPresentation = 'slash-striped',
): CSSProperties {
  const stops = normalize(colors);
  if (stops.length === 0) return { background: '#808080' };
  const translucent = hasTranslucentStop(stops);
  if (stops.length === 1) {
    if (!translucent) return { background: stops[0] };
    // A translucent solid still needs the checkerboard under it, which
    // requires the gradient form (background-image layers).
    return layered(`linear-gradient(${stops[0]}, ${stops[0]})`, true);
  }

  if (presentation === 'gradient') {
    const parts = stops.map((c, i) => `${c} ${((i / (stops.length - 1)) * 100).toFixed(1)}%`);
    return layered(`linear-gradient(135deg, ${parts.join(', ')})`, translucent);
  }

  const angle =
    presentation === 'vertical-striped' ? 'to right' : presentation === 'backslash-striped' ? '45deg' : '135deg';
  const n = stops.length;
  const parts = stops.map((c, i) => {
    const from = ((i / n) * 100).toFixed(2);
    const to = (((i + 1) / n) * 100).toFixed(2);
    return `${c} ${from}% ${to}%`;
  });
  return layered(`linear-gradient(${angle}, ${parts.join(', ')})`, translucent);
}

// Special fills for reserved filter groups (see ColorManager kinds).
const GRAYSCALE_RAMP = 'linear-gradient(135deg, #0a0a0c 0%, #8e9089 50%, #fbfbf9 100%)';
const EARTH_RAMP = 'linear-gradient(135deg, #e8d5b7 0%, #b98a5a 45%, #6f5034 75%, #4f3624 100%)';
const MULTICOLOR_CONIC =
  'conic-gradient(#c12e1f, #ff9016, #fec600, #00ae42, #0056b8, #7e3f98, #ec4899, #c12e1f)';

export interface FillGroup {
  families: string[];
  kind: 'hues' | 'grayscale' | 'earth-tones' | 'multicolor';
}

/** Fill for one ColorFilter group: reserved kinds get their signature ramp;
 *  a single family gets its canonical dot color; a merged arc gets the
 *  category diagonal stripes of its families' canonical colors. */
export function groupFill(group: FillGroup): CSSProperties {
  if (group.kind === 'grayscale') return { background: GRAYSCALE_RAMP };
  if (group.kind === 'earth-tones') return { background: EARTH_RAMP };
  if (group.kind === 'multicolor') return { background: MULTICOLOR_CONIC };
  const hexes = group.families
    .filter((f): f is Exclude<HueFamily, 'multicolor'> => f in FAMILY_DOT_HEX)
    .map((f) => FAMILY_DOT_HEX[f]);
  return colorFill(hexes, 'slash-striped');
}
