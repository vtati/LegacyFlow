import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lf-control text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-accent-500 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'bg-lf-primary-900 text-lf-primary-foreground hover:bg-lf-primary-800 dark:hover:text-lf-primary-950',
        secondary: 'bg-lf-accent-600/10 text-lf-text hover:bg-lf-accent-600/20',
        accent: 'bg-lf-accent-600 text-lf-accent-foreground hover:bg-lf-accent-700',
        destructive: 'bg-lf-error-600 text-lf-primary-foreground hover:bg-lf-error-700 dark:text-lf-error-foreground',
        outline:
          'border border-lf-border bg-lf-canvas-elevated text-lf-text hover:border-lf-border-strong hover:bg-lf-canvas',
        ghost: 'text-lf-text hover:bg-lf-accent-600/10',
        link: 'text-lf-accent-700 underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-10 px-4 py-2',
        // UI-020: 44px touch target on mobile, compact on desktop.
        sm: 'h-11 px-3 text-xs sm:h-8',
        lg: 'h-12 px-6 text-base',
        icon: 'size-10',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
