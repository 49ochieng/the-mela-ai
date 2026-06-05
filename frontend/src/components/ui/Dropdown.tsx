/**
 * Mela AI - Dropdown Component
 */

'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { ChevronDown, Check } from 'lucide-react';

export interface DropdownOption {
  value: string;
  label: string;
  description?: string;
  icon?: React.ReactNode;
  group?: string; // optional company/category grouping header
}

interface DropdownProps {
  value: string;
  onChange: (value: string) => void;
  options: DropdownOption[];
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  /** Direction the dropdown panel opens. Defaults to 'down'. Use 'up' when near the bottom of the screen. */
  position?: 'down' | 'up';
  /** Min-width for the dropdown panel (overrides trigger width). */
  panelMinWidth?: string;
}

export function Dropdown({
  value,
  onChange,
  options,
  placeholder = 'Select...',
  className,
  disabled,
  position = 'down',
  panelMinWidth,
}: DropdownProps) {
  const [isOpen, setIsOpen] = React.useState(false);
  const dropdownRef = React.useRef<HTMLDivElement>(null);

  const selectedOption = options.find((opt) => opt.value === value);

  React.useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Build grouped structure if any option has a group field
  const hasGroups = options.some((o) => o.group);
  const groups: { label: string | null; items: DropdownOption[] }[] = [];
  if (hasGroups) {
    const seen = new Map<string, DropdownOption[]>();
    for (const opt of options) {
      const key = opt.group ?? '';
      if (!seen.has(key)) seen.set(key, []);
      seen.get(key)!.push(opt);
    }
    seen.forEach((items, label) => groups.push({ label: label || null, items }));
  } else {
    groups.push({ label: null, items: options });
  }

  return (
    <div ref={dropdownRef} className={cn('relative', className)}>
      <button
        type="button"
        onClick={() => !disabled && setIsOpen(!isOpen)}
        disabled={disabled}
        className={cn(
          'flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background',
          'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
          'disabled:cursor-not-allowed disabled:opacity-50',
          isOpen && 'ring-2 ring-ring ring-offset-2'
        )}
      >
        <span className={cn(!selectedOption && 'text-muted-foreground')}>
          {selectedOption ? (
            <span className="flex items-center gap-2">
              {selectedOption.icon}
              {selectedOption.label}
            </span>
          ) : (
            placeholder
          )}
        </span>
        <ChevronDown className={cn('h-4 w-4 transition-transform shrink-0 ml-2', isOpen && 'rotate-180')} />
      </button>

      {isOpen && (
        <div
          className={cn(
            'absolute z-50 rounded-md border bg-popover shadow-md animate-in fade-in-0 zoom-in-95',
            position === 'up' ? 'bottom-full mb-1' : 'top-full mt-1',
            panelMinWidth ? '' : 'w-full',
          )}
          style={panelMinWidth ? { minWidth: panelMinWidth } : undefined}
        >
          <div className="max-h-[420px] overflow-y-auto p-1">
            {groups.map((group, gi) => (
              <React.Fragment key={gi}>
                {group.label && (
                  <div className={cn(
                    'px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground select-none',
                    gi > 0 && 'mt-1 pt-1 border-t border-border',
                  )}>
                    {group.label}
                  </div>
                )}
                {group.items.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => {
                      onChange(option.value);
                      setIsOpen(false);
                    }}
                    className={cn(
                      'relative flex w-full cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none',
                      'hover:bg-accent hover:text-accent-foreground',
                      option.value === value && 'bg-accent'
                    )}
                  >
                    <div className="flex flex-1 items-center gap-2 min-w-0">
                      {option.icon}
                      <div className="flex flex-col items-start min-w-0">
                        <span className="truncate">{option.label}</span>
                        {option.description && (
                          <span className="text-xs text-muted-foreground truncate max-w-[220px]">{option.description}</span>
                        )}
                      </div>
                    </div>
                    {option.value === value && <Check className="h-4 w-4 shrink-0 ml-1" />}
                  </button>
                ))}
              </React.Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
