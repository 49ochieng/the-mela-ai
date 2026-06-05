/**
 * Mela AI - Chat Page
 */

'use client';

import { useEffect, useRef, useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { useChatStore } from '@/lib/store';
import { api, InlineAttachment } from '@/lib/api';
import { fetchGraphMe, resolveEmail, type GraphUserProfile } from '@/lib/graph';
import { ChatSidebar } from '@/components/chat/ChatSidebar';
import { ChatMessage } from '@/components/chat/ChatMessage';
import { ChatInput } from '@/components/chat/ChatInput';
import { WorkerEventBar } from '@/components/chat/WorkerEventBar';
import { VoiceChatOverlay } from '@/components/chat/VoiceChatOverlay';
import { TypingIndicator } from '@/components/chat/TypingIndicator';
import { ModelIndicator } from '@/components/chat/ModelIndicator';
import { ModelInsightsPanel } from '@/components/chat/ModelInsightsPanel';
import ShareModal from '@/components/chat/ShareModal';
import { NotificationCenter } from '@/components/chat/NotificationCenter';
import { BudgetWarningBanner } from '@/components/chat/BudgetWarningBanner';
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';
import { Button } from '@/components/ui/Button';
import { Avatar } from '@/components/ui/Avatar';
import { SettingsModal } from '@/components/settings/SettingsModal';
import {
  Sparkles,
  FileText,
  Calendar,
  Mail,
  ListTodo,
  LogOut,
  Settings,
  Loader2,
  ImageIcon,
  Mic,
  Share2,
} from 'lucide-react';
import Image from 'next/image';
import { toast } from 'sonner';

export default function ChatPage() {
  const router = useRouter();
  const { instance, accounts } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const initializedRef = useRef(false);
  const [isShareModalOpen, setIsShareModalOpen] = useState(false);
  // Graph /me profile — fetched after MSAL sign-in via the login app registration.
  // Provides richer data than ID token claims (e.g. department, verified mail).
  const [graphProfile, setGraphProfile] = useState<GraphUserProfile | null>(null);

  const {
    messages,
    isStreaming,
    streamingContent,
    isNewChat,
    isLoadingConversation,
    loadModels,
    loadConversations,
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
    startPrivateChat,
    loadProjects,
    activeProfile,
    tenantId,
    voiceModeEnabled,
    setTenantId,
  } = useChatStore();

  // Sync API client profile context from persisted store state (runs once on mount).
  // Without this the api._profileMode would default to 'personal' every page load,
  // ignoring the persisted activeProfile / tenantId.
  useEffect(() => {
    const profileMode = activeProfile === 'org' ? 'work' : 'personal';
    api.setProfileContext(profileMode, activeProfile === 'org' ? (tenantId ?? null) : null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally run only once on mount

  const msalUser = accounts[0];
  // Dev auth removed — Microsoft sign-in only

  // Build the user object shown in the header and welcome screen.
  // Priority: Graph /me (richest, delegated) → ID token claims → MSAL account.
  const claims = msalUser?.idTokenClaims as Record<string, any> | undefined;
  const user = graphProfile
    ? {
        name: graphProfile.displayName || msalUser?.name || 'User',
        // mail may be null; resolveEmail falls back to userPrincipalName.
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

      // Persist the real Entra tenant ID from the MSAL account so work-profile
      // requests send the correct X-Tenant-Id header (not just the dev sentinel).
      // AccountInfo.tenantId is the home-tenant GUID, always populated on sign-in.
      const msalTenantId =
        (msalUser?.tenantId) ||
        (msalUser?.idTokenClaims?.tid as string | undefined) ||
        null;
      if (msalTenantId) {
        setTenantId(msalTenantId);
      }

      // Fetch delegated Graph /me via the login app registration.
      // Runs once; result enriches the user display in the header.
      fetchGraphMe(instance).then(setGraphProfile).catch(() => {});
    }

    api
      .login()
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
      .then(() => {
        startFeaturesPolling();
      })
      .catch(() => {
        // Login may fail if backend auth endpoint is not configured -
        // still load conversations via the stored token
        loadModels();
        loadConversations();
        loadProjects();
        fetchPreferences().catch(() => {});
        fetchFeatures().then(() => startFeaturesPolling()).catch(() => {});
      });
  }, [isAuthenticated, instance, msalUser, loadModels, loadConversations, loadProjects, fetchPreferences, fetchFeatures, startFeaturesPolling, setTenantId]);

  // Check if this user was recently promoted to admin — show a one-time banner.
  useEffect(() => {
    if (!isAuthenticated) return;
    api.getAdminStatus().then((status) => {
      if (status.newly_promoted) {
        toast.success('You have been promoted to Admin!', {
          description: 'You now have access to the Admin Console. Go to /admin to manage users and settings.',
          duration: 10000,
          action: {
            label: 'Open Admin',
            onClick: () => router.push('/admin'),
          },
        });
        api.ackAdminPromotion().catch(() => {});
      }
    }).catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated]);

  // Stop features polling when the chat page unmounts
  useEffect(() => {
    return () => { stopFeaturesPolling(); };
  }, [stopFeaturesPolling]);

  // Auto-activate private mode when user preference says so
  useEffect(() => {
    if (userPreferences?.default_private_mode && isNewChat && !isPrivateMode) {
      startPrivateChat();
    }
  }, [userPreferences?.default_private_mode, isNewChat, isPrivateMode, startPrivateChat]);

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
            <span className="font-semibold truncate">
              {isLoadingConversation
                ? 'Loading…'
                : currentConversation?.title || 'New Chat'}
            </span>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {activeProfile === 'work' && !isPrivateMode && currentConversation && (
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
          ) : messages.length === 0 && isNewChat ? (
            <WelcomeScreen userName={user?.givenName || user?.name} onSuggestionClick={handleSend} />
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
          {/* Phase 5A: live worker activity banners — absolutely positioned above the input. */}
          <WorkerEventBar />
          <ChatInput onSend={handleSend} />
        </div>
      </div>

      {/* Voice Chat Overlay */}
      {voiceModeEnabled && <VoiceChatOverlay />}

      {/* Share Modal — Work profile only */}
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

// ── Welcome screen ─────────────────────────────────────────────────────────────

interface WelcomeScreenProps {
  userName?: string;
  onSuggestionClick: (message: string) => void;
}

function getGreeting(): string {
  const hour = new Date().getHours(); // user's local time
  if (hour >= 5 && hour < 12) return 'Good morning';
  if (hour >= 12 && hour < 17) return 'Good afternoon';
  return 'Good evening';
}

function WelcomeScreen({ userName, onSuggestionClick }: WelcomeScreenProps) {
  // userName is already the given name when available
  const firstName = userName?.split(' ')[0];
  const [greeting, setGreeting] = useState(getGreeting);

  // Keep the greeting current if the user leaves the tab open across time boundaries
  useEffect(() => {
    const id = setInterval(() => setGreeting(getGreeting()), 60_000);
    return () => clearInterval(id);
  }, []);

  const suggestions = [
    {
      icon: <ImageIcon className="h-5 w-5" />,
      title: 'Generate an image',
      description: 'Create visuals with DALL-E',
      prompt: 'Generate an image of a futuristic city at sunset',
    },
    {
      icon: <FileText className="h-5 w-5" />,
      title: 'Analyze a document',
      description: 'Upload and ask questions about any file',
      prompt: 'I have a document I want to analyze. How do I attach it?',
    },
    {
      icon: <Mic className="h-5 w-5" />,
      title: 'Voice input',
      description: 'Speak instead of type',
      prompt: 'How do I use voice input with Mela AI?',
    },
    {
      icon: <Mail className="h-5 w-5" />,
      title: 'Draft an email',
      description: 'Write professional messages fast',
      prompt: 'Help me draft a follow-up email after a client meeting',
    },
    {
      icon: <Calendar className="h-5 w-5" />,
      title: 'Schedule a meeting',
      description: 'Manage your calendar with AI',
      prompt: 'Schedule a 30-minute team standup for next Monday at 9 AM',
    },
    {
      icon: <ListTodo className="h-5 w-5" />,
      title: 'Create a task',
      description: 'Add items to your Planner',
      prompt: 'Create a task to review the quarterly report by end of this week',
    },
  ];

  return (
    <div className="flex flex-col items-center justify-center min-h-full p-8">
      <div className="text-center w-full max-w-4xl">
        {/* Logo */}
        <div className="w-20 h-20 rounded-2xl bg-white border border-gray-200 shadow-sm flex items-center justify-center mx-auto mb-6 overflow-hidden">
          <Image src="/mela-logo.png" alt="Mela AI" width={56} height={56} className="object-contain" />
        </div>

        {/* Greeting */}
        <h1 className="text-3xl font-bold mb-2">
          {greeting}{firstName ? `, ${firstName}` : ''}!
        </h1>
        <p className="text-muted-foreground text-lg mb-8">
          I&apos;m Mela AI. How can I help you today?
        </p>

        {/* Model Insights — compact live grid from governance settings */}
        <div className="mb-7 text-left">
          <ModelInsightsPanel />
        </div>

        {/* Suggestion grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {suggestions.map((s, i) => (
            <button
              key={i}
              onClick={() => onSuggestionClick(s.prompt)}
              className="flex items-start gap-3 p-4 text-left rounded-xl border bg-card hover:bg-accent hover:border-primary/20 transition-all group"
            >
              <div className="shrink-0 w-9 h-9 rounded-lg bg-primary/10 text-primary flex items-center justify-center group-hover:bg-primary group-hover:text-white transition-colors">
                {s.icon}
              </div>
              <div>
                <p className="font-medium text-sm">{s.title}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{s.description}</p>
              </div>
            </button>
          ))}
        </div>

        {/* Capabilities strip */}
        <div className="mt-10 flex items-center justify-center gap-6 text-xs text-muted-foreground flex-wrap">
          <span className="flex items-center gap-1.5">
            <Sparkles className="h-3.5 w-3.5 text-primary" />
            GPT-5.2 · GPT-4.1 · Kimi · Mistral
          </span>
          <span className="flex items-center gap-1.5">
            <FileText className="h-3.5 w-3.5 text-primary" />
            RAG · OCR · Document analysis
          </span>
          <span className="flex items-center gap-1.5">
            <ImageIcon className="h-3.5 w-3.5 text-primary" />
            DALL-E image generation
          </span>
        </div>
      </div>
    </div>
  );
}
