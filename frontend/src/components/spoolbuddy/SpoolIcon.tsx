interface SpoolIconProps {
  color: string;
  isEmpty: boolean;
  size?: number;
}

/** True when a CSS hex color carries a non-opaque alpha byte (#RRGGBBAA). */
function isTranslucent(color: string): boolean {
  const hex = color.replace(/^#/, '');
  return hex.length === 8 && hex.slice(6, 8).toLowerCase() !== 'ff';
}

export function SpoolIcon({ color, isEmpty, size = 32 }: SpoolIconProps) {
  if (isEmpty) {
    return (
      <div
        className="rounded-full border-2 border-dashed border-zinc-500 flex items-center justify-center"
        style={{ width: size, height: size }}
      >
        <div className="w-2 h-2 rounded-full bg-zinc-600" />
      </div>
    );
  }

  // Translucent filament (alpha < FF, e.g. Clear at #FFFFFF40): put a light
  // checkerboard under the tint so "clear" reads as glass instead of
  // whatever the card background happens to be — without this, a Clear
  // spool on the dark kiosk rendered as solid black.
  const translucent = isTranslucent(color);

  return (
    <svg width={size} height={size} viewBox="0 0 32 32">
      {translucent && (
        <defs>
          <pattern id="spool-checker" width="6" height="6" patternUnits="userSpaceOnUse">
            <rect width="6" height="6" fill="#f5f5f5" />
            <rect width="3" height="3" fill="#979797" />
            <rect x="3" y="3" width="3" height="3" fill="#979797" />
          </pattern>
        </defs>
      )}
      {translucent && <circle cx="16" cy="16" r="14" fill="url(#spool-checker)" />}
      {/* Outer ring with white stroke for visibility */}
      <circle cx="16" cy="16" r="14" fill={color} stroke="white" strokeWidth="1.5" strokeOpacity="0.7" />
      {/* Inner shadow/depth */}
      <circle cx="16" cy="16" r="11" fill={color} style={{ filter: 'brightness(0.85)' }} />
    </svg>
  );
}
