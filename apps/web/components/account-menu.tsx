'use client';

import { useEffect, useRef, useState } from 'react';
import { ChevronsUpDown, LogOut, UserRound } from 'lucide-react';
import { getAuthAdapter } from '@/lib/auth';
import type { AuthUser } from '@/lib/auth/adapter';
import { Button } from '@/components/ui/button';

interface AccountMenuProps {
  user: AuthUser;
}

/** Header account menu: identity, mock badge when relevant, sign out. */
export function AccountMenu({ user }: AccountMenuProps): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    // UI-023: an Escape close returns focus to the trigger — without
    // it, keyboard focus is dropped to <body> when the menu unmounts.
    // An outside-mousedown close must NOT move focus: the pointer
    // user's click target takes focus itself, and forcing the trigger
    // here would race (and usually beat) that.
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    const onClick = (e: MouseEvent): void => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [open]);

  async function handleSignOut(): Promise<void> {
    setSigningOut(true);
    setError(null);
    try {
      await getAuthAdapter().signOut();
      window.location.href = '/login';
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign out failed.');
      setSigningOut(false);
    }
  }

  return (
    <div className="relative" ref={menuRef}>
      <Button
        ref={triggerRef}
        variant="ghost"
        size="sm"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={`Account menu for ${user.displayName ?? user.email ?? 'user'}`}
      >
        <UserRound aria-hidden="true" />
        <span className="hidden max-w-32 truncate sm:inline">
          {user.displayName ?? user.email ?? 'Account'}
        </span>
        <ChevronsUpDown className="size-3" aria-hidden="true" />
      </Button>
      {open && (
        <div
          role="menu"
          aria-label="Account"
          className="absolute right-0 z-50 mt-2 w-64 rounded-lf-card border border-lf-border bg-lf-canvas-elevated p-2 shadow-lf-card"
        >
          <div className="px-3 py-2">
            <p className="truncate text-sm font-medium text-lf-text">
              {user.displayName ?? 'Account'}
            </p>
            {user.email && <p className="truncate text-xs text-lf-text-muted">{user.email}</p>}
            {user.provider === 'mock' && (
              <p className="mt-1 inline-block rounded-full bg-lf-canvas-subtle px-2 py-0.5 text-xs font-medium text-lf-text-muted">
                Mock session — synthetic user
              </p>
            )}
          </div>
          {error && (
            <p role="alert" className="px-3 py-1 text-xs text-lf-error-700">
              {error}
            </p>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start text-lf-error-700 hover:text-lf-error-700"
            onClick={handleSignOut}
            disabled={signingOut}
            role="menuitem"
          >
            <LogOut aria-hidden="true" />
            {signingOut ? 'Signing out…' : 'Sign out'}
          </Button>
        </div>
      )}
    </div>
  );
}
