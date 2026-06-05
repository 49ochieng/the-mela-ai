/**
 * Mela AI - Avatar Component
 */

import * as React from 'react';
import { cn } from '@/lib/utils';

interface AvatarProps extends React.HTMLAttributes<HTMLDivElement> {
  src?: string | null;
  alt?: string;
  fallback?: string;
  size?: 'sm' | 'md' | 'lg';
}

const sizeClasses = {
  sm: 'h-8 w-8 text-xs',
  md: 'h-10 w-10 text-sm',
  lg: 'h-12 w-12 text-base',
};

const Avatar = React.forwardRef<HTMLDivElement, AvatarProps>(
  ({ className, src, alt, fallback, size = 'md', ...props }, ref) => {
    const [hasError, setHasError] = React.useState(false);

    const initials = React.useMemo(() => {
      if (fallback) return fallback;
      if (!alt) return '?';
      return alt
        .split(' ')
        .map((word) => word[0])
        .join('')
        .toUpperCase()
        .slice(0, 2);
    }, [alt, fallback]);

    if (!src || hasError) {
      return (
        <div
          ref={ref}
          className={cn(
            'relative flex shrink-0 items-center justify-center rounded-full bg-primary/10 font-medium text-primary',
            sizeClasses[size],
            className
          )}
          {...props}
        >
          {initials}
        </div>
      );
    }

    return (
      <div
        ref={ref}
        className={cn(
          'relative shrink-0 overflow-hidden rounded-full',
          sizeClasses[size],
          className
        )}
        {...props}
      >
        <img
          src={src}
          alt={alt || 'Avatar'}
          className="h-full w-full object-cover"
          onError={() => setHasError(true)}
        />
      </div>
    );
  }
);
Avatar.displayName = 'Avatar';

export { Avatar };
