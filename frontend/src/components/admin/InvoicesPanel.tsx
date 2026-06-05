'use client';

import { useEffect, useState } from 'react';
import { Receipt, Download } from 'lucide-react';
import { api, TenantSummary } from '@/lib/api';

export default function InvoicesPanel() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    api.getAdminTenants(year, month)
      .then((r) => setTenants(r.tenants))
      .finally(() => setLoading(false));
  }, [year, month]);

  const download = async (tenantId: string) => {
    setDownloading(tenantId);
    try {
      const data = await api.getAdminInvoice(tenantId, year, month);
      const blob = new Blob([JSON.stringify(data.invoice, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `invoice-${tenantId}-${year}-${String(month).padStart(2, '0')}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(null);
    }
  };

  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  const totalCost = tenants.reduce((s, t) => s + t.total_cost, 0);
  const totalTokens = tenants.reduce((s, t) => s + t.total_tokens, 0);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Receipt size={20} />
          Invoices
        </h2>
        <div className="flex gap-2">
          <select value={month} onChange={(e) => setMonth(Number(e.target.value))} className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm">
            {months.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
          <select value={year} onChange={(e) => setYear(Number(e.target.value))} className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm">
            {[now.getFullYear(), now.getFullYear() - 1].map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </div>
      </div>

      {!loading && tenants.length > 0 && (
        <div className="grid grid-cols-2 gap-4">
          <div className="bg-blue-50 rounded-xl p-4">
            <p className="text-xs text-blue-500">Total Tokens ({months[month - 1]} {year})</p>
            <p className="text-2xl font-bold text-blue-900">{totalTokens.toLocaleString()}</p>
          </div>
          <div className="bg-green-50 rounded-xl p-4">
            <p className="text-xs text-green-500">Total Estimated Cost</p>
            <p className="text-2xl font-bold text-green-900">${totalCost.toFixed(4)}</p>
          </div>
        </div>
      )}

      {loading ? (
        <p className="text-gray-400 text-sm">Loading invoices…</p>
      ) : tenants.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <Receipt size={40} className="mx-auto mb-3 opacity-30" />
          <p>No billing data for this period</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Tenant</th>
                <th className="text-right px-5 py-2.5 text-gray-500 font-medium">Tokens</th>
                <th className="text-right px-5 py-2.5 text-gray-500 font-medium">Cost (USD)</th>
                <th className="px-5 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.tenant_id} className="border-t border-gray-50 hover:bg-gray-50">
                  <td className="px-5 py-3 font-mono text-xs text-gray-700">{t.tenant_id}</td>
                  <td className="px-5 py-3 text-right text-gray-700">{t.total_tokens.toLocaleString()}</td>
                  <td className="px-5 py-3 text-right font-semibold text-gray-800">${t.total_cost}</td>
                  <td className="px-5 py-3 text-right">
                    <button
                      onClick={() => download(t.tenant_id)}
                      disabled={downloading === t.tenant_id}
                      className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 disabled:opacity-40"
                    >
                      <Download size={13} />
                      {downloading === t.tenant_id ? 'Downloading…' : 'Invoice'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
