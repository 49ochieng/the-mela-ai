/**
 * Mela AI - Project Workspace
 * Route: /projects/[projectId]
 * Three-tab workspace: Chats | Files | Instructions
 */

'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { useChatStore } from '@/lib/store';
import { api, ProjectFile, ProjectConversation } from '@/lib/api';
import { ChatSidebar } from '@/components/chat/ChatSidebar';
import ShareModal from '@/components/chat/ShareModal';
import { Button } from '@/components/ui/Button';
import { SettingsModal } from '@/components/settings/SettingsModal';
import { cn, formatRelativeTime, formatFileSize } from '@/lib/utils';
import { toast } from 'sonner';
import {
  MessageSquare,
  Upload,
  FileText,
  Trash2,
  Plus,
  Loader2,
  ChevronLeft,
  Settings,
  LogOut,
  Pencil,
  Check,
  BookOpen,
  FolderOpen,
  AlertCircle,
  Share2,
} from 'lucide-react';
import Image from 'next/image';
import { Avatar } from '@/components/ui/Avatar';

type Tab = 'chats' | 'files' | 'instructions';

export default function ProjectWorkspacePage() {
  const params = useParams();
  const projectId = params.projectId as string;
  const router = useRouter();
  const { instance, accounts } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const initializedRef = useRef(false);

  const {
    projects,
    currentProject,
    loadProject,
    loadProjects,
    setCurrentProject,
    setSettingsOpen,
    projectFiles,
    isLoadingProjectFiles,
    projectConversations,
    isLoadingProjectConversations,
    loadProjectFiles,
    uploadProjectFile,
    deleteProjectFile,
    loadProjectConversations,
    updateProjectInstructions,
    deleteConversation,
    loadConversation,
    startNewChat,
    moveConversationToProject,
    isLoadingProjects,
    fetchPreferences,
    fetchFeatures,
    startFeaturesPolling,
    stopFeaturesPolling,
    loadModels,
    loadConversations,
    activeProfile,
  } = useChatStore();

  const devUser = api.getDevUser();
  const isDevAuth = api.isDevAuthenticated();
  const msalUser = accounts[0];
  const user = devUser
    ? { name: devUser.name, username: devUser.email }
    : msalUser
    ? { name: msalUser.name || 'User', username: msalUser.username || msalUser.name || 'user' }
    : null;

  const [activeTab, setActiveTab] = useState<Tab>('chats');
  const [instructions, setInstructions] = useState('');
  const [isSavingInstructions, setIsSavingInstructions] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [uploadingFiles, setUploadingFiles] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Redirect if not authenticated
  useEffect(() => {
    if (!isAuthenticated && !isDevAuth) {
      router.push('/');
    }
  }, [isAuthenticated, isDevAuth, router]);

  // Initialize app data on first auth
  useEffect(() => {
    if ((!isAuthenticated && !isDevAuth) || initializedRef.current) return;
    initializedRef.current = true;

    if (isAuthenticated) api.setMsalInstance(instance);

    api.login()
      .then(() => {
        loadModels();
        loadConversations();
        loadProjects();
        fetchPreferences();
        // Trigger automatic OneDrive sync for the user
        api.syncOneDrive(false).catch((err) => {
          console.warn('[OneDrive] Auto-sync failed:', err);
        });
        return fetchFeatures();
      })
      .then(() => startFeaturesPolling())
      .catch(() => {
        loadModels();
        loadConversations();
        loadProjects();
        fetchPreferences();
        fetchFeatures().then(() => startFeaturesPolling());
      });
  }, [isAuthenticated, isDevAuth, instance, loadModels, loadConversations, loadProjects, fetchPreferences, fetchFeatures, startFeaturesPolling]);

  useEffect(() => {
    return () => { stopFeaturesPolling(); };
  }, [stopFeaturesPolling]);

  // Load project and its data on mount / projectId change
  useEffect(() => {
    if (!projectId) return;
    loadProject(projectId);
    loadProjectFiles(projectId);
    loadProjectConversations(projectId);
  }, [projectId, loadProject, loadProjectFiles, loadProjectConversations]);

  // Sync instructions textarea with loaded project
  useEffect(() => {
    if (currentProject?.id === projectId) {
      setInstructions(currentProject.system_prompt || '');
    }
  }, [currentProject, projectId]);

  const handleOpenChat = useCallback(
    async (conv: ProjectConversation) => {
      await loadConversation(conv.id);
      // Set currentProject so messages are routed to this project
      if (currentProject?.id !== projectId) {
        await loadProject(projectId);
      }
      router.push('/chat');
    },
    [loadConversation, loadProject, currentProject?.id, projectId, router],
  );

  const handleNewChat = useCallback(async () => {
    // Ensure currentProject is loaded so new chat inherits project context
    if (currentProject?.id !== projectId) {
      await loadProject(projectId);
    }
    startNewChat();
    router.push('/chat');
  }, [currentProject?.id, projectId, loadProject, startNewChat, router]);

  const handleDeleteConversation = async (convId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Delete this chat?')) return;
    await deleteConversation(convId);
    loadProjectConversations(projectId);
  };

  const handleMoveOutOfProject = async (convId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await moveConversationToProject(convId, null);
    loadProjectConversations(projectId);
    toast.success('Chat moved to All Chats');
  };

  // ── File upload ─────────────────────────────────────────────────────────────

  const handleFileDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const files = Array.from(e.dataTransfer.files);
      await uploadFiles(files);
    },
    // uploadFiles is a stable inline function; projectId is the real dep
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [projectId],
  );

  const handleFileInput = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || []);
      await uploadFiles(files);
      if (fileInputRef.current) fileInputRef.current.value = '';
    },
    // uploadFiles is a stable inline function; projectId is the real dep
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [projectId],
  );

  const uploadFiles = async (files: File[]) => {
    for (const file of files) {
      setUploadingFiles((prev) => [...prev, file.name]);
      try {
        await uploadProjectFile(projectId, file);
        toast.success(`"${file.name}" uploaded`);
      } catch (err: any) {
        toast.error(`Failed to upload "${file.name}": ${err?.message || 'Unknown error'}`);
      } finally {
        setUploadingFiles((prev) => prev.filter((n) => n !== file.name));
      }
    }
  };

  const handleDeleteFile = async (file: ProjectFile) => {
    if (!confirm(`Delete "${file.filename}"?`)) return;
    try {
      await deleteProjectFile(projectId, file.id);
      toast.success('File deleted');
    } catch {
      toast.error('Failed to delete file');
    }
  };

  // ── Instructions save ────────────────────────────────────────────────────────

  const handleSaveInstructions = async () => {
    setIsSavingInstructions(true);
    try {
      await updateProjectInstructions(projectId, instructions);
      toast.success('Instructions saved');
    } catch {
      toast.error('Failed to save instructions');
    } finally {
      setIsSavingInstructions(false);
    }
  };

  const handleLogout = async () => {
    await api.logout();
    if (isDevAuth) {
      api.clearDevAuth();
      router.push('/');
    } else {
      instance.logoutRedirect();
    }
  };

  if (!isAuthenticated && !isDevAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  const projectData = currentProject?.id === projectId ? currentProject : projects.find((p) => p.id === projectId);
  const projectName = projectData?.name || 'Project';
  const projectIcon = projectData?.icon || '📁';

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Sidebar */}
      <ChatSidebar onSettingsClick={() => setSettingsOpen(true)} />

      {/* Main workspace area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="flex items-center justify-between px-4 py-3 border-b bg-background shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => router.push('/chat')}
              className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
              title="Back to chats"
            >
              <ChevronLeft className="h-5 w-5" />
            </button>
            <span className="text-lg shrink-0">{projectIcon}</span>
            <h1 className="font-semibold text-lg truncate">{projectName}</h1>
            {isLoadingProjects && (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground shrink-0" />
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {/* Share only available in Work mode */}
            {activeProfile === 'org' && (
              <Button
                variant="ghost"
                size="sm"
                title="Share this project"
                onClick={() => setShareOpen(true)}
              >
                <Share2 className="h-4 w-4 mr-1.5" />
                <span className="hidden sm:inline text-xs">Share</span>
              </Button>
            )}
            <Button variant="ghost" size="sm" title="Settings" onClick={() => setSettingsOpen(true)}>
              <Settings className="h-4 w-4" />
            </Button>
            <div className="flex items-center gap-2">
              <Avatar src={null} alt={user?.name} size="sm" />
              <div className="hidden sm:block">
                <p className="text-sm font-medium leading-none">{user?.name}</p>
                <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-[160px]">
                  {user?.username}
                </p>
              </div>
            </div>
            <Button variant="ghost" size="icon" onClick={handleLogout} title="Sign out">
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </header>

        {/* Project Share Modal */}
        <ShareModal
          isOpen={shareOpen}
          onClose={() => setShareOpen(false)}
          resourceType="project"
          resourceId={projectId}
          resourceName={projectName}
        />

        {/* Project description */}
        {projectData?.description && (
          <div className="px-6 pt-4 pb-1">
            <p className="text-sm text-muted-foreground">{projectData.description}</p>
          </div>
        )}

        {/* Tabs */}
        <div className="border-b px-6">
          <div className="flex gap-1">
            {([
              { id: 'chats', label: 'Chats', icon: <MessageSquare className="h-4 w-4" /> },
              { id: 'files', label: 'Files', icon: <FileText className="h-4 w-4" /> },
              { id: 'instructions', label: 'Instructions', icon: <BookOpen className="h-4 w-4" /> },
            ] as const).map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
                  activeTab === tab.id
                    ? 'border-primary text-primary'
                    : 'border-transparent text-muted-foreground hover:text-foreground',
                )}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeTab === 'chats' && (
            <ChatsTab
              projectId={projectId}
              conversations={projectConversations}
              isLoading={isLoadingProjectConversations}
              onOpenChat={handleOpenChat}
              onNewChat={handleNewChat}
              onDeleteConversation={handleDeleteConversation}
              onMoveOut={handleMoveOutOfProject}
            />
          )}

          {activeTab === 'files' && (
            <FilesTab
              projectId={projectId}
              files={projectFiles}
              isLoading={isLoadingProjectFiles}
              uploadingFiles={uploadingFiles}
              isDragging={isDragging}
              fileInputRef={fileInputRef}
              onDrop={handleFileDrop}
              onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
              onDragLeave={() => setIsDragging(false)}
              onFileInput={handleFileInput}
              onDeleteFile={handleDeleteFile}
            />
          )}

          {activeTab === 'instructions' && (
            <InstructionsTab
              instructions={instructions}
              isSaving={isSavingInstructions}
              onChange={setInstructions}
              onSave={handleSaveInstructions}
            />
          )}
        </div>
      </div>

      <SettingsModal />
    </div>
  );
}

