/**
 * Mela AI - API Client
 */

import { IPublicClientApplication, InteractionRequiredAuthError } from '@azure/msal-browser';
import { backendTokenRequest } from '@/lib/msal/config';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const DEV_TOKEN_KEY = 'mela_dev_token';
const DEV_USER_KEY = 'mela_dev_user';

// ─────────────────────────────────────────────────────────────────────────────
// LRU cache with TTL — keeps recently used entries, evicts oldest when full
// ─────────────────────────────────────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  expiresAt: number;
}

class SimpleCache {
  private store = new Map<string, CacheEntry<unknown>>();
  private maxSize: number;

  constructor(maxSize = 500) {
    this.maxSize = maxSize;
  }

  get<T>(key: string): T | null {
    const entry = this.store.get(key) as CacheEntry<T> | undefined;
    if (!entry) return null;
    if (Date.now() > entry.expiresAt) {
      this.store.delete(key);
      return null;
    }
    // Move to end (most recently used) — Map preserves insertion order
    this.store.delete(key);
    this.store.set(key, entry);
    return entry.data;
  }

  set<T>(key: string, data: T, ttlMs: number): void {
    // Delete first to update insertion order
    this.store.delete(key);
    this.store.set(key, { data, expiresAt: Date.now() + ttlMs });
    // Evict oldest entries if over capacity
    while (this.store.size > this.maxSize) {
      const oldest = this.store.keys().next().value;
      if (oldest !== undefined) this.store.delete(oldest);
    }
  }

  invalidate(keyPrefix: string): void {
    for (const key of Array.from(this.store.keys())) {
      if (key.startsWith(keyPrefix)) this.store.delete(key);
    }
  }

  has(key: string): boolean {
    const entry = this.store.get(key);
    if (!entry) return false;
    if (Date.now() > entry.expiresAt) {
      this.store.delete(key);
      return false;
    }
    return true;
  }
}

const cache = new SimpleCache();

// Separate long-lived cache for conversation messages (survives across navigation)
const messageCache = new SimpleCache(100);

// ─────────────────────────────────────────────────────────────────────────────

export interface DevUser {
  id: string;
  email: string;
  name: string;
  roles: string[];
  department?: string;
  job_title?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Profile context carried on every API request
// ─────────────────────────────────────────────────────────────────────────────

export type ProfileMode = 'work' | 'personal';
export type Mode = 'work' | 'personal';

export interface UserSession {
  mode: Mode;
  userId: string;
  tenantId?: string;
}

/** Dev-mode sentinel — sent as X-Tenant-Id when no real Entra tenant exists. */
export const DEV_TENANT_ID = 'dev-tenant-001';

class ApiClient {
  private baseUrl: string;
  private msalInstance?: IPublicClientApplication;

  // Active profile context — updated by the store on every profile switch
  private _profileMode: ProfileMode = 'personal';
  private _tenantId: string | null = null;

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  /** Called by the store whenever the active profile changes. */
  setProfileContext(profileMode: ProfileMode, tenantId: string | null): void {
    this._profileMode = profileMode;
    this._tenantId = profileMode === 'work' ? (tenantId ?? DEV_TENANT_ID) : null;
  }

  getProfileMode(): ProfileMode {
    return this._profileMode;
  }

  buildUserSession(userId: string): UserSession {
    return {
      mode: this._profileMode,
      userId,
      ...(this._profileMode === 'work' && this._tenantId ? { tenantId: this._tenantId } : {}),
    };
  }

  setMsalInstance(instance: IPublicClientApplication) {
    this.msalInstance = instance;
  }

  /** Build the profile namespace headers to attach to every request. */
  private _profileHeaders(): Record<string, string> {
    const h: Record<string, string> = { 'X-Profile-Mode': this._profileMode };
    if (this._profileMode === 'work' && this._tenantId) {
      h['X-Tenant-Id'] = this._tenantId;
    }
    // Locale: send the user's IANA timezone so the LLM gets accurate "now"
    // context. Falls back to America/Chicago (CDT/CST) on the backend when
    // absent or invalid.
    try {
      if (typeof Intl !== 'undefined') {
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        if (tz) h['X-User-Timezone'] = tz;
      }
    } catch {
      /* ignore \u2014 backend default to America/Chicago */
    }
    return h;
  }

  // ── Dev auth ──────────────────────────────────────────────────────────────

  getDevToken(): string | null {
    if (typeof window === 'undefined') return null;
    return localStorage.getItem(DEV_TOKEN_KEY);
  }

  getDevUser(): DevUser | null {
    if (typeof window === 'undefined') return null;
    const userStr = localStorage.getItem(DEV_USER_KEY);
    if (!userStr) return null;
    try {
      return JSON.parse(userStr);
    } catch {
      return null;
    }
  }

  isDevAuthenticated(): boolean {
    return !!this.getDevToken();
  }

  clearDevAuth(): void {
    if (typeof window === 'undefined') return;
    localStorage.removeItem(DEV_TOKEN_KEY);
    localStorage.removeItem(DEV_USER_KEY);
  }

  // ── Token acquisition ──────────────────────────────────────────────────────

  private async getAccessToken(): Promise<string | null> {
    const devToken = this.getDevToken();
    if (devToken) return devToken;

    if (!this.msalInstance) return null;
    const accounts = this.msalInstance.getAllAccounts();
    if (accounts.length === 0) return null;

    try {
      // Acquire token for the backend API (not Graph) — audience must match backend
      const response = await this.msalInstance.acquireTokenSilent({
        ...backendTokenRequest,
        account: accounts[0],
      });
      return response.accessToken;
    } catch (err) {
      // For ANY silent failure (InteractionRequired, stale cache, interaction in
      // progress, etc.) fall through to interactive popup.  This ensures the user
      // gets a valid token even after long idle periods or after backend restarts
      // where the cached token may not match the expected audience.
      if (process.env.NODE_ENV === 'development') {
        console.warn('[MSAL] acquireTokenSilent failed, trying popup:', err);
      }
      try {
        const response = await this.msalInstance.acquireTokenPopup({
          ...backendTokenRequest,
          account: accounts[0],
        });
        return response.accessToken;
      } catch (popupErr) {
        if (process.env.NODE_ENV === 'development') {
          console.warn('[MSAL] acquireTokenPopup also failed:', popupErr);
        }
        return null;
      }
    }
  }

  // ── Generic fetch wrapper ──────────────────────────────────────────────────

  async fetch<T>(
    endpoint: string,
    options: RequestInit = {},
    signal?: AbortSignal,
  ): Promise<T> {
    const token = await this.getAccessToken();

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      // Profile namespace headers — enforced on every request
      ...this._profileHeaders(),
      ...(options.headers as Record<string, string> || {}),
    };

    if (token) headers['Authorization'] = `Bearer ${token}`;

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${endpoint}`, {
        ...options,
        headers,
        signal,
      });
    } catch (err: any) {
      if (err?.name === 'AbortError') throw err;
      throw new Error('Unable to connect to the server. Please check that the backend is running.');
    }

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'An error occurred' }));
      if (response.status === 401) {
        if (this.getDevToken()) {
          // Dev token rejected (e.g. backend restarted with new JWT secret) — clear and re-login
          this.clearDevAuth();
          if (typeof window !== 'undefined') {
            window.location.href = '/';
          }
        } else if (this.msalInstance) {
          // MSAL token rejected by backend — session likely stale. Trigger silent token
          // refresh; if that fails the next request will try the popup.
          const accounts = this.msalInstance.getAllAccounts();
          if (accounts.length > 0) {
            this.msalInstance.acquireTokenSilent({
              ...backendTokenRequest,
              account: accounts[0],
              forceRefresh: true,
            }).catch(() => {
              // Refresh failed — on the next request the popup path will run.
              if (process.env.NODE_ENV === 'development') {
                console.warn('[MSAL] Force-refresh failed after 401. Next request will try popup.');
              }
            });
          }
        }
      }
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return response.json();
  }

  // ── Generic public helpers (used by admin tabs) ───────────────────────────

  async get<T>(path: string): Promise<T> {
    return this.fetch<T>(`/api/v1${path}`);
  }

  async post<T>(path: string, body: unknown): Promise<T> {
    return this.fetch<T>(`/api/v1${path}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async patch<T>(path: string, body: unknown): Promise<T> {
    return this.fetch<T>(`/api/v1${path}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  }

  async delete<T = void>(path: string): Promise<T> {
    return this.fetch<T>(`/api/v1${path}`, { method: 'DELETE' });
  }

  // ── Notifications ─────────────────────────────────────────────────────────

  async getNotifications(unreadOnly = false): Promise<any[]> {
    const q = unreadOnly ? '?unread_only=true' : '';
    return this.fetch<any[]>(`/api/v1/notifications${q}`);
  }

  async getUnreadCount(): Promise<{ unread_count: number }> {
    return this.fetch<{ unread_count: number }>('/api/v1/notifications/unread-count');
  }

  async markNotificationRead(id: string): Promise<void> {
    await this.fetch<void>(`/api/v1/notifications/${id}/read`, { method: 'PATCH' });
  }

  async markAllNotificationsRead(): Promise<void> {
    await this.fetch<void>('/api/v1/notifications/mark-all-read', { method: 'POST' });
  }

  async deleteNotification(id: string): Promise<void> {
    await this.fetch<void>(`/api/v1/notifications/${id}`, { method: 'DELETE' });
  }

  // ── Auth endpoints ─────────────────────────────────────────────────────────

  async login() {
    return this.fetch<{ user: User; welcome_message: string; tenant_id: string | null }>('/api/v1/auth/login', {
      method: 'POST',
    });
  }

  /**
   * Revoke the current backend session and clear all client-side auth state.
   * Callers should follow with `instance.logoutRedirect()` to complete the
   * MSAL sign-out at the IdP.
   */
  async logout(): Promise<void> {
    try {
      await this.fetch<{ message: string; revoked: number }>('/api/v1/auth/logout', {
        method: 'POST',
      });
    } catch {
      // Best-effort: even if the backend call fails (network, expired token),
      // we still clear local state below so the user is logged out client-side.
    }
    if (typeof window !== 'undefined') {
      try {
        localStorage.removeItem(DEV_TOKEN_KEY);
        localStorage.removeItem(DEV_USER_KEY);
        sessionStorage.clear();
      } catch {
        /* ignore */
      }
    }
  }

  async devLogin(
    username = process.env.NEXT_PUBLIC_DEV_USERNAME || 'dev',
    password = process.env.NEXT_PUBLIC_DEV_PASSWORD || 'dev',
  ): Promise<{ user: DevUser; access_token: string }> {
    const response = await fetch(`${this.baseUrl}/api/v1/auth/dev-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Dev login failed' }));
      throw new Error(error.detail || 'Dev login failed');
    }

    const data = await response.json();
    if (typeof window !== 'undefined') {
      localStorage.setItem(DEV_TOKEN_KEY, data.access_token);
      localStorage.setItem(DEV_USER_KEY, JSON.stringify(data.user));
    }
    return { user: data.user, access_token: data.access_token };
  }

  async getCurrentUser() {
    return this.fetch<User>('/api/v1/auth/me');
  }

