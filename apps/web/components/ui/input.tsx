import * as React from 'react';
import { cn } from '@/lib/utils';

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      ref={ref}
      className={cn(
        'flex min-h-11 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-4 py-2 text-base text-lf-text transition-colors placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-accent-500 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-lf-error-600',
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = 'Input';

export { Input };
