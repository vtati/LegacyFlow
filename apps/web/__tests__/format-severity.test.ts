/**
 * UI-014: severity must be displayed truthfully. The backend/escalation
 * vocabulary is info / review / attention / blocking — `attention` must
 * not fall through to the neutral pill (or, worse, success green), and
 * `info` must be distinguishable from it.
 */
import { describe, expect, it } from 'vitest';
import { severityDisplay, statusBadgeClass } from '../lib/format';

describe('statusBadgeClass severity branches (UI-014)', () => {
  it('maps attention to warning classes', () => {
    const cls = statusBadgeClass('attention');
    expect(cls).toContain('bg-lf-warning-100');
    expect(cls).toContain('text-lf-warning-700');
    expect(cls).not.toContain('bg-lf-canvas-subtle');
    expect(cls).not.toContain('success');
  });

  it('maps info to a sky tint', () => {
    const cls = statusBadgeClass('info');
    expect(cls).toContain('bg-lf-sky-100');
    expect(cls).toContain('text-lf-sky-700');
  });

  it('keeps blocking red and review accent', () => {
    expect(statusBadgeClass('blocking')).toContain('bg-lf-error-100');
    expect(statusBadgeClass('review')).toContain('bg-lf-accent-100');
  });
});

describe('severityDisplay (interview consistency findings, UI-014)', () => {
  it('maps attention to warning styling with a visible label', () => {
    const display = severityDisplay('attention');
    expect(display.label).toBe('Needs attention');
    expect(display.containerClass).toContain('warning');
    expect(display.containerClass).not.toContain('success');
  });

  it('maps review to accent styling with a visible label', () => {
    const display = severityDisplay('review');
    expect(display.label).toBe('For review');
    expect(display.containerClass).toContain('accent');
    expect(display.containerClass).not.toContain('success');
  });

  it('maps unknown severities to neutral with the raw label title-cased', () => {
    const display = severityDisplay('something_else');
    expect(display.label).toBe('Something Else');
    expect(display.containerClass).not.toContain('success');
    expect(display.containerClass).not.toContain('warning');
  });
});
