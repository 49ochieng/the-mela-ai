/**
 * Mela AI - Chat Store (Zustand)
 *
 * Enterprise dual-namespace design:
 *   - `namespaces.personal` and `namespaces.work` are completely isolated objects.
 *   - Root-level fields (conversations, projects, …) are always in sync with the
 *     ACTIVE namespace so existing components need no changes.
 *   - On profile switch: current namespace is saved → new namespace restored.
 *   - All profile-scoped write actions (nsSet) write to BOTH root AND the
 *     corresponding namespace atomically, so a mid-flight profile switch never
 *     mixes data: the race guard checks activeProfile === profileAtStart first,
 *     and nsSet writes to the explicitly-captured profile regardless.
 */

// Module-level timer for polling /user/features — lives outside Zustand state
let _featuresPollTimer: ReturnType<typeof setInterval> | null = null;

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import {
  api,
  Conversation,
  ConversationDetail,
  Message,
  ChatChunk,
  ModelInfo,
  InlineAttachment,
  UserUsage,
  UserPreferences,
  UserFeatures,
  ConnectorInfo,
  GeneratedImage as GeneratedImageType,
  Project,
  ProjectDetail,
  ProjectCreateRequest,
  ProjectUpdateRequest,
  ProjectFile,
  ProjectConversation,
  ProjectMember,
  AddMemberRequest,
  UpdateMemberRoleRequest,
  WorkerEventChunk,
  DEV_TENANT_ID,
} from './api';

// Phase 5A: a worker event with the local id used to dismiss it from
// WorkerEventBar.  Independent of WorkerEventChunk so the chunk stays a
// pure wire-shape mirror of the backend.
export interface WorkerEventBanner extends WorkerEventChunk {
  /** Local-only id; assigned on receipt for dismissal. */
  banner_id: string;
}

/** Max simultaneous banners — oldest drops off when a fourth arrives. */
export const WORKER_EVENT_BANNER_CAP = 3;

export type ProfileType = 'personal' | 'org' | 'work';

// ── Namespace shape ───────────────────────────────────────────────────────────

/** All state that is scoped to a single profile (work or personal). */
export interface ProfileNamespace {
  conversations: Conversation[];
  currentConversation: ConversationDetail | null;
  isLoadingConversations: boolean;
  projects: Project[];
  currentProject: ProjectDetail | null;
  isLoadingProjects: boolean;
  projectFiles: ProjectFile[];
  isLoadingProjectFiles: boolean;
  projectConversations: ProjectConversation[];
  isLoadingProjectConversations: boolean;
  /** Conversations shared with this user by colleagues (work mode). */
  sharedWithMe: Conversation[];
  /** Conversations this user has shared with colleagues. */
  sharedByMe: Conversation[];
  /** Projects shared with this user by colleagues (work mode). */
  sharedProjectsWithMe: Project[];
  /** Projects this user has shared with colleagues. */
  sharedProjectsByMe: Project[];
  isLoadingShared: boolean;
}

const emptyNamespace = (): ProfileNamespace => ({
  conversations: [],
  currentConversation: null,
  isLoadingConversations: false,
  projects: [],
  currentProject: null,
  isLoadingProjects: false,
  projectFiles: [],
  isLoadingProjectFiles: false,
  projectConversations: [],
  isLoadingProjectConversations: false,
  sharedWithMe: [],
  sharedByMe: [],
  sharedProjectsWithMe: [],
  sharedProjectsByMe: [],
  isLoadingShared: false,
});

// ── Full store state ──────────────────────────────────────────────────────────

interface ChatState extends ProfileNamespace {
  // ── Profile ───────────────────────────────────────────────────────────────
  activeProfile: ProfileType;
  /** Entra tenant ID for work profile. null = personal (or dev sentinel when DEV). */
  tenantId: string | null;
  /** Isolated namespace storage — the inactive profile's data lives here. */
  namespaces: {
    personal: ProfileNamespace;
    work: ProfileNamespace;
  };

  // ── Per-session (not namespace-scoped) ────────────────────────────────────
  messages: Message[];
  isStreaming: boolean;
  streamingContent: string;
  isLoadingConversation: boolean;
  isNewChat: boolean;

  // ── Private chat (session-only — not persisted) ───────────────────────────
  isPrivateMode: boolean;
  privateConversationId: string | null;

  // ── Models ────────────────────────────────────────────────────────────────
  models: ModelInfo[];
  selectedModel: string;
  // Live, admin-controlled per-model display name + cost multiplier.
  // Keyed by model_id. Loaded from /settings/models on app start and
  // refreshed whenever rankings change.
  modelAttribution: Record<string, { name: string; multiplier: number }>;

  // ── UI ────────────────────────────────────────────────────────────────────
  isSidebarOpen: boolean;
  useRag: boolean;
  useWebSearch: boolean;

  // ── Settings ──────────────────────────────────────────────────────────────
  isSettingsOpen: boolean;
  userPreferences: UserPreferences | null;
  userUsage: UserUsage | null;
  userFeatures: UserFeatures | null;
  connectors: ConnectorInfo[];
  isLoadingSettings: boolean;

  // ── Voice chat ────────────────────────────────────────────────────────────
  voiceModeEnabled: boolean;
  selectedVoice: string;
  isPlayingAudio: boolean;
  ttsError: string | null;

  // ── Claude usage ──────────────────────────────────────────────────────────
  claudeUsage: { question_count: number; limit: number; remaining: number; date: string } | null;

  // ── Worker events (Phase 5A) ──────────────────────────────────────────────
  // Live worker activity from the orchestration brain, pushed via the SSE
  // /orchestration/events/stream channel.  Session-only (NOT persisted).
  // Capped at 3 active banners — older entries drop off when a fourth lands.
  workerEvents: WorkerEventBanner[];

  // ── Error ─────────────────────────────────────────────────────────────────
  lastError: string | null;

  // ── Actions ───────────────────────────────────────────────────────────────
  loadConversations: () => Promise<void>;
  loadConversation: (id: string) => Promise<void>;
  createConversation: (title?: string, model?: string) => Promise<Conversation>;
  deleteConversation: (id: string) => Promise<void>;
  updateConversation: (id: string, updates: Partial<Conversation>) => Promise<void>;

  sendMessage: (
    message: string,
    attachments?: string[],
    inlineAttachments?: InlineAttachment[],
  ) => Promise<void>;
  stopStreaming: () => void;

  loadModels: () => Promise<void>;
  loadModelAttribution: () => Promise<void>;
  setSelectedModel: (model: string) => void;

  toggleSidebar: () => void;
  startNewChat: () => void;
  setUseRag: (useRag: boolean) => void;
  setUseWebSearch: (useWebSearch: boolean) => void;
  clearError: () => void;
  _silentRefreshConversations: () => Promise<void>;

  startPrivateChat: () => void;
  exitPrivateChat: () => void;

  setSettingsOpen: (open: boolean) => void;
  fetchPreferences: () => Promise<void>;
  updateUserPreferences: (prefs: Partial<UserPreferences>) => Promise<void>;
  fetchUsage: (days?: number) => Promise<void>;
  fetchFeatures: () => Promise<void>;
  startFeaturesPolling: (intervalMs?: number) => void;
  stopFeaturesPolling: () => void;
  fetchConnectors: () => Promise<void>;
  createConnector: (data: { name: string; connector_type: string; config?: Record<string, any>; is_enabled?: boolean }) => Promise<void>;
  updateConnector: (id: string, data: { name?: string; config?: Record<string, any>; is_enabled?: boolean }) => Promise<void>;
  deleteConnector: (id: string) => Promise<void>;
  testConnector: (id: string) => Promise<{ status: string; message: string }>;
  deleteAllHistory: () => Promise<void>;
  exportData: () => Promise<void>;

