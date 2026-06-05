'use client';

/**
 * Mela AI - Embed Chat Interface (Phase 6B)
 *
 * Stripped-down chat surface designed to run inside an iframe.
 * Excludes: sidebar, conversation switching, profile switcher, admin
 * links, settings modal, voice overlay.  Includes: message stream,
 * input, WorkerEventBar (so embedded users still see live worker
 * activity).
 *
 * Auth: the embed token from the URL.  All API calls present this
 * token via the standard Bearer header — Mela's auth path validates
 * it as a scoped, time-limited credential issued by an MCP client.
 */

import { useEffect, useRef, useState } from 'react';
import { Loader2, Send } from 'lucide-react';
import type { ChatChunk } from '@/lib/api';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || '';

interface EmbedConfig {
  user_id: string;
  tenant_id: string | null;
  profile_mode: 'personal' | 'work';
  allowed_tools: string[];
  client_id: string;
  client_name: string;
  expires_at: string;
  theme: Record<string, unknown>;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  /** True while the assistant message is still streaming. */
  streaming?: boolean;
}

interface EmbedChatInterfaceProps {
  token: string;
}

export function EmbedChatInterface({ token }: EmbedChatInterfaceProps) {
  const [config, setConfig] = useState<EmbedConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Resolve config on mount.  Failure → render an error state and
  // refuse to send messages — without config we can't enforce scope.
  useEffect(() => {
    let aborted = false;
    async function run() {
      try {
        const url = `${API_BASE_URL}/api/v1/embed/config?token=${encodeURIComponent(token)}`;
        const res = await fetch(url);
        if (!res.ok) {
          throw new Error(`Embed config failed (HTTP ${res.status})`);
        }
        const data = (await res.json()) as EmbedConfig;
        if (!aborted) setConfig(data);
      } catch (err: unknown) {
        if (!aborted) {
          const msg = err instanceof Error ? err.message : 'Embed init failed';
          setConfigError(msg);
        }
      }
    }
    void run();
    return () => {
      aborted = true;
    };
  }, [token]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function send() {
    const text = input.trim();
    if (!text || sending || !config) return;
    setInput('');
    setSending(true);
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: text },
      { role: 'assistant', content: '', streaming: true },
    ]);

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
        'X-Profile-Mode': config.profile_mode,
      };
      if (config.profile_mode === 'work' && config.tenant_id) {
        headers['X-Tenant-Id'] = config.tenant_id;
      }

      const res = await fetch(`${API_BASE_URL}/api/v1/chat/completions`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          message: text,
          conversation_id: conversationId,
          model: 'auto',
          stream: true,
          context_type: config.profile_mode === 'work' ? 'org' : 'personal',
        }),
      });

      if (!res.ok || !res.body) {
        throw new Error(`Chat failed (HTTP ${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let assistantText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6).trim();
          if (data === '[DONE]') continue;
          try {
            const chunk = JSON.parse(data) as ChatChunk;
            if (chunk.type === 'content' && chunk.content) {
              assistantText += chunk.content;
              setMessages((prev) => {
                const next = [...prev];
                next[next.length - 1] = {
                  role: 'assistant',
                  content: assistantText,
                  streaming: true,
                };
                return next;
              });
            } else if (chunk.type === 'done' && chunk.data) {
              const cid = (chunk.data as Record<string, unknown>).conversation_id;
              if (typeof cid === 'string') setConversationId(cid);
            }
          } catch {
            // Skip malformed chunk lines — connection stays open.
          }
        }
      }

      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          role: 'assistant',
          content: assistantText,
          streaming: false,
        };
        return next;
      });

      // Phase 6B: notify the host page (the parent window) so the
      // mela-chat web component can dispatch a 'mela-response' event.
      if (typeof window !== 'undefined' && window.parent !== window) {
        window.parent.postMessage(
          {
            type: 'mela-response',
            payload: { content: assistantText, conversationId },
          },
          '*',
        );
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : String(err);
      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          role: 'assistant',
          content: `Mela could not respond: ${errMsg}`,
          streaming: false,
        };
        return next;
      });
    } finally {
      setSending(false);
    }
  }

  // Listen for sendMessage() calls from the host web component, which
  // arrive as 'mela-send' postMessage frames.
  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      const data = ev.data as { type?: string; payload?: { text?: string } } | undefined;
      if (!data || data.type !== 'mela-send') return;
      const text = data.payload?.text;
      if (typeof text === 'string' && text.trim()) {
        setInput(text);
        // Defer so the input state lands before send() reads it.
        setTimeout(() => void send(), 0);
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
    // send() captures config + token via closure — re-bind on either change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config, token]);

  if (configError) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-red-500 p-4">
        {configError}
      </div>
    );
  }
  if (!config) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-5 w-5 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-background">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center pt-12">
            Ask Mela anything. (Embedded by {config.client_name})
          </p>
        ) : (
          messages.map((m, i) => (
            <div
              key={i}
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                m.role === 'user'
                  ? 'ml-auto bg-primary text-primary-foreground'
                  : 'mr-auto bg-card border'
              }`}
            >
              {m.content || (m.streaming ? '…' : '')}
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="border-t p-3 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder="Ask Mela…"
          disabled={sending}
          className="flex-1 text-sm px-3 py-2 border rounded-md bg-background"
        />
        <button
          onClick={() => void send()}
          disabled={sending || !input.trim()}
          aria-label="Send"
          className="px-3 py-2 bg-primary text-primary-foreground rounded-md disabled:opacity-50"
        >
          {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </button>
      </div>
    </div>
  );
}
