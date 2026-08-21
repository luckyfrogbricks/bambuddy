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
  },
}));

import { api } from '../../api/client';

function makeScan(over: Partial<ScannedBarcode> = {}): ScannedBarcode {
  return {
    barcode: '6975337031234',
    kind: 'gtin',
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

  it('creates a spool with the scanned barcode in the payload (local mode)', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    fireEvent.click(await screen.findByRole('button', { name: /^Add to Inventory$/i }));

    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.barcode).toBe('6975337031234');
    expect(payload.material).toBe('PLA');
    expect(payload.tag_uid).toBe('0C1C8364');
    expect(payload.data_origin).toBe('barcode_scan');
  });

  it('marks the spool as a refill (barcode_is_refill + zero core weight) when the toggle is on', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    // On the confirm screen, flip the "This is a refill" toggle, then add.
    fireEvent.click(await screen.findByRole('switch'));
    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));

    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.barcode_is_refill).toBe(true);
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
    expect(payload.barcode_is_refill).toBe(true);
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

  it('opens the Find step and renders search results without crashing', async () => {
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

    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    // Find step should render (no crash on the transition)
    const input = await screen.findByPlaceholderText(/polymaker charcoal/i);
    fireEvent.change(input, { target: { value: 'polymaker' } });
    // Debounced search result should render (this exercises the row + SourcePill)
    expect(await screen.findByText('Open Filament DB')).toBeInTheDocument();
  });

  it('offers "Find This Filament" on the scan-waiting screen (B) and Back returns there', async () => {
    // No scan yet (NFC + weight only, no box/barcode) → the modal sits on screen B.
    render(<BarcodeAddModal {...baseProps} scan={null} tagUid="0C1C8364" scaleWeight={1247} />);
    expect(await screen.findByText('Scan Barcode to Add')).toBeInTheDocument();

    // The Find button jumps straight to the Find screen without needing a scan.
    fireEvent.click(screen.getByRole('button', { name: /Find This Filament/i }));
    expect(await screen.findByPlaceholderText(/polymaker charcoal/i)).toBeInTheDocument();

    // Back returns to screen B (not the no-match screen it defaults to).
    fireEvent.click(screen.getByRole('button', { name: /^Back$/i }));
    expect(await screen.findByText('Scan Barcode to Add')).toBeInTheDocument();
  });

  it('does not render modal content when closed', () => {
    render(
      <BarcodeAddModal {...baseProps} isOpen={false} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    expect(screen.queryByText('Charcoal Black')).not.toBeInTheDocument();
  });
});
