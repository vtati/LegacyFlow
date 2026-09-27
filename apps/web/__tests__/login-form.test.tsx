/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LoginForm } from '../components/login-form';

describe('LoginForm', () => {
  it('renders the Google button and the magic-link email form', () => {
    render(<LoginForm />);

    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeDefined();
    expect(screen.getByLabelText('Email address')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Email me a sign-in link' })).toBeDefined();
  });

  it('labels mock mode when Supabase is not configured', () => {
    render(<LoginForm />);
    expect(screen.getByRole('note')).toHaveTextContent('Local dev mock auth');
  });

  it('shows an inline error when submitting an empty email', async () => {
    render(<LoginForm />);
    fireEvent.click(screen.getByRole('button', { name: 'Email me a sign-in link' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Email is required.');
  });

  it('shows the mock "sent" state after a valid email submit', async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText('Email address'), 'demo@example.com');
    await user.click(screen.getByRole('button', { name: 'Email me a sign-in link' }));
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('Magic link sent');
    expect(status).toHaveTextContent('(mock)');
  });
});
