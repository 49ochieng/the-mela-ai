'use client';

import { useChatStore, ProfileType } from '@/lib/store';

const profiles: { id: ProfileType; label: string; description: string }[] = [
  {
    id: 'org',
    label: 'Work',
    description: 'Team chats, projects and enterprise knowledge access',
  },
  {
    id: 'personal',
    label: 'Personal',
    description: 'Your private chats — no sharing or enterprise access',
  },
];

export default function ProfileSwitcher() {
  const { activeProfile, setActiveProfile } = useChatStore();

  return (
    <div className="flex items-center gap-1 px-2 py-1 bg-neutral-800/60 rounded-lg border border-neutral-700/50">
      {profiles.map((p) => {
        const active = activeProfile === p.id;
        return (
          <button
            key={p.id}
            onClick={() => !active && setActiveProfile(p.id)}
            title={p.description}
            className={[
              'px-2.5 py-1 rounded-md text-xs font-medium transition-all',
              active
                ? 'bg-primary text-white shadow-sm'
                : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-700/50',
            ].join(' ')}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}
