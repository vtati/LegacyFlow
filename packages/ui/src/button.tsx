import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from './cn';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lf-control text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-accent-500 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        /** Primary action — ink, ivory text. */
        primary: 'bg-lf-primary-900 text-lf-primary-foreground hover:bg-lf-primary-800',
        /** Terracotta action accent. */
        accent: 'bg-lf-accent-600 text-lf-primary-foreground hover:bg-lf-accent-700',
        /** Quiet action — no chrome until hovered (warm wash). */
        ghost: 'text-lf-text hover:bg-lf-accent-600/10',
        /** Destructive — error red, paired with text/icon, never color-only. */
        destructive: 'bg-lf-error-600 text-lf-primary-foreground hover:bg-lf-error-700',
        /** Secondary surface action — warm border, white surface. */
        outline:
          'border border-lf-border bg-white text-lf-text hover:border-lf-border-strong hover:bg-lf-canvas',
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
