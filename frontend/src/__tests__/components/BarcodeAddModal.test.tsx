/**
 * Tests for BarcodeAddModal — the SpoolBuddy kiosk barcode add-to-inventory
 * state machine (screens B/C/D/E/F). Covers: matched scan → confirm, a
 * barcode-first scan with no tag → "Add Without Tag", an unmatched scan →
 * "Find This Filament", and that creating a spool sends the scanned barcode.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { BarcodeAddModal } from '../../components/spoolbuddy/BarcodeAddModal';
import type { ScannedBarcode } from '../../hooks/useSpoolBuddyState';

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
    createSpool: vi.fn().mockResolvedValue({ id: 1 }),
    createSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 1 }),
    linkTagToSpoolmanSpool: vi.fn().mockResolvedValue({ id: 1 }),
    lookupFilamentBarcode: vi.fn(),
    searchBarcodeCatalog: vi.fn().mockResolvedValue([]),
    browseBarcodeCatalog: vi.fn().mockResolvedValue({
      enabled: true, level: 'brands', brands: [], groups: [], colors: [], total: null,
    }),
    browseCatalogHues: vi.fn().mockResolvedValue({
      enabled: true,
      groups: ['red', 'orange', 'brown', 'yellow', 'green', 'blue', 'purple', 'pink', 'black', 'gray', 'white']
        .map((f) => ({ families: [f], count: 1, kind: 'hues' })),
    }),
    getLocations: vi.fn().mockResolvedValue([]),
    createLocation: vi.fn(),
  },
}));

import { api } from '../../api/client';

function makeScan(over: Partial<ScannedBarcode> = {}): ScannedBarcode {
  return {
    barcode: '6975337031234',
    kind: 'gtin',
    symbology: 'ean-upc',
    valid: true,
    matched: true,
    source: 'ofd',
    material: 'PLA',
    brand: 'Polymaker',
    subtype: 'PolyTerra Matte',
    color_name: 'Charcoal Black',
    rgba: '3B3B3FFF',
    label_weight: 1000,
    nozzle_temp_min: 190,
    nozzle_temp_max: 230,
    is_refill: false,
    linked_codes: [],
    deviceId: 'sb-1',
    receivedAt: Date.now(),
    ...over,
  };
}

const baseProps = {
  isOpen: true,
  onClose: vi.fn(),
  trayUuid: null,
  spoolmanMode: false,
  spools: [],
  onCreated: vi.fn(),
  onFallbackQuickAdd: vi.fn(),
  clearScan: vi.fn(),
};

describe('BarcodeAddModal', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the confirm screen with filament + Add to Inventory when a matched scan has a tag', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    expect(await screen.findByText('Charcoal Black')).toBeInTheDocument();
    expect(screen.getByText(/Matched in Open Filament Database/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Add to Inventory$/i })).toBeInTheDocument();
  });

  it('offers "Add Without Tag" for a barcode-first scan (no tag on scale)', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid={null} scaleWeight={null} />,
    );
    expect(await screen.findByText('Charcoal Black')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Without Tag/i })).toBeInTheDocument();
  });

  it('routes a valid-but-unmatched scan to the no-match screen with Find This Filament', async () => {
    render(
      <BarcodeAddModal
        {...baseProps}
        scan={makeScan({ matched: false, source: null, material: null, brand: null, subtype: null, color_name: null, rgba: null, label_weight: null })}
        tagUid="0C1C8364"
        scaleWeight={1247}
      />,
    );
    expect(await screen.findByText(/No Match Found/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Find This Filament/i })).toBeInTheDocument();
  });

  it('ignores further scans while a resolved screen is showing (presentation-mode re-fires)', async () => {
    const { rerender } = render(
      <BarcodeAddModal {...baseProps} scan={makeScan({ receivedAt: 1000 })} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    expect(await screen.findByText('Charcoal Black')).toBeInTheDocument();

    // A second scan lands while the confirm screen is up — it must NOT
    // replace the resolved state.
    rerender(
      <BarcodeAddModal
        {...baseProps}
        scan={makeScan({ receivedAt: 2000, barcode: '9999999999990', color_name: 'Lava Red' })}
        tagUid="0C1C8364"
        scaleWeight={1247}
      />,
    );
    expect(screen.getByText('Charcoal Black')).toBeInTheDocument();
    expect(screen.queryByText('Lava Red')).not.toBeInTheDocument();
  });

  it('Rescan re-arms the gate: the next NEW scan applies (the dropped one does not replay)', async () => {
    const { rerender } = render(
      <BarcodeAddModal {...baseProps} scan={makeScan({ receivedAt: 1000 })} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    expect(await screen.findByText('Charcoal Black')).toBeInTheDocument();

    // Gated scan (consumed and dropped).
    rerender(
      <BarcodeAddModal
        {...baseProps}
        scan={makeScan({ receivedAt: 2000, barcode: '9999999999990', color_name: 'Lava Red' })}
        tagUid="0C1C8364"
        scaleWeight={1247}
      />,
    );

    // Rescan returns to the waiting screen — the dropped scan must not replay.
    fireEvent.click(screen.getByRole('button', { name: /^Rescan$/i }));
    expect(await screen.findByText(/Scan Barcode to Add/i)).toBeInTheDocument();
    expect(screen.queryByText('Lava Red')).not.toBeInTheDocument();

    // A genuinely new scan applies again.
    rerender(
      <BarcodeAddModal
        {...baseProps}
        scan={makeScan({ receivedAt: 3000, barcode: '8888888888880', color_name: 'Jade Green' })}
        tagUid="0C1C8364"
        scaleWeight={1247}
      />,
    );
    expect(await screen.findByText('Jade Green')).toBeInTheDocument();
  });

  it('creates a spool with the scanned barcode in the payload (local mode)', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    fireEvent.click(await screen.findByRole('button', { name: /^Add to Inventory$/i }));

    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.scanned_code).toBe('6975337031234');
    // The scan's AIM symbology hint rides along so backend routing keeps
    // the scan-time classification.
    expect(payload.scanned_symbology).toBe('ean-upc');
    expect(payload.material).toBe('PLA');
    expect(payload.tag_uid).toBe('0C1C8364');
    expect(payload.data_origin).toBe('barcode_scan');
    // A plain scan defers all code routing to the backend — no explicit codes.
    expect(payload.gtin_code).toBeNull();
  });

  it('marks the spool as bought-as-refill (+ zero core weight) when the toggle is on', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    // On the confirm screen, flip the "This is a refill" toggle, then add.
    fireEvent.click(await screen.findByRole('switch'));
    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));

    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.bought_as_refill).toBe(true);
    expect(payload.core_weight).toBe(0);
  });

  it('auto-arms the refill toggle when the scanned code is itself a refill (no user action)', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan({ is_refill: true })} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    // The toggle should already be on from the backend-detected refill flag.
    const sw = await screen.findByRole('switch');
    expect(sw).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));

    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.bought_as_refill).toBe(true);
    expect(payload.core_weight).toBe(0);
  });

  it('warns when refill is on but the roll is too heavy for a bare refill', async () => {
    // 1300 g on a 1000 g-label roll: normal as a with-spool, impossible as a refill.
    render(<BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1300} />);
    await screen.findByText('Charcoal Black');
    // With-spool (default): no warning.
    expect(screen.queryByText(/Heavier than a bare refill/i)).not.toBeInTheDocument();
    // Flip to refill → the impossible-weight warning appears.
    fireEvent.click(screen.getByRole('switch'));
    expect(await screen.findByText(/Heavier than a bare refill/i)).toBeInTheDocument();
  });

  it('opens the catalog sheet from Find This Filament and searches inline', async () => {
    (api.searchBarcodeCatalog as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        source: 'ofd',
        spool_id: null,
        material: 'PLA',
        brand: 'Polymaker',
        subtype: 'PolyTerra Matte',
        color_name: 'Charcoal',
        rgba: '3B3B3FFF',
        label_weight: 1000,
        nozzle_temp_min: 190,
        nozzle_temp_max: 230,
        codes: [{ code: '6975337031234', kind: 'gtin', is_refill: false }],
      },
    ]);
    const unmatched = makeScan({
      matched: false, source: null, material: null, brand: null, subtype: null,
      color_name: null, rgba: null, label_weight: null,
    });
    render(<BarcodeAddModal {...baseProps} scan={unmatched} tagUid="0C1C8364" scaleWeight={1247} />);

    // One entry point now: Find This Filament IS the catalog browser, with
    // the keyword search inline in the sheet — no separate screen.
    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    expect(await screen.findByTestId('browse-sheet')).toBeInTheDocument();
    const input = await screen.findByPlaceholderText(/polymaker charcoal/i);
    fireEvent.change(input, { target: { value: 'polymaker' } });
    // Debounced search result renders as a FilamentCard with its source pill.
    expect(await screen.findByText('Open Filament DB')).toBeInTheDocument();
  });

  it('distinguishes refill-SKU twins in Find results with a badge and the code', async () => {
    // Two SpoolmanDB entries for the same color — one with-spool, one refill —
    // used to render as identical rows with no way to tell them apart.
    const twin = {
      source: 'spoolmandb-community', spool_id: null, material: 'PLA', brand: 'Bambu Lab',
      subtype: 'PLA Pure', color_name: 'Baby Blue', rgba: '89CFF0FF', label_weight: 1000,
      nozzle_temp_min: null, nozzle_temp_max: null,
    };
    (api.searchBarcodeCatalog as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...twin, codes: [{ code: '6975337031111', kind: 'gtin', is_refill: false }] },
      { ...twin, codes: [{ code: '6975337032222', kind: 'gtin', is_refill: true }] },
    ]);
    const unmatched = makeScan({
      matched: false, source: null, material: null, brand: null, subtype: null,
      color_name: null, rgba: null, label_weight: null,
    });
    render(<BarcodeAddModal {...baseProps} scan={unmatched} tagUid="0C1C8364" scaleWeight={1247} />);

    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    const input = await screen.findByPlaceholderText(/polymaker charcoal/i);
    fireEvent.change(input, { target: { value: 'baby blue' } });

    // Each row shows its code, and only the all-refill row carries the badge.
    expect(await screen.findByText(/6975337032222/)).toBeInTheDocument();
    expect(screen.getByText(/6975337031111/)).toBeInTheDocument();
    expect(screen.getAllByText('Refill pack')).toHaveLength(1);
  });

  const mixedCodesRow = {
    source: 'spoolmandb-community', spool_id: null, material: 'PLA', brand: 'Bambu Lab',
    subtype: 'PLA Pure', color_name: 'Baby Blue', rgba: '89CFF0FF', label_weight: 1000,
    nozzle_temp_min: 190, nozzle_temp_max: 230,
    codes: [
      { code: '111', kind: 'gtin', is_refill: false },
      { code: '222', kind: 'gtin', is_refill: true },
    ],
  };

  async function openFindAndSearch() {
    const unmatched = makeScan({
      matched: false, source: null, material: null, brand: null, subtype: null,
      color_name: null, rgba: null, label_weight: null,
    });
    render(<BarcodeAddModal {...baseProps} scan={unmatched} tagUid="0C1C8364" scaleWeight={1247} />);
    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    const input = await screen.findByPlaceholderText(/polymaker charcoal/i);
    fireEvent.change(input, { target: { value: 'baby blue' } });
    return screen.findByText('Baby Blue');
  }


  it('search flow: tapping a result picks it, and Back from confirm keeps the search', async () => {
    (api.searchBarcodeCatalog as ReturnType<typeof vi.fn>).mockResolvedValue([mixedCodesRow]);
    const rowTitle = await openFindAndSearch();

    // Single tap picks the card — straight to the confirm screen.
    fireEvent.click(rowTitle);
    expect(await screen.findByText('Confirm New Spool')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Cancel$/i })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Back$/i }));

    // Query and results survive the round-trip.
    expect(await screen.findByDisplayValue('baby blue')).toBeInTheDocument();
    expect(await screen.findByText('Baby Blue')).toBeInTheDocument();
  });

  it('find row Details discloses every code with its own refill flag', async () => {
    (api.searchBarcodeCatalog as ReturnType<typeof vi.fn>).mockResolvedValue([mixedCodesRow]);
    await openFindAndSearch();

    // Mixed-code row: no collapsed badge (it is not purely a refill entry) …
    expect(screen.queryByText('Refill pack')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /^Details$/i }));
    // … but the disclosure lists both codes, flagging only the refill one.
    // ('111' also shows collapsed as the card's primary code, hence getAllBy.)
    expect(await screen.findByText('222')).toBeInTheDocument();
    expect(screen.getAllByText('111').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Refill pack')).toHaveLength(1);
  });

  it('offers "Find This Filament" on the scan-waiting screen (B) and Back returns there', async () => {
    // No scan yet (NFC + weight only, no box/barcode) → the modal sits on screen B.
    render(<BarcodeAddModal {...baseProps} scan={null} tagUid="0C1C8364" scaleWeight={1247} />);
    expect(await screen.findByText('Scan Barcode to Add')).toBeInTheDocument();

    // The Find button opens the catalog sheet (with its inline search).
    fireEvent.click(screen.getByRole('button', { name: /Find This Filament/i }));
    expect(await screen.findByTestId('browse-sheet')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/polymaker charcoal/i)).toBeInTheDocument();

    // Back at the sheet root returns to screen B.
    fireEvent.click(screen.getByRole('button', { name: /^Back$/i }));
    expect(await screen.findByText('Scan Barcode to Add')).toBeInTheDocument();
  });

  it('does not render modal content when closed', () => {
    render(
      <BarcodeAddModal {...baseProps} isOpen={false} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    expect(screen.queryByText('Charcoal Black')).not.toBeInTheDocument();
  });

  const spoolsWithLocations = [
    {
      id: 1, archived_at: null, created_at: '2026-08-20T10:00:00Z',
      location_id: 5, storage_location: 'Shelf A',
      material: 'PLA', brand: null, subtype: null, color_name: null, rgba: null,
      label_weight: 1000, barcode: null,
    },
    {
      id: 2, archived_at: null, created_at: '2026-08-22T10:00:00Z',
      location_id: 7, storage_location: 'Dry Box 1',
      material: 'PLA', brand: null, subtype: null, color_name: null, rgba: null,
      label_weight: 1000, barcode: null,
    },
  ] as unknown as import('../../api/client').InventorySpool[];

  it("defaults the location to the last added spool's and sends it in the payload", async () => {
    (api.getLocations as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: 5, name: 'Shelf A' }, { id: 7, name: 'Dry Box 1' },
    ]);
    render(
      <BarcodeAddModal {...baseProps} spools={spoolsWithLocations} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    // Chip pre-selects spool #2's location (newest created_at) with the hint.
    expect(await screen.findByText('Dry Box 1')).toBeInTheDocument();
    expect(screen.getByText(/last used/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));
    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    expect((api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0].location_id).toBe(7);
  });

  it('lets the user clear the location via the picker', async () => {
    (api.getLocations as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: 5, name: 'Shelf A' }, { id: 7, name: 'Dry Box 1' },
    ]);
    render(
      <BarcodeAddModal {...baseProps} spools={spoolsWithLocations} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    // Open the picker via the chip, pick "No location".
    fireEvent.click(await screen.findByRole('button', { name: /Dry Box 1/ }));
    fireEvent.click(await screen.findByRole('button', { name: /^No location$/ }));

    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));
    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    expect((api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0].location_id).toBeNull();
  });

  // ── Browse Catalog (screen G): tap-first brand → material → line → color ──

  const pumpkinRow = {
    source: 'spoolmandb-community', spool_id: null, material: 'PLA', brand: 'Bambu Lab',
    subtype: 'PLA Basic', color_name: 'Pumpkin Orange', rgba: 'FF9016FF', label_weight: 1000,
    nozzle_temp_min: 190, nozzle_temp_max: 230,
    codes: [{ code: '10301', kind: 'sku', is_refill: false }],
  };

  function mockBrowseTree() {
    (api.browseBarcodeCatalog as ReturnType<typeof vi.fn>).mockImplementation(
      (params: { brand?: string; material?: string; line?: string; hue?: string }) => {
        const empty = { enabled: true, brands: [], groups: [], colors: [], total: null };
        if (params.hue) {
          return Promise.resolve({
            ...empty, level: 'colors', colors: [pumpkinRow], total: 88,
          });
        }
        if (params.line) {
          return Promise.resolve({ ...empty, level: 'colors', colors: [pumpkinRow], total: 1 });
        }
        if (params.material) {
          return Promise.resolve({
            ...empty, level: 'lines',
            groups: [{ name: 'PLA Basic', variant_count: 48, preview_rgbas: ['FF9016FF'] }],
          });
        }
        if (params.brand) {
          return Promise.resolve({
            ...empty, level: 'materials',
            groups: [{ name: 'PLA', variant_count: 212, preview_rgbas: ['FF9016FF'] }],
          });
        }
        return Promise.resolve({
          ...empty, level: 'brands',
          brands: [
            { name: 'Bambu Lab', variant_count: 373, owned: true },
            { name: 'Sunlu', variant_count: 540, owned: false },
          ],
        });
      },
    );
  }

  it('browse: drills brand → material → line → color and creates with the picked codes', async () => {
    mockBrowseTree();
    render(<BarcodeAddModal {...baseProps} scan={null} tagUid="0C1C8364" scaleWeight={1247} />);

    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    // Brands level: owned brands surface in their own "Your brands" section.
    expect(await screen.findByText('Your brands')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Bambu Lab/ }));
    // Materials → lines → colors.
    fireEvent.click(await screen.findByRole('button', { name: /PLA 212 colors/ }));
    fireEvent.click(await screen.findByRole('button', { name: /PLA Basic 48 colors/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Pumpkin Orange/ }));

    // Confirm screen: a pure browse pick has no barcode to link.
    expect(await screen.findByText('Confirm New Spool')).toBeInTheDocument();
    expect(screen.getAllByText(/Picked from the catalog/i).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));
    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.scanned_code).toBeNull();
    expect(payload.brand).toBe('Bambu Lab');
    expect(payload.color_name).toBe('Pumpkin Orange');
    // The picked row's per-package codes are stored explicitly, like a Find pick.
    expect(payload.sku_code).toBe('10301');
    expect(payload.tag_uid).toBe('0C1C8364');
  });

  it('browse: the hue filter works at the top level and shows the truncation note', async () => {
    mockBrowseTree();
    render(<BarcodeAddModal {...baseProps} scan={null} tagUid={null} scaleWeight={null} />);

    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    await screen.findByText('Your brands');
    fireEvent.click(screen.getByRole('button', { name: /^Orange$/ }));

    // Hue results render as FilamentCards (brand · line) with the capped total.
    expect(await screen.findByText('Pumpkin Orange')).toBeInTheDocument();
    expect(screen.getByText(/Bambu Lab · PLA Basic/)).toBeInTheDocument();
    expect(screen.getByText(/Showing 1 of 88/)).toBeInTheDocument();
  });

  it('browse: Back from confirm returns to the same browse screen', async () => {
    mockBrowseTree();
    render(<BarcodeAddModal {...baseProps} scan={null} tagUid="0C1C8364" scaleWeight={1247} />);

    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    fireEvent.click(await screen.findByRole('button', { name: /Bambu Lab/ }));
    fireEvent.click(await screen.findByRole('button', { name: /PLA 212 colors/ }));
    fireEvent.click(await screen.findByRole('button', { name: /PLA Basic 48 colors/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Pumpkin Orange/ }));

    expect(await screen.findByText('Confirm New Spool')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Back$/i }));
    // The color grid is restored — nav state survived the round-trip.
    expect(await screen.findByRole('button', { name: /Pumpkin Orange/ })).toBeInTheDocument();
    expect(screen.getByText('PLA Basic')).toBeInTheDocument();
  });

  it('browse: a hardware scan mid-browse wins and resolves normally', async () => {
    mockBrowseTree();
    const { rerender } = render(
      <BarcodeAddModal {...baseProps} scan={null} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    await screen.findByText('Your brands');

    // The box lands in front of the scanner while the user is browsing.
    rerender(
      <BarcodeAddModal {...baseProps} scan={makeScan({ receivedAt: 5000 })} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    expect(await screen.findByText('Charcoal Black')).toBeInTheDocument();
    expect(screen.getByText(/Matched in Open Filament Database/i)).toBeInTheDocument();
  });

  it('browse: typing in the inline search swaps the body to results; Back restores browsing', async () => {
    mockBrowseTree();
    (api.searchBarcodeCatalog as ReturnType<typeof vi.fn>).mockResolvedValue([pumpkinRow]);
    render(<BarcodeAddModal {...baseProps} scan={null} tagUid="0C1C8364" scaleWeight={1247} />);
    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    await screen.findByText('Your brands');

    // Type ≥2 chars → the body becomes search results in place, same sheet.
    fireEvent.change(screen.getByPlaceholderText(/polymaker charcoal/i), { target: { value: 'pumpkin' } });
    expect(await screen.findByText('Pumpkin Orange')).toBeInTheDocument();
    expect(screen.queryByText('Your brands')).not.toBeInTheDocument();

    // Back clears the search first, restoring the browse level underneath.
    fireEvent.click(screen.getByRole('button', { name: /^Back$/i }));
    expect(await screen.findByText('Your brands')).toBeInTheDocument();
  });

  it('browse: explains when community lookups are disabled', async () => {
    (api.browseBarcodeCatalog as ReturnType<typeof vi.fn>).mockResolvedValue({
      enabled: false, level: 'brands', brands: [], groups: [], colors: [], total: null,
    });
    render(<BarcodeAddModal {...baseProps} scan={null} tagUid={null} scaleWeight={null} />);
    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    expect(await screen.findByText(/Community catalog lookups are turned off/i)).toBeInTheDocument();
  });

  it('shows the backend error and stays open when the create is rejected', async () => {
    // e.g. the duplicate-tag 409 guard: a stale tag already linked to another
    // spool must surface as a visible error, not a silently dead button.
    (api.createSpool as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('Tag 72DB77EB is already linked to spool #41'),
    );
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="72DB77EB" scaleWeight={1247} />,
    );
    fireEvent.click(await screen.findByRole('button', { name: /^Add to Inventory$/i }));

    expect(await screen.findByText(/already linked to spool #41/i)).toBeInTheDocument();
    expect(baseProps.onClose).not.toHaveBeenCalled();
    expect(baseProps.onCreated).not.toHaveBeenCalled();

    // A retry after the failure works and closes the modal.
    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));
    await waitFor(() => expect(baseProps.onCreated).toHaveBeenCalledTimes(1));
  });
});
