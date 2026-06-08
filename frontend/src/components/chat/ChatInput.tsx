/**
 * Mela AI - Chat Input Component
 * Supports: real-time voice (Web Speech API), file attachments (drag-and-drop),
 * image OCR, document extraction, multi-model switching.
 */

'use client';

import {
  useState,
  useRef,
  useEffect,
  useCallback,
  KeyboardEvent,
  DragEvent,
} from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/Button';
import { Dropdown } from '@/components/ui/Dropdown';
import { useChatStore } from '@/lib/store';
import { api, ProcessedAttachment, InlineAttachment } from '@/lib/api';
import {
  Send,
  Paperclip,
  Mic,
  MicOff,
  StopCircle,
  Sparkles,
  X,
  FileText,
  Loader2,
  AlertCircle,
  FileAudio,
  ImageIcon,
  UploadCloud,
  CheckCircle2,
  Table2,
  Shield,
  Globe,
  Phone,
} from 'lucide-react';

interface AttachmentPreview {
  id: string;
  file: File;
  processed: ProcessedAttachment | null;
  loading: boolean;
  error: string | null;
}

interface ChatInputProps {
  onSend: (
    message: string,
    attachments?: string[],
    inlineAttachments?: InlineAttachment[],
  ) => void;
  disabled?: boolean;
}

const MAX_FILE_SIZE_MB = 20;

const SPREADSHEET_EXTS = new Set(['.xlsx', '.xls', '.csv', '.tsv']);
const SPREADSHEET_MIMES = new Set([
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.ms-excel',
  'text/csv',
  'application/csv',
]);

