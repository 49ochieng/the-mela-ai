/**
 * Mela AI - Notification Center
 *
 * Bell icon with unread count badge + dropdown panel showing notifications.
 */

'use client';

import { useEffect, useState, useCallback } from 'react';
import { Bell, Check, CheckCheck, Trash2, Mail, AlertTriangle, Info, UserPlus } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';

type NotificationType = 'share_invite' | 'share_accepted' | 'budget_warning' | 'budget_exceeded' | 'system' | 'mention';

interface Notification {
  id: string;
  type: NotificationType;
  title: string;
  message: string | null;
  link_type: string | null;
  link_id: string | null;
  actor_name: string | null;
  is_read: boolean;
  created_at: string;
}

const TYPE_ICONS: Record<NotificationType, React.ReactNode> = {
  share_invite: <UserPlus className="h-4 w-4 text-blue-500" />,
  share_accepted: <Check className="h-4 w-4 text-green-500" />,
  budget_warning: <AlertTriangle className="h-4 w-4 text-amber-500" />,
  budget_exceeded: <AlertTriangle className="h-4 w-4 text-red-500" />,
  system: <Info className="h-4 w-4 text-gray-500" />,
  mention: <Mail className="h-4 w-4 text-purple-500" />,
};

function formatTimeAgo(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

export function NotificationCenter() {
  const [isOpen, setIsOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);

  const fetchUnreadCount = useCallback(async () => {
    try {
      const res = await api.getUnreadCount();
      setUnreadCount(res.unread_count ?? 0);
    } catch {
      // Silently fail if notifications not configured
    }
  }, []);

  const fetchNotifications = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getNotifications();
      setNotifications(res);
    } catch {
      setNotifications([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll unread count every 30 seconds
  useEffect(() => {
    fetchUnreadCount();
    const interval = setInterval(fetchUnreadCount, 30000);
    return () => clearInterval(interval);
  }, [fetchUnreadCount]);

  // Fetch full list when dropdown opens
  useEffect(() => {
    if (isOpen) {
      fetchNotifications();
    }
  }, [isOpen, fetchNotifications]);

  const handleMarkRead = async (id: string) => {
    try {
      await api.markNotificationRead(id);
      setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, is_read: true } : n)));
      setUnreadCount((c) => Math.max(0, c - 1));
    } catch {}
  };

  const handleMarkAllRead = async () => {
    try {
      await api.markAllNotificationsRead();
      setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
      setUnreadCount(0);
    } catch {}
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteNotification(id);
      const removed = notifications.find((n) => n.id === id);
      setNotifications((prev) => prev.filter((n) => n.id !== id));
      if (removed && !removed.is_read) {
        setUnreadCount((c) => Math.max(0, c - 1));
      }
    } catch {}
  };

  return (
    <div className="relative">
      {/* Bell button */}
      <Button
        variant="ghost"
        size="icon"
        title="Notifications"
        onClick={() => setIsOpen(!isOpen)}
        className="relative"
      >
        <Bell className="h-4 w-4" />
        {unreadCount > 0 && (
          <span className="absolute top-0.5 right-0.5 flex items-center justify-center h-4 w-4 text-[10px] font-bold bg-red-500 text-white rounded-full">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </Button>

      {/* Dropdown */}
      {isOpen && (
        <>
          {/* Backdrop to close on outside click */}
          <div className="fixed inset-0 z-40" onClick={() => setIsOpen(false)} />

          <div className="absolute right-0 mt-2 w-80 max-h-[400px] overflow-y-auto bg-background border rounded-lg shadow-lg z-50">
            <div className="flex items-center justify-between px-4 py-2 border-b">
              <h3 className="font-semibold text-sm">Notifications</h3>
              {unreadCount > 0 && (
                <button
                  onClick={handleMarkAllRead}
                  className="text-xs text-primary hover:underline flex items-center gap-1"
                >
                  <CheckCheck className="h-3 w-3" />
                  Mark all read
                </button>
              )}
            </div>

            {loading ? (
              <div className="p-4 text-center text-sm text-muted-foreground">Loading...</div>
            ) : notifications.length === 0 ? (
              <div className="p-4 text-center text-sm text-muted-foreground">No notifications</div>
            ) : (
              <ul className="divide-y">
                {notifications.map((n) => (
                  <li
                    key={n.id}
                    className={cn(
                      'flex items-start gap-3 px-4 py-3 hover:bg-muted/50 transition-colors',
                      !n.is_read && 'bg-primary/5'
                    )}
                  >
                    <div className="pt-0.5">{TYPE_ICONS[n.type]}</div>
                    <div className="flex-1 min-w-0">
                      <p className={cn('text-sm leading-snug', !n.is_read && 'font-medium')}>
                        {n.title}
                      </p>
                      {n.actor_name && (
                        <p className="text-xs font-medium text-primary mt-0.5">{n.actor_name}</p>
                      )}
                      {n.message && (
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{n.message}</p>
                      )}
                      {n.link_type && n.link_id && (
                        <a
                          href={
                            n.link_type === 'conversation'
                              ? `/chat?id=${n.link_id}`
                              : n.link_type === 'project'
                              ? `/projects/${n.link_id}`
                              : '#'
                          }
                          className="text-xs text-primary underline mt-0.5 inline-block"
                          onClick={() => setIsOpen(false)}
                        >
                          View {n.link_type}
                        </a>
                      )}
                      <p className="text-[10px] text-muted-foreground mt-1">{formatTimeAgo(n.created_at)}</p>
                    </div>
                    <div className="flex flex-col gap-1">
                      {!n.is_read && (
                        <button
                          onClick={() => handleMarkRead(n.id)}
                          className="p-1 hover:bg-muted rounded"
                          title="Mark as read"
                        >
                          <Check className="h-3 w-3 text-muted-foreground" />
                        </button>
                      )}
                      <button
                        onClick={() => handleDelete(n.id)}
                        className="p-1 hover:bg-muted rounded"
                        title="Delete"
                      >
                        <Trash2 className="h-3 w-3 text-muted-foreground" />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
