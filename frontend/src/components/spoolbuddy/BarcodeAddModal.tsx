import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Barcode, Check, Loader2, Search, AlertTriangle } from 'lucide-react';
import { api, type InventorySpool, type CatalogSearchRow } from '../../api/client';
import type { ScannedBarcode, LinkedCode } from '../../hooks/useSpoolBuddyState';
import { spoolColorString } from '../../utils/colors';
import { SpoolIcon } from './SpoolIcon';
import { KioskToggle } from './KioskToggle';
import { getDefaultCoreWeight } from './coreWeight';

// NOTE: this is the SpoolBuddy (kiosk) add-to-inventory flow, driven entirely
// by the hardware USB barcode scanner. It deliberately does NOT use the main
// app's camera/OCR BarcodeScannerModal — the kiosk has no camera and runs over
// plain HTTP where getUserMedia is unavailable.

type Step = 'waiting' | 'manual' | 'looking_up' | 'confirm' | 'no_match' | 'find';

// A resolved filament ready to preview/create. Barcode-derived scans, manual
// lookups, and catalog picks all normalize to this shape.
interface Resolved {
  barcode: string;
  source: ScannedBarcode['source'];
  material: string | null;
  brand: string | null;
  subtype: string | null;
  color_name: string | null;
  rgba: string | null;
  label_weight: number | null;
  /** The resolved code is itself a no-spool refill (backend-detected). */
  is_refill: boolean;
  linked_codes: LinkedCode[];
}

function fromScan(scan: ScannedBarcode): Resolved {
  return {
    barcode: scan.barcode,
    source: scan.source,
    material: scan.material,
    brand: scan.brand,
    subtype: scan.subtype,
    color_name: scan.color_name,
    rgba: scan.rgba,
    label_weight: scan.label_weight,
    is_refill: scan.is_refill,
    linked_codes: scan.linked_codes,
  };
}

interface BarcodeAddModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Live latest scan from useSpoolBuddyState — a new receivedAt supersedes the current one. */
  scan: ScannedBarcode | null;
  /** Tag currently on the scale (null → barcode-first / tagless add). */
  tagUid: string | null;
  trayUuid: string | null;
  /** Live gross scale weight in grams. */
  scaleWeight: number | null;
  spoolmanMode: boolean;
  /** Loaded inventory, for instant local search in the Find step. */
  spools: InventorySpool[];
  onCreated: () => void;
  /** "Add Without Barcode" → fall back to the legacy quick-add dialog. */
  onFallbackQuickAdd: () => void;
  /** Ack the consumed scan so a stale one can't re-open the modal later. */
  clearScan: () => void;
}