function isSpreadsheet(file: File) {
  if (SPREADSHEET_MIMES.has(file.type)) return true;
  const ext = '.' + file.name.split('.').pop()?.toLowerCase();
  return SPREADSHEET_EXTS.has(ext);
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [message, setMessage] = useState('');
  const [attachments, setAttachments] = useState<AttachmentPreview[]>([]);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingError, setRecordingError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  // Interim speech text shown below input while user is speaking
  const [interimText, setInterimText] = useState('');

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<any>(null);
  const dragCounterRef = useRef(0);
  // Tracks committed (final) text contributed by speech this session
  const speechFinalRef = useRef('');

  const {
    isStreaming,
    selectedModel,
    setSelectedModel,
    models,
    useWebSearch,
    setUseWebSearch,
    stopStreaming,
    isPrivateMode,
    startPrivateChat,
    exitPrivateChat,
    userFeatures,
    voiceModeEnabled,
    setVoiceModeEnabled,
    claudeUsage,
    activeProfile,
  } = useChatStore();

  const isWorkProfile = activeProfile === 'org' || activeProfile === 'work';

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, [message]);

  // Focus textarea on mount
  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  // Stop speech recognition if the component unmounts mid-recording
  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
        recognitionRef.current = null;
      }
    };
  }, []);

  // ── Send ──────────────────────────────────────────────────────────────────

  const handleSend = useCallback(() => {
    const trimmed = message.trim();
    if (!trimmed && attachments.length === 0) return;
    if (isStreaming || disabled) return;
    if (attachments.some((a) => a.loading)) return;

    const inlineAttachments: InlineAttachment[] = attachments
      .filter((a) => a.processed && !a.error)
      .map((a) => ({
        filename: a.file.name,
        content_type: a.processed!.content_type,
        text_content: a.processed!.text_content,
        base64_data: a.processed!.base64_data,
        ocr_text: a.processed!.ocr_text,
        raw_base64: a.processed!.raw_base64, // for spreadsheets → code interpreter
      }));

    onSend(trimmed, undefined, inlineAttachments.length ? inlineAttachments : undefined);
    setMessage('');
    setAttachments([]);
    speechFinalRef.current = '';

    if (textareaRef.current) textareaRef.current.style.height = 'auto';
  }, [message, attachments, isStreaming, disabled, onSend]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Alt+Enter: insert newline (same as Shift+Enter)
    if (e.key === 'Enter' && e.altKey) {
      e.preventDefault();
      const textarea = textareaRef.current;
      if (textarea) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const newValue = message.slice(0, start) + '\n' + message.slice(end);
        setMessage(newValue);
        // Move cursor after the inserted newline
        requestAnimationFrame(() => {
          textarea.selectionStart = textarea.selectionEnd = start + 1;
        });
      }
      return;
    }
    // Enter alone: send message
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // ── File processing ────────────────────────────────────────────────────────

  const processFiles = useCallback(async (files: File[]) => {
    for (const file of files) {
      if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
        const preview: AttachmentPreview = {
          id: Math.random().toString(36).slice(2),
          file,
          processed: null,
          loading: false,
          error: `File too large (max ${MAX_FILE_SIZE_MB} MB)`,
        };
        setAttachments((prev) => [...prev, preview]);
        continue;
      }

      const id = Math.random().toString(36).slice(2);
      const preview: AttachmentPreview = { id, file, processed: null, loading: true, error: null };
      setAttachments((prev) => [...prev, preview]);

      try {
        const processed = await api.processAttachment(file, true);
        setAttachments((prev) =>
          prev.map((a) => (a.id === id ? { ...a, processed, loading: false } : a)),
        );
      } catch (err: any) {
        setAttachments((prev) =>
          prev.map((a) =>
            a.id === id
              ? { ...a, loading: false, error: err.message || 'Processing failed' }
              : a,
          ),
        );
      }
    }
  }, []);

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (fileInputRef.current) fileInputRef.current.value = '';
    if (files.length > 0) await processFiles(files);
  };

  const removeAttachment = (id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  };

  // ── Drag and drop ──────────────────────────────────────────────────────────

  const handleDragEnter = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current += 1;
    if (dragCounterRef.current === 1) setIsDragging(true);
  };

  const handleDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current === 0) setIsDragging(false);
  };

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = async (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setIsDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) await processFiles(files);
  };

  // ── Paste (Ctrl+V) support ─────────────────────────────────────────────────

  const handlePaste = useCallback(
    async (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      const items = Array.from(e.clipboardData?.items || []);
      const fileItems = items.filter(
        (item) => item.kind === 'file' && item.getAsFile(),
      );

      if (fileItems.length === 0) {
        // Plain text paste — let the browser handle it normally
        return;
      }

      // Prevent the default text insertion for file pastes
      e.preventDefault();

      const files = fileItems
        .map((item) => item.getAsFile())
        .filter((f): f is File => f !== null);

      if (files.length > 0) {
        await processFiles(files);
      }
    },
    [processFiles],
  );

  // ── Real-time Voice (Web Speech API) ──────────────────────────────────────

  const startSpeech = useCallback(async () => {
    setRecordingError(null);

    const SpeechRecognition =
      (typeof window !== 'undefined' &&
        ((window as any).SpeechRecognition ||
          (window as any).webkitSpeechRecognition)) ||
      null;

    if (!SpeechRecognition) {
      setRecordingError('Speech recognition is not supported in this browser. Try Chrome or Edge.');
      return;
    }

    try {
      // Request mic permission explicitly before starting (gives a clear error)
      await navigator.mediaDevices.getUserMedia({ audio: true }).then((s) =>
        s.getTracks().forEach((t) => t.stop()),
      );
    } catch {
      setRecordingError('Microphone access denied. Please allow microphone access and try again.');
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';
    recognition.maxAlternatives = 1;

    speechFinalRef.current = message; // start from current text

    recognition.onresult = (event: any) => {
      let interim = '';
      let finalSoFar = speechFinalRef.current;

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          // Append final segment with a space
          finalSoFar = finalSoFar
            ? `${finalSoFar.trimEnd()} ${transcript.trim()}`
            : transcript.trim();
          speechFinalRef.current = finalSoFar;
        } else {
          interim += transcript;
        }
      }

      setMessage(finalSoFar);
      setInterimText(interim);
    };

    recognition.onerror = (event: any) => {
      if (event.error === 'no-speech') return; // ignore silence
      if (event.error === 'aborted') return;    // user stopped intentionally
      setRecordingError(`Speech error: ${event.error}`);
      setIsRecording(false);
      setInterimText('');
    };

    recognition.onend = () => {
      setIsRecording(false);
      setInterimText('');
      recognitionRef.current = null;
    };

    recognitionRef.current = recognition;
    recognition.start();
    setIsRecording(true);
  }, [message]);

  const stopSpeech = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
    }
    setIsRecording(false);
    setInterimText('');
  }, []);

  const toggleRecording = () => {
    if (isRecording) stopSpeech();
    else startSpeech();
  };

  // ── Model options ─────────────────────────────────────────────────────────

  // Company grouping for model picker
  const MODEL_COMPANY: Record<string, string> = {
    'gpt-5.2-chat': 'OpenAI',  'gpt-4.1': 'OpenAI',
    'kimi-k2.5': 'Moonshot AI',
    'mistral-large-3': 'Mistral',
    'grok-3-mini': 'xAI',
    'llama-4-maverick': 'Meta',
    'gemini-2.0-flash': 'Google',
    'claude-sonnet-4-6': 'Anthropic',  'claude-haiku-4-5': 'Anthropic',
  };

  const _autoOption = {
    value: 'auto',
    label: 'Auto ✦',
    description: 'Best model selected automatically per task',
    icon: <Sparkles className="h-4 w-4 text-primary" />,
    group: 'Recommended',
  };

  // Models served by Azure AI Foundry (Foundry IQ). External providers
  // (Google Gemini, Anthropic Claude) call out to their own APIs; everything
  // else routes through our Azure AI Foundry deployments.
  const _NON_FOUNDRY_GROUPS = ['Google', 'Anthropic'];
  const _isFoundry = (group: string) => !_NON_FOUNDRY_GROUPS.includes(group);

  const modelOptions =
    models.length > 0
      ? [_autoOption, ...models.map((m) => {
          const group = MODEL_COMPANY[m.id] ?? 'Other';
          const baseDesc = m.preview
            ? `${m.description} · ⚠ 3 req/min`
            : m.description;
          return {
            value: m.id,
            label: m.preview ? `${m.name} ⚗` : m.name,
            description: _isFoundry(group)
              ? `${baseDesc} · Azure AI Foundry`
              : baseDesc,
            icon: <Sparkles className="h-4 w-4" />,
            group,
          };
        })]
      : [
          _autoOption,
          { value: 'gpt-5.2-chat',       label: 'GPT-5.2',              description: 'Next-gen frontier',                            icon: <Sparkles className="h-4 w-4" />, group: 'OpenAI' },
          { value: 'gpt-4.1',            label: 'GPT-4.1',              description: 'Vision & tools',                               icon: <Sparkles className="h-4 w-4" />, group: 'OpenAI' },
          { value: 'kimi-k2.5',          label: 'Kimi-K2.5',            description: 'Long context',                                 icon: <Sparkles className="h-4 w-4" />, group: 'Moonshot AI' },
          { value: 'mistral-large-3',    label: 'Mistral Large 3',      description: 'Multilingual',                                 icon: <Sparkles className="h-4 w-4" />, group: 'Mistral' },
          { value: 'grok-3-mini',        label: 'Grok-3-mini',          description: 'Fast reasoning',                               icon: <Sparkles className="h-4 w-4" />, group: 'xAI' },
          { value: 'llama-4-maverick',   label: 'Llama 4 Maverick',     description: 'Meta MoE model',                               icon: <Sparkles className="h-4 w-4" />, group: 'Meta' },
          { value: 'gemini-2.0-flash',   label: 'Gemini 2.0 Flash',     description: 'Google AI · free tier',                        icon: <Sparkles className="h-4 w-4" />, group: 'Google' },
          { value: 'claude-sonnet-4-6',  label: 'Claude Sonnet 4.6 ⚗',  description: 'Advanced reasoning & writing · ⚠ 3 req/min',   icon: <Sparkles className="h-4 w-4" />, group: 'Anthropic' },
          { value: 'claude-haiku-4-5',   label: 'Claude Haiku 4.5 ⚗',   description: 'Fast, efficient responses · ⚠ 3 req/min',      icon: <Sparkles className="h-4 w-4" />, group: 'Anthropic' },
        ];

  const processingCount = attachments.filter((a) => a.loading).length;
  const readyCount = attachments.filter((a) => !a.loading && !a.error).length;
  const hasPendingAttachments = processingCount > 0;
  const canSend =
    !disabled && !isStreaming && !hasPendingAttachments &&
    (message.trim().length > 0 || attachments.length > 0);

  return (
    <div
      className={cn(
        'border-t bg-background p-4 transition-colors',
        isDragging && 'bg-primary/5 border-primary',
      )}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragging && (
        <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-background/90 border-2 border-dashed border-primary rounded-xl pointer-events-none">
          <UploadCloud className="h-10 w-10 text-primary" />
          <p className="font-medium text-primary">Drop files to attach</p>
        </div>
      )}

      {/* Files received / processing banner */}
      {attachments.length > 0 && (
        <div className={cn(
          'flex items-center gap-2 mb-2 px-3 py-1.5 rounded-lg text-sm border',
          processingCount > 0
            ? 'bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300'
            : 'bg-green-50 dark:bg-green-950/30 border-green-200 dark:border-green-800 text-green-700 dark:text-green-300',
        )}>
          {processingCount > 0 ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
              <span>
                Processing {processingCount} file{processingCount > 1 ? 's' : ''}
                {readyCount > 0 ? ` · ${readyCount} ready` : ''}
                …
              </span>
            </>
          ) : (
            <>
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
              <span>
                {attachments.length} file{attachments.length > 1 ? 's' : ''} received and ready
                {attachments.some((a) => isSpreadsheet(a.file)) && ' · spreadsheets available for calculations'}
              </span>
            </>
          )}
        </div>
      )}

      {/* Attachment previews */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3 max-h-40 overflow-y-auto">
          {attachments.map((preview) => (
            <AttachmentBadge
              key={preview.id}
              preview={preview}
              onRemove={() => removeAttachment(preview.id)}
            />
          ))}
        </div>
      )}

      {/* Recording indicator */}
      {isRecording && (
        <div className="flex items-center gap-2 mb-2 px-3 py-1.5 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-300">
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-red-500" />
          </span>
          Listening… speak now · tap mic to stop
          {interimText && (
            <span className="ml-2 italic opacity-70 truncate max-w-[200px]">{interimText}</span>
          )}
        </div>
      )}

      {/* Error banner */}
      {recordingError && (
        <div className="flex items-center gap-2 mb-2 px-3 py-1.5 rounded-lg bg-destructive/10 border border-destructive/30 text-sm text-destructive">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">{recordingError}</span>
          <button onClick={() => setRecordingError(null)}>
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Private mode banner */}
      {isPrivateMode && (
        <div className="flex items-center gap-2 mb-2 px-3 py-1.5 rounded-lg bg-violet-50 dark:bg-violet-950/30 border border-violet-200 dark:border-violet-800 text-sm text-violet-700 dark:text-violet-300">
          <Shield className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">Private mode — this conversation won't be saved to your history and will auto-delete after 20 days</span>
          <button onClick={exitPrivateChat} title="Exit private mode">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Input row */}
      <div className="flex items-end gap-3">
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            value={message}
            onChange={(e) => {
              setMessage(e.target.value);
              // Keep final ref in sync with manual edits
              if (!isRecording) speechFinalRef.current = e.target.value;
            }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={
              isRecording
                ? 'Listening… speak now'
                : isStreaming
                ? 'Mela AI is responding…'
                : 'Message Mela AI…'
            }
            disabled={disabled || isStreaming}
            rows={1}
            className={cn(
              'w-full resize-none rounded-xl border border-input bg-background px-4 py-3 pr-24 text-sm',
              'placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring',
              'disabled:cursor-not-allowed disabled:opacity-50',
              'min-h-[48px] max-h-[200px] transition-shadow',
              isRecording && 'ring-2 ring-red-300 dark:ring-red-700',
            )}
          />

          {/* Buttons inside textarea */}
          <div className="absolute right-2 bottom-2 flex items-center gap-1">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              onChange={handleFileSelect}
              className="hidden"
              accept="*/*"
            />
            <Button
              variant="ghost"
              size="icon"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled || isStreaming}
              title="Attach files (images, PDFs, Excel, CSV, Word, audio, …)"
              className="h-8 w-8"
            >
              <Paperclip className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleRecording}
              disabled={disabled || isStreaming}
              title={isRecording ? 'Stop recording' : 'Voice input (real-time)'}
              className={cn(
                'h-8 w-8 transition-colors',
                isRecording && 'text-red-500 bg-red-50 dark:bg-red-950/30',
              )}
            >
              {isRecording ? (
                <MicOff className="h-4 w-4" />
              ) : (
                <Mic className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>

        {/* Send / Stop */}
        {isStreaming ? (
          <Button
            onClick={stopStreaming}
            variant="destructive"
            size="icon"
            className="h-12 w-12 rounded-xl shrink-0"
            title="Stop generating"
          >
            <StopCircle className="h-5 w-5" />
          </Button>
        ) : (
          <Button
            onClick={handleSend}
            disabled={!canSend}
            className="h-12 w-12 rounded-xl shrink-0 bg-primary hover:bg-primary/90"
            title={hasPendingAttachments ? 'Processing attachments…' : 'Send'}
          >
            {hasPendingAttachments ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <Send className="h-5 w-5" />
            )}
          </Button>
        )}
      </div>

      {/* Bottom bar */}
      <div className="flex items-center justify-between mt-3">
        <div className="flex items-center gap-4 flex-wrap">
          {/* Model selector */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Model:</span>
            <Dropdown
              value={selectedModel}
              onChange={setSelectedModel}
              options={modelOptions}
              className="w-44"
              position="up"
              panelMinWidth="260px"
            />
          </div>

          {/* Enterprise mode indicator — Work profile only */}
          {isWorkProfile && (
            <div
              className={cn(
                'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs transition-colors',
                true
                  ? 'bg-primary/10 text-primary border border-primary/20'
                  : 'text-muted-foreground hover:text-foreground',
              )}
              title="Work mode uses enterprise knowledge sources automatically"
            >
              <FileText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Enterprise Knowledge</span>
            </div>
          )}

          {/* Public web search toggle */}
          <button
            onClick={() => setUseWebSearch(!useWebSearch)}
            className={cn(
              'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs transition-colors',
              useWebSearch
                ? 'bg-green-500/10 text-green-600 dark:text-green-400 border border-green-500/20'
                : 'text-muted-foreground hover:text-foreground',
            )}
            title={useWebSearch ? 'Disable web search' : 'Enable live web search'}
          >
            <Globe className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Web</span>
          </button>

          {/* Private mode toggle */}
          {userFeatures?.features?.['private_chat'] && (
            <button
              onClick={() => isPrivateMode ? exitPrivateChat() : startPrivateChat()}
              className={cn(
                'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs transition-colors',
                isPrivateMode
                  ? 'bg-violet-600 text-white border border-violet-700 shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
              title={isPrivateMode ? 'Exit private mode' : 'Start private (incognito) chat — not saved to history, auto-deletes after 20 days'}
              disabled={isStreaming}
            >
              <Shield className="h-3.5 w-3.5" />
              <span>Private</span>
            </button>
          )}

          {/* Voice chat mode toggle */}
          <button
            onClick={() => setVoiceModeEnabled(!voiceModeEnabled)}
            className={cn(
              'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs transition-colors',
              voiceModeEnabled
                ? 'bg-blue-600 text-white border border-blue-700 shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
            title={voiceModeEnabled ? 'Exit voice chat' : 'Start voice chat mode — AI speaks back to you'}
          >
            <Phone className="h-3.5 w-3.5" />
            <span>Voice</span>
          </button>
        </div>

        {/* Claude daily usage badge */}
        {claudeUsage && selectedModel?.startsWith('claude-') && claudeUsage.limit > 0 && (
          <span
            className={cn(
              'text-xs px-2 py-0.5 rounded-full font-medium',
              claudeUsage.remaining === 0
                ? 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
                : claudeUsage.remaining <= 2
                ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                : 'bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400',
            )}
            title={`Claude questions today: ${claudeUsage.question_count} / ${claudeUsage.limit}`}
          >
            {claudeUsage.remaining === 0
              ? `Daily limit reached (${claudeUsage.limit}/${claudeUsage.limit})`
              : `${claudeUsage.question_count}/${claudeUsage.limit} Claude questions`}
          </span>
        )}

        <p className="text-xs text-muted-foreground hidden sm:block">
          Shift+Enter for new line · Drop files anywhere
        </p>
      </div>
    </div>
  );
}

// ── Attachment badge ───────────────────────────────────────────────────────────

function AttachmentBadge({
  preview,
  onRemove,
}: {
  preview: AttachmentPreview;
  onRemove: () => void;
}) {
  const { file, processed, loading, error } = preview;
  const ct = file.type;
  const spreadsheet = isSpreadsheet(file);

  const icon = loading ? (
    <Loader2 className="h-4 w-4 animate-spin shrink-0" />
  ) : error ? (
    <AlertCircle className="h-4 w-4 text-destructive shrink-0" />
  ) : spreadsheet ? (
    <Table2 className="h-4 w-4 shrink-0 text-green-600" />
  ) : ct.startsWith('image/') ? (
    <ImageIcon className="h-4 w-4 shrink-0" />
  ) : ct.startsWith('audio/') ? (
    <FileAudio className="h-4 w-4 shrink-0" />
  ) : (
    <FileText className="h-4 w-4 shrink-0" />
  );

  const sublabel = error
    ? error
    : loading
    ? 'Processing…'
    : processed?.type === 'image'
    ? processed.ocr_text
      ? 'Image + OCR'
      : 'Image ready'
    : processed?.type === 'audio'
    ? 'Transcribed'
    : spreadsheet && processed?.raw_base64
    ? 'Ready for calculations'
    : processed?.text_content
    ? `${Math.round(processed.text_content.length / 1000)}k chars`
    : 'Ready';

  return (
    <div
      className={cn(
        'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm border',
        error
          ? 'bg-destructive/10 border-destructive/30 text-destructive'
          : loading
          ? 'bg-muted border-border'
          : spreadsheet && !loading
          ? 'bg-green-50 dark:bg-green-950/30 border-green-200 dark:border-green-800'
          : 'bg-blue-50 dark:bg-blue-950/30 border-blue-200 dark:border-blue-800',
      )}
    >
      {/* Image thumbnail */}
      {processed?.type === 'image' && processed.base64_data ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={processed.base64_data}
          alt={file.name}
          className="h-8 w-8 rounded object-cover shrink-0"
        />
      ) : (
        icon
      )}

      <div className="flex flex-col min-w-0">
        <span className="max-w-[140px] truncate font-medium text-xs">{file.name}</span>
        <span className="text-xs text-muted-foreground">{sublabel}</span>
      </div>

      <button
        onClick={onRemove}
        className="text-muted-foreground hover:text-foreground ml-0.5 shrink-0"
        title="Remove"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
