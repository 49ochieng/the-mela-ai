'use client';

import { useEffect, useState } from 'react';
import { Users, MessageSquare, Zap, TrendingUp, AlertTriangle, Activity } from 'lucide-react';
import { api, MonitoringData } from '@/lib/api';

function StatCard({ icon: Icon, label, value, sub, color = 'blue' }: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
}) {
  const colors: Record<string, string> = {
    blue: 'bg-blue-50 text-blue-600',
    green: 'bg-green-50 text-green-600',
    purple: 'bg-purple-50 text-purple-600',
    orange: 'bg-orange-50 text-orange-600',
    red: 'bg-red-50 text-red-600',
  };
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 flex gap-4 items-start">
      <div className={`p-2 rounded-lg ${colors[color] ?? colors.blue}`}>
        <Icon size={20} />
      </div>
      <div>
        <p className="text-sm text-gray-500">{label}</p>
        <p className="text-2xl font-bold text-gray-900">{value.toLocaleString()}</p>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

export default function OverviewPanel() {
  const [data, setData] = useState<MonitoringData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getMonitoringData()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-8 text-gray-500">Loading overview...</div>;
  if (error) return <div className="p-8 text-red-500">Error: {error}</div>;
  if (!data) return null;

  const { users, activity, quality, model_health } = data;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-xl font-semibold text-gray-900">Overview</h2>
        <span
          title="All LLM inference and text embeddings run on Azure AI Foundry"
          className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700"
        >
          <Zap size={12} />
          Powered by Azure AI Foundry (Foundry IQ)
        </span>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        <StatCard icon={Users} label="Total Users" value={users.total} sub={`${users.active} active`} color="blue" />
        <StatCard icon={Activity} label="Active Sessions (1h)" value={activity.active_sessions_1h} color="green" />
        <StatCard icon={MessageSquare} label="Messages (24h)" value={activity.messages_24h} sub={`${activity.messages_1h} last hour`} color="purple" />
        <StatCard icon={Zap} label="Tokens (24h)" value={activity.tokens_24h.toLocaleString()} sub={`${activity.tokens_1h.toLocaleString()} last hour`} color="orange" />
        <StatCard icon={AlertTriangle} label="Error Rate (24h)" value={`${quality.error_rate_pct}%`} sub={`${quality.errors_24h} errors`} color={quality.error_rate_pct > 5 ? 'red' : 'green'} />
        <StatCard icon={TrendingUp} label="Requests (24h)" value={activity.messages_24h} color="blue" />
      </div>

      {model_health.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
            <h3 className="font-medium text-gray-800">Model Activity (24h)</h3>
            <span className="text-xs font-medium text-blue-600">Served via Azure AI Foundry</span>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-5 py-2 text-gray-500 font-medium">Model</th>
                <th className="text-right px-5 py-2 text-gray-500 font-medium">Requests</th>
                <th className="text-right px-5 py-2 text-gray-500 font-medium">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {model_health.map((m) => (
                <tr key={m.model} className="border-t border-gray-50 hover:bg-gray-50">
                  <td className="px-5 py-2.5 font-mono text-xs text-gray-700">{m.model}</td>
                  <td className="px-5 py-2.5 text-right text-gray-700">{m.requests_24h}</td>
                  <td className="px-5 py-2.5 text-right text-gray-700">{(m.tokens_24h ?? 0).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.recent_errors.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-100">
            <h3 className="font-medium text-gray-800">Recent Errors</h3>
          </div>
          <ul className="divide-y divide-gray-50">
            {data.recent_errors.map((e) => (
              <li key={e.id} className="px-5 py-3 text-sm text-gray-600">
                <span className="font-medium text-red-600">{e.action}</span>
                {' — '}
                <span className="text-xs text-gray-400">{new Date(e.created_at).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
