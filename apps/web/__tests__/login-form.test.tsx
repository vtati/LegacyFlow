/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LoginForm } from '../components/login-form';

describe('LoginForm', () => {
  it('renders the Google button and the password tab by default', () => {
    render(<LoginForm />);

    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeDefined();
    expect(screen.getByRole('tab', { name: 'Password' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('Email address')).toBeDefined();
    expect(screen.getByLabelText('Password')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDefined();
  });

  it('shows the attorney-review footer in human copy only — no raw status enum (F6)', () => {
    const { container } = render(<LoginForm />);
    expect(container.textContent).toContain('Attorney review pending.');
    expect(container.textContent).not.toContain('attorney-pending');
  });

  it('labels mock mode when Supabase is not configured', () => {
    render(<LoginForm />);
    expect(screen.getByRole('note')).toHaveTextContent('Local dev mock auth');
  });

  it('links to sign-up and password reset from the password tab', () => {
    render(<LoginForm />);
    expect(screen.getByRole('link', { name: 'Create an account' })).toHaveAttribute(
      'href',
      '/signup',
    );
    expect(screen.getByRole('link', { name: 'Forgot password?' })).toHaveAttribute(
      'href',
      '/auth/reset-password',
    );
  });

  it('shows an inline error when submitting an empty password form', async () => {
    render(<LoginForm />);
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((a) => a.textContent === 'Email is required.')).toBe(true);
  });

  it('switches to the magic-link tab on click', async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.click(screen.getByRole('tab', { name: 'Magic link' }));
    expect(screen.getByRole('tab', { name: 'Magic link' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Email me a sign-in link' })).toBeDefined();
  });

  it('implements the tab keyboard pattern: arrows/Home/End, roving tabindex, aria pairing (UI-017)', async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    const passwordTab = screen.getByRole('tab', { name: 'Password' });
    const magicTab = screen.getByRole('tab', { name: 'Magic link' });

    // Roving tabindex: only the selected tab is in the tab order.
    expect(passwordTab).toHaveAttribute('tabindex', '0');
    expect(magicTab).toHaveAttribute('tabindex', '-1');

    // id / aria-controls / aria-labelledby pairing with the panels.
    expect(passwordTab.id).toBeTruthy();
    const panelId = passwordTab.getAttribute('aria-controls');
    expect(panelId).toBeTruthy();
    const panel = document.getElementById(panelId!);
    expect(panel?.getAttribute('aria-labelledby')).toBe(passwordTab.id);

    // ArrowRight moves selection and focus.
    passwordTab.focus();
    await user.keyboard('{ArrowRight}');
    expect(magicTab).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(magicTab);
    expect(magicTab).toHaveAttribute('tabindex', '0');
    expect(passwordTab).toHaveAttribute('tabindex', '-1');

    // ArrowLeft wraps back; Home/End jump to the ends.
    await user.keyboard('{ArrowLeft}');
    expect(passwordTab).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(passwordTab);
    await user.keyboard('{End}');
    expect(magicTab).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{Home}');
    expect(passwordTab).toHaveAttribute('aria-selected', 'true');
  });

  it('shows the mock "sent" state after a valid magic-link submit', async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.click(screen.getByRole('tab', { name: 'Magic link' }));
    await user.type(screen.getByLabelText('Email address'), 'demo@example.com');
    await user.click(screen.getByRole('button', { name: 'Email me a sign-in link' }));
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('Magic link sent');
    expect(status).toHaveTextContent('(mock)');
  });
});
