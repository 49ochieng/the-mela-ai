'use client';

import { useState, useEffect, useCallback } from 'react';
import { useChatStore } from '@/lib/store';
import { ProjectMember, MemberRole, AddMemberRequest } from '@/lib/api';
import { toast } from 'sonner';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  resourceType: 'project' | 'chat';
  resourceId: string;
  resourceName: string;
  /** Owner cannot be removed or have role changed via share modal */
  ownerUserId?: string;
}

const ROLE_LABELS: Record<MemberRole, string> = {
  owner: 'Owner',
  editor: 'Editor',
  viewer: 'Viewer',
};

const ROLE_DESCRIPTIONS: Record<MemberRole, string> = {
  owner: 'Full control — manage members, edit settings, delete',
  editor: 'Can send messages, upload files, create chats',
  viewer: 'Read-only — can view but not contribute',
};

export default function ShareModal({
  isOpen,
  onClose,
  resourceType,
  resourceId,
  resourceName,
  ownerUserId,
}: Props) {
  const {
    loadProjectMembers, addProjectMember, updateProjectMemberRole, removeProjectMember,
    loadChatMembers, addChatMember, updateChatMemberRole, removeChatMember,
  } = useChatStore();

  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [email, setEmail] = useState('');
  const [newRole, setNewRole] = useState<MemberRole>('viewer');
  const [isInviting, setIsInviting] = useState(false);

  const loadMembers = useCallback(async () => {
    if (!resourceId) return;
    setIsLoading(true);
    try {
      const data =
        resourceType === 'project'
          ? await loadProjectMembers(resourceId)
          : await loadChatMembers(resourceId);
      setMembers(data);
    } catch (err: any) {
      toast.error(`Failed to load members: ${err?.message ?? 'Unknown error'}`);
    } finally {
      setIsLoading(false);
    }
  }, [resourceId, resourceType, loadProjectMembers, loadChatMembers]);

  useEffect(() => {
    if (isOpen) {
      loadMembers();
    }
  }, [isOpen, loadMembers]);

  const handleInvite = async () => {
    if (!email.trim()) return;
    setIsInviting(true);
    try {
      const req: AddMemberRequest = { email: email.trim(), role: newRole };
      const member =
        resourceType === 'project'
          ? await addProjectMember(resourceId, req)
          : await addChatMember(resourceId, req);
      if (member.pending) {
        toast.success(`Invite sent to ${email}. They'll get access when they register.`);
      } else {
        toast.success(`${member.user_name} added as ${newRole}`);
      }
      setEmail('');
      await loadMembers();
    } catch (err: any) {
      toast.error(err?.message ?? 'Failed to invite user');
    } finally {
      setIsInviting(false);
    }
  };

  const handleRoleChange = async (member: ProjectMember, role: MemberRole) => {
    try {
      if (resourceType === 'project') {
        await updateProjectMemberRole(resourceId, member.user_id, { role });
      } else {
        await updateChatMemberRole(resourceId, member.user_id, { role });
      }
      toast.success('Role updated');
      await loadMembers();
    } catch (err: any) {
      toast.error(err?.message ?? 'Failed to update role');
    }
  };

  const handleRemove = async (member: ProjectMember) => {
    try {
      if (resourceType === 'project') {
        await removeProjectMember(resourceId, member.user_id);
      } else {
        await removeChatMember(resourceId, member.user_id);
      }
      toast.success(`${member.user_name} removed`);
      await loadMembers();
    } catch (err: any) {
      toast.error(err?.message ?? 'Failed to remove member');
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />

      {/* Modal */}
      <div className="relative z-10 w-full max-w-lg mx-4 bg-neutral-900 border border-neutral-700 rounded-2xl shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
          <div>
            <h2 className="text-base font-semibold text-neutral-100">Share "{resourceName}"</h2>
            <p className="text-xs text-neutral-500 mt-0.5">
              {resourceType === 'project' ? 'Invite people to collaborate on this project' : 'Invite people to this conversation'}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-neutral-500 hover:text-neutral-200 p-1 rounded-md transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Invite form */}
        <div className="px-6 py-4 border-b border-neutral-800">
          <div className="flex gap-2">
            <input
              type="email"
              placeholder="Enter email address"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleInvite()}
              className="flex-1 px-3 py-2 bg-neutral-800 border border-neutral-700 rounded-lg text-sm text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-primary"
            />
            <select
              value={newRole}
              onChange={(e) => setNewRole(e.target.value as MemberRole)}
              className="px-2 py-2 bg-neutral-800 border border-neutral-700 rounded-lg text-sm text-neutral-200 focus:outline-none focus:border-primary"
            >
              <option value="viewer">Viewer</option>
              <option value="editor">Editor</option>
            </select>
            <button
              onClick={handleInvite}
              disabled={isInviting || !email.trim()}
              className="px-4 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
            >
              {isInviting ? 'Inviting…' : 'Invite'}
            </button>
          </div>
          <div className="mt-2 flex gap-4">
            {(['viewer', 'editor'] as MemberRole[]).map((r) => (
              <div key={r} className={`text-xs ${newRole === r ? 'text-primary' : 'text-neutral-500'}`}>
                <span className="font-medium capitalize">{r}:</span>{' '}
                <span>{ROLE_DESCRIPTIONS[r]}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Members list */}
        <div className="px-6 py-4 max-h-72 overflow-y-auto">
          {isLoading ? (
            <div className="text-center text-neutral-500 text-sm py-4">Loading members…</div>
          ) : members.length === 0 ? (
            <div className="text-center text-neutral-500 text-sm py-4">
              No members yet. Invite someone above.
            </div>
          ) : (
            <ul className="space-y-3">
              {members.map((m) => {
                const isOwner = m.role === 'owner' || m.user_id === ownerUserId;
                return (
                  <li key={m.id} className="flex items-center gap-3">
                    {/* Avatar */}
                    <div className="w-8 h-8 rounded-full bg-neutral-700 flex items-center justify-center text-sm font-medium text-neutral-300 flex-shrink-0">
                      {(m.user_name || m.user_email)[0].toUpperCase()}
                    </div>
                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-neutral-200 truncate">
                        {m.user_name || m.user_email}
                        {m.pending && (
                          <span className="ml-2 text-xs text-amber-400 font-medium">(pending)</span>
                        )}
                      </div>
                      <div className="text-xs text-neutral-500 truncate">{m.user_email}</div>
                    </div>
                    {/* Role selector / badge */}
                    {isOwner ? (
                      <span className="text-xs text-neutral-400 font-medium px-2 py-0.5 bg-neutral-800 rounded-md">
                        Owner
                      </span>
                    ) : (
                      <select
                        value={m.role}
                        onChange={(e) => handleRoleChange(m, e.target.value as MemberRole)}
                        className="text-xs bg-neutral-800 border border-neutral-700 rounded-md px-2 py-1 text-neutral-300 focus:outline-none focus:border-primary"
                      >
                        <option value="viewer">Viewer</option>
                        <option value="editor">Editor</option>
                      </select>
                    )}
                    {/* Remove button */}
                    {!isOwner && (
                      <button
                        onClick={() => handleRemove(m)}
                        className="text-neutral-600 hover:text-red-400 transition-colors p-1 rounded"
                        title="Remove member"
                      >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-neutral-800 bg-neutral-900/50 rounded-b-2xl">
          <p className="text-xs text-neutral-600">
            Share links are disabled for enterprise security. Contact your admin to enable them.
          </p>
        </div>
      </div>
    </div>
  );
}