// ── Chats Tab ──────────────────────────────────────────────────────────────────

interface ChatsTabProps {
  projectId: string;
  conversations: ProjectConversation[];
  isLoading: boolean;
  onOpenChat: (conv: ProjectConversation) => void;
  onNewChat: () => void;
  onDeleteConversation: (id: string, e: React.MouseEvent) => void;
  onMoveOut: (id: string, e: React.MouseEvent) => void;
}

function ChatsTab({
  conversations,
  isLoading,
  onOpenChat,
  onNewChat,
  onDeleteConversation,
  onMoveOut,
}: ChatsTabProps) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const { updateConversation } = useChatStore();

  const startRename = (conv: ProjectConversation, e: React.MouseEvent) => {
    e.stopPropagation();
    setRenamingId(conv.id);
    setRenameValue(conv.title);
  };

  const commitRename = async (id: string) => {
    if (renameValue.trim()) {
      await updateConversation(id, { title: renameValue.trim() });
    }
    setRenamingId(null);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      {/* New Chat button */}
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">
          {conversations.length} chat{conversations.length !== 1 ? 's' : ''}
        </h2>
        <Button size="sm" onClick={onNewChat}>
          <Plus className="h-4 w-4 mr-1.5" />
          New Chat
        </Button>
      </div>

      {conversations.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center gap-3">
          <MessageSquare className="h-12 w-12 text-muted-foreground/30" />
          <div>
            <p className="text-sm font-medium text-muted-foreground">No chats yet</p>
            <p className="text-xs text-muted-foreground mt-1">
              Start a new chat — it will be saved to this project.
            </p>
          </div>
          <Button size="sm" onClick={onNewChat} className="mt-2">
            <Plus className="h-4 w-4 mr-1.5" />
            New Chat
          </Button>
        </div>
      ) : (
        <div className="space-y-1">
          {conversations.map((conv) => (
            <div
              key={conv.id}
              className="group flex items-center gap-3 px-4 py-3 rounded-xl border bg-card hover:bg-accent/50 cursor-pointer transition-colors"
              onClick={() => onOpenChat(conv)}
            >
              <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />

              <div className="flex-1 min-w-0">
                {renamingId === conv.id ? (
                  <input
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={() => commitRename(conv.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitRename(conv.id);
                      if (e.key === 'Escape') setRenamingId(null);
                    }}
                    onClick={(e) => e.stopPropagation()}
                    autoFocus
                    className="w-full bg-transparent border-b border-primary outline-none text-sm font-medium"
                  />
                ) : (
                  <p className="text-sm font-medium truncate">{conv.title}</p>
                )}
                <p className="text-xs text-muted-foreground mt-0.5">
                  {conv.message_count} message{conv.message_count !== 1 ? 's' : ''} ·{' '}
                  {formatRelativeTime(conv.updated_at)}
                </p>
              </div>

              <div className="opacity-0 group-hover:opacity-100 flex items-center gap-1 transition-opacity shrink-0">
                <button
                  onClick={(e) => startRename(conv, e)}
                  className="p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                  title="Rename"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={(e) => onMoveOut(conv.id, e)}
                  className="p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                  title="Remove from project"
                >
                  <FolderOpen className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={(e) => onDeleteConversation(conv.id, e)}
                  className="p-1.5 rounded hover:bg-destructive/10 hover:text-destructive transition-colors text-muted-foreground"
                  title="Delete"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Files Tab ──────────────────────────────────────────────────────────────────

interface FilesTabProps {
  projectId: string;
  files: ProjectFile[];
  isLoading: boolean;
  uploadingFiles: string[];
  isDragging: boolean;
  fileInputRef: React.RefObject<HTMLInputElement>;
  onDrop: (e: React.DragEvent) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onFileInput: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDeleteFile: (file: ProjectFile) => void;
}

function FilesTab({
  files,
  isLoading,
  uploadingFiles,
  isDragging,
  fileInputRef,
  onDrop,
  onDragOver,
  onDragLeave,
  onFileInput,
  onDeleteFile,
}: FilesTabProps) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      {/* Upload area */}
      <div
        className={cn(
          'border-2 border-dashed rounded-xl p-8 text-center transition-colors cursor-pointer',
          isDragging
            ? 'border-primary bg-primary/5'
            : 'border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/50',
        )}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onClick={() => fileInputRef.current?.click()}
      >
        <Upload className="h-8 w-8 mx-auto mb-3 text-muted-foreground" />
        <p className="text-sm font-medium text-muted-foreground">
          Drag & drop files here, or click to upload
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          PDF, Word, Excel, PowerPoint, images, code files, and more
        </p>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          multiple
          onChange={onFileInput}
          accept="*/*"
        />
      </div>

      {/* Uploading indicators */}
      {uploadingFiles.length > 0 && (
        <div className="space-y-2">
          {uploadingFiles.map((name) => (
            <div key={name} className="flex items-center gap-3 px-4 py-2 rounded-lg border bg-muted/50">
              <Loader2 className="h-4 w-4 animate-spin text-primary shrink-0" />
              <span className="text-sm truncate text-muted-foreground">Uploading {name}…</span>
            </div>
          ))}
        </div>
      )}

      {/* File count */}
      {files.length > 0 && (
        <p className="text-sm text-muted-foreground">
          {files.length} file{files.length !== 1 ? 's' : ''} in this project
        </p>
      )}

      {/* File list */}
      {files.length === 0 && uploadingFiles.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-center gap-2">
          <FileText className="h-10 w-10 text-muted-foreground/30" />
          <p className="text-sm text-muted-foreground">No files yet</p>
          <p className="text-xs text-muted-foreground">
            Upload files and they&apos;ll be available as context in all chats within this project.
          </p>
        </div>
      ) : (
        <div className="space-y-1">
          {files.map((file) => (
            <FileItem key={file.id} file={file} onDelete={onDeleteFile} />
          ))}
        </div>
      )}
    </div>
  );
}

function FileItem({ file, onDelete }: { file: ProjectFile; onDelete: (f: ProjectFile) => void }) {
  const ext = file.filename.split('.').pop()?.toLowerCase() || '';
  const iconColor =
    ['pdf'].includes(ext) ? 'text-red-500' :
    ['doc', 'docx'].includes(ext) ? 'text-blue-600' :
    ['xls', 'xlsx', 'csv'].includes(ext) ? 'text-green-600' :
    ['ppt', 'pptx'].includes(ext) ? 'text-orange-500' :
    ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext) ? 'text-purple-500' :
    'text-muted-foreground';

  return (
    <div className="group flex items-center gap-3 px-4 py-3 rounded-xl border bg-card hover:bg-accent/50 transition-colors">
      <FileText className={cn('h-5 w-5 shrink-0', iconColor)} />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{file.filename}</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {formatFileSize(file.file_size)} · {formatRelativeTime(file.created_at)}
        </p>
      </div>
      <button
        onClick={() => onDelete(file)}
        className="opacity-0 group-hover:opacity-100 p-1.5 rounded hover:bg-destructive/10 hover:text-destructive transition-all text-muted-foreground"
        title="Delete file"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ── Instructions Tab ────────────────────────────────────────────────────────────

interface InstructionsTabProps {
  instructions: string;
  isSaving: boolean;
  onChange: (value: string) => void;
  onSave: () => void;
}

function InstructionsTab({ instructions, isSaving, onChange, onSave }: InstructionsTabProps) {
  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <div>
        <h2 className="text-base font-semibold mb-1">Project Instructions</h2>
        <p className="text-sm text-muted-foreground">
          These instructions are injected into the system prompt for every chat in this project.
          Use them to set tone, persona, constraints, or domain context.
        </p>
      </div>

      <div className="rounded-xl border bg-card p-4 flex items-start gap-3">
        <AlertCircle className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
        <p className="text-xs text-muted-foreground">
          Instructions apply to <strong>all new messages</strong> in project chats. Existing messages
          are not affected. The AI will follow these instructions alongside its default behaviour.
        </p>
      </div>

      <div>
        <label className="block text-sm font-medium mb-2">Instructions</label>
        <textarea
          value={instructions}
          onChange={(e) => onChange(e.target.value)}
          placeholder={
            'Example:\n' +
            '- Always respond in formal British English.\n' +
            '- Focus on legal and compliance topics.\n' +
            '- Do not provide financial advice.\n' +
            '- When in doubt, ask for clarification before proceeding.'
          }
          rows={12}
          className="w-full rounded-xl border bg-background px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary resize-none font-mono"
        />
        <p className="text-xs text-muted-foreground mt-1">
          {instructions.length} character{instructions.length !== 1 ? 's' : ''}
        </p>
      </div>

      <div className="flex justify-end">
        <Button onClick={onSave} disabled={isSaving} size="sm">
          {isSaving ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Saving…
            </>
          ) : (
            <>
              <Check className="h-4 w-4 mr-2" />
              Save Instructions
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
