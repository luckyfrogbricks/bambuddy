/**
 * Join a spool's material and product line for display without stuttering.
 *
 * Catalog line names legitimately include the material — Bambu's own store
 * sells "PETG Translucent" and "PLA Matte" — so the naive
 * `material + ' ' + subtype` join produced "PETG PETG Translucent" /
 * "PLA PLA Pure" all over the kiosk. When the subtype already starts with
 * the material word, the subtype alone IS the full product line.
 */
export function materialLine(material?: string | null, subtype?: string | null): string {
  if (!subtype) return material ?? '';
  if (!material) return subtype;
  return subtype.toLowerCase().startsWith(material.toLowerCase()) ? subtype : `${material} ${subtype}`;
}