  async updatePreferences(preferredModel: string) {
    return this.fetch<User>('/api/v1/auth/me', {
      method: 'PUT',
      body: JSON.stringify({ preferred_model: preferredModel }),
    });
  }

  // ── Chat endpoints ─────────────────────────────────────────────────────────

  async getConversations(limit = 50, offset = 0, archived = false, contextType?: 'org' | 'personal') {
    const mode = this._profileMode;
    const cacheKey = `conversations:${mode}:${limit}:${offset}:${archived}:${contextType ?? ''}`;
    const cached = cache.get<Conversation[]>(cacheKey);
    if (cached) return cached;

    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
      archived: String(archived),
    });
    if (contextType) params.set('context_type', contextType);

    const data = await this.fetch<Conversation[]>(`/api/v1/chat/conversations?${params}`);
    cache.set(cacheKey, data, 60_000); // 60-second TTL
    return data;
  }

  invalidateConversationCache() {
    cache.invalidate('conversations:');
  }

  async getSharedWithMe(): Promise<Conversation[]> {
    return this.fetch<Conversation[]>('/api/v1/chat/conversations/shared-with-me');
  }

  async getSharedByMe(): Promise<Conversation[]> {
    return this.fetch<Conversation[]>('/api/v1/chat/conversations/shared-by-me');
  }

  /** Invalidate only the message-level cache for a specific conversation. */
  invalidateConversationDetail(id: string) {
    cache.invalidate(`conversation:${this._profileMode}:${id}`);
    messageCache.invalidate(`msgs:${this._profileMode}:${id}`);
  }

  async getConversation(id: string) {
    // Check the long-lived message cache first for instant switching
    const msgCacheKey = `msgs:${this._profileMode}:${id}`;
    const cachedDetail = messageCache.get<ConversationDetail>(msgCacheKey);
    if (cachedDetail) return cachedDetail;

    // Short-lived API cache
    const mode = this._profileMode;
    const cacheKey = `conversation:${mode}:${id}`;
    const cached = cache.get<ConversationDetail>(cacheKey);
    if (cached) {
      // Promote to long-lived message cache
      messageCache.set(msgCacheKey, cached, 10 * 60_000); // 10-minute TTL
      return cached;
    }

    const data = await this.fetch<ConversationDetail>(`/api/v1/chat/conversations/${id}`);
    cache.set(cacheKey, data, 2 * 60_000); // 2-minute TTL
    messageCache.set(msgCacheKey, data, 10 * 60_000); // 10-minute message cache
    return data;
  }

  /** Update the message cache after sending a new message (avoids re-fetch). */
  updateCachedMessages(conversationId: string, messages: Message[]) {
    const msgCacheKey = `msgs:${this._profileMode}:${conversationId}`;
    const cached = messageCache.get<ConversationDetail>(msgCacheKey);
    if (cached) {
      const updated = { ...cached, messages, message_count: messages.length };
      messageCache.set(msgCacheKey, updated, 10 * 60_000);
      cache.set(`conversation:${this._profileMode}:${conversationId}`, updated, 2 * 60_000);
    }
  }

  async createConversation(data: CreateConversationRequest) {
    const result = await this.fetch<Conversation>('/api/v1/chat/conversations', {
      method: 'POST',
      body: JSON.stringify(data),
    });
    cache.invalidate('conversations:');
    return result;
  }

  async updateConversation(id: string, data: UpdateConversationRequest) {
    const result = await this.fetch<Conversation>(`/api/v1/chat/conversations/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
    cache.invalidate('conversations:');
    this.invalidateConversationDetail(id);
    return result;
  }

  async deleteConversation(id: string) {
    const result = await this.fetch<{ message: string }>(`/api/v1/chat/conversations/${id}`, {
      method: 'DELETE',
    });
    cache.invalidate('conversations:');
    this.invalidateConversationDetail(id);
    return result;
  }

  async generateTitle(firstMessage: string): Promise<string> {
    const result = await this.fetch<{ title: string }>('/api/v1/chat/conversations/generate-title', {
      method: 'POST',
      body: JSON.stringify({ first_message: firstMessage }),
    });
    return result.title;
  }

  async getModels() {
    const cached = cache.get<ModelInfo[]>('models');
    if (cached) return cached;

    const data = await this.fetch<ModelInfo[]>('/api/v1/chat/models');
    cache.set('models', data, 5 * 60_000); // 5-minute TTL for models
    return data;
  }

  async getModelInsights(): Promise<ModelInsight[]> {
    // Short TTL so governance changes propagate quickly to the welcome screen
    const cached = cache.get<ModelInsight[]>('model_insights');
    if (cached) return cached;

    const data = await this.fetch<ModelInsight[]>('/api/v1/chat/models/insights');
    cache.set('model_insights', data, 60_000); // 1-minute TTL
    return data;
  }

  // ── Streaming chat ─────────────────────────────────────────────────────────

  async *streamChat(
    request: ChatRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatChunk, void, unknown> {
    const token = await this.getAccessToken();

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}/api/v1/chat/completions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          // Profile namespace headers on streaming requests too
          ...this._profileHeaders(),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ ...request, stream: true }),
        signal,
      });
    } catch (fetchErr: any) {
      if (fetchErr?.name === 'AbortError') throw fetchErr;
      throw new Error(
        'Unable to connect to the server. Please check that the backend is running and try again.',
      );
    }

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'An error occurred' }));
      if (response.status === 401 && this.getDevToken()) {
        this.clearDevAuth();
        if (typeof window !== 'undefined') {
          window.location.href = '/';
        }
      }
      if (response.status === 429) {
        throw new Error('Rate limit exceeded. Please wait a moment and try again.');
      }
      if (response.status >= 500) {
        throw new Error('Server error. The AI service may be temporarily unavailable.');
      }
      throw new Error(error.detail || `Request failed (HTTP ${response.status})`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error('No response body');

    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        let readResult;
        try {
          readResult = await reader.read();
        } catch (readErr: any) {
          if (readErr?.name === 'AbortError') throw readErr;
          throw new Error('Connection lost while receiving response. Please try again.');
        }
        const { done, value } = readResult;
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (data === '[DONE]') return;
            try {
              const chunk = JSON.parse(data) as ChatChunk;
              yield chunk;
            } catch {
              // Skip invalid JSON lines
            }
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  // ── Document endpoints ─────────────────────────────────────────────────────

  async uploadDocument(file: File, title?: string, addToKnowledgeBase = true) {
    const token = await this.getAccessToken();
    const formData = new FormData();
    formData.append('file', file);
    if (title) formData.append('title', title);
    formData.append('add_to_knowledge_base', String(addToKnowledgeBase));

    const response = await fetch(`${this.baseUrl}/api/v1/documents/upload`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...this._profileHeaders() },
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
      throw new Error(error.detail);
    }

    return response.json() as Promise<Document>;
  }

  async getDocuments(limit = 50, offset = 0, source?: string) {
    let url = `/api/v1/documents/?limit=${limit}&offset=${offset}`;
    if (source) url += `&source=${source}`;
    return this.fetch<Document[]>(url);
  }

  async deleteDocument(id: string) {
    return this.fetch<{ message: string }>(`/api/v1/documents/${id}`, { method: 'DELETE' });
  }

  async searchDocuments(query: string, topK = 5) {
    return this.fetch<SearchResponse>('/api/v1/documents/search', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK }),
    });
  }

  // ── Generated file download ────────────────────────────────────────────────

  async downloadGeneratedFile(fileLogId: string): Promise<Blob> {
    const token = await this.getAccessToken();
    const response = await fetch(`${this.baseUrl}/api/v1/files/${fileLogId}`, {
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...this._profileHeaders(),
      },
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: 'Download failed' }));
      throw new Error(err.detail);
    }
    return response.blob();
  }

  // ── Admin endpoints ────────────────────────────────────────────────────────

  async getAdminStatus(): Promise<{ is_admin: boolean; newly_promoted?: boolean }> {
    return this.fetch<{ is_admin: boolean; newly_promoted?: boolean }>('/api/v1/admin/me');
  }

  async ackAdminPromotion(): Promise<void> {
    await this.fetch<{ ok: boolean }>('/api/v1/admin/me/ack-promotion', { method: 'POST' });
  }

  async requestAdminAccess(): Promise<{ requested: boolean }> {
    return this.fetch<{ requested: boolean }>('/api/v1/admin/request-access', { method: 'POST' });
  }

  async getAdminAccessRequests(): Promise<AdminAccessRequest[]> {
    return this.fetch<AdminAccessRequest[]>('/api/v1/admin/access-requests');
  }

  async getMonitoring() {
    return this.fetch<any>('/api/v1/admin/monitoring');
  }

  async getStats() {
    return this.fetch<UsageStats>('/api/v1/admin/stats');
  }

  async getAnalytics(days = 30) {
    return this.fetch<AnalyticsResponse>(`/api/v1/admin/analytics?days=${days}`);
  }

  async getUsers(limit = 50, offset = 0) {
    return this.fetch<User[]>(`/api/v1/admin/users?limit=${limit}&offset=${offset}`);
  }

  async updateUser(id: string, data: UpdateUserRequest) {
    return this.fetch<User>(`/api/v1/admin/users/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async getAuditLogs(params: AuditLogParams = {}) {
    const searchParams = new URLSearchParams();
    if (params.limit) searchParams.set('limit', String(params.limit));
    if (params.offset) searchParams.set('offset', String(params.offset));
    if (params.userId) searchParams.set('user_id', params.userId);
    if (params.action) searchParams.set('action', params.action);
    return this.fetch<AuditLog[]>(`/api/v1/admin/audit-logs?${searchParams}`);
  }

  async listAdminUsers(): Promise<any[]> {
    return this.fetch<any[]>('/api/v1/admin/users');
  }

  async updateAdminUser(userId: string, data: { role?: string; is_active?: boolean; daily_token_limit?: number }): Promise<any> {
    return this.fetch<any>(`/api/v1/admin/users/${userId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }

  async getBootstrapList() {
    return this.fetch<{
      bootstrap_admins: {
        email: string;
        in_db: boolean;
        user_id: string | null;
        current_role: string | null;
        is_admin: boolean;
        bootstrap_elevated_at: string | null;
      }[];
    }>('/api/v1/admin/bootstrap-list');
  }

  async getTokenUsage(days = 7) {
    return this.fetch<{
      days: number;
      users: {
        user_id: string;
        name: string;
        email: string;
        role: string;
        daily_token_limit: number;
        tokens_used_today: number;
        period_tokens: number;
        period_prompt_tokens: number;
        period_completion_tokens: number;
        period_requests: number;
        pct_daily_limit_used: number;
      }[];
    }>(`/api/v1/admin/token-usage?days=${days}`);
  }

  // ── Admin: Private conversations ──────────────────────────────────────────

  async getPrivateConversations(limit = 50, offset = 0, userId?: string) {
    let url = `/api/v1/admin/private-conversations?limit=${limit}&offset=${offset}`;
    if (userId) url += `&user_id=${userId}`;
    return this.fetch<Conversation[]>(url);
  }

  async getPrivateConversationDetail(id: string) {
    return this.fetch<ConversationDetail>(`/api/v1/admin/private-conversations/${id}`);
  }

  async deletePrivateConversation(id: string) {
    return this.fetch<{ message: string }>(`/api/v1/admin/private-conversations/${id}`, {
      method: 'DELETE',
    });
  }

  // ── Admin: Org settings ───────────────────────────────────────────────────

  async getOrgSettings() {
    return this.fetch<OrgSettings>('/api/v1/admin/org-settings');
  }

  async updateOrgSettings(settings: Partial<OrgSettings>) {
    return this.fetch<OrgSettings>('/api/v1/admin/org-settings', {
      method: 'PUT',
      body: JSON.stringify(settings),
    });
  }

  // ── Translation endpoints ──────────────────────────────────────────────────

  async translateText(text: string, targetLanguage: string, sourceLanguage?: string) {
    return this.fetch<TranslationResult>('/api/v1/translation/translate', {
      method: 'POST',
      body: JSON.stringify({ text, target_language: targetLanguage, source_language: sourceLanguage }),
    });
  }

  async translateBatch(texts: string[], targetLanguage: string, sourceLanguage?: string) {
    return this.fetch<{ translations: TranslationResult[] }>('/api/v1/translation/translate/batch', {
      method: 'POST',
      body: JSON.stringify({ texts, target_language: targetLanguage, source_language: sourceLanguage }),
    });
  }

  async detectLanguage(text: string) {
    return this.fetch<LanguageDetectionResult>('/api/v1/translation/detect', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
  }

  async getSupportedLanguages() {
    return this.fetch<{ languages: Record<string, string> }>('/api/v1/translation/languages');
  }

  // ── Image Generation (DALL-E) endpoints ───────────────────────────────────

  async generateImage(
    prompt: string,
    size: ImageSize = '1024x1024',
    quality: ImageQuality = 'standard',
    style: ImageStyle = 'vivid',
  ) {
    return this.fetch<ImageGenerationResult>('/api/v1/images/generate', {
      method: 'POST',
      body: JSON.stringify({ prompt, size, quality, style }),
    });
  }

  async generateImagesBatch(
    prompts: string[],
    size: ImageSize = '1024x1024',
    quality: ImageQuality = 'standard',
    style: ImageStyle = 'vivid',
  ) {
    return this.fetch<{ images: ImageGenerationResult[] }>('/api/v1/images/generate/batch', {
      method: 'POST',
      body: JSON.stringify({ prompts, size, quality, style }),
    });
  }

  async getImageServiceStatus() {
    return this.fetch<ImageServiceStatus>('/api/v1/images/status');
  }

  // ── Document Intelligence endpoints ───────────────────────────────────────

  async analyzeDocument(file: File, model: DocumentAnalysisModel = 'prebuilt-document') {
    const token = await this.getAccessToken();
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(
      `${this.baseUrl}/api/v1/document-intelligence/analyze?model=${model}`,
      { method: 'POST', headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...this._profileHeaders() }, body: formData },
    );
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Analysis failed' }));
      throw new Error(error.detail);
    }
    return response.json() as Promise<DocumentAnalysisResult>;
  }

  async extractText(file: File) {
    const token = await this.getAccessToken();
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${this.baseUrl}/api/v1/document-intelligence/extract-text`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...this._profileHeaders() },
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Extraction failed' }));
      throw new Error(error.detail);
    }
    return response.json() as Promise<TextExtractionResult>;
  }

  async extractTables(file: File) {
    const token = await this.getAccessToken();
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${this.baseUrl}/api/v1/document-intelligence/extract-tables`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...this._profileHeaders() },
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Table extraction failed' }));
      throw new Error(error.detail);
    }
    return response.json() as Promise<TableExtractionResult>;
  }

  async analyzeInvoice(file: File) {
    const token = await this.getAccessToken();
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${this.baseUrl}/api/v1/document-intelligence/analyze/invoice`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...this._profileHeaders() },
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Invoice analysis failed' }));
      throw new Error(error.detail);
    }
    return response.json() as Promise<InvoiceAnalysisResult>;
  }

  async analyzeReceipt(file: File) {
    const token = await this.getAccessToken();
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${this.baseUrl}/api/v1/document-intelligence/analyze/receipt`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...this._profileHeaders() },
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Receipt analysis failed' }));
      throw new Error(error.detail);
    }
    return response.json() as Promise<ReceiptAnalysisResult>;
  }

  async getDocumentIntelligenceModels() {
    return this.fetch<{ models: DocumentModelInfo[] }>('/api/v1/document-intelligence/models');
  }

  async getDocumentIntelligenceStatus() {
    return this.fetch<DocumentIntelligenceStatus>('/api/v1/document-intelligence/status');
  }

  // ── User Settings endpoints ─────────────────────────────────────────────────

  async getUserUsage(days?: number) {
    // Pass the browser's UTC offset so the backend buckets data in the user's local timezone
    const tzOffset = -new Date().getTimezoneOffset(); // e.g. +180 for UTC+3, -300 for UTC-5
    const params = new URLSearchParams({ tz_offset: String(tzOffset) });
    if (days) params.set('days', String(days));
    return this.fetch<UserUsage>(`/api/v1/user/usage?${params}`);
  }

  async getUserPreferences() {
    return this.fetch<UserPreferences>('/api/v1/user/preferences');
  }

  async updateUserPreferences(prefs: UserPreferences) {
    return this.fetch<UserPreferences>('/api/v1/user/preferences', {
      method: 'PUT',
      body: JSON.stringify(prefs),
    });
  }

  async getUserFeatures() {
    return this.fetch<UserFeatures>('/api/v1/user/features');
  }

  async deleteUserHistory() {
    return this.fetch<{ detail: string }>('/api/v1/user/history', { method: 'DELETE' });
  }

  async exportUserData() {
    const token = await this.getAccessToken();
    const response = await fetch(`${this.baseUrl}/api/v1/user/export`, {
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    });
    if (!response.ok) throw new Error('Export failed');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'mela-export.json';
    a.click();
    URL.revokeObjectURL(url);
  }

  // ── Connectors endpoints ──────────────────────────────────────────────────

  async getConnectors() {
    return this.fetch<ConnectorInfo[]>('/api/v1/connectors/');
  }

  async createConnector(data: { name: string; connector_type: string; config?: Record<string, any>; is_enabled?: boolean }) {
    return this.fetch<ConnectorInfo>('/api/v1/connectors/', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async updateConnector(id: string, data: { name?: string; config?: Record<string, any>; is_enabled?: boolean }) {
    return this.fetch<ConnectorInfo>(`/api/v1/connectors/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async deleteConnector(id: string) {
    return this.fetch<{ detail: string }>(`/api/v1/connectors/${id}`, { method: 'DELETE' });
  }

  async testConnector(id: string) {
    return this.fetch<{ status: string; message: string }>(`/api/v1/connectors/${id}/test`, {
      method: 'POST',
    });
  }

  // ── Speech / Voice endpoints ───────────────────────────────────────────────

  async transcribeAudio(
    audioBlob: Blob,
    language = 'en-US',
  ): Promise<{ text: string; confidence: number; duration_ms: number }> {
    const token = await this.getAccessToken();
    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');
    formData.append('language', language);

    const response = await fetch(`${this.baseUrl}/api/v1/speech/transcribe`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...this._profileHeaders() },
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Transcription failed' }));
      throw new Error(error.detail || 'Transcription failed');
    }

    return response.json();
  }

  async synthesizeText(text: string, voice = 'en-US-AriaNeural'): Promise<ArrayBuffer> {
    const token = await this.getAccessToken();
    const params = new URLSearchParams({ text, voice });
    const response = await fetch(`${this.baseUrl}/api/v1/speech/synthesize?${params}`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...this._profileHeaders() },
    });
    if (!response.ok) throw new Error('Speech synthesis failed');
    return response.arrayBuffer();
  }

  // ── Enterprise Connector endpoints ────────────────────────────────────────

  async getConnectorStatus(): Promise<ConnectorStatus[]> {
    return this.fetch<ConnectorStatus[]>('/api/v1/connectors/status');
  }

  async triggerSync(connectorType: string, sourceId: string, fullSync = false) {
    return this.fetch(`/api/v1/connectors/${connectorType}/sync`, {
      method: 'POST',
      body: JSON.stringify({ source_id: sourceId, full_sync: fullSync }),
    });
  }

  async reindexSharePoint() {
    return this.fetch('/api/v1/connectors/sharepoint/reindex', { method: 'POST' });
  }

  async reindexOrgWebsite() {
    return this.fetch('/api/v1/connectors/org_website/reindex', { method: 'POST' });
  }

  async syncOneDrive(fullSync = false): Promise<{ job_id: string; status: string }> {
    const token = await this.getAccessToken();
    if (!token) {
      throw new Error('No access token available for OneDrive sync');
    }
    return this.fetch<{ job_id: string; status: string }>('/api/v1/connectors/onedrive/sync', {
      method: 'POST',
      body: JSON.stringify({ 
        delegated_token: token, 
        full_sync: fullSync 
      }),
    });
  }

  async getConnectorJobs(connectorType?: string) {
    const url = connectorType
      ? `/api/v1/connectors/jobs?connector_type=${connectorType}`
      : '/api/v1/connectors/jobs';
    return this.fetch<{ jobs: ConnectorJob[] }>(url);
  }

  async getIndexStatus() {
    return this.fetch<{ indexes: IndexStats[] }>('/api/v1/connectors/index/status');
  }

  // ── Projects endpoints ─────────────────────────────────────────────────────

  async listProjects(includeArchived = false, contextType?: 'org' | 'personal'): Promise<Project[]> {
    const params = new URLSearchParams({ include_archived: String(includeArchived) });
    if (contextType) params.set('context_type', contextType);
    return this.fetch<Project[]>(`/api/v1/projects?${params}`);
  }

  async getSharedWithMeProjects(): Promise<Project[]> {
    return this.fetch<Project[]>('/api/v1/projects/shared-with-me');
  }

  async getSharedByMeProjects(): Promise<Project[]> {
    return this.fetch<Project[]>('/api/v1/projects/shared-by-me');
  }

  async createProject(data: ProjectCreateRequest): Promise<Project> {
    return this.fetch<Project>('/api/v1/projects', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async getProject(id: string): Promise<ProjectDetail> {
    return this.fetch<ProjectDetail>(`/api/v1/projects/${id}`);
  }

  async updateProject(id: string, data: ProjectUpdateRequest): Promise<Project> {
    return this.fetch<Project>(`/api/v1/projects/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async deleteProject(id: string): Promise<void> {
    await this.fetch<void>(`/api/v1/projects/${id}`, { method: 'DELETE' });
  }

  async assignConversation(projectId: string, convId: string): Promise<void> {
    await this.fetch<void>(`/api/v1/projects/${projectId}/conversations/${convId}`, {
      method: 'POST',
    });
  }

  async removeConversation(projectId: string, convId: string): Promise<void> {
    await this.fetch<void>(`/api/v1/projects/${projectId}/conversations/${convId}`, {
      method: 'DELETE',
    });
  }

  async addProjectMemory(projectId: string, fact: string): Promise<ProjectMemoryItem> {
    return this.fetch<ProjectMemoryItem>(`/api/v1/projects/${projectId}/memories`, {
      method: 'POST',
      body: JSON.stringify({ fact }),
    });
  }

  async deleteProjectMemory(projectId: string, memoryId: string): Promise<void> {
    await this.fetch<void>(`/api/v1/projects/${projectId}/memories/${memoryId}`, {
      method: 'DELETE',
    });
  }

  async getProjectConversations(projectId: string): Promise<ProjectConversation[]> {
    return this.fetch<ProjectConversation[]>(`/api/v1/projects/${projectId}/conversations`);
  }

  async getProjectFiles(projectId: string): Promise<ProjectFile[]> {
    return this.fetch<ProjectFile[]>(`/api/v1/projects/${projectId}/files`);
  }

  async uploadProjectFile(projectId: string, file: File): Promise<ProjectFile> {
    const token = await this.getAccessToken();
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${this.baseUrl}/api/v1/projects/${projectId}/files`, {
      method: 'POST',
      headers: {
        ...this._profileHeaders(),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
      throw new Error(error.detail || 'Failed to upload file');
    }
    return response.json();
  }

  async deleteProjectFile(projectId: string, fileId: string): Promise<void> {
    await this.fetch<void>(`/api/v1/projects/${projectId}/files/${fileId}`, { method: 'DELETE' });
  }

  async getProjectInstructions(projectId: string): Promise<{ system_prompt: string }> {
    return this.fetch<{ system_prompt: string }>(`/api/v1/projects/${projectId}/instructions`);
  }

  async updateProjectInstructions(projectId: string, systemPrompt: string): Promise<void> {
    await this.fetch<void>(`/api/v1/projects/${projectId}/instructions`, {
      method: 'PUT',
      body: JSON.stringify({ system_prompt: systemPrompt }),
    });
  }

  // ── Collaboration: Project members ─────────────────────────────────────────

  async getProjectMembers(projectId: string): Promise<ProjectMember[]> {
    return this.fetch<ProjectMember[]>(`/api/v1/projects/${projectId}/members`);
  }

  async addProjectMember(projectId: string, data: AddMemberRequest): Promise<ProjectMember> {
    return this.fetch<ProjectMember>(`/api/v1/projects/${projectId}/members`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async updateProjectMemberRole(projectId: string, userId: string, data: UpdateMemberRoleRequest): Promise<ProjectMember> {
    return this.fetch<ProjectMember>(`/api/v1/projects/${projectId}/members/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  }

  async removeProjectMember(projectId: string, userId: string): Promise<void> {
    await this.fetch<void>(`/api/v1/projects/${projectId}/members/${userId}`, { method: 'DELETE' });
  }

  // ── Collaboration: Chat members ─────────────────────────────────────────────

  async getChatMembers(chatId: string): Promise<ProjectMember[]> {
    return this.fetch<ProjectMember[]>(`/api/v1/chats/${chatId}/members`);
  }

  async addChatMember(chatId: string, data: AddMemberRequest): Promise<ProjectMember> {
    return this.fetch<ProjectMember>(`/api/v1/chats/${chatId}/members`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async updateChatMemberRole(chatId: string, userId: string, data: UpdateMemberRoleRequest): Promise<ProjectMember> {
    return this.fetch<ProjectMember>(`/api/v1/chats/${chatId}/members/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  }

  async removeChatMember(chatId: string, userId: string): Promise<void> {
    await this.fetch<void>(`/api/v1/chats/${chatId}/members/${userId}`, { method: 'DELETE' });
  }

  // ── Model settings ────────────────────────────────────────────────────────

  async getModelRankings(): Promise<ModelRanking[]> {
    return this.fetch<ModelRanking[]>('/api/v1/settings/models');
  }

  async updateModelRankings(rankings: ModelRankingUpdate[]): Promise<ModelRanking[]> {
    return this.fetch<ModelRanking[]>('/api/v1/settings/models/rankings', {
      method: 'PUT',
      body: JSON.stringify({ rankings }),
    });
  }

  async getClaudeUsage(): Promise<ClaudeUsageInfo> {
    return this.fetch<ClaudeUsageInfo>('/api/v1/settings/claude-usage');
  }

  async getProviderStatus(): Promise<Record<string, any>> {
    return this.fetch<Record<string, any>>('/api/v1/settings/providers/status');
  }

  async getFeatureFlags(): Promise<Record<string, boolean>> {
    return this.fetch<Record<string, boolean>>('/api/v1/settings/feature-flags');
  }

  // ── Instructions ──────────────────────────────────────────────────────────

  async listInstructions(scope?: string): Promise<Instruction[]> {
    const q = scope ? `?scope=${scope}` : '';
    return this.fetch<Instruction[]>(`/api/v1/settings/instructions${q}`);
  }

  async createInstruction(data: InstructionCreate): Promise<Instruction> {
    return this.fetch<Instruction>('/api/v1/settings/instructions', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async updateInstruction(id: string, data: InstructionUpdate): Promise<Instruction> {
    return this.fetch<Instruction>(`/api/v1/settings/instructions/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async deleteInstruction(id: string): Promise<void> {
    await this.fetch<void>(`/api/v1/settings/instructions/${id}`, { method: 'DELETE' });
  }

  // ── Skills ────────────────────────────────────────────────────────────────

  async listSkills(category?: string): Promise<Skill[]> {
    const q = category ? `?category=${category}` : '';
    return this.fetch<Skill[]>(`/api/v1/settings/skills${q}`);
  }

  async createSkill(data: SkillCreate): Promise<Skill> {
    return this.fetch<Skill>('/api/v1/settings/skills', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async updateSkill(id: string, data: SkillUpdate): Promise<Skill> {
    return this.fetch<Skill>(`/api/v1/settings/skills/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async deleteSkill(id: string): Promise<void> {
    await this.fetch<void>(`/api/v1/settings/skills/${id}`, { method: 'DELETE' });
  }

  // ── Admin: Monitoring ─────────────────────────────────────────────────────

  async getMonitoringData() {
    return this.fetch<MonitoringData>('/api/v1/admin/monitoring');
  }

  // Orchestration brain: cross-worker health snapshot.
  // Read-only, any authenticated user (the admin tab already gates).
  async getOrchestrationHealth(): Promise<OrchestrationHealthSummary> {
    return this.fetch<OrchestrationHealthSummary>('/api/v1/orchestration/health');
  }

  // Phase 4: admin trace viewer.  List is cached for 30s — trace detail
  // is hit on row expand and never cached.
  async getOrchestrationTraces(
    params: {
      tenantId?: string;
      userId?: string;
      status?: 'pending' | 'completed' | 'partial' | 'failed';
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<OrchestrationTraceListResponse> {
    const qs = new URLSearchParams();
    if (params.tenantId) qs.set('tenant_id', params.tenantId);
    if (params.userId) qs.set('user_id', params.userId);
    if (params.status) qs.set('status', params.status);
    qs.set('limit', String(params.limit ?? 20));
    qs.set('offset', String(params.offset ?? 0));
    const cacheKey = `orch_traces:${qs.toString()}`;
    const cached = cache.get<OrchestrationTraceListResponse>(cacheKey);
    if (cached) return cached;
    const data = await this.fetch<OrchestrationTraceListResponse>(
      `/api/v1/orchestration/traces?${qs.toString()}`,
    );
    cache.set(cacheKey, data, 30_000);
    return data;
  }

  async getOrchestrationTraceDetail(
    traceId: string,
  ): Promise<OrchestrationTraceDetail> {
    return this.fetch<OrchestrationTraceDetail>(
      `/api/v1/orchestration/traces/${encodeURIComponent(traceId)}`,
    );
  }

  async getOrchestrationKBStats(): Promise<OrchestrationKBStats> {
    return this.fetch<OrchestrationKBStats>('/api/v1/orchestration/kb/stats');
  }

  // Phase 5C: per-tenant worker access (admin only).
  async listWorkerAccess(
    params: {
      workerId?: string;
      tenantId?: string;
      includeRevoked?: boolean;
    } = {},
  ): Promise<WorkerAccessListResponse> {
    const qs = new URLSearchParams();
    if (params.workerId) qs.set('worker_id', params.workerId);
    if (params.tenantId) qs.set('tenant_id', params.tenantId);
    if (params.includeRevoked) qs.set('include_revoked', 'true');
    const url = qs.toString()
      ? `/api/v1/orchestration/access?${qs.toString()}`
      : '/api/v1/orchestration/access';
    return this.fetch<WorkerAccessListResponse>(url);
  }

  async grantWorkerAccess(
    workerId: string,
    tenantId: string,
  ): Promise<WorkerAccessGrant> {
    return this.fetch<WorkerAccessGrant>('/api/v1/orchestration/access', {
      method: 'POST',
      body: JSON.stringify({ worker_id: workerId, tenant_id: tenantId }),
    });
  }

  async revokeWorkerAccess(accessId: string): Promise<WorkerAccessGrant> {
    return this.fetch<WorkerAccessGrant>(
      `/api/v1/orchestration/access/${encodeURIComponent(accessId)}`,
      { method: 'DELETE' },
    );
  }

  // ── Phase 7: code-free worker connection (admin only) ──────────────

  // Worker registry CRUD.
  async listWorkers(): Promise<WorkerManifest[]> {
    return this.fetch<WorkerManifest[]>('/api/v1/orchestration/registry');
  }

  async upsertWorker(manifest: WorkerManifest): Promise<WorkerManifest> {
    return this.fetch<WorkerManifest>(
      `/api/v1/orchestration/registry/${encodeURIComponent(manifest.id)}`,
      { method: 'PUT', body: JSON.stringify(manifest) },
    );
  }

  async deleteWorker(workerId: string): Promise<{ removed: string }> {
    return this.fetch<{ removed: string }>(
      `/api/v1/orchestration/registry/${encodeURIComponent(workerId)}`,
      { method: 'DELETE' },
    );
  }

  // Discovery probe — talks to a candidate worker without persisting.
  async probeWorker(payload: {
    base_url: string;
    api_key?: string;
    auth_header?: string;
    health_path?: string;
  }): Promise<WorkerProbeResult> {
    return this.fetch<WorkerProbeResult>('/api/v1/orchestration/probe', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  // Fire one dry MelaTask against a registered worker.
  async testWorker(
    workerId: string,
    body: { capability?: string; params?: Record<string, any> } = {},
  ): Promise<WorkerTestResult> {
    return this.fetch<WorkerTestResult>(
      `/api/v1/orchestration/registry/${encodeURIComponent(workerId)}/test`,
      { method: 'POST', body: JSON.stringify(body) },
    );
  }

  // ── MCP clients (admin only) ───────────────────────────────────────

  async listMcpClients(
    includeRevoked = false,
  ): Promise<{ clients: McpClient[] }> {
    const qs = includeRevoked ? '?include_revoked=true' : '';
    return this.fetch<{ clients: McpClient[] }>(
      `/api/v1/orchestration/mcp-clients${qs}`,
    );
  }

  async createMcpClient(body: {
    client_name: string;
    tenant_id?: string | null;
    scopes: string[];
  }): Promise<McpClientCreated> {
    return this.fetch<McpClientCreated>('/api/v1/orchestration/mcp-clients', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async revokeMcpClient(clientId: string): Promise<McpClient> {
    return this.fetch<McpClient>(
      `/api/v1/orchestration/mcp-clients/${encodeURIComponent(clientId)}`,
      { method: 'DELETE' },
    );
  }

  // Phase 6B: mint a one-hour embed JWT.  Admin pastes the plaintext
  // MCP client key (which Mela never stores) — same auth shape the
  // external embedder would use.  Returns the embed URL ready to
  // drop into an <iframe> or <mela-chat> web component.
  async mintEmbedToken(
    apiKey: string,
    body: {
      user_id: string;
      tenant_id?: string | null;
      profile_mode?: 'personal' | 'work';
      allowed_tools?: string[];
    },
  ): Promise<{ embed_token: string; expires_at: string; embed_url: string }> {
    const res = await fetch(`${this.baseUrl}/api/v1/embed/token`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Mela-Client-Key': apiKey,
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`Embed token mint failed (${res.status}): ${detail}`);
    }
    return res.json();
  }

  // Public capability manifest of Mela itself (no auth).  Used to
  // render the "what your client can call" panel inside the create
  // MCP client modal.
  async getMelaCapabilities(): Promise<{ tools: McpToolDef[] }> {
    return this.fetch<{ tools: McpToolDef[] }>(
      '/api/v1/orchestration/capabilities',
    );
  }

  // Phase 5A: per-user worker-event SSE stream.  Stays open across
  // chat requests; the layout component owns the lifecycle and
  // reconnects with backoff on non-user-initiated drops.  Heartbeats
  // (type=='heartbeat') are filtered out for callers — they're only
  // useful to keep the connection alive through proxies.
  async *streamWorkerEvents(
    signal: AbortSignal,
  ): AsyncGenerator<ChatChunk, void, unknown> {
    const token = await this.getAccessToken();

    let response: Response;
    try {
      response = await fetch(
        `${this.baseUrl}/api/v1/orchestration/events/stream`,
        {
          headers: {
            ...this._profileHeaders(),
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal,
        },
      );
    } catch (err: any) {
      if (err?.name === 'AbortError') throw err;
      throw new Error(
        'Unable to connect to the orchestration event stream.',
      );
    }
    if (!response.ok || !response.body) {
      throw new Error(
        `Event stream failed (HTTP ${response.status})`,
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      while (true) {
        let readResult;
        try {
          readResult = await reader.read();
        } catch (readErr: any) {
          if (readErr?.name === 'AbortError') throw readErr;
          throw new Error('Event stream connection lost.');
        }
        const { done, value } = readResult;
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (!data) continue;
            try {
              const chunk = JSON.parse(data) as ChatChunk;
              yield chunk;
            } catch {
              // Ignore malformed lines — connection stays open.
            }
          }
        }
      }
    } finally {
      try { reader.releaseLock(); } catch { /* noop */ }
    }
  }

  async getAdminTokenUsage(days = 7) {
    return this.fetch<{ days: number; users: TokenUsageRow[] }>(`/api/v1/admin/token-usage?days=${days}`);
  }

  async getAdminAuditLogs(params: AuditLogParams = {}) {
    const q = new URLSearchParams();
    if (params.limit) q.set('limit', String(params.limit));
    if (params.offset) q.set('offset', String(params.offset));
    if (params.userId) q.set('user_id', params.userId);
    if (params.action) q.set('action', params.action);
    return this.fetch<AuditLog[]>(`/api/v1/admin/audit-logs?${q}`);
  }

  // ── Enterprise Control-Plane ──────────────────────────────────────────────

  async getAdminTenants(year?: number, month?: number) {
    const q = new URLSearchParams();
    if (year) q.set('year', String(year));
    if (month) q.set('month', String(month));
    return this.fetch<{ year: number; month: number; tenants: TenantSummary[] }>(
      `/api/v1/admin/tenants?${q}`
    );
  }

  async getAdminTenantDetail(tenantId: string, year?: number, month?: number) {
    const q = new URLSearchParams();
    if (year) q.set('year', String(year));
    if (month) q.set('month', String(month));
    return this.fetch<TenantDetail>(`/api/v1/admin/tenants/${tenantId}?${q}`);
  }

  async getAdminInvoice(tenantId: string, year: number, month: number) {
    return this.fetch<InvoiceData>(
      `/api/v1/admin/invoices/${tenantId}/${year}/${month}`
    );
  }

  async getModelGovernance() {
    return this.fetch<{ models: ModelGovernance[] }>('/api/v1/admin/models/governance');
  }

  async updateModelQuota(modelId: string, data: ModelGovernanceUpdate) {
    return this.fetch<ModelGovernance>(`/api/v1/admin/models/${modelId}/quota`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async getErrorLogs(params: {
    limit?: number;
    offset?: number;
    severity?: string;
    userId?: string;
    tenantId?: string;
    route?: string;
  } = {}) {
    const q = new URLSearchParams();
    if (params.limit) q.set('limit', String(params.limit));
    if (params.offset) q.set('offset', String(params.offset));
    if (params.severity) q.set('severity', params.severity);
    if (params.userId) q.set('user_id', params.userId);
    if (params.tenantId) q.set('tenant_id', params.tenantId);
    if (params.route) q.set('route', params.route);
    return this.fetch<ErrorLogsResponse>(`/api/v1/admin/errors?${q}`);
  }

  async getErrorDetail(errorId: string) {
    return this.fetch<ErrorLogDetail>(`/api/v1/admin/errors/${errorId}`);
  }

  async getUserDetail(userId: string, days = 30) {
    return this.fetch<UserDetail>(`/api/v1/admin/users/${userId}/detail?days=${days}`);
  }

  // ── Model Access Governance ───────────────────────────────────────────────

  async getModelAccessRules(): Promise<ModelAccessRule[]> {
    return this.fetch<ModelAccessRule[]>('/api/v1/settings/model-access');
  }

  async setModelAccessRule(data: {
    model_id: string;
    is_allowed: boolean;
    user_id?: string;
    role?: string;
  }): Promise<ModelAccessRule> {
    return this.fetch<ModelAccessRule>('/api/v1/settings/model-access', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async deleteModelAccessRule(ruleId: string): Promise<void> {
    await this.fetch<void>(`/api/v1/settings/model-access/${ruleId}`, { method: 'DELETE' });
  }

  async getUserEffectiveModels(userId: string): Promise<{ model_id: string; display_name: string; rank: number }[]> {
    return this.fetch(`/api/v1/settings/model-access/user/${userId}`);
  }

  // ── Budget / Governance ───────────────────────────────────────────────────

  async getMyBudget(): Promise<UserBudgetStatus> {
    return this.fetch<UserBudgetStatus>('/api/v1/budgets/me');
  }

  async checkMyBudget(): Promise<UserBudgetStatus> {
    return this.fetch<UserBudgetStatus>('/api/v1/budgets/check');
  }

  async setUserBudget(data: {
    user_id: string;
    token_budget?: number | null;
    cost_budget?: number | null;
    period?: string;
    hard_stop?: boolean;
    token_warning_pct?: number;
    cost_warning_pct?: number;
  }): Promise<{ status: string; budget_id: string }> {
    return this.fetch('/api/v1/budgets/admin/set', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  // ── Onboarding ────────────────────────────────────────────────────────────

  async triggerOnboarding(data: {
    new_user_email: string;
    new_user_name: string;
    department?: string;
    manager_email?: string;
    send_welcome_email?: boolean;
    schedule_orientation?: boolean;
    create_tasks?: boolean;
  }) {
    return this.fetch<{
      log_id: string;
      status: string;
      new_user_email: string;
      new_user_name: string;
      steps_completed: string[];
      steps_failed: { step: string; error: string }[];
    }>('/api/v1/admin/onboard', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async getOnboardingLogs(limit = 50, offset = 0) {
    return this.fetch<{
      total: number;
      limit: number;
      offset: number;
      logs: {
        id: string;
        new_user_email: string;
        new_user_name: string;
        department: string | null;
        manager_email: string | null;
        initiated_by_email: string | null;
        status: string;
        steps_completed: string[];
        steps_failed: { step: string; error: string }[];
        created_at: string;
        completed_at: string | null;
      }[];
    }>(`/api/v1/admin/onboarding-logs?limit=${limit}&offset=${offset}`);
  }

  async getModelHealth() {
    return this.fetch<{
      checked: number;
      healthy: number;
      models: { model: string; status: string; latency_ms: number; error?: string }[];
    }>('/api/v1/admin/models/health');
  }

  // ── Workflows ─────────────────────────────────────────────────────────────

  async getWorkflowTemplates() {
    return this.fetch<{ templates: any[] }>('/api/v1/workflows/templates');
  }

  async listWorkflows() {
    return this.fetch<Workflow[]>('/api/v1/workflows/');
  }

  async createWorkflow(data: WorkflowCreate) {
    return this.fetch<Workflow>('/api/v1/workflows/', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async getWorkflow(id: string) {
    return this.fetch<Workflow>(`/api/v1/workflows/${id}`);
  }

  async updateWorkflow(id: string, data: WorkflowUpdate) {
    return this.fetch<Workflow>(`/api/v1/workflows/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async deleteWorkflow(id: string) {
    await this.fetch<void>(`/api/v1/workflows/${id}`, { method: 'DELETE' });
  }

  async runWorkflow(id: string, inputData?: Record<string, any>) {
    return this.fetch<WorkflowRun>(`/api/v1/workflows/${id}/run`, {
      method: 'POST',
      body: inputData ? JSON.stringify(inputData) : undefined,
    });
  }

  async listWorkflowRuns(workflowId: string, limit = 20) {
    return this.fetch<WorkflowRun[]>(`/api/v1/workflows/${workflowId}/runs?limit=${limit}`);
  }

  // ── Chat attachment processing ─────────────────────────────────────────────

  async processAttachment(file: File, extractText = true): Promise<ProcessedAttachment> {
    const token = await this.getAccessToken();
    const formData = new FormData();
    formData.append('file', file);
    formData.append('extract_text', String(extractText));

    const response = await fetch(`${this.baseUrl}/api/v1/chat/process-attachment`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Processing failed' }));
      throw new Error(error.detail || 'Failed to process attachment');
    }

    return response.json();
  }

  // ── Microsoft Graph API methods ──────────────────────────────────────────

  /** Check whether OBO is configured and the current user has Graph consent. */
  async getGraphStatus(): Promise<GraphStatus> {
    return this.fetch<GraphStatus>('/api/v1/graph/status');
  }

  /** Read the signed-in user's inbox. */
  async getInbox(limit = 10, filter?: string): Promise<{ messages: GraphMailMessage[]; count: number }> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (filter) params.set('filter', filter);
    return this.fetch(`/api/v1/graph/mail/inbox?${params}`);
  }

  /** Send an email as the signed-in user. */
  async sendMail(payload: {
    to: string[];
    subject: string;
    body: string;
    cc?: string[];
    bcc?: string[];
    is_html?: boolean;
  }): Promise<{ success: boolean; message: string }> {
    return this.fetch('/api/v1/graph/mail/send', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  /** Save a draft email to the Drafts folder. */
  async createDraftMail(payload: {
    to: string[];
    subject: string;
    body: string;
  }): Promise<{ success: boolean; draft_id: string; message: string }> {
    return this.fetch('/api/v1/graph/mail/draft', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  /** Send an existing draft email by its Graph message ID. */
  async sendDraftMail(draftId: string): Promise<{ success: boolean; message: string }> {
    return this.fetch('/api/v1/graph/mail/send-draft', {
      method: 'POST',
      body: JSON.stringify({ draft_id: draftId }),
    });
  }

  /** Get upcoming calendar events. */
  async getCalendarEvents(daysAhead = 7): Promise<{ events: GraphCalendarEvent[]; count: number }> {
    return this.fetch(`/api/v1/graph/calendar/events?days_ahead=${daysAhead}`);
  }

  /** Create a calendar event / meeting. */
  async createCalendarEvent(payload: {
    subject: string;
    start: string;
    end: string;
    timezone?: string;
    attendees?: string[];
    body?: string;
    location?: string;
    is_online_meeting?: boolean;
  }): Promise<{
    success: boolean;
    event_id: string;
    subject: string;
    start: string;
    end: string;
    meeting_link: string | null;
    web_link: string | null;
  }> {
    return this.fetch('/api/v1/graph/calendar/events', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  /** List Planner tasks for the signed-in user (or a specific plan). */
  async getPlannerTasks(planId?: string): Promise<{ tasks: GraphPlannerTask[]; count: number }> {
    const params = planId ? `?plan_id=${planId}` : '';
    return this.fetch(`/api/v1/graph/planner/tasks${params}`);
  }

  /** Create a Planner task in a specific plan. */
  async createPlannerTask(payload: {
    plan_id: string;
    title: string;
    due_date?: string;
    assigned_to?: string;
  }): Promise<{ success: boolean; task_id: string; title: string }> {
    return this.fetch('/api/v1/graph/planner/tasks', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  /** Create a Microsoft To Do task (no plan_id required). */
  async createTodoTask(payload: {
    title: string;
    due_date?: string;
    notes?: string;
  }): Promise<{ success: boolean; task_id: string; title: string }> {
    return this.fetch('/api/v1/graph/todo/tasks', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  // ── Agent Memory ──────────────────────────────────────────────────────────

  async uploadAgentMemoryFile(
    file: File,
    opts: { scope?: 'personal' | 'workspace' | 'tenant'; tag?: AgentMemoryTag; title?: string } = {},
  ): Promise<AgentMemoryItem> {
    const token = await this.getAccessToken();
    const formData = new FormData();
    formData.append('file', file);
    formData.append('scope', opts.scope || 'personal');
    formData.append('tag', opts.tag || 'knowledge');
    if (opts.title) formData.append('title', opts.title);
    const response = await fetch(`${this.baseUrl}/api/v1/agent-memory/upload`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...this._profileHeaders() },
      body: formData,
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: 'Upload failed' }));
      throw new Error(err.detail || 'Upload failed');
    }
    cache.invalidate('/api/v1/agent-memory/items');
    return response.json();
  }

  async addAgentMemoryWebsite(payload: {
    url: string;
    scope?: 'personal' | 'workspace' | 'tenant';
    tag?: AgentMemoryTag;
    title?: string;
  }): Promise<AgentMemoryItem> {
    const result = await this.fetch<AgentMemoryItem>('/api/v1/agent-memory/web', {
      method: 'POST',
      body: JSON.stringify({
        url: payload.url,
        scope: payload.scope || 'personal',
        tag: payload.tag || 'knowledge',
        title: payload.title,
      }),
    });
    cache.invalidate('/api/v1/agent-memory/items');
    return result;
  }

  async listAgentMemoryItems(
    filter: { scope?: string; tag?: AgentMemoryTag } = {},
  ): Promise<{ items: AgentMemoryItem[]; total: number }> {
    const params = new URLSearchParams();
    if (filter.scope) params.set('scope', filter.scope);
    if (filter.tag) params.set('tag', filter.tag);
    const qs = params.toString();
    const path = `/api/v1/agent-memory/items${qs ? `?${qs}` : ''}`;
    return this.fetch(path);
  }

  async getAgentMemoryItem(id: string): Promise<AgentMemoryItem> {
    return this.fetch(`/api/v1/agent-memory/items/${id}`);
  }

  async deleteAgentMemoryItem(id: string): Promise<void> {
    const token = await this.getAccessToken();
    const response = await fetch(`${this.baseUrl}/api/v1/agent-memory/items/${id}`, {
      method: 'DELETE',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...this._profileHeaders() },
    });
    if (!response.ok && response.status !== 204) {
      const err = await response.json().catch(() => ({ detail: 'Delete failed' }));
      throw new Error(err.detail || 'Delete failed');
    }
    cache.invalidate('/api/v1/agent-memory/items');
  }

  async reindexAgentMemoryItem(id: string): Promise<AgentMemoryItem> {
    const result = await this.fetch<AgentMemoryItem>(`/api/v1/agent-memory/items/${id}/reindex`, {
      method: 'POST',
    });
    cache.invalidate('/api/v1/agent-memory/items');
    return result;
  }

  async toggleAgentMemorySession(
    id: string,
    conversationId: string,
    disabled: boolean,
  ): Promise<AgentMemoryItem> {
    return this.fetch(`/api/v1/agent-memory/items/${id}/session`, {
      method: 'PATCH',
      body: JSON.stringify({ conversation_id: conversationId, disabled }),
    });
  }

  async listAgentMemoryTemplates(): Promise<{ items: AgentMemoryItem[]; total: number }> {
    return this.fetch('/api/v1/agent-memory/templates');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export type AgentMemoryTag = 'knowledge' | 'template' | 'brand' | 'policy' | 'demo';
export type AgentMemoryScope = 'personal' | 'workspace' | 'tenant';
export type AgentMemoryStatus =
  | 'pending' | 'parsing' | 'embedding' | 'crawling' | 'ready' | 'failed';

export interface AgentMemoryItem {
  id: string;
  user_id: string;
  tenant_id?: string | null;
  scope: AgentMemoryScope;
  tag: AgentMemoryTag;
  source_type: string;
  title: string;
  url?: string | null;
  file_type?: string | null;
  file_size?: number | null;
  status: AgentMemoryStatus;
  error_message?: string | null;
  chunk_count: number;
  page_count: number;
  has_template_schema: boolean;
  last_synced_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConnectorStatus {
  connector_type: string;
  enabled: boolean;
  healthy?: boolean;
  health_message?: string;
  latency_ms?: number;
  last_sync?: string;
  docs_indexed: number;
  errors: number;
}

export interface ConnectorJob {
  id: string;
  connector_type: string;
  job_type: string;
  source_id: string;
  status: string;
  attempts: number;
  docs_processed: number;
  created_at?: string;
  finished_at?: string;
  error?: string;
}

export interface IndexStats {
  index_name: string;
  document_count?: number;
  storage_size_mb?: number;
  error?: string;
}

export interface User {
  id: string;
  email: string;
  name: string;
  department?: string;
  job_title?: string;
  role: 'admin' | 'user' | 'viewer';
  preferred_model: string;
  daily_token_limit: number;
  tokens_used_today: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  model: string;
  system_prompt?: string;
  is_archived: boolean;
  context_type?: 'org' | 'personal' | 'work';
  is_private?: boolean;
  private_expires_at?: string;
  project_id?: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface GeneratedFile {
  file_log_id?: string;
  name: string;
  base64: string;
  mime_type: string;
  size: number;
  output_type?: string;
}

/** Represents a draft email saved to Outlook Drafts by the AI. */
export interface EmailDraft {
  draft_id: string;
  to: string[];
  subject: string;
  body_preview: string;
  status: 'saved' | 'sending' | 'sent' | 'error';
  error?: string;
}

export interface Message {
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  citations?: Citation[];
  attachments?: Attachment[];
  generated_files?: GeneratedFile[];
  generated_file_meta?: Array<{ file_log_id?: string; name: string; mime_type: string; size: number; output_type: string }>;
  images?: GeneratedImage[];
  inline_attachments?: InlineAttachment[]; // persisted for preview rendering
  email_draft?: EmailDraft;              // structured email draft card
  model?: string;                        // resolved model id (e.g. "claude-sonnet-4-6")
  provider?: string;                     // resolved provider (e.g. "anthropic")
  created_at?: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface Citation {
  document_id: string;
  document_title: string;
  chunk_id: string;
  content: string;
  /** Backend field name – matches schema.Citation.relevance_score */
  relevance_score: number;
  source_url?: string;
}

export interface Attachment {
  id: string;
  filename: string;
  file_type: string;
  file_size: number;
  url?: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  description: string;
  max_tokens: number;
  supports_vision: boolean;
  supports_tools: boolean;
  is_default: boolean;
  preview?: boolean; // true = rate-limited preview (e.g. Claude models)
}

export interface ModelInsight {
  id: string;
  name: string;
  provider: string; // azure_openai | azure_ai_foundry | anthropic
  description: string;
  cost_per_1k_tokens: number;
  performance_label: string;
  supports_vision: boolean;
  supports_tools: boolean;
  is_default: boolean;
  preview: boolean;
  badge?: string | null; // "Popular" | "Fastest" | "Best Value" | null
  usage_rank: number;
}

export interface ChatRequest {
  message: string;
  conversation_id?: string;
  model?: string;
  use_rag?: boolean;
  use_web_search?: boolean;
  stream?: boolean;
  attachments?: string[];
  inline_attachments?: InlineAttachment[];
  is_private?: boolean;
  project_id?: string;
  context_type?: 'org' | 'personal' | 'work';
}

export interface InlineAttachment {
  filename: string;
  content_type: string;
  text_content?: string;
  base64_data?: string;
  ocr_text?: string;
  raw_base64?: string; // Raw bytes for spreadsheets/CSV — used by code interpreter
}

export interface ProcessedAttachment {
  filename: string;
  content_type: string;
  type: 'image' | 'document' | 'audio' | 'text';
  base64_data?: string;
  text_content?: string;
  ocr_text?: string;
  raw_base64?: string; // Present for Excel/CSV files
  size: number;
}

// Phase 0: stable error codes mirrored from backend app/schemas/chat.py.
// Frontend maps these to friendly, actionable messages — see ERROR_MESSAGES.
export type ChatErrorCode =
  | 'llm_timeout'
  | 'llm_rate_limited'
  | 'llm_provider_down'
  | 'llm_content_filtered'
  | 'auth_expired'
  | 'auth_forbidden'
  | 'tool_failed'
  | 'tool_timeout'
  | 'search_unavailable'
  | 'db_unavailable'
  | 'budget_exceeded'
  | 'quota_exceeded'
  | 'input_too_large'
  | 'input_invalid'
  | 'unknown';

export const ERROR_MESSAGES: Record<ChatErrorCode, string> = {
  llm_timeout:          "The AI took too long to answer. Try again, or shorten your message.",
  llm_rate_limited:     "We're rate-limited by the AI provider. Please wait a few seconds and try again.",
  llm_provider_down:    "The AI provider is having an outage. We've logged this — please retry shortly.",
  llm_content_filtered: "The AI declined to answer due to content safety filters. Try rephrasing.",
  auth_expired:         "Your session expired. Please sign in again.",
  auth_forbidden:       "You don't have permission to perform this action.",
  tool_failed:          "A tool I tried to use failed. Your message was saved — please try again.",
  tool_timeout:         "A tool I tried to use timed out. Try again, or ask me to skip that step.",
  search_unavailable:   "I couldn't reach the document index. The answer may be incomplete — please retry.",
  db_unavailable:       "Our database is temporarily unavailable. Your message was not saved — please retry.",
  budget_exceeded:      "You've exceeded the budget for this conversation. Start a new chat to continue.",
  quota_exceeded:       "You've hit your daily quota. It resets at midnight UTC.",
  input_too_large:      "Your message (or attachments) is too large. Please shorten it or remove some attachments.",
  input_invalid:        "Your request was malformed. Please try again.",
  unknown:              "Something went wrong on our side. Your message was saved — please try again.",
};

export interface ChatChunk {
  type: 'content' | 'thinking' | 'citation' | 'tool_call' | 'tool_result' | 'tool_executing' | 'image_generated' | 'file_generated' | 'model_switched' | 'claude_usage' | 'claude_limit_reached' | 'email_draft' | 'error' | 'done' | 'worker_event' | 'heartbeat' | 'ping';
  content?: string;
  data?: any;
  error_code?: ChatErrorCode;
  correlation_id?: string;
}

// Phase 5A: typed payload of a worker_event chunk on the per-user event
// stream.  Mirrors backend ``app.schemas.chat.WorkerEventChunk`` exactly.
export type WorkerEventType =
  | 'scan_completed'
  | 'meeting_ended'
  | 'task_updated'
  | 'worker_available'
  | 'worker_unavailable';

export interface WorkerEventChunk {
  worker_id: string;
  event_type: WorkerEventType;
  title: string;
  summary: string;
  trace_id: string | null;
  timestamp: string;
}

// Orchestration brain — worker health snapshot from /api/v1/orchestration/health.
export interface WorkerHealthSnapshot {
  id: string;
  display_name: string;
  version: string;
  protocol: string;
  status: 'healthy' | 'degraded' | 'unreachable' | 'unknown';
  registered_status: string;
  last_health_check: string | null;
  breaker: {
    state: 'closed' | 'open' | 'half_open';
    failure_count: number;
    opened_at: number | null;
    last_failure_at: number | null;
    last_success_at: number | null;
  };
}

export interface OrchestrationHealthSummary {
  generated_at: string;
  worker_count: number;
  summary: {
    healthy: number;
    degraded: number;
    unreachable: number;
    unknown: number;
    unconfigured?: number;
  };
  workers: WorkerHealthSnapshot[];
  // Phase 5C: tells the frontend whether the Access Control tab is
  // actionable (default-deny) or just a notice (default-allow).
  access_default_allow?: boolean;
}

// Phase 5C: per-tenant worker access types.
export interface WorkerAccessGrant {
  id: string;
  worker_id: string;
  tenant_id: string;
  granted_at: string | null;
  granted_by: string;
  revoked_at: string | null;
}

export interface WorkerAccessListResponse {
  default_allow: boolean;
  grants: WorkerAccessGrant[];
}

// ── Phase 7: code-free worker connection types ─────────────────────────

export type WorkerProtocol = 'mcp' | 'rest' | 'webhook' | 'grpc';
export type WorkerAuthScheme = 'bearer' | 'api_key' | 'oauth2' | 'none';

export interface WorkerCapabilityManifest {
  name: string;
  description: string;
  input_params?: Record<string, any>;
  output_shape?: Record<string, any>;
  is_async?: boolean;
  estimated_ms?: number;
}

export interface WorkerManifest {
  id: string;
  display_name: string;
  version: string;
  capabilities: WorkerCapabilityManifest[];
  protocol: WorkerProtocol;
  base_url: string;
  health_check_url: string;
  auth_scheme: WorkerAuthScheme;
  auth_config?: Record<string, any>;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  timeout_ms?: number;
  retry_policy?: {
    max_attempts: number;
    backoff_ms: number;
    backoff_multiplier: number;
  };
  report_back_url?: string | null;
  registered_at?: string;
  last_health_check?: string | null;
  status?: 'healthy' | 'degraded' | 'unreachable' | 'unknown' | 'unconfigured';
}

export interface WorkerProbeResult {
  success: boolean;
  base_url: string;
  suggested_id: string | null;
  suggested_display_name: string | null;
  suggested_version: string | null;
  suggested_auth_header: string;
  capabilities: Array<{
    name: string;
    description: string;
    input_params: Record<string, any>;
    is_async: boolean;
  }>;
  health_ok: boolean | null;
  health_latency_ms: number | null;
  error_code: string | null;
  error_message: string | null;
}

export interface WorkerTestResult {
  capability: string;
  result: {
    task_id: string;
    trace_id: string;
    worker_id: string;
    capability: string;
    success: boolean;
    summary: string;
    data: Record<string, any>;
    metadata: { latency_ms: number; source: string; retrieved_at: string };
    error: { code: string; message: string; retryable: boolean } | null;
  };
}

export interface McpClient {
  id: string;
  client_name: string;
  tenant_id: string | null;
  scopes: string[];
  created_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
}

export interface McpClientCreated extends McpClient {
  api_key: string; // shown ONCE
}

export interface McpToolDef {
  name: string;
  description: string;
  inputSchema?: Record<string, any>;
}

// Phase 4: admin trace viewer types.
export interface OrchestrationTraceRow {
  trace_id: string;
  goal_id: string;
  goal: string | null;
  status: 'pending' | 'completed' | 'partial' | 'failed';
  user_id: string;
  tenant_id: string | null;
  profile_mode: string;
  created_at: string | null;
  completed_at: string | null;
  task_count: number;
  failed_task_count: number;
}

export interface OrchestrationTraceListResponse {
  total: number;
  limit: number;
  offset: number;
  traces: OrchestrationTraceRow[];
}

export interface OrchestrationTaskRow {
  task_id: string;
  worker_id: string;
  capability: string;
  execution_mode: 'sync' | 'async';
  status: string;
  summary: string | null;
  error_code: string | null;
  error_message: string | null;
  latency_ms: number;
  created_at: string | null;
  completed_at: string | null;
}

export interface OrchestrationTraceDetail extends OrchestrationTraceRow {
  plan_json: Record<string, unknown>;
  tasks: OrchestrationTaskRow[];
}

export interface OrchestrationKBStats {
  total_entries: number;
  entries_by_type: Record<string, number>;
  entries_expiring_within_7_days: number;
  oldest_entry_age_days: number;
  search_index?: {
    index_name: string;
    document_count?: number;
    storage_size_mb?: number;
    error?: string;
  };
}

export interface GeneratedImage {
  url: string;
  revised_prompt?: string;
  original_prompt?: string;
}

export interface Document {
  id: string;
  title: string;
  filename: string;
  file_type: string;
  file_size: number;
  source: string;
  is_indexed: boolean;
  chunk_count: number;
  created_at: string;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  total_results: number;
}

export interface SearchResult {
  document_id: string;
  document_title: string;
  chunk_id: string;
  content: string;
  score: number;
}

export interface UsageStats {
  total_users: number;
  active_users_today: number;
  total_conversations: number;
  total_messages: number;
  total_tokens_used: number;
  total_documents: number;
  indexed_documents: number;
}

export interface AnalyticsResponse {
  overview: UsageStats;
  daily_usage: DailyUsage[];
  model_usage: ModelUsageStats[];
  top_users: UserUsageStats[];
}

export interface DailyUsage {
  date: string;
  users: number;
  conversations: number;
  messages: number;
  tokens: number;
}

export interface ModelUsageStats {
  model: string;
  request_count: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface UserUsageStats {
  user_id: string;
  user_name: string;
  user_email: string;
  total_conversations: number;
  total_messages: number;
  total_tokens: number;
}

export interface AuditLog {
  id: string;
  user_id: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  details?: Record<string, any>;
  ip_address?: string;
  success: boolean;
  created_at: string;
}

export interface CreateConversationRequest {
  title?: string;
  model?: string;
  system_prompt?: string;
  is_private?: boolean;
  context_type?: 'org' | 'personal' | 'work';
}

export interface UpdateConversationRequest {
  title?: string;
  model?: string;
  system_prompt?: string;
  is_archived?: boolean;
}

export interface UpdateUserRequest {
  role?: string;
  daily_token_limit?: number;
  is_active?: boolean;
}

export interface AuditLogParams {
  limit?: number;
  offset?: number;
  userId?: string;
  action?: string;
}

// Translation
export interface TranslationResult {
  original_text: string;
  translated_text: string;
  source_language: string;
  target_language: string;
  confidence: number;
}

export interface LanguageDetectionResult {
  language: string;
  confidence: number;
  is_translation_supported: boolean;
}

// Image Generation
export type ImageSize = '1024x1024' | '1792x1024' | '1024x1792';
export type ImageQuality = 'standard' | 'hd';
export type ImageStyle = 'vivid' | 'natural';

export interface ImageGenerationResult {
  url: string;
  revised_prompt: string;
  original_prompt: string;
  size: string;
  quality: string;
  style: string;
}

export interface ImageServiceStatus {
  available: boolean;
  model: string;
  supported_sizes: string[];
  supported_qualities: string[];
  supported_styles: string[];
}

// Document Intelligence
export type DocumentAnalysisModel =
  | 'prebuilt-read'
  | 'prebuilt-layout'
  | 'prebuilt-document'
  | 'prebuilt-invoice'
  | 'prebuilt-receipt'
  | 'prebuilt-idDocument'
  | 'prebuilt-businessCard'
  | 'prebuilt-contract';

export interface DocumentAnalysisResult {
  content: string;
  pages: PageInfo[];
  tables: TableInfo[];
  key_value_pairs: KeyValuePair[];
  documents: ExtractedDocument[];
  styles: StyleInfo[];
  model_id: string;
  api_version: string;
}

export interface PageInfo {
  page_number: number;
  width: number;
  height: number;
  unit: string;
  angle: number;
  lines_count: number;
  words_count: number;
}

export interface TableInfo {
  row_count: number;
  column_count: number;
  cells: TableCell[];
}

export interface TableCell {
  rowIndex: number;
  columnIndex: number;
  rowSpan: number;
  columnSpan: number;
  content: string;
  kind?: string;
}

export interface KeyValuePair {
  key: string;
  value: string;
  confidence: number;
}

export interface ExtractedDocument {
  doc_type: string;
  confidence: number;
  fields: Record<string, ExtractedField>;
}

export interface ExtractedField {
  value: any;
  confidence: number | null;
}

export interface StyleInfo {
  is_handwritten: boolean;
  confidence: number;
}

export interface TextExtractionResult {
  filename: string;
  text: string;
  character_count: number;
}

export interface TableExtractionResult {
  filename: string;
  table_count: number;
  tables: { row_count: number; column_count: number; data: string[][] }[];
}

export interface InvoiceAnalysisResult {
  filename: string;
  invoice: Record<string, ExtractedField>;
}

export interface ReceiptAnalysisResult {
  filename: string;
  receipt: Record<string, ExtractedField>;
}

export interface DocumentModelInfo {
  id: string;
  name: string;
  description: string;
}

export interface DocumentIntelligenceStatus {
  available: boolean;
  supported_formats: string[];
}

// ── User Settings types ──────────────────────────────────────────────────────

export interface UserDailyUsage {
  date: string;
  conversations: number;
  messages: number;
  tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost: number;
}

export interface ModelBreakdown {
  model: string;
  request_count: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost: number;
}

export interface CostBreakdown {
  category: string;
  cost: number;
  tokens: number;
  requests: number;
}

export interface UserUsage {
  total_conversations: number;
  total_messages: number;
  tokens_used_today: number;
  daily_token_limit: number;
  daily_usage: UserDailyUsage[];
  model_breakdown: ModelBreakdown[];
  total_tokens: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_requests: number;
  estimated_total_cost: number;
  avg_tokens_per_request: number;
  avg_cost_per_request: number;
  cost_by_model: CostBreakdown[];
  peak_hour: number;
  token_efficiency_ratio: number;
}

export interface UserPreferences {
  theme: string;
  memory_enabled: boolean;
  data_retention_days: number;
  default_private_mode?: boolean;
}

export interface OrgSettings {
  private_chat_enabled: boolean;
  private_chat_retention_days: number;
}

export interface Project {
  id: string;
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  context_type?: 'org' | 'personal' | 'work';
  system_prompt?: string;
  is_archived: boolean;
  created_at: string;
  updated_at?: string;
  conversation_count: number;
}

export interface ProjectMemoryItem {
  id: string;
  fact: string;
  source_conversation_id?: string;
  created_at: string;
}

export interface ProjectDetail extends Project {
  memories: ProjectMemoryItem[];
}

export interface ProjectCreateRequest {
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  system_prompt?: string;
  context_type?: 'org' | 'personal' | 'work';
  workspace_id?: string;
}

export interface ProjectUpdateRequest {
  name?: string;
  description?: string;
  icon?: string;
  color?: string;
  system_prompt?: string;
  is_archived?: boolean;
}

export interface UserFeatures {
  role: string;
  sso_configured: boolean;
  features: Record<string, boolean>;
}

export interface ConnectorInfo {
  id: string;
  name: string;
  connector_type: string;
  config: Record<string, any>;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProjectFile {
  id: string;
  project_id: string;
  filename: string;
  file_type: string;
  file_size: number;
  created_at: string;
}

export interface ProjectConversation {
  id: string;
  title: string;
  model: string;
  is_private: boolean;
  message_count: number;
  created_at: string;
  updated_at: string;
}

// ── Collaboration types ────────────────────────────────────────────────────────

export type MemberRole = 'owner' | 'editor' | 'viewer';

export interface ProjectMember {
  id: string;
  user_id: string;
  user_email: string;
  user_name: string;
  role: MemberRole;
  added_by: string;
  added_at: string;
  pending?: boolean;
}

export interface AddMemberRequest {
  email: string;
  role: MemberRole;
}

export interface UpdateMemberRoleRequest {
  role: MemberRole;
}

// ── Model settings types ────────────────────────────────────────────────────

export interface ModelRanking {
  id: string;
  model_id: string;
  display_name: string;
  provider: string;
  rank: number;
  is_enabled: boolean;
  is_default: boolean;
  max_tokens: number | null;
  notes: string | null;
  cost_multiplier: number;
  updated_at: string;
}

export interface ModelRankingUpdate {
  model_id: string;
  rank?: number;
  is_enabled?: boolean;
  is_default?: boolean;
  notes?: string;
  cost_multiplier?: number;
}

export interface ClaudeUsageInfo {
  question_count: number;
  token_count: number;
  limit: number;
  remaining: number;
  date: string;
}

// ── Instructions types ─────────────────────────────────────────────────────

export interface Instruction {
  id: string;
  name: string;
  content: string;
  scope: 'global' | 'org' | 'team' | 'user';
  priority: number;
  is_enabled: boolean;
  created_by: string;
}

export interface InstructionCreate {
  name: string;
  content: string;
  scope?: string;
  priority?: number;
}

export interface InstructionUpdate {
  name?: string;
  content?: string;
  priority?: number;
  is_enabled?: boolean;
}

// ── Skills types ───────────────────────────────────────────────────────────

export interface Skill {
  id: string;
  name: string;
  description: string | null;
  category: string;
  trigger_keywords: string | null; // JSON string
  instruction_block: string;
  model_preference: string | null;
  is_enabled: boolean;
  is_builtin: boolean;
  rank: number;
  visibility: string;
  created_by: string;
}

export interface SkillCreate {
  name: string;
  description?: string;
  category?: string;
  trigger_keywords?: string[];
  instruction_block: string;
  model_preference?: string;
  rank?: number;
  is_enabled?: boolean;
  visibility?: string;
}

export interface SkillUpdate {
  name?: string;
  description?: string;
  category?: string;
  trigger_keywords?: string[];
  instruction_block?: string;
  model_preference?: string;
  rank?: number;
  is_enabled?: boolean;
}

// ── Workflow types ─────────────────────────────────────────────────────────

export interface Workflow {
  id: string;
  name: string;
  description: string | null;
  trigger_type: string;
  trigger_config: Record<string, any> | null;
  actions: Array<{ type: string; config: Record<string, any> }> | null;
  status: 'active' | 'paused' | 'draft' | 'archived';
  visibility: string;
  created_by: string;
  run_count: number;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowCreate {
  name: string;
  description?: string;
  trigger_type?: string;
  trigger_config?: Record<string, any>;
  actions?: Array<{ type: string; config: Record<string, any> }>;
  status?: string;
  visibility?: string;
}

export interface WorkflowUpdate {
  name?: string;
  description?: string;
  trigger_type?: string;
  trigger_config?: Record<string, any>;
  actions?: Array<{ type: string; config: Record<string, any> }>;
  status?: string;
  visibility?: string;
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  triggered_by: string | null;
  trigger_type: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  steps_completed: number;
  steps_total: number;
  output_data: Record<string, any> | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

// ── Monitoring types ───────────────────────────────────────────────────────

export interface MonitoringData {
  timestamp: string;
  db_status: string;
  python_version: string;
  users: { total: number; active: number };
  activity: {
    active_sessions_1h: number;
    messages_1h: number;
    messages_24h: number;
    tokens_1h: number;
    tokens_24h: number;
  };
  quality: {
    error_rate_pct: number;
    errors_24h: number;
    total_audit_24h: number;
  };
  model_health: Array<{ model: string; requests_24h: number; tokens_24h: number }>;
  recent_errors: Array<{
    id: string;
    user_id: string;
    action: string;
    resource_type: string;
    created_at: string;
  }>;
}

export interface TokenUsageRow {
  user_id: string;
  name: string;
  email: string;
  role: string;
  daily_token_limit: number;
  tokens_used_today: number;
  period_tokens: number;
  period_prompt_tokens: number;
  period_completion_tokens: number;
  period_requests: number;
  pct_daily_limit_used: number;
}

// ── Enterprise Control-Plane Types ───────────────────────────────────────────

export interface ModelCostBreakdown {
  model: string;
  tokens: number;
  cost: number;
}

export interface TenantSummary {
  tenant_id: string;
  total_tokens: number;
  total_cost: number;
  by_model: ModelCostBreakdown[];
}

export interface TenantDetail extends TenantSummary {
  year: number;
  month: number;
  user_count: number;
  conversation_count: number;
}

export interface InvoiceData {
  invoice: {
    tenant_id: string;
    period: string;
    generated_at: string;
    total_tokens: number;
    total_cost: number;
    by_model: ModelCostBreakdown[];
  };
}

export interface ModelGovernance {
  id: string;
  model_id: string;
  display_name: string | null;
  provider: string;
  is_enabled: boolean;
  cost_rate_per_1k_tokens: number;
  daily_token_limit: number | null;
  daily_request_limit: number | null;
  updated_by: string | null;
  updated_at: string | null;
}

export interface ModelGovernanceUpdate {
  is_enabled?: boolean;
  cost_rate_per_1k_tokens?: number;
  daily_token_limit?: number | null;
  daily_request_limit?: number | null;
}

export interface ErrorLogEntry {
  id: string;
  user_id: string | null;
  user_email: string | null;
  tenant_id: string | null;
  method: string;
  route: string;
  status_code: number;
  error_type: string;
  message: string;
  severity: string;
  request_id: string | null;
  created_at: string;
}

export interface ErrorLogDetail extends ErrorLogEntry {
  stack_trace: string | null;
}

export interface ErrorLogsResponse {
  total: number;
  limit: number;
  offset: number;
  errors: ErrorLogEntry[];
}

export interface UserDetailUsage {
  total_tokens: number;
  total_requests: number;
  conversations: number;
  estimated_cost_usd: number;
}

export interface UserDetail {
  user: {
    id: string;
    name: string;
    email: string;
    role: string;
    is_active: boolean;
    daily_token_limit: number | null;
    tokens_used_today: number;
    created_at: string;
  };
  period_days: number;
  usage: UserDetailUsage;
  recent_conversations: { id: string; title: string; updated_at: string }[];
  recent_audit: { action: string; resource_type: string; success: boolean; created_at: string }[];
}

export interface AdminAccessRequest {
  user_id: string;
  email: string;
  name: string | null;
  requested_at: string | null;
}

export interface ModelAccessRule {
  id: string;
  model_id: string;
  user_id: string | null;
  role: string | null;
  is_allowed: boolean;
  set_by: string | null;
  created_at: string;
}

export interface UserBudgetStatus {
  has_budget?: boolean;
  allowed?: boolean;
  usage_pct?: number;
  warning?: boolean;
  hard_stop?: boolean;
  message?: string | null;
  budget_type?: string | null;
  period?: string;
  token_budget?: number | null;
  token_used?: number;
  token_warning_pct?: number;
  cost_budget?: number | null;
  cost_used?: number;
  cost_warning_pct?: number;
  requests?: number;
}

// ── Microsoft Graph types ─────────────────────────────────────────────────────

export interface GraphMailMessage {
  id: string;
  subject: string | null;
  from_name: string | null;
  from_address: string | null;
  preview: string;
  received: string;
  is_read: boolean;
  has_attachments: boolean;
  importance: string;
}

export interface GraphCalendarEvent {
  id: string;
  subject: string;
  start: string;
  end: string;
  timezone: string | null;
  location: string | null;
  is_online: boolean;
  meeting_link: string | null;
  organizer: string | null;
  attendees: string[];
  web_link: string | null;
}

export interface GraphPlannerTask {
  id: string;
  title: string;
  plan_id: string | null;
  bucket_id: string | null;
  due_date: string | null;
  percent_complete: number;
  priority: number | null;
  created: string | null;
  assigned_to: string[];
}

export interface GraphStatus {
  obo_configured: boolean;
  auth_client_id: string | null;
  obo_succeeded: boolean;
  obo_error: string | null;
  user_id: string;
  instructions: string | null;
}

// Singleton
export const api = new ApiClient();
