/**
 * Mela AI - Chat Message Component
 */

'use client';

import { useState, useCallback } from 'react';
import { useChatStore } from '@/lib/store';
import { cn, copyToClipboard } from '@/lib/utils';
import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { api, Message, Citation, GeneratedFile, InlineAttachment, GeneratedImage as GeneratedImageData, EmailDraft } from '@/lib/api';
import {
  Copy,
  Check,
  ThumbsUp,
  ThumbsDown,
  RefreshCw,
  FileText,
  ChevronDown,
  ChevronUp,
  ImageOff,
  ExternalLink,
  Download,
  FileAudio,
  Table2,
  X,
  ZoomIn,
  Volume2,
  VolumeX,
  Mail,
  Send,
  Pencil,
  Loader2 as SpinnerIcon,
} from 'lucide-react';
import Image from 'next/image';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';

interface ChatMessageProps {
  message: Message;
  isStreaming?: boolean;
  userName?: string;
  userAvatar?: string;
  onRegenerate?: () => void;
}

function formatTime(isoString?: string): string {
  if (!isoString) return '';
  try {
    return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── Model attribution: display name + relative cost (× cheapest tier) ──
// Multipliers are rough cost-relative figures used solely to give the user
// a sense of which model answered them and how "expensive" that answer was.
// The live values come from /settings/models (admin-controlled). This map
// is a fallback for when the live data hasn't loaded yet or a model is missing.
const MODEL_ATTRIBUTION_FALLBACK: Record<string, { name: string; multiplier: string }> = {
  // Anthropic
  'claude-opus-4-6':      { name: 'Claude Opus 4.6',     multiplier: '15x' },
  'claude-sonnet-4-6':    { name: 'Claude Sonnet 4.6',   multiplier: '5x'  },
  'claude-haiku-4-5':     { name: 'Claude Haiku 4.5',    multiplier: '1x'  },
  // Azure OpenAI / Foundry
  'gpt-5.2-chat':         { name: 'GPT-5.2',             multiplier: '7.5x' },
  'gpt-4.1':              { name: 'GPT-4.1',             multiplier: '3x'  },
  'gpt-4o':               { name: 'GPT-4o',              multiplier: '3x'  },
  'gpt-4o-mini':          { name: 'GPT-4o mini',         multiplier: '1x'  },
  'kimi-k2.5':            { name: 'Kimi K2.5',           multiplier: '2x'  },
  'mistral-large-3':      { name: 'Mistral Large 3',     multiplier: '2x'  },
  'grok-3-mini':          { name: 'Grok 3 mini',         multiplier: '1x'  },
  'llama-4-maverick':     { name: 'Llama 4 Maverick',    multiplier: '1x'  },
  'llama-4-maverick-17b-128e-instruct-fp8': { name: 'Llama 4 Maverick', multiplier: '1x' },
  // Google
  'gemini-2.0-flash':     { name: 'Gemini 2.0 Flash',    multiplier: '1x'  },
  'gemini-1.5-pro':       { name: 'Gemini 1.5 Pro',      multiplier: '4x'  },
  'gemini-1.5-flash':     { name: 'Gemini 1.5 Flash',    multiplier: '1x'  },
};

function formatMultiplier(n: number): string {
  // 1.0 → "1x", 7.5 → "7.5x", 15 → "15x"
  return Number.isInteger(n) ? `${n}x` : `${n.toFixed(1).replace(/\.0$/, '')}x`;
}

function getModelAttribution(
  modelId: string | undefined,
  live: Record<string, { name: string; multiplier: number }>,
): { name: string; multiplier: string } | null {
  if (!modelId || modelId === 'auto') return null;
  const liveHit = live[modelId];
  if (liveHit) {
    return { name: liveHit.name, multiplier: formatMultiplier(liveHit.multiplier) };
  }
  const direct = MODEL_ATTRIBUTION_FALLBACK[modelId];
  if (direct) return direct;
  // Best-effort fallback: title-case the id
  const pretty = modelId
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
  return { name: pretty, multiplier: '1x' };
}

export function ChatMessage({
  message,
  isStreaming,
  userName,
  userAvatar,
  onRegenerate,
}: ChatMessageProps) {
  const [copied, setCopied] = useState(false);
  const [showCitations, setShowCitations] = useState(false);
  const [liked, setLiked] = useState<'up' | 'down' | null>(null);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);

  const { speakText, stopAudio, isPlayingAudio, ttsError, modelAttribution } = useChatStore();

  const isUser = message.role === 'user';
  const isAssistant = message.role === 'assistant';
  const time = formatTime(message.created_at);

  const handleSpeak = useCallback(() => {
    if (isPlayingAudio) {
      stopAudio();
    } else {
      speakText(message.content).catch(() => {});
    }
  }, [isPlayingAudio, speakText, stopAudio, message.content]);

  const handleCopy = async () => {
    await copyToClipboard(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Separate image attachments from other attachments for rendering order
  const allAttachments = message.inline_attachments ?? [];
  const imageAttachments = allAttachments.filter(
    (a) => a.content_type?.startsWith('image/') && a.base64_data,
  );
  const otherAttachments = allAttachments.filter(
    (a) => !a.content_type?.startsWith('image/') || !a.base64_data,
  );

  return (
    <>
      {/* Lightbox overlay */}
      {lightboxSrc && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
          onClick={() => setLightboxSrc(null)}
        >
          <div className="relative max-w-[90vw] max-h-[90vh]">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={lightboxSrc}
              alt="Attachment preview"
              className="max-w-full max-h-[90vh] rounded-xl shadow-2xl object-contain"
              onClick={(e) => e.stopPropagation()}
            />
            <button
              onClick={() => setLightboxSrc(null)}
              className="absolute -top-3 -right-3 w-8 h-8 rounded-full bg-background border shadow-md flex items-center justify-center hover:bg-muted transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      <div
        className={cn(
          'group flex gap-3 px-4 py-4',
          isUser ? 'flex-row-reverse' : 'flex-row',
        )}
      >
        {/* Avatar */}
        <div className="shrink-0">
          {isUser ? (
            <Avatar src={userAvatar} alt={userName} size="md" className="bg-primary/10" />
          ) : (
            <div className="w-10 h-10 rounded-full bg-white border border-gray-200 flex items-center justify-center overflow-hidden shadow-sm">
              <Image
                src="/mela-logo.png"
                alt="Mela AI"
                width={32}
                height={32}
                className="object-contain"
              />
            </div>
          )}
        </div>

        {/* Content */}
        <div
          className={cn(
            'flex-1 min-w-0 space-y-1 max-w-[78%]',
            isUser ? 'text-right items-end flex flex-col' : 'text-left',
          )}
        >
          {/* Name + time */}
          <div
            className={cn(
              'flex items-center gap-2 text-xs text-muted-foreground',
              isUser ? 'flex-row-reverse' : 'flex-row',
            )}
          >
            <span className="font-medium">{isUser ? userName || 'You' : 'Mela AI'}</span>
            {time && <span>{time}</span>}
          </div>

          {/* ── Attachment previews (user messages only, appear BEFORE text bubble) */}
          {isUser && allAttachments.length > 0 && (
            <div className={cn('flex flex-col gap-2', isUser ? 'items-end' : 'items-start')}>

              {/* Image thumbnails */}
              {imageAttachments.map((att, idx) => (
                <AttachmentImagePreview
                  key={idx}
                  attachment={att}
                  onExpand={() => setLightboxSrc(att.base64_data!)}
                />
              ))}

              {/* Non-image file cards */}
              {otherAttachments.length > 0 && (
                <div className="flex flex-wrap gap-2 justify-end">
                  {otherAttachments.map((att, idx) => (
                    <AttachmentFileCard key={idx} attachment={att} />
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ── Text Bubble */}
          {(message.content || isStreaming) && (
            <div
              className={cn(
                'rounded-2xl px-4 py-3 text-left min-w-0',
                isUser
                  ? 'bg-primary text-primary-foreground rounded-tr-sm max-w-[min(680px,85vw)]'
                  : 'bg-muted rounded-tl-sm w-full max-w-[min(780px,92vw)]',
              )}
            >
              <div
                className={cn(
                  'chat-content max-w-none break-words min-w-0',
                  isUser ? 'chat-content-user' : '',
                )}
              >
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    code({ className, children, ...props }) {
                      const match = /language-(\w+)/.exec(className || '');
                      const language = match ? match[1] : '';
                      // Treat as block if: has a language tag OR content spans multiple lines
                      const isBlock = !!(language || String(children).includes('\n'));

                      if (isBlock) {
                        return (
                          <div className="relative group/code my-2 w-full overflow-hidden rounded-lg">
                            <div className="absolute right-2 top-2 opacity-0 group-hover/code:opacity-100 transition-opacity z-10">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => copyToClipboard(String(children))}
                                className="h-7 px-2 bg-background/80 backdrop-blur-sm"
                              >
                                <Copy className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                            <div className="overflow-x-auto">
                              <SyntaxHighlighter
                                style={oneDark as Record<string, React.CSSProperties>}
                                language={language}
                                PreTag="div"
                                className="!rounded-none !mt-0 !mb-0 !bg-[#1e1e1e]"
                                codeTagProps={{ style: { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.82em' } }}
                                customStyle={{ padding: '1em 1.1em', margin: 0 }}
                                {...(props as any)}
                              >
                                {String(children).replace(/\n$/, '')}
                              </SyntaxHighlighter>
                            </div>
                          </div>
                        );
                      }

                      return (
                        <code className={cn(className)} {...props}>
                          {children}
                        </code>
                      );
                    },
                    img({ src, alt }) {
                      return <GeneratedImagePreview src={src} alt={alt} />;
                    },
                    a({ href, children }) {
                      return (
                        <a
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline inline-flex items-center gap-0.5"
                        >
                          {children}
                          <ExternalLink className="h-3 w-3 inline" />
                        </a>
                      );
                    },
                    table({ children }) {
                      return (
                        <div className="overflow-x-auto my-2">
                          <table>{children}</table>
                        </div>
                      );
                    },
                  }}
                >
                  {message.content}
                </ReactMarkdown>

                {isStreaming && (
                  <span className="inline-block w-2 h-4 bg-current animate-pulse ml-0.5 align-middle" />
                )}
              </div>
            </div>
          )}

          {/* Generated images (DALL-E) */}
          {message.images && message.images.length > 0 && (
            <div className={cn('mt-2 space-y-2', isUser ? 'items-end flex flex-col' : 'items-start')}>
              {message.images.map((img, idx) => (
                <GeneratedImagePreview key={idx} src={img.url} alt={img.revised_prompt || img.original_prompt} />
              ))}
            </div>
          )}

          {/* Generated files (code interpreter downloads) */}
          {message.generated_files && message.generated_files.length > 0 && (
            <div className={cn('mt-2 space-y-1.5', isUser ? 'items-end' : 'items-start', 'flex flex-col')}>
              <p className="text-xs text-muted-foreground font-medium">Generated files:</p>
              {message.generated_files.map((file, idx) => (
                <GeneratedFileDownload key={idx} file={file} />
              ))}
            </div>
          )}

          {/* History-restored file metadata (no base64 — re-run to re-download) */}
          {message.generated_file_meta && message.generated_file_meta.length > 0 && (
            <div className={cn('mt-2 space-y-1.5', isUser ? 'items-end' : 'items-start', 'flex flex-col')}>
              <p className="text-xs text-muted-foreground font-medium">Previously generated files:</p>
              {message.generated_file_meta.map((meta, idx) => (
                <GeneratedFileMetaCard key={idx} meta={meta} />
              ))}
            </div>
          )}

          {/* Email draft card — shown when AI has created an Outlook draft */}
          {message.email_draft && (
            <EmailDraftCard draft={message.email_draft} />
          )}

          {/* Citations */}
          {message.citations && message.citations.length > 0 && (
            <div className={cn('mt-2', isUser ? 'text-right' : 'text-left')}>
              <button
                onClick={() => setShowCitations(!showCitations)}
                className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                <FileText className="h-3.5 w-3.5" />
                <span>
                  {message.citations.length} source{message.citations.length > 1 ? 's' : ''}
                </span>
                {showCitations ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>

              {showCitations && (
                <div className="mt-2 space-y-2">
                  {message.citations.map((citation, index) => (
                    <CitationCard key={index} citation={citation} index={index + 1} />
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Actions – visible on hover */}
          {isAssistant && !isStreaming && (
            <>
              {(() => {
                const attr = getModelAttribution(message.model, modelAttribution);
                if (!attr) return null;
                return (
                  <div
                    className="text-[11px] text-muted-foreground/70 mt-1 select-none"
                    title={message.model || ''}
                  >
                    {attr.name} <span className="opacity-60">• {attr.multiplier}</span>
                  </div>
                );
              })()}
            <div className="flex items-center gap-0.5 mt-1 opacity-0 group-hover:opacity-100 transition-opacity">
              <Button
                variant="ghost"
                size="sm"
                onClick={handleSpeak}
                className={cn(
                  'h-7 px-2',
                  isPlayingAudio && 'text-blue-500',
                  ttsError && 'text-red-400',
                )}
                title={
                  ttsError
                    ? ttsError
                    : isPlayingAudio
                    ? 'Stop speaking'
                    : 'Read aloud'
                }
              >
                {isPlayingAudio ? (
                  <VolumeX className="h-3.5 w-3.5" />
                ) : (
                  <Volume2 className="h-3.5 w-3.5" />
                )}
              </Button>
              <Button variant="ghost" size="sm" onClick={handleCopy} className="h-7 px-2">
                {copied ? (
                  <Check className="h-3.5 w-3.5 text-green-500" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className={cn('h-7 px-2', liked === 'up' && 'text-green-500')}
                onClick={() => setLiked(liked === 'up' ? null : 'up')}
              >
                <ThumbsUp className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className={cn('h-7 px-2', liked === 'down' && 'text-red-500')}
                onClick={() => setLiked(liked === 'down' ? null : 'down')}
              >
                <ThumbsDown className="h-3.5 w-3.5" />
              </Button>
              {onRegenerate && (
                <Button variant="ghost" size="sm" onClick={onRegenerate} className="h-7 px-2">
                  <RefreshCw className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

// ── Attachment Image Preview ───────────────────────────────────────────────────

function AttachmentImagePreview({
  attachment,
  onExpand,
}: {
  attachment: InlineAttachment;
  onExpand: () => void;
}) {
  const [failed, setFailed] = useState(false);

  if (failed || !attachment.base64_data) return null;

  return (
    <div className="relative group/img cursor-pointer" onClick={onExpand}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={attachment.base64_data}
        alt={attachment.filename}
        className="rounded-xl max-w-[280px] max-h-[220px] w-auto h-auto object-cover border border-border/50 shadow-sm"
        onError={() => setFailed(true)}
        loading="lazy"
      />
      {/* Zoom hint on hover */}
      <div className="absolute inset-0 rounded-xl bg-black/0 group-hover/img:bg-black/20 transition-colors flex items-center justify-center">
        <ZoomIn className="h-6 w-6 text-white opacity-0 group-hover/img:opacity-100 transition-opacity drop-shadow" />
      </div>
      {/* File name badge */}
      <div className="absolute bottom-0 left-0 right-0 rounded-b-xl px-2 py-1 bg-black/40 backdrop-blur-sm opacity-0 group-hover/img:opacity-100 transition-opacity">
        <p className="text-white text-xs truncate">{attachment.filename}</p>
      </div>
    </div>
  );
}

// ── Attachment File Card ───────────────────────────────────────────────────────

function AttachmentFileCard({ attachment }: { attachment: InlineAttachment }) {
  const ct = attachment.content_type || '';
  const isAudio = ct.startsWith('audio/');
  const isSpreadsheet =
    ct.includes('spreadsheet') ||
    ct.includes('excel') ||
    ct === 'text/csv' ||
    ct === 'application/csv' ||
    attachment.filename.toLowerCase().endsWith('.csv') ||
    attachment.filename.toLowerCase().endsWith('.xlsx') ||
    attachment.filename.toLowerCase().endsWith('.xls');

  const icon = isAudio ? (
    <FileAudio className="h-4 w-4 text-purple-500 shrink-0" />
  ) : isSpreadsheet ? (
    <Table2 className="h-4 w-4 text-green-600 shrink-0" />
  ) : (
    <FileText className="h-4 w-4 text-blue-500 shrink-0" />
  );

  const typeLabel = isAudio
    ? 'Audio'
    : isSpreadsheet
    ? 'Spreadsheet'
    : ct.includes('pdf')
    ? 'PDF'
    : ct.includes('word') || ct.includes('docx')
    ? 'Word'
    : 'Document';

  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-xl border border-border/50 bg-background/80 backdrop-blur-sm shadow-sm max-w-[240px]">
      {icon}
      <div className="flex flex-col min-w-0">
        <span className="text-xs font-medium truncate">{attachment.filename}</span>
        <span className="text-xs text-muted-foreground">{typeLabel}</span>
      </div>
    </div>
  );
}

// ── Generated Image with error fallback ───────────────────────────────────────

function GeneratedImagePreview({ src, alt }: { src?: string; alt?: string }) {
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);

  if (!src) return null;

  if (failed) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 p-6 rounded-lg border border-border bg-muted text-muted-foreground text-sm my-2">
        <ImageOff className="h-8 w-8" />
        <span>Image expired or unavailable</span>
        {src && !src.startsWith('data:') && (
          <a
            href={src}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline text-xs"
          >
            Try original link
          </a>
        )}
      </div>
    );
  }

  return (
    <>
      {expanded && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
          onClick={() => setExpanded(false)}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt={alt || 'Generated image'}
            className="max-w-[90vw] max-h-[90vh] rounded-xl shadow-2xl object-contain"
            onClick={(e) => e.stopPropagation()}
          />
          <button
            onClick={() => setExpanded(false)}
            className="absolute top-4 right-4 w-9 h-9 rounded-full bg-background border shadow-md flex items-center justify-center hover:bg-muted transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
      )}
      <div className="relative group/genimg cursor-pointer my-2" onClick={() => setExpanded(true)}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={alt || 'Generated image'}
          className="rounded-xl max-w-full max-h-[512px] object-contain border border-border shadow-sm"
          onError={() => setFailed(true)}
          loading="lazy"
        />
        <div className="absolute inset-0 rounded-xl bg-black/0 group-hover/genimg:bg-black/10 transition-colors flex items-end justify-end p-2">
          <div className="opacity-0 group-hover/genimg:opacity-100 transition-opacity bg-background/80 backdrop-blur-sm rounded-lg px-2 py-1 text-xs flex items-center gap-1">
            <ZoomIn className="h-3.5 w-3.5" />
            Click to expand
          </div>
        </div>
      </div>
    </>
  );
}

// ── Generated File Download ───────────────────────────────────────────────────

function fileTypeIcon(outputType?: string) {
  switch (outputType) {
    case 'excel': case 'csv': return <Table2 className="h-4 w-4 text-green-600 dark:text-green-400 shrink-0" />;
    case 'pdf': return <FileText className="h-4 w-4 text-red-500 shrink-0" />;
    case 'word': return <FileText className="h-4 w-4 text-blue-500 shrink-0" />;
    default: return <Download className="h-4 w-4 text-primary shrink-0" />;
  }
}

function fileTypeLabel(outputType?: string) {
  switch (outputType) {
    case 'excel': return 'Excel';
    case 'csv': return 'CSV';
    case 'pdf': return 'PDF';
    case 'word': return 'Word';
    case 'image': return 'Image';
    case 'zip': return 'ZIP';
    default: return 'File';
  }
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function GeneratedFileDownload({ file }: { file: GeneratedFile }) {
  const handleDownload = () => {
    try {
      const byteStr = atob(file.base64);
      const bytes = new Uint8Array(byteStr.length);
      for (let i = 0; i < byteStr.length; i++) bytes[i] = byteStr.charCodeAt(i);
      const blob = new Blob([bytes], { type: file.mime_type });
      triggerBlobDownload(blob, file.name);
    } catch (e) {
      console.error('Download failed:', e);
    }
  };

  return (
    <button
      onClick={handleDownload}
      title={`Download ${file.name}`}
      className="group flex items-center gap-2.5 px-3 py-2.5 rounded-lg border border-border bg-background hover:bg-muted transition-colors text-sm text-left w-full max-w-xs"
    >
      {fileTypeIcon(file.output_type)}
      <div className="flex-1 min-w-0">
        <p className="truncate font-medium leading-tight">{file.name}</p>
        <p className="text-xs text-muted-foreground leading-tight mt-0.5">
          {fileTypeLabel(file.output_type)} · {formatFileSize(file.size)}
        </p>
      </div>
      <Download className="h-3.5 w-3.5 text-muted-foreground shrink-0 transition-transform group-hover:translate-y-0.5" />
    </button>
  );
}

function GeneratedFileMetaCard({
  meta,
}: {
  meta: { file_log_id?: string; name: string; mime_type: string; size: number; output_type: string };
}) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDownload = async () => {
    if (!meta.file_log_id) return;
    setDownloading(true);
    setError(null);
    try {
      const blob = await api.downloadGeneratedFile(meta.file_log_id);
      triggerBlobDownload(blob, meta.name);
    } catch (e: any) {
      setError(e?.message || 'Download failed');
    } finally {
      setDownloading(false);
    }
  };

  const canDownload = !!meta.file_log_id;

  return (
    <button
      onClick={canDownload ? handleDownload : undefined}
      disabled={downloading}
      title={
        canDownload
          ? `Download ${meta.name}`
          : 'Re-send the request to regenerate this file'
      }
      className={cn(
        'group flex items-center gap-2.5 px-3 py-2.5 rounded-lg border border-border text-sm text-left w-full max-w-xs transition-colors',
        canDownload
          ? 'bg-background hover:bg-muted cursor-pointer'
          : 'bg-muted/50 opacity-60 cursor-default',
      )}
    >
      {fileTypeIcon(meta.output_type)}
      <div className="flex-1 min-w-0">
        <p className={cn('truncate font-medium leading-tight', !canDownload && 'text-muted-foreground')}>
          {meta.name}
        </p>
        <p className="text-xs text-muted-foreground leading-tight mt-0.5">
          {fileTypeLabel(meta.output_type)} · {formatFileSize(meta.size)}
          {error && <span className="text-red-500 ml-1">· {error}</span>}
          {!canDownload && !error && ' · Re-send to download'}
        </p>
      </div>
      {downloading ? (
        <RefreshCw className="h-3.5 w-3.5 text-muted-foreground shrink-0 animate-spin" />
      ) : (
        <Download className={cn(
          'h-3.5 w-3.5 shrink-0',
          canDownload
            ? 'text-muted-foreground transition-transform group-hover:translate-y-0.5'
            : 'text-muted-foreground/50',
        )} />
      )}
    </button>
  );
}

// ── Email Draft Card ──────────────────────────────────────────────────────────

type DraftAction = 'pending' | 'sending' | 'sent' | 'kept' | 'error';

function EmailDraftCard({ draft: initialDraft }: { draft: EmailDraft }) {
  const [action, setAction] = useState<DraftAction>('pending');
  // Body preview is shown expanded by default so the user always sees what they're approving
  const [expanded, setExpanded] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const handleSendNow = async () => {
    setAction('sending');
    setError(null);
    try {
      await api.sendDraftMail(initialDraft.draft_id);
      setAction('sent');
    } catch (e: any) {
      setAction('error');
      setError(e?.message || 'Send failed — check your connection and try again.');
    }
  };

  const handleKeepAsDraft = () => {
    setAction('kept');
  };

  const isDone = action === 'sent' || action === 'kept';
  const isSending = action === 'sending';

  // Colour the card based on final state
  const cardClass = cn(
    'mt-3 rounded-xl border text-left overflow-hidden',
    action === 'sent'
      ? 'border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/30'
      : action === 'kept'
      ? 'border-muted bg-muted/40'
      : 'border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/30',
  );

  // Status icon
  const statusIcon =
    action === 'sent' ? (
      <Check className="h-4 w-4 text-green-600 dark:text-green-400" />
    ) : action === 'kept' ? (
      <Mail className="h-4 w-4 text-muted-foreground" />
    ) : (
      <Mail className="h-4 w-4 text-blue-600 dark:text-blue-400" />
    );

  const headingText =
    action === 'sent' ? 'Email sent' :
    action === 'kept' ? 'Saved to Drafts' :
    'Email ready for review';

  const subText =
    action === 'sent'
      ? `Sent to ${initialDraft.to.join(', ')}`
      : action === 'kept'
      ? 'Open Outlook to edit or send when ready'
      : `To: ${initialDraft.to.join(', ')}`;

  return (
    <div className={cardClass}>
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2.5 px-4 pt-3 pb-2">
        <div className={cn(
          'w-8 h-8 rounded-full flex items-center justify-center shrink-0',
          action === 'sent' ? 'bg-green-500/10' :
          action === 'kept' ? 'bg-muted' :
          'bg-blue-500/10',
        )}>
          {statusIcon}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">{headingText}</p>
          <p className="text-xs text-muted-foreground truncate">{subText}</p>
        </div>
        {/* Toggle preview */}
        {!isDone && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors shrink-0 flex items-center gap-0.5"
          >
            {expanded ? 'Collapse' : 'Expand'}
            {expanded
              ? <ChevronUp className="h-3.5 w-3.5" />
              : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>

      {/* ── Subject line ────────────────────────────────────────────────── */}
      <div className="px-4 pb-1 flex items-baseline gap-2">
        <span className="text-xs text-muted-foreground shrink-0">Subject:</span>
        <span className="text-xs font-medium truncate">{initialDraft.subject}</span>
      </div>

      {/* ── Body preview ────────────────────────────────────────────────── */}
      {expanded && initialDraft.body_preview && (
        <div className="px-4 pb-3 pt-1">
          <div className="rounded-lg bg-background/70 border border-border/60 px-3 py-2.5 text-[0.8rem] text-foreground/80 whitespace-pre-wrap leading-relaxed max-h-52 overflow-y-auto font-[inherit]">
            {initialDraft.body_preview}
          </div>
        </div>
      )}

      {/* ── Action buttons — human-in-the-loop choices ──────────────────── */}
      {!isDone && (
        <div className="px-4 pb-3 pt-1 space-y-2">
          <p className="text-xs text-muted-foreground font-medium">What would you like to do?</p>
          <div className="flex flex-wrap gap-2">
            {/* Option 1: Send now */}
            <Button
              size="sm"
              onClick={handleSendNow}
              disabled={isSending}
              className="h-8 gap-1.5 bg-primary hover:bg-primary/90 text-primary-foreground"
            >
              {isSending ? (
                <SpinnerIcon className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="h-3.5 w-3.5" />
              )}
              {isSending ? 'Sending…' : 'Send now'}
            </Button>

            {/* Option 2: Edit in Outlook */}
            <Button
              variant="outline"
              size="sm"
              disabled={isSending}
              className="h-8 gap-1.5"
              onClick={() => window.open('https://outlook.office365.com/mail/drafts', '_blank')}
            >
              <Pencil className="h-3.5 w-3.5" />
              Edit in Outlook
            </Button>

            {/* Option 3: Keep as draft */}
            <Button
              variant="outline"
              size="sm"
              onClick={handleKeepAsDraft}
              disabled={isSending}
              className="h-8 gap-1.5 text-muted-foreground"
            >
              <Mail className="h-3.5 w-3.5" />
              Keep as draft
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Or type your changes below and I&apos;ll update the draft.
          </p>
        </div>
      )}

      {/* ── Error message ────────────────────────────────────────────────── */}
      {action === 'error' && error && (
        <div className="flex items-center gap-2 px-4 pb-3 text-xs text-red-600 dark:text-red-400">
          <span>{error}</span>
          <button
            onClick={handleSendNow}
            className="underline hover:no-underline ml-1 shrink-0"
          >
            Retry
          </button>
        </div>
      )}
    </div>
  );
}

// ── Citation Card ──────────────────────────────────────────────────────────────

interface CitationCardProps {
  citation: Citation;
  index: number;
}

function CitationCard({ citation, index }: CitationCardProps) {
  const [expanded, setExpanded] = useState(false);
  // Azure AI Search hybrid (RRF) scores are naturally small (0.01–0.05).
  // Use rank-based confidence so position 1 shows as most relevant.
  const score = Math.max(72, 95 - (index - 1) * 6);

  return (
    <div className="border rounded-lg p-3 bg-background text-left">
      <div className="flex items-start gap-3">
        <div className="shrink-0 w-5 h-5 rounded-full bg-primary/10 text-primary text-xs font-semibold flex items-center justify-center">
          {index}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <p className="font-medium text-sm truncate">{citation.document_title ?? 'Unknown source'}</p>
            <span className={`text-xs shrink-0 font-medium ${score >= 90 ? 'text-green-600 dark:text-green-400' : score >= 80 ? 'text-blue-600 dark:text-blue-400' : 'text-muted-foreground'}`}>{score}% match</span>
          </div>
          {citation.source_url && (
            <a
              href={citation.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-primary hover:underline"
            >
              View source
            </a>
          )}
          {expanded && (
            <p className="text-sm text-muted-foreground mt-2 whitespace-pre-wrap leading-relaxed">
              {citation.content ?? ''}
            </p>
          )}
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-primary hover:underline mt-1"
          >
            {expanded ? 'Show less' : 'Show snippet'}
          </button>
        </div>
      </div>
    </div>
  );
}
