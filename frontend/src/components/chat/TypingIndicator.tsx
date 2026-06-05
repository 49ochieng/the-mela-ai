/**
 * Mela AI - Typing Indicator Component
 */

'use client';

import Image from 'next/image';
import { cn } from '@/lib/utils';

interface TypingIndicatorProps {
  className?: string;
}

export function TypingIndicator({ className }: TypingIndicatorProps) {
  return (
    <div className={cn('flex gap-3 px-4 py-4', className)}>
      {/* Avatar */}
      <div className="shrink-0">
        <div className="w-10 h-10 rounded-full bg-white border border-gray-200 flex items-center justify-center overflow-hidden">
          <Image
            src="/mela-logo.png"
            alt="Mela AI"
            width={32}
            height={32}
            className="object-contain animate-pulse"
          />
        </div>
      </div>

      {/* Typing Bubble */}
      <div className="flex flex-col space-y-1">
        <p className="font-medium text-sm text-muted-foreground">Mela AI</p>
        <div className="inline-flex items-center gap-1.5 rounded-2xl rounded-tl-sm bg-muted px-4 py-3">
          <span className="w-2 h-2 rounded-full bg-primary/60 animate-bounce [animation-delay:-0.3s]"></span>
          <span className="w-2 h-2 rounded-full bg-primary/60 animate-bounce [animation-delay:-0.15s]"></span>
          <span className="w-2 h-2 rounded-full bg-primary/60 animate-bounce"></span>
        </div>
      </div>
    </div>
  );
}
