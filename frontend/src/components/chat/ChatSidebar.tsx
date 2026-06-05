/**
 * Mela AI - Chat Sidebar
 */

'use client';

import { useState, useMemo, useRef, useEffect } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { cn, formatRelativeTime } from '@/lib/utils';
import { useChatStore, ProfileType } from '@/lib/store';
import { Conversation, Project } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { ProjectModal } from '@/components/chat/ProjectModal';
import { ProjectMemoryPanel } from '@/components/chat/ProjectMemoryPanel';
import ShareModal from '@/components/chat/ShareModal';
import {
  Plus,
  MessageSquare,
  Trash2,
  Settings,
  Search,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Lock,
  FolderOpen,
  FolderPlus,
  Pencil,
  X,
  Check,
  Users,
  Building2,
  User,
  Shield,
} from 'lucide-react';
import Image from 'next/image';

interface ChatSidebarProps {
  onSettingsClick?: () => void;
}

// Group conversations by date bucket
type DateGroup = 'Today' | 'Yesterday' | 'This week' | 'Last month' | 'Older';

function getDateGroup(dateStr: string): DateGroup {
  const now = new Date();
  const d = new Date(dateStr);
  const diffMs = now.getTime() - d.getTime();
  const diffDays = diffMs / (1000 * 60 * 60 * 24);

  if (diffDays < 1) return 'Today';
  if (diffDays < 2) return 'Yesterday';
  if (diffDays < 7) return 'This week';
  if (diffDays < 30) return 'Last month';
  return 'Older';
}

const GROUP_ORDER: DateGroup[] = ['Today', 'Yesterday', 'This week', 'Last month', 'Older'];

// Map color names to Tailwind classes
const PROJECT_COLOR_MAP: Record<string, string> = {
  violet: 'bg-violet-500',
  blue: 'bg-blue-500',
  green: 'bg-green-500',
  amber: 'bg-amber-500',
  rose: 'bg-rose-500',
  slate: 'bg-slate-500',
};

// ── Sidebar profile switcher (lives inside sidebar) ────────────────────────────

function SidebarProfileSwitcher() {
  const { activeProfile, setActiveProfile } = useChatStore();
  return (
    <div className="flex items-center rounded-lg bg-muted/60 p-0.5 gap-0.5">
      <button
        onClick={() => activeProfile !== 'personal' && setActiveProfile('personal')}
        className={cn(
          'flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all',
          activeProfile === 'personal'
            ? 'bg-background text-foreground shadow-sm'
            : 'text-muted-foreground hover:text-foreground',
        )}
      >
        <User className="h-3 w-3" />
        Personal
      </button>
      <button
        onClick={() => activeProfile !== 'org' && setActiveProfile('org')}
        className={cn(
          'flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all',
          activeProfile === 'org'
            ? 'bg-background text-foreground shadow-sm'
            : 'text-muted-foreground hover:text-foreground',
        )}
      >
        <Building2 className="h-3 w-3" />
        Work
      </button>
    </div>
  );
}

