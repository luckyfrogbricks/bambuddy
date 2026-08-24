/**
 * Tests for the Color Kit atoms/molecules (components/color/) — the fill
 * model, the indicator-bar active state, dimming, and the family-set-keyed
 * ColorFilter. The kit's reasoning (classify/partition) is backend-only and
 * tested there; these tests cover rendering contracts.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { colorFill, groupFill } from '../../components/color/colorFill';
import { ColorDot } from '../../components/color/ColorDot';
import { ColorPill } from '../../components/color/ColorPill';
import { ColorSwatch } from '../../components/color/ColorSwatch';
import { TileButton } from '../../components/color/TileButton';
import { ColorFilter, type HueGroupData } from '../../components/color/ColorFilter';
import { FilamentCard } from '../../components/color/FilamentCard';

describe('colorFill', () => {
  it('renders one color solid regardless of presentation', () => {
    expect(colorFill('FF9016', 'gradient')).toEqual({ background: '#FF9016' });
    expect(colorFill('#FF9016', 'slash-striped')).toEqual({ background: '#FF9016' });
  });

  it('splits two colors into equal halves across the requested axis', () => {
    expect(colorFill(['131316', 'C12E1F'], 'slash-striped').background).toBe(
      'linear-gradient(135deg, #131316 0.00% 50.00%, #C12E1F 50.00% 100.00%)',
    );
    expect(colorFill(['131316', 'C12E1F'], 'backslash-striped').background).toContain('45deg');
    // Vertical is the filament-identity default — matches upstream FilamentSwatch.
    expect(colorFill(['131316', 'C12E1F'], 'vertical-striped').background).toContain('to right');
  });

  it('gives 3+ colors equal-width stripes and smooth stops for gradient', () => {
    const striped = colorFill(['111111', '222222', '333333'], 'slash-striped').background as string;
    expect(striped).toContain('#222222 33.33% 66.67%');
    const smooth = colorFill(['111111', '222222', '333333'], 'gradient').background as string;
    expect(smooth).toContain('#222222 50.0%');
  });

  it('ignores invalid tokens and falls back to gray when nothing is left', () => {
    expect(colorFill(['nope'], 'gradient')).toEqual({ background: '#808080' });
  });

  it('gives reserved groups their signature ramps', () => {
    expect(groupFill({ families: ['black', 'gray', 'white'], kind: 'grayscale' }).background).toContain(
      'linear-gradient',
    );
    expect(groupFill({ families: ['multicolor'], kind: 'multicolor' }).background).toContain('conic-gradient');
    // A merged hue arc stripes its families' canonical colors diagonally.
    expect(groupFill({ families: ['purple', 'pink'], kind: 'hues' }).background).toContain('135deg');
  });
});

describe('ColorDot', () => {
  it('shows the indicator bar and aria-pressed when active', () => {
    render(<ColorDot colors="8a5a34" label="Brown" active onClick={() => {}} />);
    const btn = screen.getByRole('button', { name: 'Brown' });
    expect(btn).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('active-indicator').className).toContain('bg-green-500');
  });

  it('keeps the indicator footprint (transparent) when inactive', () => {
    render(<ColorDot colors="8a5a34" label="Brown" onClick={() => {}} />);
    expect(screen.getByTestId('active-indicator').className).toContain('bg-transparent');
  });

  it('dims to non-tappable when the family is empty in scope', () => {
    const onClick = vi.fn();
    render(<ColorDot colors="fec600" label="Yellow" dimmed onClick={onClick} />);
    const btn = screen.getByRole('button', { name: 'Yellow' });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('ColorSwatch', () => {
  it('small renders the label under the chiclet and toggles selection styling', () => {
    render(<ColorSwatch colors="FF9016" size="small" label="Pumpkin Orange" selected onClick={() => {}} />);
    const btn = screen.getByRole('button', { name: 'Pumpkin Orange' });
    expect(btn).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Pumpkin Orange')).toBeInTheDocument();
    expect(screen.getByTestId('active-indicator').className).toContain('bg-green-500');
  });

  it('mini renders label as tooltip only', () => {
    render(<ColorSwatch colors="FF9016" size="mini" label="Pumpkin Orange" />);
    const el = screen.getByLabelText('Pumpkin Orange');
    expect(el).toHaveAttribute('title', 'Pumpkin Orange');
    expect(screen.queryByText('Pumpkin Orange')).not.toBeInTheDocument();
  });
});

describe('TileButton', () => {
  it('renders label, sublabel, and a content face', () => {
    render(
      <TileButton label="PLA" sublabel="18,621 colors" onClick={() => {}}>
        <span>face</span>
      </TileButton>,
    );
    fireEvent.click(screen.getByRole('button', { name: /PLA/ }));
    expect(screen.getByText('18,621 colors')).toBeInTheDocument();
    expect(screen.getByText('face')).toBeInTheDocument();
  });
});

describe('ColorFilter', () => {
  const groups: HueGroupData[] = [
    { families: ['orange'], count: 12, kind: 'hues' },
    { families: ['purple', 'pink'], count: 5, kind: 'hues' },
    { families: ['yellow'], count: 0, kind: 'hues' },
  ];

  it('renders family labels, dims empties, and toggles by family set', () => {
    const onChange = vi.fn();
    render(<ColorFilter groups={groups} value={null} onChange={onChange} showLabels />);
    expect(screen.getByText('Purple · Pink')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Yellow' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Purple · Pink' }));
    expect(onChange).toHaveBeenCalledWith(['purple', 'pink'], groups[1]);
  });

  it('tapping the active group clears the filter', () => {
    const onChange = vi.fn();
    // Selection is keyed by family SET — order must not matter.
    render(<ColorFilter groups={groups} value={['pink', 'purple']} onChange={onChange} />);
    const active = screen.getByRole('button', { name: 'Purple · Pink' });
    expect(active).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(active);
    expect(onChange).toHaveBeenCalledWith(null, groups[1]);
  });
});

describe('FilamentCard', () => {
  it('renders title, source pill, meta, and the codes disclosure', () => {
    const onToggle = vi.fn();
    render(
      <FilamentCard
        colors={['131316', 'C12E1F']}
        title="Velvet Eclipse"
        subtitle="Bambu · PLA Basic"
        source="spoolmandb-community"
        weight={1000}
        tempRange={[190, 230]}
        codes={[{ code: '10301', kind: 'sku', is_refill: true }]}
        expanded
        onToggleExpand={onToggle}
        onClick={() => {}}
      />,
    );
    expect(screen.getByText('Velvet Eclipse')).toBeInTheDocument();
    expect(screen.getByText('SpoolmanDB')).toBeInTheDocument();
    expect(screen.getByText('1 kg · 190–230 °C')).toBeInTheDocument();
    expect(screen.getByText('10301')).toBeInTheDocument();
    expect(screen.getByText('Refill pack')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Details/ }));
    expect(onToggle).toHaveBeenCalled();
  });
});
