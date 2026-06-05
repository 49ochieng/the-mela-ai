/**
 * Mela AI - Chat Page with UUID-based Routing
 *
 * Loads a specific conversation by chat ID from the URL.
 * Supports direct linking: /chat/{uuid}
 */

'use client';

import { useEffect, useRef, useCallback, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { useChatStore } from '@/lib/store';
import { api, InlineAttachment } from '@/lib/api';
import { fetchGraphMe, resolveEmail, type GraphUserProfile } from '@/lib/graph';
import { ChatSidebar } from '@/components/chat/ChatSidebar';
import { ChatMessage } from '@/components/chat/ChatMessage';
import { ChatInput } from '@/components/chat/ChatInput';
import { VoiceChatOverlay } from '@/components/chat/VoiceChatOverlay';
import { TypingIndicator } from '@/components/chat/TypingIndicator';
import { ModelIndicator } from '@/components/chat/ModelIndicator';
import ShareModal from '@/components/chat/ShareModal';
import { NotificationCenter } from '@/components/chat/NotificationCenter';
import { BudgetWarningBanner } from '@/components/chat/BudgetWarningBanner';
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';
import { Button } from '@/components/ui/Button';
import { Avatar } from '@/components/ui/Avatar';
import { SettingsModal } from '@/components/settings/SettingsModal';
import {
  Settings,
  LogOut,
  Loader2,
  Share2,
  Pencil,
  Check,
  X,
} from 'lucide-react';
import Image from 'next/image';
import { toast } from 'sonner';

export default function ChatIdPage() {
  const router = useRouter();
  const params = useParams();
  const chatId = params.id as string;
  const { instance, accounts } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const initializedRef = useRef(false);
  const loadedChatIdRef = useRef<string | null>(null);
  const [isShareModalOpen, setIsShareModalOpen] = useState(false);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editedTitle, setEditedTitle] = useState('');
  const [graphProfile, setGraphProfile] = useState<GraphUserProfile | null>(null);

  const {
    messages,
    isStreaming,
    streamingContent,
    isLoadingConversation,
    loadModels,
    loadConversations,
    loadConversation,
    sendMessage,
    currentConversation,
    lastError,
    clearError,
    setSettingsOpen,
    userPreferences,
    fetchPreferences,
    fetchFeatures,
    startFeaturesPolling,
    stopFeaturesPolling,
    isPrivateMode,
    loadProjects,
    activeProfile,
    tenantId,
    voiceModeEnabled,
    setTenantId,
    updateConversation,
  } = useChatStore();

  // Sync API client profile context from persisted store state
  useEffect(() => {
    const profileMode = activeProfile === 'org' ? 'work' : 'personal';
    api.setProfileContext(profileMode, activeProfile === 'org' ? (tenantId ?? null) : null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const msalUser = accounts[0];

  const claims = msalUser?.idTokenClaims as Record<string, any> | undefined;
  const user = graphProfile
    ? {
        name: graphProfile.displayName || msalUser?.name || 'User',
        username: resolveEmail(graphProfile),
        givenName: graphProfile.givenName || graphProfile.displayName?.split(' ')[0],
        jobTitle: graphProfile.jobTitle ?? undefined,
        department: graphProfile.department ?? undefined,
      }
    : msalUser
    ? {
        name: msalUser.name || 'User',
        username: msalUser.username || msalUser.name || 'user',
        givenName: (claims?.given_name as string) || msalUser.name?.split(' ')[0],
        jobTitle: claims?.jobTitle as string | undefined,
        department: claims?.department as string | undefined,
      }
    : null;

  // Redirect if not authenticated
  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/');
    }
  }, [isAuthenticated, router]);

  // Initialize on first auth
  useEffect(() => {
    if (!isAuthenticated || initializedRef.current) return;
    initializedRef.current = true;

    if (isAuthenticated) {
      api.setMsalInstance(instance);

      const msalTenantId =
        (msalUser?.tenantId) ||
        (msalUser?.idTokenClaims?.tid as string | undefined) ||
        null;
      if (msalTenantId) {
        setTenantId(msalTenantId);
      }

      fetchGraphMe(instance).then(setGraphProfile).catch(() => {});
    }

    api
      .login()
      .then(() => {
        loadModels();
        loadConversations();
        loadProjects();
        fetchPreferences();
        return fetchFeatures();
      })
      .then(() => {
        startFeaturesPolling();
      })
      .catch(() => {
        loadModels();
        loadConversations();
        loadProjects();
        fetchPreferences().catch(() => {});
        fetchFeatures().then(() => startFeaturesPolling()).catch(() => {});
      });
  }, [isAuthenticated, instance, msalUser, loadModels, loadConversations, loadProjects, fetchPreferences, fetchFeatures, startFeaturesPolling, setTenantId]);

  // Load the specific conversation from URL when chatId changes
  useEffect(() => {
    if (!isAuthenticated || !chatId || loadedChatIdRef.current === chatId) return;
    loadedChatIdRef.current = chatId;
    loadConversation(chatId);
  }, [isAuthenticated, chatId, loadConversation]);

  // Stop features polling on unmount
  useEffect(() => {
    return () => { stopFeaturesPolling(); };
  }, [stopFeaturesPolling]);

  // Show errors as toasts
  useEffect(() => {
    if (lastError) {
      toast.error(lastError, {
        action: { label: 'Dismiss', onClick: clearError },
      });
    }
  }, [lastError, clearError]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent]);

  const handleSend = useCallback(
    async (message: string, attachments?: string[], inlineAttachments?: InlineAttachment[]) => {
      await sendMessage(message, attachments, inlineAttachments);
    },
    [sendMessage],
  );

  const handleLogout = async () => {
    await api.logout();
    instance.logoutRedirect();
  };

  const handleStartEditTitle = () => {
    setEditedTitle(currentConversation?.title || '');
    setIsEditingTitle(true);
  };

  const handleSaveTitle = async () => {
    if (currentConversation && editedTitle.trim()) {
      await updateConversation(currentConversation.id, { title: editedTitle.trim() });
    }
    setIsEditingTitle(false);
  };

  const handleCancelEditTitle = () => {
    setIsEditingTitle(false);
    setEditedTitle('');
  };

  if (!isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Sidebar */}
      <ChatSidebar onSettingsClick={() => setSettingsOpen(true)} />

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="flex items-center justify-between px-4 py-3 border-b bg-background shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <Image
              src="/mela-logo.png"
              alt="Mela AI"
              width={24}
              height={24}
              className="object-contain shrink-0"
            />
            {isEditingTitle ? (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={editedTitle}
                  onChange={(e) => setEditedTitle(e.target.value)}
                  className="px-2 py-1 text-sm border rounded bg-background"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleSaveTitle();
                    if (e.key === 'Escape') handleCancelEditTitle();
                  }}
                />
                <Button variant="ghost" size="icon" onClick={handleSaveTitle}>
                  <Check className="h-4 w-4 text-green-500" />
                </Button>
                <Button variant="ghost" size="icon" onClick={handleCancelEditTitle}>
                  <X className="h-4 w-4 text-red-500" />
                </Button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className="font-semibold truncate">
                  {isLoadingConversation
                    ? 'Loading…'
                    : currentConversation?.title || 'Chat'}
                </span>
                {currentConversation && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6"
                    onClick={handleStartEditTitle}
                    title="Edit title"
                  >
                    <Pencil className="h-3 w-3" />
                  </Button>
                )}
              </div>
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {activeProfile === 'org' && !isPrivateMode && currentConversation && (
              <Button
                variant="ghost"
                size="sm"
                title="Share conversation"
                onClick={() => setIsShareModalOpen(true)}
              >
                <Share2 className="h-4 w-4" />
                <span className="hidden sm:inline ml-1.5 text-xs">Share</span>
              </Button>
            )}
            <NotificationCenter />
            <Button variant="ghost" size="sm" title="Settings" onClick={() => setSettingsOpen(true)}>
              <Settings className="h-4 w-4" />
            </Button>
            <div className="flex items-center gap-2">
              <Avatar src={null} alt={user?.name} size="sm" />
              <div className="hidden sm:block">
                <p className="text-sm font-medium leading-none">{user?.name}</p>
                <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-[160px]">
                  {user?.jobTitle || user?.username}
                </p>
              </div>
            </div>
            <Button variant="ghost" size="icon" onClick={handleLogout} title="Sign out">
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </header>

        {/* Budget Warning Banner */}
        <BudgetWarningBanner />

        {/* Messages */}
        <div className="flex-1 overflow-y-auto relative">
          <ModelIndicator />
          {isLoadingConversation ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <div className="chat-messages-pane mx-auto pb-4">
              {messages.map((message, index) => (
                <ErrorBoundary key={index}>
                  <ChatMessage
                    message={message}
                    userName={user?.name}
                  />
                </ErrorBoundary>
              ))}

              {/* Typing indicator while waiting for first token */}
              {isStreaming && !streamingContent && <TypingIndicator />}

              {/* Streaming message */}
              {isStreaming && streamingContent && (
                <ChatMessage
                  message={{ role: 'assistant', content: streamingContent }}
                  isStreaming
                />
              )}

              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* Input */}
        <div className="chat-messages-pane mx-auto w-full shrink-0 relative">
          <ChatInput onSend={handleSend} />
        </div>
      </div>

      {/* Voice Chat Overlay */}
      {voiceModeEnabled && <VoiceChatOverlay />}

      {/* Share Modal */}
      {currentConversation && (
        <ShareModal
          isOpen={isShareModalOpen}
          onClose={() => setIsShareModalOpen(false)}
          resourceType="chat"
          resourceId={currentConversation.id}
          resourceName={currentConversation.title || 'Chat'}
        />
      )}

      {/* Settings Modal */}
      <SettingsModal />
    </div>
  );
}
