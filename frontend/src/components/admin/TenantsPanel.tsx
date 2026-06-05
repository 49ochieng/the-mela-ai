'use client';

import { useEffect, useState } from 'react';
import { Building2, DollarSign, Zap, Download } from 'lucide-react';
import { api, TenantSummary, TenantDetail } from '@/lib/api';

function TenantDetailDrawer({ tenantId, year, month, onClose }: {
  tenantId: string; year: number; month: number; onClose: () => void;
}) {
  const [detail, setDetail] = useState<TenantDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getAdminTenantDetail(tenantId, year, month)
      .then(setDetail)
      .finally(() => setLoading(false));
  }, [tenantId, year, month]);

  const downloadInvoice = async () => {
    const data = await api.getAdminInvoice(tenantId, year, month);
    const blob = new Blob([JSON.stringify(data.invoice, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `invoice-${tenantId}-${year}-${String(month).padStart(2, '0')}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative bg-white w-full max-w-md shadow-xl flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h3 className="font-semibold text-gray-900 truncate">{tenantId}</h3>
          <div className="flex gap-2">
            <button onClick={downloadInvoice} className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-700" title="Download invoice">
              <Download size={16} />
            </button>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {loading && <p className="text-gray-400">Loading…</p>}
          {detail && (
            <>
              <div className="grid grid-cols-2 gap-3">
                {([
                  ['Total Tokens', detail.total_tokens.toLocaleString()],
                  ['Est. Cost', `$${detail.total_cost}`],
                  ['Users', detail.user_count],
                  ['Conversations', detail.conversation_count],
                ] as [string, string | number][]).map(([k, v]) => (
                  <div key={k} className="bg-gray-50 rounded-lg p-3">
                    <p className="text-xs text-gray-400">{k}</p>
                    <p className="font-semibold text-gray-800">{v}</p>
                  </div>
                ))}
              </div>
              {detail.by_model.length > 0 && (
                <div>
                  <p className="text-xs text-gray-400 uppercase tracking-wide mb-2">By Model</p>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-xs text-gray-400">
                        <th className="text-left py-1">Model</th>
                        <th className="text-right py-1">Tokens</th>
                        <th className="text-right py-1">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.by_model.map((m) => (
                        <tr key={m.model} className="border-t border-gray-50">
                          <td className="py-1.5 font-mono text-xs text-gray-600">{m.model}</td>
                          <td className="py-1.5 text-right text-gray-600">{m.tokens.toLocaleString()}</td>
                          <td className="py-1.5 text-right text-gray-600">${m.cost}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function TenantsPanel() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    api.getAdminTenants(year, month)
      .then((r) => setTenants(r.tenants))
      .finally(() => setLoading(false));
  }, [year, month]);

  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-900">Tenants</h2>
        <div className="flex gap-2">
          <select value={month} onChange={(e) => setMonth(Number(e.target.value))} className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm">
            {months.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
          <select value={year} onChange={(e) => setYear(Number(e.target.value))} className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm">
            {[now.getFullYear(), now.getFullYear() - 1].map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </div>
      </div>

      {loading ? (
        <p className="text-gray-400 text-sm">Loading tenants…</p>
      ) : tenants.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <Building2 size={40} className="mx-auto mb-3 opacity-30" />
          <p>No tenant data for this period</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Tenant</th>
                <th className="text-right px-5 py-2.5 text-gray-500 font-medium">
                  <Zap size={13} className="inline mr-1" />Tokens
                </th>
                <th className="text-right px-5 py-2.5 text-gray-500 font-medium">
                  <DollarSign size={13} className="inline mr-1" />Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {tenants.map((t) => (
                <tr
                  key={t.tenant_id}
                  className="border-t border-gray-50 hover:bg-gray-50 cursor-pointer"
                  onClick={() => setSelected(t.tenant_id)}
                >
                  <td className="px-5 py-3 font-mono text-xs text-gray-700">{t.tenant_id}</td>
                  <td className="px-5 py-3 text-right text-gray-700">{t.total_tokens.toLocaleString()}</td>
                  <td className="px-5 py-3 text-right text-gray-700">${t.total_cost}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <TenantDetailDrawer
          tenantId={selected}
          year={year}
          month={month}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
