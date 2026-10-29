import * as React from 'react';
import { cn } from '@/lib/utils';

const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn('rounded-lf-card border border-lf-border bg-lf-canvas-elevated text-lf-text', className)}
      {...props}
    />
  ),
);
Card.displayName = 'Card';

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('flex flex-col gap-1.5 p-6', className)} {...props} />
  ),
);
CardHeader.displayName = 'CardHeader';

export type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;

export interface CardTitleProps extends React.HTMLAttributes<HTMLHeadingElement> {
  /**
   * Heading level to render (UI-002). Defaults to h3 so existing callers
   * are unchanged; pass level={1} where the card title is the page's
   * primary heading (auth screens) or `as` for an explicit tag.
   */
  level?: HeadingLevel;
  as?: `h${HeadingLevel}`;
}

function headingTag(level: HeadingLevel | undefined, as: CardTitleProps['as']): string {
  return as ?? `h${level ?? 3}`;
}

const CardTitle = React.forwardRef<HTMLHeadingElement, CardTitleProps>(
  ({ className, level, as, ...props }, ref) => {
    const Tag = headingTag(level, as) as React.ElementType;
    return (
      <Tag
        ref={ref}
        className={cn('font-semibold leading-none tracking-tight', className)}
        {...props}
      />
    );
  },
);
CardTitle.displayName = 'CardTitle';

const CardTitleEditorial = React.forwardRef<HTMLHeadingElement, CardTitleProps>(
  ({ className, level, as, ...props }, ref) => {
    const Tag = headingTag(level, as) as React.ElementType;
    return (
      <Tag
        ref={ref}
        className={cn('font-lf-serif text-lg leading-none tracking-tight', className)}
        {...props}
      />
    );
  },
);
CardTitleEditorial.displayName = 'CardTitleEditorial';

const CardDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p ref={ref} className={cn('text-sm text-lf-text-muted', className)} {...props} />
));
CardDescription.displayName = 'CardDescription';

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('p-6 pt-0', className)} {...props} />
  ),
);
CardContent.displayName = 'CardContent';

const CardFooter = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('flex items-center p-6 pt-0', className)} {...props} />
  ),
);
CardFooter.displayName = 'CardFooter';

export { Card, CardHeader, CardFooter, CardTitle, CardTitleEditorial, CardDescription, CardContent };
