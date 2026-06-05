'use client';

import { useEffect, useState } from 'react';
import { Settings, Save } from 'lucide-react';
import { api, OrgSettings } from '@/lib/api';

export default function SettingsPanel() {
  const [settings, setSettings] = useState<OrgSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getOrgSettings().then(setSettings).finally(() => setLoading(false));
  }, []);

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      await api.updateOrgSettings(settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="p-8 text-gray-400">Loading settings…</div>;
  if (!settings) return null;

  const toggles: { key: keyof OrgSettings; label: string; description: string }[] = [
    { key: 'private_chat_enabled', label: 'Private Chat', description: 'Allow users to create 20-day ephemeral private conversations' },
  ];

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Settings size={20} />
          Global Settings
        </h2>
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          <Save size={15} />
          {saved ? 'Saved!' : saving ? 'Saving…' : 'Save Changes'}
        </button>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-50">
        {toggles.map(({ key, label, description }) => (
          <div key={key} className="flex items-center justify-between px-5 py-4">
            <div>
              <p className="font-medium text-gray-800">{label}</p>
              <p className="text-sm text-gray-400">{description}</p>
            </div>
            <button
              onClick={() => setSettings({ ...settings, [key]: !settings[key as keyof OrgSettings] })}
              className={`relative w-11 h-6 rounded-full transition-colors ${settings[key as keyof OrgSettings] ? 'bg-blue-600' : 'bg-gray-200'}`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${settings[key as keyof OrgSettings] ? 'translate-x-5' : ''}`}
              />
            </button>
          </div>
        ))}
      </div>

      <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-700">
        Changes are applied immediately after saving. Connector availability and quota thresholds are managed in the <strong>Models</strong> section.
      </div>
    </div>
  );
}