  setActiveProfile: (profile: ProfileType, tenantId?: string | null) => void;
  /** Update the stored Entra tenant ID (called after MSAL sign-in resolves the real tid). */
  setTenantId: (tenantId: string | null) => void;

  // Phase 5A: worker event banner actions.  Push-only — the SSE stream
  // owns ingestion and component-level effects own the auto-dismiss
  // timer, so the store stays minimal.
  pushWorkerEvent: (event: WorkerEventChunk) => void;
  dismissWorkerEvent: (banner_id: string) => void;
  clearWorkerEvents: () => void;

  /** Load shared-with-me and shared-by-me for the active profile. */
  loadShared: () => Promise<void>;

  loadProjects: () => Promise<void>;
  loadProject: (id: string) => Promise<void>;
  setCurrentProject: (project: ProjectDetail | null) => void;
  createProject: (data: ProjectCreateRequest) => Promise<void>;
  updateProject: (id: string, data: ProjectUpdateRequest) => Promise<void>;
  deleteProject: (id: string) => Promise<void>;
  deleteProjectMemory: (projectId: string, memoryId: string) => Promise<void>;
  moveConversationToProject: (conversationId: string, projectId: string | null) => Promise<void>;

  loadProjectFiles: (projectId: string) => Promise<void>;
  uploadProjectFile: (projectId: string, file: File) => Promise<void>;
  deleteProjectFile: (projectId: string, fileId: string) => Promise<void>;

  loadProjectConversations: (projectId: string) => Promise<void>;
  updateProjectInstructions: (projectId: string, systemPrompt: string) => Promise<void>;

  loadProjectMembers: (projectId: string) => Promise<ProjectMember[]>;
  addProjectMember: (projectId: string, data: AddMemberRequest) => Promise<ProjectMember>;
  updateProjectMemberRole: (projectId: string, userId: string, data: UpdateMemberRoleRequest) => Promise<ProjectMember>;
  removeProjectMember: (projectId: string, userId: string) => Promise<void>;

  loadChatMembers: (chatId: string) => Promise<ProjectMember[]>;
  addChatMember: (chatId: string, data: AddMemberRequest) => Promise<ProjectMember>;
  updateChatMemberRole: (chatId: string, userId: string, data: UpdateMemberRoleRequest) => Promise<ProjectMember>;
  removeChatMember: (chatId: string, userId: string) => Promise<void>;

  setVoiceModeEnabled: (enabled: boolean) => void;
  setSelectedVoice: (voice: string) => void;
  speakText: (text: string) => Promise<void>;
  stopAudio: () => void;

  reset: () => void;
}

const initialState = {
  // Namespace data (active profile at root for backward compat)
  ...emptyNamespace(),
  // Both namespaces start empty
  namespaces: {
    personal: emptyNamespace(),
    work: emptyNamespace(),
  },

  // Profile
  activeProfile: 'personal' as ProfileType,
  tenantId: null as string | null,

  // Session state
  messages: [] as Message[],
  isStreaming: false,
  streamingContent: '',
  isLoadingConversation: false,
  isNewChat: true,
  isPrivateMode: false,
  privateConversationId: null as string | null,

  // Models
  models: [] as ModelInfo[],
  selectedModel: 'auto',
  modelAttribution: {} as Record<string, { name: string; multiplier: number }>,

  // UI
  isSidebarOpen: true,
  useRag: true,
  useWebSearch: false,

  // Settings
  isSettingsOpen: false,
  userPreferences: null as UserPreferences | null,
  userUsage: null as UserUsage | null,
  userFeatures: null as UserFeatures | null,
  connectors: [] as ConnectorInfo[],
  isLoadingSettings: false,

  lastError: null as string | null,

  // Voice chat
  voiceModeEnabled: false,
  selectedVoice: 'en-US-AriaNeural',
  isPlayingAudio: false,
  ttsError: null,
  claudeUsage: null,
  workerEvents: [],
};

// Abort controller lives outside the store so it doesn't cause re-renders
let abortController: AbortController | null = null;
// Audio element for TTS playback — lives outside store to avoid re-renders
let currentAudio: HTMLAudioElement | null = null;

// TTS generation counter — incremented on every stopAudio/new speakText call.
// Any in-flight synthesis that sees a stale generation is silently discarded,
// preventing noise from overlapping audio instances.
let ttsGeneration = 0;

// Pre-synthesized first sentence fetched during streaming so audio can start
// the instant streaming ends (zero gap between response text and first word).
let _presynthPromise: Promise<ArrayBuffer | null> | null = null;
let _presynthText = '';

