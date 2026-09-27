import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from './cn';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lf-control text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        /** Primary brand action — indigo/navy (§5.2). */
        primary:
          'bg-lf-primary-800 text-lf-primary-foreground shadow-lf-subtle hover:bg-lf-primary-700',
        /** Warm amber action accent (§5.2). */
        accent:
          'bg-lf-accent-700 text-lf-accent-foreground shadow-lf-subtle hover:bg-lf-accent-600',
        /** Quiet action — no chrome until hovered. */
        ghost: 'text-lf-text hover:bg-lf-primary-50 hover:text-lf-primary-800',
        /** Destructive — error red, paired with text/icon, never color-only (§5.2). */
        destructive:
          'bg-lf-error-600 text-lf-error-foreground shadow-lf-subtle hover:bg-lf-error-700',
        /** Secondary surface action. */
        outline:
          'border border-lf-border bg-lf-canvas-elevated text-lf-text shadow-lf-subtle hover:bg-lf-canvas-subtle',
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        md: 'h-10 px-4',
        lg: 'h-12 px-6 text-base',
        icon: 'size-10',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

/** Minimal branded button. The full shadcn set lives in `apps/web`. */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, type = 'button', ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  ),
);
Button.displayName = 'Button';

export { buttonVariants };
