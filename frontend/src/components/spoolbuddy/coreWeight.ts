// Storage key for default core weight (same key the kiosk settings page writes).
const DEFAULT_CORE_WEIGHT_KEY = 'spoolbuddy-default-core-weight';

/**
 * The kiosk-wide default empty-spool core weight (grams).
 *
 * The same logic is currently inlined in SpoolInfoCard / TagDetectedModal /
 * InventorySpoolInfoCard (predates this feature); they can migrate here in a
 * follow-up without touching this feature's code.
 */
/**
 * The core weight to use in remaining-filament math for a spool record.
 *
 * A refill's core_weight of 0 is REAL — the coil has no spool — and must
 * never fall back to the 250g default (that phantom spool made a fresh
 * 1071g refill display as 821g remaining). For non-refills the legacy
 * defensive fallback stays: a missing/zero core (older data, Spoolman
 * mappings) uses the kiosk default.
 */
export function effectiveCoreWeight(spool: {
  core_weight?: number | null;
  bought_as_refill?: boolean | null;
}): number {
  if (spool.bought_as_refill) return spool.core_weight ?? 0;
  return spool.core_weight && spool.core_weight > 0 ? spool.core_weight : getDefaultCoreWeight();
}

export function getDefaultCoreWeight(): number {
  try {
    const stored = localStorage.getItem(DEFAULT_CORE_WEIGHT_KEY);
    if (stored) {
      const w = parseInt(stored, 10);
      if (w >= 0 && w <= 500) return w;
    }
  } catch {
    // ignore
  }
  return 250; // Default 250g (typical Bambu spool core)
}