function _stripMdForTts(text: string): string {
  return text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // [link](url) → link
    .replace(/!\[[^\]]*\]\([^)]+\)/g, '')     // remove images
    .replace(/[*_`#>~|]/g, '')
    .replace(/\n{2,}/g, '. ')
    .replace(/\n/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

function _splitTtsChunks(text: string): string[] {
  // Split on sentence endings, grouping into ~80-char chunks for
  // fast first-word latency while keeping prosody natural.
  const parts = text.match(/[^.!?]+[.!?]*/g) ?? [text];
  const chunks: string[] = [];
  let buf = '';
  for (const p of parts) {
    buf += p;
    if (buf.length >= 80 || /[.!?]\s*$/.test(buf.trim())) {
      const t = buf.trim();
      if (t) chunks.push(t);
      buf = '';
    }
  }
  if (buf.trim()) chunks.push(buf.trim());
  return chunks.filter(Boolean);
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => {
      // ── Namespace write helper ───────────────────────────────────────────────
      //
      // Writes `patch` to BOTH the root level (so components see it immediately)
      // AND to `namespaces[profile]` (so it survives profile switches).
      //
      // Always pass the profile captured BEFORE the async operation so that a
      // mid-flight profile switch writes to the correct namespace and the race
      // guard (get().activeProfile === profileAtStart) can then decide whether
      // to also sync to root.
      const nsSet = (profile: ProfileType, patch: Partial<ProfileNamespace>) => {
        // Map 'org' alias → 'work' for namespace key
        const nsKey: 'personal' | 'work' = profile === 'org' ? 'work' : (profile as 'personal' | 'work');
        set((s) => ({
          // Sync to root only if the profile is still active
          ...(s.activeProfile === profile ? patch : {}),
          namespaces: {
            ...s.namespaces,
            [nsKey]: { ...s.namespaces[nsKey], ...patch },
          },
        }));
      };

      return {
        ...initialState,

        // ── Conversations ────────────────────────────────────────────────────

        loadConversations: async () => {
          const profileAtStart = get().activeProfile;
          const nsKey: 'personal' | 'work' = profileAtStart === 'org' ? 'work' : (profileAtStart as 'personal' | 'work');
          const contextType = profileAtStart === 'personal' ? 'personal' : 'org';

          // Use cached namespace data first, refresh in background
          const currentNsConvs = get().namespaces[nsKey].conversations;
          if (currentNsConvs.length > 0 && !get().namespaces[nsKey].isLoadingConversations) {
            api.invalidateConversationCache();
            api.getConversations(50, 0, false, contextType).then((conversations) => {
              if (get().activeProfile === profileAtStart) {
                nsSet(profileAtStart, { conversations });
              }
            }).catch(() => {/* non-critical */});
            return;
          }

          nsSet(profileAtStart, { isLoadingConversations: true });
          try {
            const conversations = await api.getConversations(50, 0, false, contextType);
            if (get().activeProfile === profileAtStart) {
              nsSet(profileAtStart, { conversations, isLoadingConversations: false });
            } else {
              // Profile switched while we were fetching — save to namespace but don't push to root
              const nk: 'personal' | 'work' = profileAtStart === 'org' ? 'work' : (profileAtStart as 'personal' | 'work');
              set((s) => ({
                namespaces: {
                  ...s.namespaces,
                  [nk]: { ...s.namespaces[nk], conversations, isLoadingConversations: false },
                },
              }));
            }
          } catch (error) {
            console.error('Failed to load conversations:', error);
            nsSet(profileAtStart, { isLoadingConversations: false });
          }
        },

        loadConversation: async (id: string) => {
          const profileAtStart = get().activeProfile;
          set({ isLoadingConversation: true });
          try {
            const conversation = await api.getConversation(id);
            const messages = conversation.messages.map((m) => ({
              ...m,
              created_at: m.created_at || conversation.created_at,
            }));
            nsSet(profileAtStart, { currentConversation: conversation });
            if (get().activeProfile === profileAtStart) {
              set({
                messages,
                selectedModel: conversation.model,
                isNewChat: false,
                isLoadingConversation: false,
              });
              // Push URL for direct navigation
              if (typeof window !== 'undefined' && !window.location.pathname.includes(id)) {
                window.history.pushState(null, '', `/chat/${id}`);
              }
            } else {
              set({ isLoadingConversation: false });
            }
          } catch (error) {
            console.error('Failed to load conversation:', error);
            set({ isLoadingConversation: false, lastError: 'Failed to load conversation.' });
          }
        },

        createConversation: async (title?: string, model?: string) => {
          // Capture profile before the async API call
          const profileAtStart = get().activeProfile;
          const conversation = await api.createConversation({
            title: title || 'New Chat',
            model: model || get().selectedModel,
            context_type: (profileAtStart === 'work' || profileAtStart === 'org') ? 'org' : 'personal',
          });
          // Always add to the profile that made the request (not the current active one)
          const nsKey: 'personal' | 'work' = (profileAtStart === 'work' || profileAtStart === 'org') ? 'work' : 'personal';
          nsSet(profileAtStart, {
            conversations: [conversation, ...get().namespaces[nsKey].conversations],
            currentConversation: { ...conversation, messages: [] },
          });
          if (get().activeProfile === profileAtStart) {
            set({ messages: [], isNewChat: false });
          }
          return conversation;
        },

        deleteConversation: async (id: string) => {
          const profileAtStart = get().activeProfile;
          const currentConv = get().currentConversation;
          await api.deleteConversation(id);
          const nsKey: 'personal' | 'work' = (profileAtStart === 'work' || profileAtStart === 'org') ? 'work' : 'personal';
          nsSet(profileAtStart, {
            conversations: get().namespaces[nsKey].conversations.filter((c) => c.id !== id),
            currentConversation: currentConv?.id === id ? null : get().currentConversation,
          });
          if (currentConv?.id === id && get().activeProfile === profileAtStart) {
            set({ messages: [], isNewChat: true });
          }
        },

        updateConversation: async (id: string, updates: Partial<Conversation>) => {
          try {
            const profileAtStart = get().activeProfile;
            await api.updateConversation(id, updates);
            const nsKey: 'personal' | 'work' = (profileAtStart === 'work' || profileAtStart === 'org') ? 'work' : 'personal';
            const currentConv = get().currentConversation;
            nsSet(profileAtStart, {
              conversations: get().namespaces[nsKey].conversations.map((c) =>
                c.id === id ? { ...c, ...updates } : c,
              ),
              currentConversation:
                currentConv?.id === id
                  ? { ...currentConv, ...updates }
                  : currentConv,
            });
          } catch (error) {
            console.error('Failed to update conversation:', error);
          }
        },

        // ── Send message ─────────────────────────────────────────────────────

        sendMessage: async (
          message: string,
          attachments?: string[],
          inlineAttachments?: InlineAttachment[],
        ) => {
          const state = get();
          const now = new Date().toISOString();
          const wasNewChat = !state.currentConversation?.id;

          const userMessage: Message = {
            role: 'user',
            content: message,
            inline_attachments: inlineAttachments,
            attachments: attachments?.map((id) => ({
              id,
              filename: '',
              file_type: '',
              file_size: 0,
            })),
            created_at: now,
          };

          set((s) => ({
            messages: [...s.messages, userMessage],
            isStreaming: true,
            streamingContent: '',
            lastError: null,
          }));

          const existingConversationId = state.currentConversation?.id;
          abortController = new AbortController();
          let assistantContent = '';
          const citations: any[] = [];
          const generatedFiles: any[] = [];
          const generatedImages: GeneratedImageType[] = [];
          let emailDraft: import('@/lib/api').EmailDraft | undefined = undefined;
          let finalConversationId = existingConversationId;
          let resolvedModel: string | undefined = undefined;
          let resolvedProvider: string | undefined = undefined;
          // Buffer for detecting first complete sentence to pre-synthesize in voice mode
          let _voiceBuf = '';
          let _voicePresynthDone = false;

          // Human-readable labels for tool_executing status
          const _toolLabel = (name: string): string => {
            const labels: Record<string, string> = {
              get_inbox: '📧 Reading inbox\u2026',
              search_emails: '🔍 Searching emails\u2026',
              get_email_details: '📧 Opening email\u2026',
              get_email_thread: '🧵 Loading thread\u2026',
              reply_to_email: '↩️ Sending reply\u2026',
              send_email: '📤 Sending email\u2026',
              create_draft_email: '📝 Saving draft\u2026',
              get_calendar: '📅 Checking calendar\u2026',
              schedule_meeting: '📆 Scheduling meeting\u2026',
              check_availability: '🔍 Checking availability\u2026',
              list_planner_tasks: '✅ Loading tasks\u2026',
              create_task: '➕ Creating task\u2026',
              search_documents: '🔍 Searching knowledge base\u2026',
              run_python_code: '🐍 Running code\u2026',
              onboard_user: '👤 Onboarding user\u2026',
            };
            return labels[name] ?? `⚙️ Running ${name}\u2026`;
          };

          try {
            for await (const chunk of api.streamChat(
              {
                message,
                conversation_id: existingConversationId,
                model: state.selectedModel,
                use_rag: state.useRag,
                use_web_search: state.useWebSearch,
                attachments,
                inline_attachments: inlineAttachments,
                is_private: state.isPrivateMode,
                project_id: state.currentProject?.id,
                context_type: (state.activeProfile === 'work' || state.activeProfile === 'org') ? 'org' : 'personal',
              },
              abortController.signal,
            )) {
              if (abortController?.signal.aborted) break;

              // Phase 0.6: keepalive ping — backend emits this every 20s while
              // streaming a long answer so the SSE connection stays open. No-op.
              if (chunk.type === 'ping' || chunk.type === 'heartbeat') continue;

              if (chunk.type === 'tool_executing' && chunk.data?.name) {
                // Show which tool is running as a streaming status line
                const label = _toolLabel(chunk.data.name);
                set({ streamingContent: assistantContent || label });
              } else if (chunk.type === 'tool_call' || chunk.type === 'tool_result') {
                // Internal bookkeeping — never add to visible content
              } else if (chunk.type === 'thinking' && chunk.content) {
                // Reasoning models stream thinking before the answer — show a subtle indicator
                set({ streamingContent: assistantContent || '\u2026thinking\u2026' });
              } else if (chunk.type === 'content' && chunk.content) {
                assistantContent += chunk.content;
                set({ streamingContent: assistantContent });

                // Voice mode: pre-synthesize the first complete sentence while
                // streaming so audio can begin the instant the stream ends.
                if (get().voiceModeEnabled && !_voicePresynthDone) {
                  _voiceBuf += chunk.content;
                  // Wait for a complete sentence (≥15 chars before punctuation) for a
                  // natural-sounding first chunk, or fall back after 80 chars.
                  const sentenceMatch = _voiceBuf.match(/^([^.!?]{15,}[.!?]+\s*)/);
                  if (sentenceMatch || _voiceBuf.length > 80) {
                    _voicePresynthDone = true;
                    const presynthSrc = sentenceMatch ? sentenceMatch[1] : _voiceBuf;
                    const firstSentence = _stripMdForTts(presynthSrc.trim());
                    if (firstSentence) {
                      _presynthText = firstSentence;
                      _presynthPromise = api.synthesizeText(firstSentence, get().selectedVoice).catch(() => null);
                    }
                  }
                }
              } else if (chunk.type === 'model_switched' && chunk.data?.to_model) {
                // Silently update the active model — don't pollute the chat content
                set({ selectedModel: chunk.data.to_model });
              } else if (chunk.type === 'claude_usage' && chunk.data) {
                set({ claudeUsage: chunk.data });
              } else if (chunk.type === 'claude_limit_reached' && chunk.data) {
                set({
                  claudeUsage: { question_count: chunk.data.question_count, limit: chunk.data.limit, remaining: 0, date: '' },
                  selectedModel: chunk.data.fallback_model ?? get().selectedModel,
                });
              } else if (chunk.type === 'email_draft' && chunk.data) {
                // Store the latest draft — one per message is sufficient
                emailDraft = {
                  draft_id: chunk.data.draft_id,
                  to: chunk.data.to ?? [],
                  subject: chunk.data.subject ?? '',
                  body_preview: chunk.data.body_preview ?? '',
                  status: 'saved' as const,
                };
              } else if (chunk.type === 'citation' && chunk.data) {
                citations.push(chunk.data);
              } else if (chunk.type === 'file_generated' && chunk.data) {
                generatedFiles.push({
                  name: chunk.data.name,
                  base64: chunk.data.base64,
                  mime_type: chunk.data.mime_type,
                  size: chunk.data.size,
                  output_type: chunk.data.output_type,
                });
              } else if (chunk.type === 'image_generated' && chunk.data) {
                generatedImages.push(chunk.data);
              } else if (chunk.type === 'done' && chunk.data?.conversation_id) {
                finalConversationId = chunk.data.conversation_id;
                if (chunk.data.model) resolvedModel = chunk.data.model;
                if (chunk.data.provider) resolvedProvider = chunk.data.provider;
              } else if (chunk.type === 'error') {
                // Phase 0: prefer the typed code → friendly message map; fall
                // back to backend-provided content; finally a generic apology.
                const friendly = (() => {
                  if (chunk.error_code) {
                    const mod = require('@/lib/api');
                    const map = mod.ERROR_MESSAGES as Record<string, string> | undefined;
                    const base = map?.[chunk.error_code];
                    if (base) return chunk.correlation_id ? `${base} (ref: ${chunk.correlation_id})` : base;
                  }
                  return chunk.content || 'An error occurred';
                })();
                const err = new Error(friendly) as Error & { code?: string; correlationId?: string };
                if (chunk.error_code) err.code = chunk.error_code;
                if (chunk.correlation_id) err.correlationId = chunk.correlation_id;
                throw err;
              }
            }

            const assistantMessage: Message = {
              role: 'assistant',
              content: assistantContent,
              citations: citations.length > 0 ? citations : undefined,
              generated_files: generatedFiles.length > 0 ? generatedFiles : undefined,
              images: generatedImages.length > 0 ? generatedImages : undefined,
              email_draft: emailDraft,
              model: resolvedModel ?? state.selectedModel,
              provider: resolvedProvider,
              created_at: new Date().toISOString(),
            };

            set((s) => {
              const updatedMessages = [...s.messages, assistantMessage];
              if (finalConversationId) {
                api.updateCachedMessages(finalConversationId, updatedMessages);
              }
              return {
                messages: updatedMessages,
                isStreaming: false,
                streamingContent: '',
              };
            });

            // Auto-speak AI response in voice chat mode
            if (get().voiceModeEnabled && assistantContent) {
              get().speakText(assistantContent).catch(() => {});
            }

            // Use the profile captured at the start of sendMessage (not current active profile)
            // so that mid-stream profile switches don't corrupt the namespace.
            const sendProfile = state.activeProfile;
            const sendNsKey: 'personal' | 'work' = (sendProfile === 'work' || sendProfile === 'org') ? 'work' : 'personal';

            if (wasNewChat && finalConversationId) {
              // Use a temporary title initially (first 60 chars)
              const tempTitle = message.slice(0, 60);
              const newConv: ConversationDetail = {
                id: finalConversationId,
                title: tempTitle,
                model: state.selectedModel,
                system_prompt: undefined,
                is_archived: false,
                is_private: state.isPrivateMode,
                project_id: state.currentProject?.id,
                context_type: (sendProfile === 'work' || sendProfile === 'org') ? 'org' : 'personal',
                message_count: 2,
                created_at: now,
                updated_at: now,
                messages: [],
              };
              if (state.isPrivateMode) {
                nsSet(sendProfile, { currentConversation: newConv });
                if (get().activeProfile === sendProfile) {
                  set({ isNewChat: false, privateConversationId: finalConversationId });
                }
              } else {
                const existing = get().namespaces[sendNsKey].conversations;
                nsSet(sendProfile, {
                  currentConversation: newConv,
                  conversations: existing.some((c) => c.id === finalConversationId)
                    ? existing
                    : [{ ...newConv }, ...existing],
                });
                if (get().activeProfile === sendProfile) {
                  set({ isNewChat: false });
                }

                // Push browser history for URL-based navigation
                if (typeof window !== 'undefined') {
                  window.history.pushState(null, '', `/chat/${finalConversationId}`);
                }
              }

              // Generate AI title in background (non-blocking)
              // Capture the conversation ID as a const for type safety in the callback
              const convIdForTitle = finalConversationId;
              api.generateTitle(message).then((aiTitle) => {
                // Update conversation title in store
                const currentConv = get().namespaces[sendNsKey].currentConversation;
                nsSet(sendProfile, {
                  conversations: get().namespaces[sendNsKey].conversations.map((c) =>
                    c.id === convIdForTitle ? { ...c, title: aiTitle } : c,
                  ),
                  currentConversation:
                    currentConv?.id === convIdForTitle && currentConv != null
                      ? ({ ...currentConv, title: aiTitle, messages: currentConv.messages ?? [] } as ConversationDetail)
                      : currentConv,
                });
                // Also update on backend
                api.updateConversation(convIdForTitle, { title: aiTitle }).catch(() => {});
              }).catch(() => {
                // Fallback already set above — non-critical
              });
            } else if (!wasNewChat && finalConversationId) {
              if (state.isNewChat) {
                const title = message.slice(0, 60);
                const currentConv = get().namespaces[sendNsKey].currentConversation;
                nsSet(sendProfile, {
                  conversations: get().namespaces[sendNsKey].conversations.map((c) =>
                    c.id === finalConversationId ? { ...c, title } : c,
                  ),
                  currentConversation:
                    currentConv?.id === finalConversationId && currentConv != null
                      ? ({ ...currentConv, title, messages: currentConv.messages ?? [] } as ConversationDetail)
                      : currentConv,
                });
                if (get().activeProfile === sendProfile) {
                  set({ isNewChat: false });
                }
              }
            }

            if (!state.isPrivateMode) {
              // Delay the background refresh so the backend DB commit has time to
              // land before we query the list.  Without this delay, the list request
              // can arrive at the server before the streaming-response session is
              // committed, returning an empty list that then overwrites the store.
              setTimeout(() => get()._silentRefreshConversations(), 500);
            }
          } catch (error: any) {
            if (error?.name === 'AbortError') return;

            console.error('Failed to send message:', error);

            const rawMsg = error instanceof Error ? error.message : 'Unknown error';
            let errMsg = rawMsg;
            const lower = rawMsg.toLowerCase();
            if (lower.includes('failed to fetch') || lower.includes('network') || lower.includes('unable to connect')) {
              errMsg = 'Unable to connect to the server. Please check your internet connection and ensure the backend is running.';
            } else if (lower.includes('rate limit') || lower.includes('429')) {
              errMsg = 'The AI service is temporarily busy. Please wait a moment and try again.';
            } else if (lower.includes('timeout') || lower.includes('timed out')) {
              errMsg = 'The request timed out. Please try again.';
            }

            set((s) => ({
              messages: [
                ...s.messages,
                {
                  role: 'assistant' as const,
                  content: `Sorry, I encountered an error: ${errMsg}`,
                  created_at: new Date().toISOString(),
                },
              ],
              isStreaming: false,
              streamingContent: '',
              lastError: errMsg,
            }));
          } finally {
            abortController = null;
          }
        },

        stopStreaming: () => {
          if (abortController) {
            abortController.abort();
            abortController = null;
          }

          const streamingContent = get().streamingContent;
          if (streamingContent) {
            set((s) => ({
              messages: [
                ...s.messages,
                {
                  role: 'assistant' as const,
                  content: streamingContent + ' [stopped]',
                  created_at: new Date().toISOString(),
                },
              ],
              isStreaming: false,
              streamingContent: '',
            }));
          } else {
            set({ isStreaming: false, streamingContent: '' });
          }
        },

        // ── Internal helpers ─────────────────────────────────────────────────

        _silentRefreshConversations: async () => {
          try {
            api.invalidateConversationCache();
            const profileAtStart = get().activeProfile;
            const nsKey: 'personal' | 'work' = (profileAtStart === 'work' || profileAtStart === 'org') ? 'work' : 'personal';
            const contextType = (profileAtStart === 'work' || profileAtStart === 'org') ? 'org' : 'personal';
            const conversations = await api.getConversations(50, 0, false, contextType);
            if (get().activeProfile === profileAtStart) {
              // Guard: if the backend returned an empty list but the store already
              // has conversations (e.g., a brand-new chat was just added optimistically),
              // keep the existing list.  This prevents a race condition where the DB
              // commit hasn't landed yet by the time this refresh runs.
              const existing = get().namespaces[nsKey].conversations;
              if (conversations.length > 0 || existing.length === 0) {
                nsSet(profileAtStart, { conversations });
              }
            }
          } catch {
            // Non-critical – ignore
          }
        },

        // ── Models ───────────────────────────────────────────────────────────

        loadModels: async () => {
          try {
            const models = await api.getModels();
            set({ models });

            const current = get().selectedModel;
            // 'auto' is always valid — the orchestrator handles model selection
            const exists = current === 'auto' || models.find((m) => m.id === current);

            if (!exists) {
              const defaultModel =
                models.find((m) => m.is_default) ||
                models.find((m) => m.id === 'gpt-5.2-chat') ||
                models[0];
              if (defaultModel) set({ selectedModel: defaultModel.id });
            }

            // Best-effort: pull live multipliers in the background.
            get().loadModelAttribution().catch(() => {});
          } catch (error) {
            console.error('Failed to load models:', error);
          }
        },

        loadModelAttribution: async () => {
          try {
            const rankings = await api.getModelRankings();
            const map: Record<string, { name: string; multiplier: number }> = {};
            for (const r of rankings) {
              map[r.model_id] = {
                name: r.display_name,
                multiplier: typeof r.cost_multiplier === 'number'
                  ? r.cost_multiplier
                  : 1.0,
              };
            }
            set({ modelAttribution: map });
          } catch (err) {
            // Non-critical — ChatMessage falls back to its built-in map.
            console.debug('loadModelAttribution failed:', err);
          }
        },

        setSelectedModel: (model: string) => {
          set({ selectedModel: model });
          const conv = get().currentConversation;
          if (conv && conv.model !== model) {
            get().updateConversation(conv.id, { model });
          }
        },

        // ── UI ───────────────────────────────────────────────────────────────

        toggleSidebar: () => set((s) => ({ isSidebarOpen: !s.isSidebarOpen })),

        startNewChat: () => {
          const profile = get().activeProfile;
          nsSet(profile, { currentConversation: null });
          set({
            messages: [],
            isNewChat: true,
            streamingContent: '',
            lastError: null,
            isPrivateMode: false,
            privateConversationId: null,
          });
          // Reset URL to base chat path
          if (typeof window !== 'undefined' && window.location.pathname !== '/chat') {
            window.history.pushState(null, '', '/chat');
          }
        },

        startPrivateChat: () => {
          const profile = get().activeProfile;
          nsSet(profile, { currentConversation: null });
          set({
            isPrivateMode: true,
            privateConversationId: null,
            messages: [],
            isNewChat: true,
            streamingContent: '',
            lastError: null,
          });
        },

        exitPrivateChat: () => {
          const profile = get().activeProfile;
          nsSet(profile, { currentConversation: null });
          set({
            isPrivateMode: false,
            privateConversationId: null,
            messages: [],
            isNewChat: true,
            streamingContent: '',
          });
        },

        setUseRag: (useRag: boolean) => set({ useRag }),
        setUseWebSearch: (useWebSearch: boolean) => set({ useWebSearch }),

        clearError: () => set({ lastError: null }),

        // ── Worker events (Phase 5A) ─────────────────────────────────────────
        pushWorkerEvent: (event: WorkerEventChunk) => {
          const banner: WorkerEventBanner = {
            ...event,
            banner_id:
              (typeof crypto !== 'undefined' && crypto.randomUUID
                ? crypto.randomUUID()
                : `banner-${Date.now()}-${Math.random()}`),
          };
          const existing = get().workerEvents;
          // FIFO cap — drop the oldest when a fourth would arrive.
          const trimmed =
            existing.length >= WORKER_EVENT_BANNER_CAP
              ? existing.slice(existing.length - WORKER_EVENT_BANNER_CAP + 1)
              : existing;
          set({ workerEvents: [...trimmed, banner] });
        },
        dismissWorkerEvent: (banner_id: string) => {
          set({
            workerEvents: get().workerEvents.filter(
              (e) => e.banner_id !== banner_id,
            ),
          });
        },
        clearWorkerEvents: () => set({ workerEvents: [] }),

        // ── Settings ─────────────────────────────────────────────────────────

        setSettingsOpen: (open: boolean) => set({ isSettingsOpen: open }),

        fetchPreferences: async () => {
          try {
            const prefs = await api.getUserPreferences();
            set({ userPreferences: prefs });
          } catch (error) {
            console.error('Failed to fetch preferences:', error);
          }
        },

        updateUserPreferences: async (prefs: Partial<UserPreferences>) => {
          const current = get().userPreferences;
          const merged = { ...current, ...prefs } as UserPreferences;
          set({ userPreferences: merged });
          try {
            const updated = await api.updateUserPreferences(merged);
            set({ userPreferences: updated });
          } catch (error) {
            console.error('Failed to update preferences:', error);
            set({ userPreferences: current });
          }
        },

        fetchUsage: async (days?: number) => {
          set({ isLoadingSettings: true });
          try {
            const usage = await api.getUserUsage(days);
            set({ userUsage: usage, isLoadingSettings: false });
          } catch (error) {
            console.error('Failed to fetch usage:', error);
            set({ isLoadingSettings: false });
          }
        },

        fetchFeatures: async () => {
          try {
            const features = await api.getUserFeatures();
            set({ userFeatures: features });
          } catch (error) {
            console.error('Failed to fetch features:', error);
          }
        },

        startFeaturesPolling: (intervalMs = 30_000) => {
          if (_featuresPollTimer !== null) return;
          _featuresPollTimer = setInterval(() => {
            get().fetchFeatures();
          }, intervalMs);
        },

        stopFeaturesPolling: () => {
          if (_featuresPollTimer !== null) {
            clearInterval(_featuresPollTimer);
            _featuresPollTimer = null;
          }
        },

        fetchConnectors: async () => {
          try {
            const connectors = await api.getConnectors();
            set({ connectors });
          } catch (error) {
            console.error('Failed to fetch connectors:', error);
          }
        },

        createConnector: async (data) => {
          try {
            const connector = await api.createConnector(data);
            set((s) => ({ connectors: [...s.connectors, connector] }));
          } catch (error) {
            console.error('Failed to create connector:', error);
            throw error;
          }
        },

        updateConnector: async (id, data) => {
          try {
            const updated = await api.updateConnector(id, data);
            set((s) => ({
              connectors: s.connectors.map((c) => (c.id === id ? updated : c)),
            }));
          } catch (error) {
            console.error('Failed to update connector:', error);
            throw error;
          }
        },

        deleteConnector: async (id) => {
          try {
            await api.deleteConnector(id);
            set((s) => ({ connectors: s.connectors.filter((c) => c.id !== id) }));
          } catch (error) {
            console.error('Failed to delete connector:', error);
            throw error;
          }
        },

        testConnector: async (id) => {
          return api.testConnector(id);
        },

        deleteAllHistory: async () => {
          try {
            await api.deleteUserHistory();
            const profile = get().activeProfile;
            nsSet(profile, { conversations: [], currentConversation: null });
            set({ messages: [], isNewChat: true });
          } catch (error) {
            console.error('Failed to delete history:', error);
            throw error;
          }
        },

        exportData: async () => {
          try {
            await api.exportUserData();
          } catch (error) {
            console.error('Failed to export data:', error);
            throw error;
          }
        },

        // ── Profile switching ─────────────────────────────────────────────────
        //
        // This is the core of the dual-namespace design.
        // 1. Save current root-level namespace state to namespaces[currentProfile]
        // 2. Restore namespaces[newProfile] to root level
        // 3. Tell the API client to send the correct profile headers
        // 4. Load fresh data if the new namespace is empty

        setActiveProfile: (profile: ProfileType, tenantId: string | null = null) => {
          // Abort any in-flight streaming request before switching profiles
          if (abortController) {
            abortController.abort();
            abortController = null;
          }

          const s = get();
          const currentProfileKey: 'personal' | 'work' =
            s.activeProfile === 'org' ? 'work' : (s.activeProfile as 'personal' | 'work');
          const newProfileKey: 'personal' | 'work' =
            profile === 'org' ? 'work' : (profile as 'personal' | 'work');

          // Snapshot of current namespace-scoped root state
          const currentNsSnapshot: ProfileNamespace = {
            conversations: s.conversations,
            currentConversation: s.currentConversation,
            isLoadingConversations: s.isLoadingConversations,
            projects: s.projects,
            currentProject: s.currentProject,
            isLoadingProjects: s.isLoadingProjects,
            projectFiles: s.projectFiles,
            isLoadingProjectFiles: s.isLoadingProjectFiles,
            projectConversations: s.projectConversations,
            isLoadingProjectConversations: s.isLoadingProjectConversations,
            sharedWithMe: s.sharedWithMe,
            sharedByMe: s.sharedByMe,
            sharedProjectsWithMe: s.sharedProjectsWithMe,
            sharedProjectsByMe: s.sharedProjectsByMe,
            isLoadingShared: s.isLoadingShared,
          };

          // State for new profile (restore from saved namespace)
          const newNsData = s.namespaces[newProfileKey];

          // Effective tenant ID for the new profile
          const effectiveTenantId =
            newProfileKey === 'work'
              ? (tenantId ?? s.tenantId ?? DEV_TENANT_ID)
              : null;

          set({
            // Switch active profile
            activeProfile: profile,
            tenantId: effectiveTenantId,

            // Restore new profile's namespace to root level
            conversations: newNsData.conversations,
            currentConversation: newNsData.currentConversation,
            isLoadingConversations: newNsData.isLoadingConversations,
            projects: newNsData.projects,
            currentProject: newNsData.currentProject,
            isLoadingProjects: newNsData.isLoadingProjects,
            projectFiles: newNsData.projectFiles,
            isLoadingProjectFiles: newNsData.isLoadingProjectFiles,
            projectConversations: newNsData.projectConversations,
            isLoadingProjectConversations: newNsData.isLoadingProjectConversations,

            // Clear per-session state
            messages: [],
            isNewChat: true,
            streamingContent: '',
            lastError: null,
            isPrivateMode: false,
            privateConversationId: null,

            // Save current namespace to the old profile slot
            namespaces: {
              ...s.namespaces,
              [currentProfileKey]: currentNsSnapshot,
            },
          });

          // Notify the API client — all subsequent requests will use new profile headers
          api.setProfileContext(newProfileKey, effectiveTenantId);
          api.invalidateConversationCache();

          // Enterprise RAG is work-only — turn it off when switching to personal
          if (newProfileKey === 'personal') {
            set({ useRag: false });
          }

          // Load fresh data if the restored namespace is empty
          if (newNsData.conversations.length === 0) {
            get().loadConversations();
          }
          if (newNsData.projects.length === 0) {
            get().loadProjects();
          }
          // Shared views are work-only — load on every switch to work
          if (newProfileKey === 'work') {
            get().loadShared();
          }
        },

        setTenantId: (tenantId: string | null) => {
          set({ tenantId });
          // Keep the API client in sync when already in work profile
          const s = get();
          if (s.activeProfile === 'work' || s.activeProfile === 'org') {
            api.setProfileContext('work', tenantId ?? DEV_TENANT_ID);
          }
        },

        loadShared: async () => {
          const profileAtStart = get().activeProfile;
          // Shared views only apply to work mode
          if (profileAtStart !== 'org') return;
          nsSet(profileAtStart, { isLoadingShared: true });
          try {
            const [swm, sbm, pswm, psbm] = await Promise.all([
              api.getSharedWithMe().catch(() => [] as Conversation[]),
              api.getSharedByMe().catch(() => [] as Conversation[]),
              api.getSharedWithMeProjects().catch(() => [] as Project[]),
              api.getSharedByMeProjects().catch(() => [] as Project[]),
            ]);
            if (get().activeProfile === profileAtStart) {
              nsSet(profileAtStart, {
                sharedWithMe: swm,
                sharedByMe: sbm,
                sharedProjectsWithMe: pswm,
                sharedProjectsByMe: psbm,
                isLoadingShared: false,
              });
            } else {
              nsSet(profileAtStart, { isLoadingShared: false });
            }
          } catch {
            nsSet(profileAtStart, { isLoadingShared: false });
          }
        },

        // ── Projects ─────────────────────────────────────────────────────────

        loadProjects: async () => {
          const profileAtStart = get().activeProfile;
          const contextType = profileAtStart === 'personal' ? 'personal' : 'org';

          nsSet(profileAtStart, { isLoadingProjects: true });
          try {
            const projects = await api.listProjects(false, contextType);
            if (get().activeProfile === profileAtStart) {
              nsSet(profileAtStart, { projects, isLoadingProjects: false });
            } else {
              const nk: 'personal' | 'work' = profileAtStart === 'org' ? 'work' : (profileAtStart as 'personal' | 'work');
              set((s) => ({
                namespaces: {
                  ...s.namespaces,
                  [nk]: { ...s.namespaces[nk], projects, isLoadingProjects: false },
                },
              }));
            }
          } catch (error) {
            console.error('Failed to load projects:', error);
            nsSet(profileAtStart, { isLoadingProjects: false });
          }
        },

        loadProject: async (id: string) => {
          try {
            const project = await api.getProject(id);
            const profile = get().activeProfile;
            nsSet(profile, { currentProject: project });
          } catch (error) {
            console.error('Failed to load project:', error);
          }
        },

        setCurrentProject: (project: ProjectDetail | null) => {
          const profile = get().activeProfile;
          nsSet(profile, { currentProject: project });
        },

        createProject: async (data: ProjectCreateRequest) => {
          const { activeProfile } = get();
          await api.createProject({
            ...data,
            context_type: data.context_type ?? ((activeProfile === 'work' || activeProfile === 'org') ? 'org' : 'personal'),
          });
          await get().loadProjects();
        },

        updateProject: async (id: string, data: ProjectUpdateRequest) => {
          await api.updateProject(id, data);
          await get().loadProjects();
          const current = get().currentProject;
          if (current?.id === id) {
            const refreshed = await api.getProject(id);
            const profile = get().activeProfile;
            nsSet(profile, { currentProject: refreshed });
          }
        },

        deleteProject: async (id: string) => {
          await api.deleteProject(id);
          const profile = get().activeProfile;
          const current = get().currentProject;
          if (current?.id === id) {
            nsSet(profile, { currentProject: null });
          }
          await get().loadProjects();
        },

        deleteProjectMemory: async (projectId: string, memoryId: string) => {
          await api.deleteProjectMemory(projectId, memoryId);
          const current = get().currentProject;
          if (current?.id === projectId) {
            const refreshed = await api.getProject(projectId);
            const profile = get().activeProfile;
            nsSet(profile, { currentProject: refreshed });
          }
        },

        moveConversationToProject: async (conversationId: string, projectId: string | null) => {
          const profileAtStart = get().activeProfile;
          const nsKey: 'personal' | 'work' = (profileAtStart === 'work' || profileAtStart === 'org') ? 'work' : 'personal';
          // Capture the old project before any await
          const conv = get().conversations.find((c) => c.id === conversationId);
          const oldProjectId = conv?.project_id;

          if (projectId) {
            await api.assignConversation(projectId, conversationId);
          } else if (oldProjectId) {
            await api.removeConversation(oldProjectId, conversationId);
          }

          nsSet(profileAtStart, {
            conversations: get().namespaces[nsKey].conversations.map((c) =>
              c.id === conversationId ? { ...c, project_id: projectId ?? undefined } : c,
            ),
          });
          await get().loadProjects();
          const current = get().currentProject;
          if (current && (current.id === projectId || current.id === oldProjectId)) {
            const refreshed = await api.getProject(current.id);
            nsSet(profileAtStart, { currentProject: refreshed });
          }
        },

        // ── Project Files ─────────────────────────────────────────────────────

        loadProjectFiles: async (projectId: string) => {
          const profile = get().activeProfile;
          nsSet(profile, { isLoadingProjectFiles: true });
          try {
            const projectFiles = await api.getProjectFiles(projectId);
            nsSet(profile, { projectFiles, isLoadingProjectFiles: false });
          } catch (error) {
            console.error('Failed to load project files:', error);
            nsSet(profile, { isLoadingProjectFiles: false });
          }
        },

        uploadProjectFile: async (projectId: string, file: File) => {
          const newFile = await api.uploadProjectFile(projectId, file);
          const profile = get().activeProfile;
          const nsKey: 'personal' | 'work' = profile === 'org' ? 'work' : (profile as 'personal' | 'work');
          nsSet(profile, { projectFiles: [newFile, ...get().namespaces[nsKey].projectFiles] });
          await get().loadProjects();
        },

        deleteProjectFile: async (projectId: string, fileId: string) => {
          await api.deleteProjectFile(projectId, fileId);
          const profile = get().activeProfile;
          const nsKey: 'personal' | 'work' = profile === 'org' ? 'work' : (profile as 'personal' | 'work');
          nsSet(profile, {
            projectFiles: get().namespaces[nsKey].projectFiles.filter((f) => f.id !== fileId),
          });
        },

        // ── Project Conversations ─────────────────────────────────────────────

        loadProjectConversations: async (projectId: string) => {
          const profile = get().activeProfile;
          nsSet(profile, { isLoadingProjectConversations: true });
          try {
            const projectConversations = await api.getProjectConversations(projectId);
            nsSet(profile, { projectConversations, isLoadingProjectConversations: false });
          } catch (error) {
            console.error('Failed to load project conversations:', error);
            nsSet(profile, { isLoadingProjectConversations: false });
          }
        },

        // ── Project Instructions ──────────────────────────────────────────────

        updateProjectInstructions: async (projectId: string, systemPrompt: string) => {
          await api.updateProjectInstructions(projectId, systemPrompt);
          const current = get().currentProject;
          const profile = get().activeProfile;
          const nsKey: 'personal' | 'work' = profile === 'org' ? 'work' : (profile as 'personal' | 'work');
          if (current?.id === projectId) {
            nsSet(profile, { currentProject: { ...current, system_prompt: systemPrompt } });
          }
          nsSet(profile, {
            projects: get().namespaces[nsKey].projects.map((p) =>
              p.id === projectId ? { ...p, system_prompt: systemPrompt } : p,
            ),
          });
        },

        // ── Collaboration: Project members ────────────────────────────────────

        loadProjectMembers: async (projectId: string) => {
          return api.getProjectMembers(projectId);
        },

        addProjectMember: async (projectId: string, data: AddMemberRequest) => {
          return api.addProjectMember(projectId, data);
        },

        updateProjectMemberRole: async (projectId: string, userId: string, data: UpdateMemberRoleRequest) => {
          return api.updateProjectMemberRole(projectId, userId, data);
        },

        removeProjectMember: async (projectId: string, userId: string) => {
          await api.removeProjectMember(projectId, userId);
        },

        // ── Collaboration: Chat members ───────────────────────────────────────

        loadChatMembers: async (chatId: string) => {
          return api.getChatMembers(chatId);
        },

        addChatMember: async (chatId: string, data: AddMemberRequest) => {
          return api.addChatMember(chatId, data);
        },

        updateChatMemberRole: async (chatId: string, userId: string, data: UpdateMemberRoleRequest) => {
          return api.updateChatMemberRole(chatId, userId, data);
        },

        removeChatMember: async (chatId: string, userId: string) => {
          await api.removeChatMember(chatId, userId);
        },

        setVoiceModeEnabled: (enabled: boolean) => {
          if (!enabled) get().stopAudio();
          set({ voiceModeEnabled: enabled });
        },

        setSelectedVoice: (voice: string) => set({ selectedVoice: voice }),

        speakText: async (text: string) => {
          // Bump generation — any previous pipeline checks this and exits.
          // Do NOT emit isPlayingAudio:false here; that would cause VoiceChatOverlay
          // to briefly see "done" and restart the mic before the new audio begins.
          set({ ttsError: null });
          const gen = ++ttsGeneration;
          // Capture and clear presynth BEFORE stopping current audio
          const savedPresynth = _presynthPromise;
          const savedPresynthText = _presynthText;
          _presynthPromise = null;
          _presynthText = '';
          if (currentAudio) {
            currentAudio.pause();
            currentAudio.src = '';
            currentAudio = null;
          }

          const plainText = _stripMdForTts(text);
          if (!plainText) { set({ isPlayingAudio: false }); return; }

          const chunks = _splitTtsChunks(plainText);
          if (!chunks.length) { set({ isPlayingAudio: false }); return; }

          set({ isPlayingAudio: true });

          // Check if first chunk was pre-synthesized during streaming so audio
          // can start instantly. Exact match preferred; prefix fallback for strip diffs.
          let prefetch: Promise<ArrayBuffer | null>;
          if (savedPresynth && savedPresynthText && (
            chunks[0] === savedPresynthText ||
            chunks[0].startsWith(savedPresynthText.slice(0, 30)) ||
            savedPresynthText.startsWith(chunks[0].slice(0, 30))
          )) {
            prefetch = savedPresynth;
          } else {
            prefetch = api.synthesizeText(chunks[0], get().selectedVoice).catch(() => null);
          }

          for (let i = 0; i < chunks.length; i++) {
            if (ttsGeneration !== gen) break;

            const buffer = await prefetch;
            if (ttsGeneration !== gen) break;
            if (!buffer || buffer.byteLength === 0) {
              // TTS failed — show error on first chunk only (avoid repeat errors)
              if (i === 0) set({ ttsError: 'Read aloud unavailable — speech service error.' });
              break;
            }

            // Pre-fetch the NEXT chunk in the background while this one plays
            // — zero silence gap between sentences.
            prefetch = i + 1 < chunks.length
              ? api.synthesizeText(chunks[i + 1], get().selectedVoice).catch(() => null)
              : Promise.resolve(null);

            // Play current chunk. The onpause handler resolves the promise when
            // stopAudio() pauses us externally (pause() never fires onended).
            await new Promise<void>((resolve) => {
              if (ttsGeneration !== gen) { resolve(); return; }
              const blob = new Blob([buffer as ArrayBuffer], { type: 'audio/mpeg' });
              const url = URL.createObjectURL(blob);
              const audio = new Audio(url);
              currentAudio = audio;
              const done = () => { URL.revokeObjectURL(url); currentAudio = null; resolve(); };
              audio.onended = done;
              audio.onerror = done;
              // Fires when stopAudio() calls .pause() — only exit if cancelled
              audio.onpause = () => { if (ttsGeneration !== gen) done(); };
              audio.play().catch(done);
            });
          }

          if (ttsGeneration === gen) set({ isPlayingAudio: false });
        },

        stopAudio: () => {
          // Increment generation — all in-flight synthesis loops will detect
          // the mismatch and stop. Only one audio pipeline can be alive at a time.
          ttsGeneration++;
          _presynthPromise = null;
          _presynthText = '';
          if (currentAudio) {
            currentAudio.pause(); // triggers audio.onpause → done() in the loop above
            currentAudio.src = '';
            currentAudio = null;
          }
          set({ isPlayingAudio: false });
        },

        reset: () => set(initialState),
      };
    },
    {
      name: 'mela-chat-store',
      partialize: (state) => ({
        selectedModel: state.selectedModel,
        selectedVoice: state.selectedVoice,
        isSidebarOpen: state.isSidebarOpen,
        useRag: state.useRag,
        activeProfile: state.activeProfile,
        tenantId: state.tenantId,
        // Persist namespace conversation/project lists so switching profiles is instant
        // (no loading spinner on return). Message content is NOT persisted.
        namespaces: {
          personal: {
            conversations: state.namespaces.personal.conversations,
            currentConversation: null,
            isLoadingConversations: false,
            projects: state.namespaces.personal.projects,
            currentProject: null,
            isLoadingProjects: false,
            projectFiles: [],
            isLoadingProjectFiles: false,
            projectConversations: [],
            isLoadingProjectConversations: false,
            sharedWithMe: [],
            sharedByMe: [],
            sharedProjectsWithMe: [],
            sharedProjectsByMe: [],
            isLoadingShared: false,
          },
          work: {
            conversations: state.namespaces.work.conversations,
            currentConversation: null,
            isLoadingConversations: false,
            projects: state.namespaces.work.projects,
            currentProject: null,
            isLoadingProjects: false,
            projectFiles: [],
            isLoadingProjectFiles: false,
            projectConversations: [],
            isLoadingProjectConversations: false,
            sharedWithMe: state.namespaces.work.sharedWithMe ?? [],
            sharedByMe: state.namespaces.work.sharedByMe ?? [],
            sharedProjectsWithMe: state.namespaces.work.sharedProjectsWithMe ?? [],
            sharedProjectsByMe: state.namespaces.work.sharedProjectsByMe ?? [],
            isLoadingShared: false,
          },
        },
      }),

      // After Zustand restores persisted state, copy the active namespace to root
      // so the root-level `conversations`/`projects` arrays are immediately populated.
      // Without this, root fields start as `[]` (from initialState) even though the
      // namespace already has data from the previous session.
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        const nsKey: 'personal' | 'work' =
          (state.activeProfile === 'work' || state.activeProfile === 'org') ? 'work' : 'personal';
        const ns = state.namespaces[nsKey];
        // Assign namespace fields to root level (Zustand sets the initial snapshot)
        state.conversations = ns.conversations;
        state.projects = ns.projects;
        state.currentConversation = ns.currentConversation;
        state.currentProject = ns.currentProject;
        state.isLoadingConversations = ns.isLoadingConversations;
        state.isLoadingProjects = ns.isLoadingProjects;
        state.sharedWithMe = ns.sharedWithMe ?? [];
        state.sharedByMe = ns.sharedByMe ?? [];
        state.sharedProjectsWithMe = ns.sharedProjectsWithMe ?? [];
        state.sharedProjectsByMe = ns.sharedProjectsByMe ?? [];
        state.isLoadingShared = false;
        // Also prime the API client profile context from persisted state
        const profileMode: 'personal' | 'work' = nsKey;
        const tenantId =
          profileMode === 'work' ? (state.tenantId ?? null) : null;
        api.setProfileContext(profileMode, tenantId);
      },
    },
  ),
);

// ── useChatNS convenience hook ────────────────────────────────────────────────
//
// Since root-level fields are always in sync with the active namespace,
// this is just an alias for useChatStore. It exists so components can
// explicitly signal that they consume namespace-scoped state.
export const useChatNS = useChatStore;
