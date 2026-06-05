'use client';

import { useEffect, useState } from 'react';
import { Cpu, CheckCircle, XCircle, Edit2, Save, X } from 'lucide-react';
import { api, ModelGovernance } from '@/lib/api';

function ModelRow({ model, onUpdated }: { model: ModelGovernance; onUpdated: () => void }) {
  const [editing, setEditing] = useState(false);
  const [rate, setRate] = useState(model.cost_rate_per_1k_tokens);
  const [dailyTokens, setDailyTokens] = useState<number | ''>(model.daily_token_limit ?? '');
  const [dailyReqs, setDailyReqs] = useState<number | ''>(model.daily_request_limit ?? '');
  const [saving, setSaving] = useState(false);

  const toggle = async () => {
    await api.updateModelQuota(model.model_id, { is_enabled: !model.is_enabled });
    onUpdated();
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.updateModelQuota(model.model_id, {
        cost_rate_per_1k_tokens: rate,
        daily_token_limit: dailyTokens === '' ? null : Number(dailyTokens),
        daily_request_limit: dailyReqs === '' ? null : Number(dailyReqs),
      });
      onUpdated();
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => {
    setRate(model.cost_rate_per_1k_tokens);
    setDailyTokens(model.daily_token_limit ?? '');
    setDailyReqs(model.daily_request_limit ?? '');
    setEditing(false);
  };

  return (
    <tr className="border-t border-gray-50 hover:bg-gray-50 align-top">
      <td className="px-5 py-3">
        <p className="font-mono text-xs text-gray-700">{model.model_id}</p>
        <p className="text-xs text-gray-400">{model.display_name ?? model.provider}</p>
      </td>
      <td className="px-5 py-3">
        <button onClick={toggle} className="flex items-center gap-1 text-sm">
          {model.is_enabled
            ? <CheckCircle size={16} className="text-green-500" />
            : <XCircle size={16} className="text-gray-300" />
          }
          <span className={model.is_enabled ? 'text-green-700' : 'text-gray-400'}>
            {model.is_enabled ? 'Enabled' : 'Disabled'}
          </span>
        </button>
      </td>
      <td className="px-5 py-3 text-sm text-gray-500">{model.provider}</td>
      {editing ? (
        <>
          <td className="px-5 py-3">
            <input
              type="number"
              step="0.0001"
              value={rate}
              onChange={(e) => setRate(Number(e.target.value))}
              className="w-24 border border-gray-200 rounded px-2 py-1 text-xs"
            />
          </td>
          <td className="px-5 py-3">
            <input
              type="number"
              value={dailyTokens}
              onChange={(e) => setDailyTokens(e.target.value === '' ? '' : Number(e.target.value))}
              placeholder="∞"
              className="w-24 border border-gray-200 rounded px-2 py-1 text-xs"
            />
          </td>
          <td className="px-5 py-3">
            <input
              type="number"
              value={dailyReqs}
              onChange={(e) => setDailyReqs(e.target.value === '' ? '' : Number(e.target.value))}
              placeholder="∞"
              className="w-24 border border-gray-200 rounded px-2 py-1 text-xs"
            />
          </td>
          <td className="px-5 py-3">
            <div className="flex gap-1">
              <button onClick={save} disabled={saving} className="p-1.5 text-green-600 hover:bg-green-50 rounded-lg">
                <Save size={14} />
              </button>
              <button onClick={cancel} className="p-1.5 text-gray-400 hover:bg-gray-100 rounded-lg">
                <X size={14} />
              </button>
            </div>
          </td>
        </>
      ) : (
        <>
          <td className="px-5 py-3 text-sm text-gray-700">${model.cost_rate_per_1k_tokens}/1K</td>
          <td className="px-5 py-3 text-sm text-gray-500">{model.daily_token_limit?.toLocaleString() ?? '∞'}</td>
          <td className="px-5 py-3 text-sm text-gray-500">{model.daily_request_limit?.toLocaleString() ?? '∞'}</td>
          <td className="px-5 py-3">
            <button onClick={() => setEditing(true)} className="p-1.5 text-gray-400 hover:bg-gray-100 rounded-lg hover:text-gray-700">
              <Edit2 size={14} />
            </button>
          </td>
        </>
      )}
    </tr>
  );
}

export default function ModelsPanel() {
  const [models, setModels] = useState<ModelGovernance[]>([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    api.getModelGovernance()
      .then((r) => setModels(r.models))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-900">Model Governance</h2>
        <p className="text-sm text-gray-400">{models.length} models</p>
      </div>

      {loading ? (
        <p className="text-gray-400 text-sm">Loading models…</p>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
          <table className="w-full text-sm min-w-[700px]">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">
                  <Cpu size={13} className="inline mr-1" />Model
                </th>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Status</th>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Provider</th>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Cost / 1K</th>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Daily Tokens</th>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Daily Reqs</th>
                <th className="px-5 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <ModelRow key={m.model_id} model={m} onUpdated={load} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
