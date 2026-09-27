/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Footer } from '../components/footer';
import { SAFETY_LABEL } from '../lib/constants';

describe('Footer', () => {
  it('contains the safety label text', () => {
    render(<Footer />);
    expect(screen.getByRole('note')).toHaveTextContent(SAFETY_LABEL);
  });
});