export function ChatSidebar({ onSettingsClick }: ChatSidebarProps) {
  const router = useRouter();
  const pathname = usePathname();

  const {
    conversations,
    currentConversation,
    isLoadingConversations,
    isLoadingConversation,
    isSidebarOpen,
    loadConversation,
    deleteConversation,
    startNewChat,
    toggleSidebar,
    isPrivateMode,
    projects,
    currentProject,
    isLoadingProjects,
    loadProject,
    setCurrentProject,
    deleteProject,
    moveConversationToProject,
    activeProfile,
    sharedWithMe,
    sharedByMe,
    isLoadingShared,
  } = useChatStore();

  const [searchQuery, setSearchQuery] = useState('');
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [editingProject, setEditingProject] = useState<Project | null>(null);

  // Share modal state — opened from conversation hover actions
  const [shareConv, setShareConv] = useState<Conversation | null>(null);

  // Are we currently on a project workspace page?
  const isOnProjectPage = pathname?.startsWith('/projects/');

  // Defensive filter: only show conversations matching the active profile.
  // 'org' and 'work' are treated as the same namespace — 'org' is the legacy
  // frontend alias; the backend now stores 'work' as the canonical value.
  // Accept both so historically-stored ('org') and new ('work') entries both show.
  const profileConversations = useMemo(() => {
    if (activeProfile === 'personal') {
      return conversations.filter((c) => !c.context_type || c.context_type === 'personal');
    } else {
      // Work profile: match 'org' (legacy frontend alias) OR 'work' (backend canonical)
      return conversations.filter(
        (c) => !c.context_type || c.context_type === 'org' || c.context_type === 'work',
      );
    }
  }, [conversations, activeProfile]);

  // When on a project page, show all chats; when a project is selected on /chat, filter to that project
  const filteredByProject = useMemo(() => {
    if (isOnProjectPage || !currentProject) return profileConversations;
    return profileConversations.filter((c) => c.project_id === currentProject.id);
  }, [profileConversations, currentProject, isOnProjectPage]);

  // Filter + group by date
  const grouped = useMemo(() => {
    const filtered = searchQuery.trim()
      ? filteredByProject.filter((c) =>
          c.title.toLowerCase().includes(searchQuery.toLowerCase()),
        )
      : filteredByProject;

    const map = new Map<DateGroup, Conversation[]>();
    for (const group of GROUP_ORDER) map.set(group, []);

    for (const conv of filtered) {
      const group = getDateGroup(conv.updated_at);
      map.get(group)!.push(conv);
    }

    return GROUP_ORDER.filter((g) => map.get(g)!.length > 0).map((g) => ({
      label: g,
      items: map.get(g)!,
    }));
  }, [filteredByProject, searchQuery]);

  const handleProjectClick = async (project: Project) => {
    await loadProject(project.id);
    router.push(`/projects/${project.id}`);
  };

  // Active project id = from URL (project pages) or from selected project on /chat
  const activeProjectId = pathname?.startsWith('/projects/')
    ? pathname.split('/')[2]
    : currentProject?.id;

  const handleEditProject = (e: React.MouseEvent, project: Project) => {
    e.stopPropagation();
    setEditingProject(project);
    setShowProjectModal(true);
  };

  const handleDeleteProject = async (e: React.MouseEvent, project: Project) => {
    e.stopPropagation();
    if (!confirm(`Delete project "${project.name}"? Conversations will not be deleted.`)) return;
    await deleteProject(project.id);
  };

  /**
   * Navigate to a conversation. If we're on a project page, also navigate
   * to /chat so the conversation is rendered there.
   */
  const handleConversationClick = (conv: Conversation) => {
    loadConversation(conv.id);
    if (isOnProjectPage) {
      // Clear the project filter so the sidebar shows all chats
      setCurrentProject(null);
      router.push('/chat');
    }
  };

  // ── Collapsed sidebar ───────────────────────────────────────────────────────

  if (!isSidebarOpen) {
    return (
      <div className="flex flex-col items-center py-4 px-2 border-r bg-card gap-3">
        <Button variant="ghost" size="icon" onClick={toggleSidebar} title="Expand sidebar">
          <ChevronRight className="h-5 w-5" />
        </Button>
        <Button variant="ghost" size="icon" onClick={startNewChat} title="New chat">
          <Plus className="h-5 w-5" />
        </Button>
      </div>
    );
  }

  // ── Full sidebar ────────────────────────────────────────────────────────────

  return (
    <>
      <div className="flex flex-col w-72 border-r bg-card h-full">

        {/* ── Top header: logo + collapse ──────────────────────────────────── */}
        <div className="p-4 pb-3 border-b">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-white border border-gray-200 flex items-center justify-center overflow-hidden shadow-sm">
                <Image src="/mela-logo.png" alt="Mela AI" width={24} height={24} className="object-contain" />
              </div>
              <span className="font-semibold text-lg">Mela AI</span>
            </div>
            <Button variant="ghost" size="icon" onClick={toggleSidebar} title="Collapse sidebar">
              <ChevronLeft className="h-5 w-5" />
            </Button>
          </div>

          {/* ── Profile switcher ─────────────────────────────────────────── */}
          <SidebarProfileSwitcher />
        </div>

        {/* ── New chat button ───────────────────────────────────────────────── */}
        <div className="px-4 py-3 border-b">
          <Button
            onClick={startNewChat}
            className={cn(
              'w-full',
              isPrivateMode
                ? 'bg-violet-600 hover:bg-violet-700 text-white'
                : 'bg-primary hover:bg-primary/90',
            )}
          >
            {isPrivateMode ? (
              <>
                <Lock className="h-4 w-4 mr-2" />
                New Private Chat
              </>
            ) : (
              <>
                <Plus className="h-4 w-4 mr-2" />
                New Chat
              </>
            )}
          </Button>
        </div>

        {/* ── Search ───────────────────────────────────────────────────────── */}
        <div className="px-3 py-2 border-b">
          <div className="flex items-center gap-2 px-3 py-1.5 bg-muted rounded-lg text-muted-foreground text-sm">
            <Search className="h-4 w-4 shrink-0" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search conversations…"
              className="bg-transparent outline-none flex-1 min-w-0 text-foreground placeholder:text-muted-foreground"
            />
          </div>
        </div>

        {/* ── Projects section ──────────────────────────────────────────────── */}
        <div className="border-b">
          <div className="flex items-center justify-between px-4 pt-3 pb-1">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              Projects
            </span>
            <button
              onClick={() => { setEditingProject(null); setShowProjectModal(true); }}
              className="text-muted-foreground hover:text-foreground transition-colors"
              title="New project"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </div>

          {isLoadingProjects ? (
            <div className="px-4 py-2">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          ) : projects.length === 0 ? (
            <p className="px-4 pb-3 text-xs text-muted-foreground italic">No projects yet</p>
          ) : (
            <div className="px-2 pb-2 space-y-0.5">
              {projects.map((proj) => {
                const isActive = activeProjectId === proj.id;
                return (
                  <div
                    key={proj.id}
                    className={cn(
                      'group flex items-center gap-2 px-3 py-1.5 rounded-lg cursor-pointer transition-colors text-sm',
                      isActive ? 'bg-primary/10 text-primary' : 'hover:bg-muted',
                    )}
                    onClick={() => handleProjectClick(proj)}
                  >
                    <span className="text-base shrink-0">{proj.icon || '📁'}</span>
                    <span className="flex-1 truncate font-medium">{proj.name}</span>
                    {proj.conversation_count > 0 && (
                      <span className="text-[10px] text-muted-foreground shrink-0">
                        {proj.conversation_count}
                      </span>
                    )}
                    <div className="hidden group-hover:flex items-center gap-1">
                      <button
                        onClick={(e) => handleEditProject(e, proj)}
                        className="p-0.5 rounded hover:bg-muted-foreground/20 transition-colors"
                        title="Edit project"
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                      <button
                        onClick={(e) => handleDeleteProject(e, proj)}
                        className="p-0.5 rounded hover:text-destructive transition-colors"
                        title="Delete project"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* ── Conversation list ─────────────────────────────────────────────── */}
        <div className="flex-1 overflow-y-auto">
          {/* Private session banner */}
          {isPrivateMode && currentConversation && (
            <div className="mx-2 mt-2 px-3 py-2 rounded-lg border border-violet-200 dark:border-violet-800 bg-violet-50 dark:bg-violet-950/30">
              <div className="flex items-center gap-2">
                <Lock className="h-4 w-4 text-violet-600 dark:text-violet-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate text-violet-700 dark:text-violet-300">
                    {currentConversation.title || 'Private Chat'}
                  </p>
                  <p className="text-xs text-violet-500">Active private session</p>
                </div>
              </div>
            </div>
          )}

          {/* Section header — shows project name filter or "All Chats" */}
          <div className="flex items-center justify-between px-4 pt-3 pb-1">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
              {currentProject && !isOnProjectPage ? (
                <>
                  <FolderOpen className="h-3 w-3" />
                  {currentProject.name}
                </>
              ) : (
                'All Chats'
              )}
            </span>
            {/* Clear project filter while on /chat */}
            {currentProject && !isOnProjectPage && (
              <button
                onClick={() => setCurrentProject(null)}
                className="text-muted-foreground hover:text-foreground transition-colors"
                title="Show all chats"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {/* Project memory panel */}
          {currentProject && !isOnProjectPage && <ProjectMemoryPanel />}

          {/* Back-to-chat hint when browsing project page */}
          {isOnProjectPage && (
            <p className="px-4 pb-1 text-[11px] text-muted-foreground/60 italic">
              Click any chat to open it
            </p>
          )}

          {isLoadingConversations ? (
            <div className="flex items-center justify-center p-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : grouped.length === 0 ? (
            <div className="flex flex-col items-center justify-center p-8 text-center gap-3">
              <MessageSquare className="h-10 w-10 text-muted-foreground/40" />
              <div>
                <p className="text-sm text-muted-foreground font-medium">
                  {searchQuery
                    ? 'No results found'
                    : currentProject
                    ? 'No chats in this project'
                    : 'No conversations yet'}
                </p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {searchQuery
                    ? 'Try a different search'
                    : currentProject
                    ? 'Start a new chat — it will be added to this project'
                    : 'Start a new chat to begin'}
                </p>
              </div>
            </div>
          ) : (
            <div className="p-2">
              {grouped.map(({ label, items }) => (
                <div key={label} className="mb-3">
                  <p className="px-3 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    {label}
                  </p>
                  <div className="space-y-0.5">
                    {items.map((conversation) => (
                      <ConversationItem
                        key={conversation.id}
                        conversation={conversation}
                        isActive={!isOnProjectPage && currentConversation?.id === conversation.id}
                        isLoading={
                          isLoadingConversation && currentConversation?.id === conversation.id
                        }
                        projects={projects}
                        onClick={() => handleConversationClick(conversation)}
                        onDelete={() => deleteConversation(conversation.id)}
                        onMove={(projectId) => moveConversationToProject(conversation.id, projectId)}
                        onShare={() => setShareConv(conversation)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── Shared sections (Work mode only) ─────────────────────────────── */}
        {activeProfile === 'org' && (sharedWithMe.length > 0 || sharedByMe.length > 0 || isLoadingShared) && (
          <div className="border-t">
            <SharedSection
              label="Shared with Me"
              items={sharedWithMe}
              isLoading={isLoadingShared}
              currentConversationId={currentConversation?.id}
              projects={projects}
              onClickConversation={handleConversationClick}
              onDelete={(id) => deleteConversation(id)}
              onMove={(id, projectId) => moveConversationToProject(id, projectId)}
              onShare={(conv) => setShareConv(conv)}
            />
            {sharedByMe.length > 0 && (
              <SharedSection
                label="Shared by Me"
                items={sharedByMe}
                isLoading={false}
                currentConversationId={currentConversation?.id}
                projects={projects}
                onClickConversation={handleConversationClick}
                onDelete={(id) => deleteConversation(id)}
                onMove={(id, projectId) => moveConversationToProject(id, projectId)}
                onShare={(conv) => setShareConv(conv)}
              />
            )}
          </div>
        )}

        {/* ── Footer ────────────────────────────────────────────────────────── */}
        <div className="p-3 border-t space-y-1">
          <Button
            variant="ghost"
            className="w-full justify-start text-muted-foreground hover:text-foreground"
            onClick={onSettingsClick}
          >
            <Settings className="h-4 w-4 mr-2" />
            Settings
          </Button>
          <Button
            variant="ghost"
            className="w-full justify-start text-muted-foreground hover:text-foreground"
            onClick={() => router.push('/admin')}
          >
            <Shield className="h-4 w-4 mr-2" />
            Admin
          </Button>
        </div>
      </div>

      {/* Project modal */}
      <ProjectModal
        open={showProjectModal}
        onClose={() => { setShowProjectModal(false); setEditingProject(null); }}
        project={editingProject || undefined}
        onSaved={() => {
          setShowProjectModal(false);
          setEditingProject(null);
        }}
      />

      {/* Share modal — opened from conversation hover action */}
      {shareConv && (
        <ShareModal
          isOpen={!!shareConv}
          onClose={() => setShareConv(null)}
          resourceType="chat"
          resourceId={shareConv.id}
          resourceName={shareConv.title}
        />
      )}
    </>
  );
}

// ── Shared section ────────────────────────────────────────────────────────────

interface SharedSectionProps {
  label: string;
  items: Conversation[];
  isLoading: boolean;
  currentConversationId?: string;
  projects: Project[];
  onClickConversation: (conv: Conversation) => void;
  onDelete: (id: string) => void;
  onMove: (id: string, projectId: string | null) => void;
  onShare: (conv: Conversation) => void;
}

function SharedSection({
  label,
  items,
  isLoading,
  currentConversationId,
  projects,
  onClickConversation,
  onDelete,
  onMove,
  onShare,
}: SharedSectionProps) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="px-2 py-2">
      <button
        className="flex items-center gap-1.5 w-full px-2 py-0.5 mb-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors"
        onClick={() => setCollapsed((v) => !v)}
      >
        <Users className="h-3 w-3 shrink-0" />
        <span className="flex-1 text-left">{label}</span>
        {items.length > 0 && (
          <span className="text-[10px] font-normal normal-case tracking-normal">{items.length}</span>
        )}
        <ChevronRight className={cn('h-3 w-3 shrink-0 transition-transform', !collapsed && 'rotate-90')} />
      </button>

      {!collapsed && (
        isLoading ? (
          <div className="px-3 py-2">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : items.length === 0 ? (
          <p className="px-3 py-1 text-xs text-muted-foreground/60 italic">None yet</p>
        ) : (
          <div className="space-y-0.5">
            {items.map((conv) => (
              <ConversationItem
                key={conv.id}
                conversation={conv}
                isActive={currentConversationId === conv.id}
                projects={projects}
                onClick={() => onClickConversation(conv)}
                onDelete={() => onDelete(conv.id)}
                onMove={(projectId) => onMove(conv.id, projectId)}
                onShare={() => onShare(conv)}
                showWorkspaceBadge
              />
            ))}
          </div>
        )
      )}
    </div>
  );
}

// ── Conversation item ──────────────────────────────────────────────────────────

interface ConversationItemProps {
  conversation: Conversation;
  isActive: boolean;
  isLoading?: boolean;
  projects: Project[];
  onClick: () => void;
  onDelete: () => void;
  onMove: (projectId: string | null) => void;
  onShare: () => void;
  showWorkspaceBadge?: boolean;
}

function ConversationItem({
  conversation,
  isActive,
  isLoading,
  projects,
  onClick,
  onDelete,
  onMove,
  onShare,
  showWorkspaceBadge,
}: ConversationItemProps) {
  const { activeProfile } = useChatStore();
  const [showProjectPicker, setShowProjectPicker] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  // Close picker on outside click
  useEffect(() => {
    if (!showProjectPicker) return;
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setShowProjectPicker(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showProjectPicker]);

  const isPrivate = conversation.is_private;

  return (
    <div
      className={cn(
        'group relative flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors',
        isActive ? 'bg-primary/10 text-primary' : 'hover:bg-muted',
      )}
      onClick={onClick}
    >
      {isLoading ? (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
      ) : isPrivate ? (
        <Lock className="h-4 w-4 shrink-0 text-violet-500" />
      ) : (
        <MessageSquare className="h-4 w-4 shrink-0" />
      )}

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{conversation.title}</p>
        <div className="flex items-center gap-1.5">
          <p className="text-xs text-muted-foreground">
            {formatRelativeTime(conversation.updated_at)}
          </p>
          {showWorkspaceBadge && conversation.context_type && (
            <span className={cn(
              'text-[10px] px-1 py-0 rounded font-medium leading-4',
              conversation.context_type === 'org'
                ? 'bg-blue-500/10 text-blue-600 dark:text-blue-400'
                : 'bg-muted text-muted-foreground',
            )}>
              {conversation.context_type === 'org' ? 'Work' : 'Personal'}
            </span>
          )}
        </div>
      </div>

      {/* Action buttons — visible on hover */}
      <div className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 transition-opacity">
        {/* Share — only in Work mode and for non-private chats */}
        {activeProfile === 'org' && !isPrivate && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onShare();
            }}
            className="p-1 rounded hover:bg-blue-500/10 hover:text-blue-500 transition-colors"
            title="Share / invite collaborators"
          >
            <Users className="h-3.5 w-3.5" />
          </button>
        )}

        {/* Move to project */}
        {projects.length > 0 && !isPrivate && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setShowProjectPicker((v) => !v);
            }}
            className="p-1 rounded hover:bg-primary/10 hover:text-primary transition-colors"
            title="Move to project"
          >
            <FolderPlus className="h-3.5 w-3.5" />
          </button>
        )}

        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className="p-1 hover:bg-destructive/10 hover:text-destructive rounded transition-colors"
          title="Delete conversation"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Project picker dropdown */}
      {showProjectPicker && (
        <div
          ref={pickerRef}
          className="absolute right-0 top-full mt-1 z-50 w-48 bg-popover border rounded-lg shadow-lg py-1 text-sm"
          onClick={(e) => e.stopPropagation()}
        >
          <p className="px-3 py-1 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
            Move to project
          </p>
          <button
            className={cn(
              'w-full flex items-center gap-2 px-3 py-1.5 hover:bg-muted transition-colors text-left',
              !conversation.project_id && 'text-primary font-medium',
            )}
            onClick={() => {
              onMove(null);
              setShowProjectPicker(false);
            }}
          >
            {!conversation.project_id && <Check className="h-3 w-3 shrink-0" />}
            <span className={cn(!conversation.project_id ? '' : 'pl-5')}>No project</span>
          </button>
          {projects.map((proj) => (
            <button
              key={proj.id}
              className={cn(
                'w-full flex items-center gap-2 px-3 py-1.5 hover:bg-muted transition-colors text-left',
                conversation.project_id === proj.id && 'text-primary font-medium',
              )}
              onClick={() => {
                onMove(proj.id);
                setShowProjectPicker(false);
              }}
            >
              {conversation.project_id === proj.id ? (
                <Check className="h-3 w-3 shrink-0" />
              ) : (
                <span className="w-3 shrink-0">{proj.icon || '📁'}</span>
              )}
              <span className="truncate">{proj.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
