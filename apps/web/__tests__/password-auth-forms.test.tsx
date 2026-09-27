/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SignUpForm } from '../components/signup-form';
import { ResetPasswordForm } from '../components/reset-password-form';

describe('SignUpForm', () => {
  it('renders email, password and confirm fields', () => {
    render(<SignUpForm />);
    expect(screen.getByLabelText('Email address')).toBeDefined();
    expect(screen.getByLabelText('Password')).toBeDefined();
    expect(screen.getByLabelText('Confirm password')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Create account' })).toBeDefined();
  });

  it('rejects a short password', async () => {
    const user = userEvent.setup();
    render(<SignUpForm />);
    await user.type(screen.getByLabelText('Email address'), 'new@example.com');
    await user.type(screen.getByLabelText('Password'), 'short');
    await user.type(screen.getByLabelText('Confirm password'), 'short');
    await user.click(screen.getByRole('button', { name: 'Create account' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('at least 8 characters');
  });

  it('rejects mismatched passwords', async () => {
    const user = userEvent.setup();
    render(<SignUpForm />);
    await user.type(screen.getByLabelText('Email address'), 'new@example.com');
    await user.type(screen.getByLabelText('Password'), 'longenoughpassword');
    await user.type(screen.getByLabelText('Confirm password'), 'differentpassword');
    await user.click(screen.getByRole('button', { name: 'Create account' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Passwords do not match.');
  });

  it('links back to sign in', () => {
    render(<SignUpForm />);
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
  });
});

describe('ResetPasswordForm', () => {
  it('renders the email form', () => {
    render(<ResetPasswordForm />);
    expect(screen.getByLabelText('Email address')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Send reset link' })).toBeDefined();
  });

  it('shows the mock "sent" state after a valid submit', async () => {
    const user = userEvent.setup();
    render(<ResetPasswordForm />);
    await user.type(screen.getByLabelText('Email address'), 'demo@example.com');
    await user.click(screen.getByRole('button', { name: 'Send reset link' }));
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('Reset email sent');
    expect(status).toHaveTextContent('(mock)');
  });

  it('shows an inline error when submitting an empty email', async () => {
    render(<ResetPasswordForm />);
    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Email is required.');
  });
});
