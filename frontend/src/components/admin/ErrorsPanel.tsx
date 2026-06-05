'use client';

import { useEffect, useState, useCallback } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import { api, ErrorLogEntry, ErrorLogDetail } from '@/lib/api';

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red-100 text-red-700',
  error:    'bg-orange-100 text-orange-700',
  warning:  'bg-yellow-100 text-yellow-700',
  info:     'bg-blue-100 text-blue-700',
};

function ErrorDetailDrawer({ errorId, onClose }: { errorId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ErrorLogDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getErrorDetail(errorId).then(setDetail).finally(() => setLoading(false));
  }, [errorId]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative bg-white w-full max-w-lg shadow-xl flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h3 className="font-semibold text-gray-900">Error Detail</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {loading && <p className="text-gray-400">Loading…</p>}
          {detail && (
            <>
              <div className="flex gap-2 flex-wrap">
                <span className={`px-2 py-0.5 text-xs rounded-full ${SEVERITY_COLORS[detail.severity] ?? SEVERITY_COLORS.error}`}>
                  {detail.severity}
                </span>
                <span className="px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-600">{detail.status_code}</span>
                <span className="px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-600">{detail.method} {detail.route}</span>
              </div>
              <div>
                <p className="text-xs text-gray-400 mb-1">Error Type</p>
                <p className="font-mono text-sm text-red-600">{detail.error_type}</p>
              </div>
              <div>
                <p className="text-xs text-gray-400 mb-1">Message</p>
                <p className="text-sm text-gray-700 whitespace-pre-wrap">{detail.message}</p>
              </div>
              {detail.user_email && (
                <div>
                  <p className="text-xs text-gray-400 mb-1">User</p>
                  <p className="text-sm text-gray-700">{detail.user_email}</p>
                </div>
              )}
              <div>
                <p className="text-xs text-gray-400 mb-1">Time</p>
                <p className="text-sm text-gray-700">{new Date(detail.created_at).toLocaleString()}</p>
              </div>
              {detail.stack_trace && (
                <div>
                  <p className="text-xs text-gray-400 mb-1">Stack Trace</p>
                  <pre className="text-xs text-gray-600 bg-gray-50 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap max-h-80">
                    {detail.stack_trace}
                  </pre>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ErrorsPanel() {
  const [errors, setErrors] = useState<ErrorLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [severity, setSeverity] = useState('');
  const [route, setRoute] = useState('');
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const limit = 50;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getErrorLogs({ limit, offset, severity: severity || undefined, route: route || undefined });
      setErrors(res.errors);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
  }, [limit, offset, severity, route]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <AlertTriangle size={20} className="text-orange-500" />
          Error Log
        </h2>
        <div className="flex gap-2">
          <select value={severity} onChange={(e) => { setSeverity(e.target.value); setOffset(0); }} className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm">
            <option value="">All severities</option>
            <option value="critical">Critical</option>
            <option value="error">Error</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
          <input value={route} onChange={(e) => { setRoute(e.target.value); setOffset(0); }} placeholder="Filter route…" className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm w-36" />
          <button onClick={load} className="p-2 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-700">
            <RefreshCw size={15} />
          </button>
        </div>
      </div>

      {loading ? (
        <p className="text-gray-400 text-sm">Loading errors…</p>
      ) : errors.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <AlertTriangle size={40} className="mx-auto mb-3 opacity-30" />
          <p>No errors found</p>
        </div>
      ) : (
        <>
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Time</th>
                  <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Severity</th>
                  <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Route</th>
                  <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Type</th>
                  <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Message</th>
                  <th className="px-5 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {errors.map((e) => (
                  <tr key={e.id} className="border-t border-gray-50 hover:bg-gray-50 cursor-pointer" onClick={() => setSelectedId(e.id)}>
                    <td className="px-5 py-3 text-xs text-gray-400 whitespace-nowrap">
                      {new Date(e.created_at).toLocaleString()}
                    </td>
                    <td className="px-5 py-3">
                      <span className={`px-2 py-0.5 text-xs rounded-full ${SEVERITY_COLORS[e.severity] ?? SEVERITY_COLORS.error}`}>
                        {e.severity}
                      </span>
                    </td>
                    <td className="px-5 py-3 font-mono text-xs text-gray-600 max-w-[140px] truncate">
                      {e.method} {e.route}
                    </td>
                    <td className="px-5 py-3 font-mono text-xs text-red-600">{e.error_type}</td>
                    <td className="px-5 py-3 text-gray-600 max-w-[200px] truncate">{e.message}</td>
                    <td className="px-5 py-3">
                      <ChevronRight size={14} className="text-gray-300" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between text-sm text-gray-500">
            <span>{total} total errors</span>
            <div className="flex gap-2">
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))} className="px-3 py-1.5 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-40">Prev</button>
              <button disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)} className="px-3 py-1.5 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-40">Next</button>
            </div>
          </div>
        </>
      )}

      {selectedId && <ErrorDetailDrawer errorId={selectedId} onClose={() => setSelectedId(null)} />}
    </div>
  );
}