export function BarcodeAddModal({
  isOpen,
  onClose,
  scan,
  tagUid,
  trayUuid,
  scaleWeight,
  spoolmanMode,
  spools,
  onCreated,
  onFallbackQuickAdd,
  clearScan,
}: BarcodeAddModalProps) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>('waiting');
  // Where the Find screen was opened from, so its Back button returns there
  // (the scan-waiting screen B, or the no-match screen E).
  const [findBackStep, setFindBackStep] = useState<Step>('no_match');
  const [resolved, setResolved] = useState<Resolved | null>(null);
  const [invalidCode, setInvalidCode] = useState<string | null>(null);
  const [manualCode, setManualCode] = useState('');
  const [findQuery, setFindQuery] = useState('');
  const [findRows, setFindRows] = useState<CatalogSearchRow[]>([]);
  const [findLoading, setFindLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [linkedByUser, setLinkedByUser] = useState(false);
  // "Refill" vs "with spool": the community DBs mark this via eans_refill /
  // spool_refill, so a scanned/looked-up known code auto-arms this toggle
  // (applyResolved / selectCatalogRow set it from the resolved code). A
  // user-linked (Find This Filament) or manually-typed unknown code carries no
  // such signal, so the user sets it here. It drives the core-weight default (a
  // bare refill has no Bambu spool) and the is_refill flag stored on the spool.
  const [isRefill, setIsRefill] = useState(false);
  const handledReceiptRef = useRef<number | null>(null);

  const coreWeight = getDefaultCoreWeight();

  const applyResolved = useCallback((r: Resolved, matched: boolean, wasManual: boolean) => {
    setResolved(r);
    setInvalidCode(null);
    setLinkedByUser(false);
    // Auto-arm the refill toggle when the resolved code is itself a known refill
    // (community DBs flag it); the user can still override on the confirm screen.
    setIsRefill(r.is_refill);
    // A hit (matched, or fields present from OCR/manual) goes straight to
    // confirm; a valid-but-unmatched code lands on the "no match" screen so
    // the user can search for the right filament instead.
    if (matched || r.material) {
      setStep('confirm');
    } else {
      setStep('no_match');
    }
    void wasManual;
  }, []);

  // React to a fresh scan arriving (auto-open path, Rescan, or a scan landing
  // while the modal is already open — replace-latest).
  useEffect(() => {
    if (!isOpen || !scan) return;
    if (handledReceiptRef.current === scan.receivedAt) return;
    handledReceiptRef.current = scan.receivedAt;
    if (!scan.valid) {
      setInvalidCode(scan.barcode);
      setStep('waiting');
      return;
    }
    applyResolved(fromScan(scan), scan.matched, false);
  }, [isOpen, scan, applyResolved]);

  // Reset to a clean slate each time the modal opens without a scan in hand.
  useEffect(() => {
    if (!isOpen) return;
    if (!scan) {
      setStep('waiting');
      setResolved(null);
      setInvalidCode(null);
    }
    setManualCode('');
    setFindQuery('');
    setFindRows([]);
    setBusy(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const lookupCode = useCallback(
    async (code: string) => {
      const trimmed = code.trim();
      if (!trimmed) return;
      setStep('looking_up');
      try {
        const res = await api.lookupFilamentBarcode(trimmed);
        applyResolved(
          {
            barcode: res.barcode,
            source: res.source,
            material: res.material,
            brand: res.brand,
            subtype: res.subtype,
            color_name: res.color_name,
            rgba: res.rgba,
            label_weight: res.label_weight,
            is_refill: res.is_refill,
            linked_codes: res.linked_codes,
          },
          res.matched,
          true,
        );
      } catch {
        setInvalidCode(trimmed);
        setStep('waiting');
      }
    },
    [applyResolved],
  );

  // Instant local inventory matches from the already-loaded spools list, so
  // the user's own filaments appear the moment they type — no round-trip.
  const localMatches = useMemo<CatalogSearchRow[]>(() => {
    const q = findQuery.trim().toLowerCase();
    if (q.length < 2) return [];
    const tokens = q.split(/\s+/);
    return spools
      .filter((s) => !s.archived_at)
      .filter((s) => {
        const hay = [s.brand, s.material, s.subtype, s.color_name].filter(Boolean).join(' ').toLowerCase();
        return tokens.every((tok) => hay.includes(tok));
      })
      .slice(0, 10)
      .map((s) => ({
        source: 'inventory' as const,
        spool_id: s.id,
        material: s.material,
        brand: s.brand,
        subtype: s.subtype,
        color_name: s.color_name,
        rgba: s.rgba,
        label_weight: s.label_weight,
        nozzle_temp_min: s.nozzle_temp_min ?? null,
        nozzle_temp_max: s.nozzle_temp_max ?? null,
        codes: s.barcode ? [{ code: s.barcode, kind: 'gtin', is_refill: false }] : [],
      }));
  }, [findQuery, spools]);

  // Debounced catalog search for the Find step (inventory + community DBs).
  useEffect(() => {
    if (step !== 'find') return;
    const q = findQuery.trim();
    if (q.length < 2) {
      setFindRows([]);
      return;
    }
    let cancelled = false;
    setFindLoading(true);
    const handle = setTimeout(async () => {
      try {
        const rows = await api.searchBarcodeCatalog(q);
        if (!cancelled) setFindRows(rows);
      } catch {
        if (!cancelled) setFindRows([]);
      } finally {
        if (!cancelled) setFindLoading(false);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [step, findQuery]);

  // Merge instant local matches ahead of server rows, de-duplicating the
  // backend's own inventory hits (same spool_id) so they don't appear twice.
  const displayRows = useMemo<CatalogSearchRow[]>(() => {
    const localIds = new Set(localMatches.map((r) => r.spool_id));
    return [...localMatches, ...findRows.filter((r) => r.spool_id == null || !localIds.has(r.spool_id))];
  }, [localMatches, findRows]);

  const selectCatalogRow = useCallback(
    (row: CatalogSearchRow) => {
      setResolved((prev) => ({
        barcode: prev?.barcode ?? resolved?.barcode ?? '',
        source: 'inventory',
        material: row.material,
        brand: row.brand,
        subtype: row.subtype,
        color_name: row.color_name,
        rgba: row.rgba,
        label_weight: row.label_weight,
        is_refill: row.codes.length > 0 && row.codes.every((c) => c.is_refill),
        linked_codes: row.codes,
      }));
      setLinkedByUser(true);
      // Prefill refill-ness from the picked catalog row if it's known there
      // (its codes carry is_refill); otherwise leave the user's toggle as-is.
      if (row.codes.length && row.codes.every((c) => c.is_refill)) setIsRefill(true);
      setStep('confirm');
    },
    [resolved],
  );

  const handleClose = useCallback(() => {
    clearScan();
    onClose();
  }, [clearScan, onClose]);

  const buildPayload = useCallback(
    (r: Resolved, useTag: boolean): Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'> => {
      const weight = scaleWeight;
      // A refill has no Bambu spool, so default its core weight to 0 (the user
      // can adjust if they mounted it on a reusable spool).
      const effectiveCore = isRefill ? 0 : coreWeight;
      return {
        material: r.material || 'PLA',
        subtype: r.subtype ?? null,
        color_name: r.color_name ?? null,
        rgba: r.rgba ?? null,
        extra_colors: null,
        effect_type: null,
        brand: r.brand ?? null,
        label_weight: r.label_weight ?? 1000,
        core_weight: effectiveCore,
        core_weight_catalog_id: null,
        weight_used: 0,
        slicer_filament: null,
        slicer_filament_name: null,
        nozzle_temp_min: null,
        nozzle_temp_max: null,
        note: null,
        added_full: null,
        last_used: null,
        encode_time: null,
        tag_uid: !spoolmanMode && useTag ? tagUid : null,
        tray_uuid: null,
        data_origin: 'barcode_scan',
        tag_type: !spoolmanMode && useTag ? 'generic' : null,
        barcode: r.barcode || null,
        barcode_is_refill: isRefill,
        cost_per_kg: null,
        last_scale_weight: weight !== null ? Math.round(weight) : null,
        last_weighed_at: weight !== null ? new Date().toISOString() : null,
        category: null,
        low_stock_threshold_pct: null,
      };
    },
    [coreWeight, isRefill, scaleWeight, spoolmanMode, tagUid],
  );

  const handleCreate = useCallback(
    async (useTag: boolean) => {
      if (!resolved) return;
      setBusy(true);
      try {
        const payload = buildPayload(resolved, useTag);
        if (spoolmanMode) {
          const created = await api.createSpoolmanInventorySpool(payload);
          if (useTag && (tagUid || trayUuid)) {
            await api.linkTagToSpoolmanSpool(created.id, {
              tag_uid: tagUid || undefined,
              tray_uuid: !tagUid && trayUuid ? trayUuid : undefined,
            });
          }
        } else {
          await api.createSpool(payload);
        }
        onCreated();
        handleClose();
      } catch (e) {
        // Surface the error but keep the modal open so the user can retry.
        console.error('Failed to add spool from barcode:', e);
        setBusy(false);
      }
    },
    [resolved, buildPayload, spoolmanMode, tagUid, trayUuid, onCreated, handleClose],
  );

  const sourceLabel = useMemo(() => {
    if (linkedByUser) return t('spoolbuddy.barcode.sourceLinked', 'Linked by you — barcode saved for next time');
    switch (resolved?.source) {
      case 'inventory':
        return t('spoolbuddy.barcode.sourceInventory', 'Matched in your inventory');
      case 'ofd':
        return t('spoolbuddy.barcode.sourceOfd', 'Matched in Open Filament Database');
      case 'spoolmandb-community':
        return t('spoolbuddy.barcode.sourceSpoolmandb', 'Matched in SpoolmanDB Community');
      default:
        return t('spoolbuddy.barcode.sourceGuessed', 'Guessed from label');
    }
  }, [linkedByUser, resolved, t]);

  if (!isOpen) return null;

  const grossWeight = scaleWeight !== null ? Math.round(Math.max(0, scaleWeight)) : null;
  const effectiveCore = isRefill ? 0 : coreWeight;
  const estFilament = grossWeight !== null ? Math.max(0, grossWeight - effectiveCore) : null;
  const colorHex = spoolColorString(resolved?.rgba ?? null);

  // Sanity-check the refill toggle against the measured weight, flagging only
  // the physically-impossible cases (keeps false positives near zero — partial
  // spools are fine): refill ON yet heavier than a full bare coil ⇒ a spool
  // core must be present; refill OFF yet lighter than an empty spool ⇒ there's
  // no spool, so it's a refill.
  const labelWeightRef = resolved?.label_weight ?? 1000;
  let weightWarning: string | null = null;
  if (grossWeight !== null) {
    if (isRefill && grossWeight > labelWeightRef + coreWeight * 0.5) {
      weightWarning = t('spoolbuddy.barcode.refillTooHeavy', 'Heavier than a bare refill — is it on a spool?');
    } else if (!isRefill && coreWeight > 0 && grossWeight < coreWeight * 0.5) {
      weightWarning = t('spoolbuddy.barcode.withSpoolTooLight', 'Lighter than an empty spool — is this a refill?');
    }
  }

  const btnBase =
    'flex-1 min-h-[44px] px-4 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2';
  const btnPrimary = `${btnBase} bg-green-600 text-white hover:bg-green-700 disabled:opacity-50`;
  const btnSecondary = `${btnBase} bg-zinc-700 text-zinc-300 hover:bg-zinc-600`;
  const btnGhost = `${btnBase} bg-transparent border border-zinc-600 text-zinc-400 hover:bg-zinc-700`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-zinc-800 rounded-2xl p-6 w-full max-w-lg border border-zinc-700">
        {/* --- Chips: tag + scale (shared header for scan/confirm) --- */}
        {(step === 'waiting' || step === 'confirm' || step === 'looking_up') && (
          <div className="flex flex-wrap gap-2 mb-4">
            <Chip
              ok={!!tagUid}
              label={t('spoolbuddy.barcode.chipTag', 'Tag')}
              value={tagUid ? tagUid : t('spoolbuddy.barcode.chipWaiting', 'waiting…')}
            />
            <Chip
              ok={grossWeight !== null}
              label={t('spoolbuddy.barcode.chipScale', 'Scale')}
              value={grossWeight !== null ? `${grossWeight} g` : t('spoolbuddy.barcode.chipEmpty', 'empty')}
            />
          </div>
        )}

        {/* --- Screen B: waiting for scan --- */}
        {step === 'waiting' && (
          <>
            <h3 className="text-lg font-semibold text-zinc-100 mb-1">
              {t('spoolbuddy.barcode.scanTitle', 'Scan Barcode to Add')}
            </h3>
            <p className="text-sm text-zinc-400 mb-4">
              {t('spoolbuddy.barcode.scanHint', 'Hold the retail barcode in front of the scanner port on SpoolBuddy.')}
            </p>
            {invalidCode && (
              <div className="flex gap-2 items-center p-3 mb-4 rounded-lg bg-amber-500/10 border border-amber-500/25 text-amber-200 text-sm">
                <AlertTriangle className="w-4 h-4 shrink-0 text-amber-500" />
                {t('spoolbuddy.barcode.invalidScan', "Couldn't read that barcode — try scanning again.")}
              </div>
            )}
            <div className="flex items-center gap-4 p-5 mb-5 rounded-xl border-2 border-dashed border-zinc-600 bg-zinc-900/50">
              <Barcode className="w-10 h-10 text-green-500 shrink-0" />
              <div className="text-sm text-zinc-300">
                <p className="font-medium text-zinc-100">{t('spoolbuddy.barcode.waiting', 'Waiting for scan…')}</p>
                <p className="text-zinc-500">
                  {t('spoolbuddy.barcode.waitingSub', 'The scanner lights up and beeps when it reads a code.')}
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <button type="button" className={btnGhost} onClick={handleClose}>
                {t('common.cancel', 'Cancel')}
              </button>
              <button type="button" className={btnSecondary} onClick={() => setStep('manual')}>
                {t('spoolbuddy.barcode.enterManually', 'Enter Code Manually')}
              </button>
              <button
                type="button"
                className={btnSecondary}
                onClick={() => {
                  setFindBackStep('waiting');
                  setFindQuery('');
                  setFindRows([]);
                  setStep('find');
                }}
              >
                <Search className="w-4 h-4" /> {t('spoolbuddy.barcode.findFilament', 'Find This Filament…')}
              </button>
              <button
                type="button"
                className={btnSecondary}
                onClick={() => {
                  clearScan();
                  onFallbackQuickAdd();
                }}
              >
                {t('spoolbuddy.barcode.addWithoutBarcode', 'Add Without Barcode')}
              </button>
            </div>
          </>
        )}

        {/* --- Manual entry sub-screen --- */}
        {step === 'manual' && (
          <>
            <h3 className="text-lg font-semibold text-zinc-100 mb-1">
              {t('spoolbuddy.barcode.manualTitle', 'Enter Barcode')}
            </h3>
            <p className="text-sm text-zinc-400 mb-4">
              {t('spoolbuddy.barcode.manualHint', 'Type the number printed below the barcode.')}
            </p>
            <input
              type="text"
              inputMode="numeric"
              autoFocus
              value={manualCode}
              onChange={(e) => setManualCode(e.target.value)}
              className="w-full mb-5 px-4 py-3 rounded-lg bg-zinc-900 border border-zinc-600 text-zinc-100 font-mono text-lg focus:outline-none focus:border-green-500"
              placeholder="0000000000000"
            />
            <div className="flex gap-2">
              <button type="button" className={btnGhost} onClick={() => setStep('waiting')}>
                {t('common.back', 'Back')}
              </button>
              <button
                type="button"
                className={btnPrimary}
                disabled={!manualCode.trim()}
                onClick={() => lookupCode(manualCode)}
              >
                {t('spoolbuddy.barcode.lookUp', 'Look Up')}
              </button>
            </div>
          </>
        )}

        {/* --- Looking up spinner --- */}
        {step === 'looking_up' && (
          <div className="flex flex-col items-center justify-center py-10 gap-3">
            <Loader2 className="w-8 h-8 text-green-500 animate-spin" />
            <p className="text-sm text-zinc-400">{t('spoolbuddy.barcode.lookingUp', 'Looking up…')}</p>
          </div>
        )}

        {/* --- Screen C/D: confirm --- */}
        {step === 'confirm' && resolved && (
          <>
            <h3 className="text-lg font-semibold text-zinc-100 mb-1">
              {tagUid
                ? t('spoolbuddy.barcode.confirmTitle', 'Confirm New Spool')
                : t('spoolbuddy.barcode.barcodeScannedTitle', 'Barcode Scanned')}
            </h3>
            <p className="text-sm text-zinc-400 mb-4">
              {t('spoolbuddy.barcode.scannedCode', 'Scanned')} <span className="font-mono">{resolved.barcode}</span>
            </p>

            <div className="flex gap-4 p-4 mb-4 rounded-xl bg-zinc-900/60 border border-green-500/30">
              <div className="shrink-0">
                <SpoolIcon color={colorHex} isEmpty={false} size={72} />
              </div>
              <div className="min-w-0 flex-1">
                <h4 className="text-lg font-semibold text-zinc-100 truncate">
                  {resolved.color_name || t('spoolbuddy.barcode.unknownColor', 'Unknown color')}
                </h4>
                <p className="text-sm text-zinc-400 truncate">
                  {[resolved.brand, resolved.material, resolved.subtype].filter(Boolean).join(' • ')}
                  {resolved.label_weight ? ` • ${resolved.label_weight} g` : ''}
                </p>
                <span className="inline-flex items-center gap-1.5 mt-2 px-2.5 py-1 rounded-full text-xs font-medium bg-green-500/15 text-green-400 border border-green-500/30">
                  <Check className="w-3 h-3" /> {sourceLabel}
                </span>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 p-4 mb-5 rounded-lg bg-zinc-900/60 text-sm">
              <Row k={t('spoolbuddy.barcode.rowTag', 'Tag')} v={tagUid ?? '—'} />
              <Row k={t('spoolbuddy.barcode.rowGross', 'Gross weight')} v={grossWeight !== null ? `${grossWeight} g` : '—'} />
              <Row k={t('spoolbuddy.barcode.rowBarcode', 'Barcode')} v={resolved.barcode} />
              <Row k={t('spoolbuddy.barcode.rowEst', 'Est. filament')} v={estFilament !== null ? `${estFilament} g` : '—'} />
            </div>

            {/* Refill vs with-spool — the DBs can't always tell us, so let the
                user set it; drives the core weight and the stored is_refill. */}
            <label className="flex items-center justify-between gap-3 mb-5 px-1 cursor-pointer">
              <div className="min-w-0">
                <span className="text-sm text-zinc-200">
                  {t('spoolbuddy.barcode.refillTitle', 'This is a refill (no spool)')}
                </span>
                <p className="text-xs text-zinc-500">
                  {t('spoolbuddy.barcode.refillHint', 'Refills are the bare coil sold without a spool — lower core weight.')}
                </p>
              </div>
              <KioskToggle checked={isRefill} disabled={busy} onToggle={() => setIsRefill((v) => !v)} />
            </label>

            {weightWarning && (
              <div className="flex gap-2 items-center p-3 mb-4 rounded-lg bg-amber-500/10 border border-amber-500/25 text-amber-200 text-sm">
                <AlertTriangle className="w-4 h-4 shrink-0 text-amber-500" />
                {weightWarning}
              </div>
            )}

            <div className="flex gap-2">
              <button type="button" className={btnGhost} onClick={handleClose} disabled={busy}>
                {t('common.cancel', 'Cancel')}
              </button>
              <button type="button" className={btnSecondary} onClick={() => setStep('waiting')} disabled={busy}>
                {t('spoolbuddy.barcode.rescan', 'Rescan')}
              </button>
              <button type="button" className={btnPrimary} onClick={() => handleCreate(true)} disabled={busy}>
                {busy ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : tagUid ? (
                  t('spoolbuddy.barcode.addToInventory', 'Add to Inventory')
                ) : (
                  t('spoolbuddy.barcode.addWithoutTag', 'Add Without Tag')
                )}
              </button>
            </div>
          </>
        )}

        {/* --- Screen E: no match --- */}
        {step === 'no_match' && (
          <>
            <h3 className="text-lg font-semibold text-zinc-100 mb-1">
              {t('spoolbuddy.barcode.noMatchTitle', 'No Match Found')}
            </h3>
            <p className="text-sm text-zinc-400 mb-4">
              {t('spoolbuddy.barcode.scannedCode', 'Scanned')}{' '}
              <span className="font-mono">{resolved?.barcode ?? invalidCode}</span>
            </p>
            <div className="flex gap-3 p-4 mb-5 rounded-lg bg-amber-500/10 border border-amber-500/25 text-amber-200 text-sm">
              <AlertTriangle className="w-5 h-5 shrink-0 text-amber-500" />
              <p>
                {t(
                  'spoolbuddy.barcode.noMatchBody',
                  "This code isn't in your inventory or the community databases. Amazon boxes often carry an Amazon code instead of the retail barcode — check for a second barcode on the box.",
                )}
              </p>
            </div>
            <div className="flex gap-2 mb-2.5">
              <button type="button" className={btnSecondary} onClick={() => setStep('waiting')}>
                {t('spoolbuddy.barcode.rescan', 'Rescan')}
              </button>
              <button type="button" className={btnSecondary} onClick={() => setStep('manual')}>
                {t('spoolbuddy.barcode.enterManually', 'Enter Code Manually')}
              </button>
              <button
                type="button"
                className={btnSecondary}
                onClick={() => resolved && handleCreate(!!tagUid)}
                disabled={busy || !resolved}
              >
                {t('spoolbuddy.barcode.addBasicSpool', 'Add Basic Spool')}
              </button>
            </div>
            <div className="flex gap-2">
              <button type="button" className={btnGhost} onClick={handleClose}>
                {t('common.cancel', 'Cancel')}
              </button>
              <button
                type="button"
                className={btnPrimary}
                onClick={() => {
                  setFindBackStep('no_match');
                  setFindQuery('');
                  setFindRows([]);
                  setStep('find');
                }}
              >
                <Search className="w-4 h-4" /> {t('spoolbuddy.barcode.findFilament', 'Find This Filament…')}
              </button>
            </div>
          </>
        )}

        {/* --- Screen F: find this filament --- */}
        {step === 'find' && (
          <>
            <h3 className="text-lg font-semibold text-zinc-100 mb-1">
              {t('spoolbuddy.barcode.findTitle', 'Find This Filament')}
            </h3>
            <p className="text-sm text-zinc-400 mb-4">
              {t('spoolbuddy.barcode.findHint', 'Search by brand, material, or color — then link this barcode to it.')}
            </p>
            <div className="flex items-center gap-2 mb-4 px-3 py-2.5 rounded-lg bg-zinc-900 border border-zinc-600">
              <Search className="w-4 h-4 text-zinc-500 shrink-0" />
              <input
                type="text"
                autoFocus
                value={findQuery}
                onChange={(e) => setFindQuery(e.target.value)}
                className="flex-1 bg-transparent text-zinc-100 focus:outline-none"
                placeholder={t('spoolbuddy.barcode.findPlaceholder', 'e.g. polymaker charcoal')}
              />
            </div>
            <div className="max-h-64 overflow-y-auto flex flex-col gap-2 mb-5">
              {findLoading && <p className="text-sm text-zinc-500 text-center py-4">{t('common.loading', 'Loading…')}</p>}
              {!findLoading && findQuery.trim().length >= 2 && displayRows.length === 0 && (
                <p className="text-sm text-zinc-500 text-center py-4">
                  {t('spoolbuddy.barcode.findNoResults', 'No matches found')}
                </p>
              )}
              {displayRows.map((row, i) => (
                <button
                  key={`${row.source}-${row.spool_id ?? i}-${i}`}
                  type="button"
                  onClick={() => selectCatalogRow(row)}
                  className="flex items-center gap-3 p-3 rounded-lg bg-zinc-900/60 border border-zinc-700 hover:border-green-500/50 text-left transition-colors"
                >
                  <span
                    className="w-6 h-6 rounded-full border-2 border-zinc-600 shrink-0"
                    style={{ backgroundColor: spoolColorString(row.rgba) }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-zinc-100 truncate">
                      {[row.color_name, row.subtype || row.material].filter(Boolean).join(' — ')}
                    </div>
                    <div className="text-xs text-zinc-500 truncate">
                      {[row.brand, row.label_weight ? `${row.label_weight} g` : null].filter(Boolean).join(' • ')}
                    </div>
                  </div>
                  <SourcePill source={row.source} t={t} />
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <button type="button" className={btnGhost} onClick={() => setStep(findBackStep)}>
                {t('common.back', 'Back')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Chip({ ok, label, value }: { ok: boolean; label: string; value: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm border ${
        ok ? 'border-green-500/35 text-zinc-200' : 'border-amber-500/40 text-zinc-300'
      }`}
    >
      {ok ? <Check className="w-3.5 h-3.5 text-green-500" /> : <span className="text-amber-500">◌</span>}
      <span className="text-zinc-500">{label}</span>
      <span className="font-mono text-xs">{value}</span>
    </span>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-zinc-500">{k}</span>
      <span className="font-mono text-zinc-300 truncate">{v}</span>
    </div>
  );
}

function SourcePill({
  source,
  t,
}: {
  source: CatalogSearchRow['source'];
  t: (key: string, fallback: string) => string;
}) {
  const map: Record<CatalogSearchRow['source'], { label: string; cls: string }> = {
    inventory: {
      label: t('spoolbuddy.barcode.pillInventory', 'Your inventory'),
      cls: 'text-green-400 border-green-500/35',
    },
    ofd: { label: t('spoolbuddy.barcode.pillOfd', 'Open Filament DB'), cls: 'text-green-400 border-green-500/30' },
    'spoolmandb-community': {
      label: t('spoolbuddy.barcode.pillSpoolmandb', 'SpoolmanDB'),
      cls: 'text-zinc-400 border-zinc-600',
    },
  };
  const { label, cls } = map[source];
  return (
    <span className={`shrink-0 text-xs font-medium px-2.5 py-1 rounded-full border ${cls}`}>{label}</span>
  );
}
