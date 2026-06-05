'use client';

import { useEffect, useState, useCallback } from 'react';
import { ClipboardList, RefreshCw, CheckCircle, XCircle } from 'lucide-react';
import { api, AuditLog } from '@/lib/api';

export default function AuditPanel() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState('');
  const [userId, setUserId] = useState('');
  const [offset, setOffset] = useState(0);
  const limit = 100;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getAdminAuditLogs({
        limit,
        offset,
        action: action || undefined,
        userId: userId || undefined,
      });
      setLogs(res);
    } finally {
      setLoading(false);
    }
  }, [limit, offset, action, userId]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <ClipboardList size={20} />
          Audit Log
        </h2>
        <div className="flex gap-2">
          <input
            value={action}
            onChange={(e) => { setAction(e.target.value); setOffset(0); }}
            placeholder="Filter action…"
            className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm w-36"
          />
          <input
            value={userId}
            onChange={(e) => { setUserId(e.target.value); setOffset(0); }}
            placeholder="Filter user ID…"
            className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm w-36"
          />
          <button onClick={load} className="p-2 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-700">
            <RefreshCw size={15} />
          </button>
        </div>
      </div>

      {loading ? (
        <p className="text-gray-400 text-sm">Loading audit logs…</p>
      ) : logs.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <ClipboardList size={40} className="mx-auto mb-3 opacity-30" />
          <p>No audit entries found</p>
        </div>
      ) : (
        <>
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Time</th>
                  <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Action</th>
                  <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Resource</th>
                  <th className="text-left px-5 py-2.5 text-gray-500 font-medium">User</th>
                  <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id} className="border-t border-gray-50 hover:bg-gray-50">
                    <td className="px-5 py-3 text-xs text-gray-400 whitespace-nowrap">
                      {new Date(log.created_at).toLocaleString()}
                    </td>
                    <td className="px-5 py-3 font-mono text-xs text-gray-700">{log.action}</td>
                    <td className="px-5 py-3 text-xs text-gray-500">{log.resource_type}</td>
                    <td className="px-5 py-3 text-xs text-gray-500 max-w-[100px] truncate">{log.user_id}</td>
                    <td className="px-5 py-3">
                      {log.success
                        ? <CheckCircle size={14} className="text-green-500" />
                        : <XCircle size={14} className="text-red-400" />
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex justify-end gap-2 text-sm">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))} className="px-3 py-1.5 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-40">Prev</button>
            <button disabled={logs.length < limit} onClick={() => setOffset(offset + limit)} className="px-3 py-1.5 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-40">Next</button>
          </div>
        </>
      )}
    </div>
  );
}
