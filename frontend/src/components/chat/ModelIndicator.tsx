'use client';

import { useEffect, useRef, useState } from 'react';
import { useChatStore } from '@/lib/store';
import { Sparkles, Eye, Brain, Globe, Zap, FlaskConical } from 'lucide-react';

interface ModelMeta {
  label: string;
  tagline: string;
  icon: React.ReactNode;
  gradient: string;
  glow: string;
  badge: string;
  preview?: boolean; // shows "Limited Preview" pill
}

const MODEL_META: Record<string, ModelMeta> = {
  'gpt-5.2-chat': {
    label: 'GPT-5.2',
    tagline: 'Next-gen frontier intelligence',
    icon: <Sparkles className="h-4 w-4" />,
    gradient: 'from-violet-500 via-purple-500 to-indigo-500',
    glow: 'shadow-[0_0_20px_rgba(139,92,246,0.4)]',
    badge: 'bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300',
  },
  'gpt-4.1': {
    label: 'GPT-4.1',
    tagline: 'Vision + reasoning powerhouse',
    icon: <Eye className="h-4 w-4" />,
    gradient: 'from-blue-500 via-cyan-500 to-sky-500',
    glow: 'shadow-[0_0_20px_rgba(59,130,246,0.4)]',
    badge: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300',
  },
  'kimi-k2.5': {
    label: 'Kimi K2.5',
    tagline: 'Long-context deep reasoning',
    icon: <Brain className="h-4 w-4" />,
    gradient: 'from-orange-500 via-amber-500 to-yellow-500',
    glow: 'shadow-[0_0_20px_rgba(249,115,22,0.4)]',
    badge: 'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300',
  },
  'mistral-large-3': {
    label: 'Mistral Large 3',
    tagline: 'Multilingual & lightning fast',
    icon: <Globe className="h-4 w-4" />,
    gradient: 'from-rose-500 via-pink-500 to-red-500',
    glow: 'shadow-[0_0_20px_rgba(244,63,94,0.4)]',
    badge: 'bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300',
  },
  'grok-3-mini': {
    label: 'Grok-3-mini',
    tagline: 'xAI reasoning, fast & cost-efficient',
    icon: <Zap className="h-4 w-4" />,
    gradient: 'from-slate-600 via-zinc-500 to-gray-500',
    glow: 'shadow-[0_0_20px_rgba(100,116,139,0.4)]',
    badge: 'bg-slate-100 text-slate-700 dark:bg-slate-900 dark:text-slate-300',
  },
  'gemini-2.0-flash': {
    label: 'Gemini 2.0 Flash',
    tagline: 'Google AI — fast & multimodal',
    icon: <Sparkles className="h-4 w-4" />,
    gradient: 'from-blue-500 via-teal-400 to-green-500',
    glow: 'shadow-[0_0_20px_rgba(52,211,153,0.4)]',
    badge: 'bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-300',
  },
  'llama-4-maverick': {
    label: 'Llama 4 Maverick',
    tagline: "Meta's MoE powerhouse",
    icon: <Zap className="h-4 w-4" />,
    gradient: 'from-indigo-500 via-blue-500 to-sky-500',
    glow: 'shadow-[0_0_20px_rgba(99,102,241,0.4)]',
    badge: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300',
  },
  'claude-sonnet-4-6': {
    label: 'Claude Sonnet 4.6',
    tagline: 'Advanced reasoning & writing',
    icon: <FlaskConical className="h-4 w-4" />,
    gradient: 'from-amber-500 via-orange-500 to-yellow-500',
    glow: 'shadow-[0_0_20px_rgba(245,158,11,0.4)]',
    badge: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
    preview: true,
  },
  'claude-haiku-4-5': {
    label: 'Claude Haiku 4.5',
    tagline: 'Fast & efficient responses',
    icon: <FlaskConical className="h-4 w-4" />,
    gradient: 'from-yellow-400 via-amber-400 to-orange-400',
    glow: 'shadow-[0_0_20px_rgba(251,191,36,0.4)]',
    badge: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-950 dark:text-yellow-300',
    preview: true,
  },
};

const MODEL_META_AUTO: ModelMeta = {
  label: 'Auto Mode',
  tagline: 'Best model selected for your task',
  icon: <Sparkles className="h-4 w-4" />,
  gradient: 'from-primary via-blue-500 to-purple-500',
  glow: 'shadow-[0_0_20px_rgba(99,102,241,0.35)]',
  badge: 'bg-primary/10 text-primary',
};

const DEFAULT_META: ModelMeta = {
  label: 'AI Model',
  tagline: 'Ready to assist',
  icon: <Sparkles className="h-4 w-4" />,
  gradient: 'from-slate-500 to-slate-400',
  glow: '',
  badge: 'bg-slate-100 text-slate-700',
};

export function ModelIndicator() {
  const selectedModel = useChatStore((s) => s.selectedModel);
  const [visible, setVisible] = useState(false);
  const [displayModel, setDisplayModel] = useState(selectedModel);
  const prevModelRef = useRef<string | null>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    // Skip the very first render (don't flash on page load)
    if (prevModelRef.current === null) {
      prevModelRef.current = selectedModel;
      return;
    }

    // Only show when model actually changes
    if (prevModelRef.current === selectedModel) return;
    prevModelRef.current = selectedModel;

    setDisplayModel(selectedModel);
    setVisible(true);

    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setVisible(false), 2800);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [selectedModel]);

  const meta =
    displayModel === 'auto'
      ? MODEL_META_AUTO
      : MODEL_META[displayModel] || DEFAULT_META;

  return (
    <div
      className={`pointer-events-none absolute top-3 left-1/2 z-30 flex -translate-x-1/2 transition-all duration-500 ease-out ${
        visible ? 'opacity-100 translate-y-0' : 'opacity-0 -translate-y-4'
      }`}
      aria-live="polite"
      aria-label={`Now using ${meta.label}`}
    >
      {/* Pill */}
      <div
        className={`flex items-center gap-2.5 rounded-full border border-white/20 bg-white/90 dark:bg-gray-900/90 backdrop-blur-md px-4 py-2 ${meta.glow}`}
      >
        {/* Gradient icon dot */}
        <div className={`flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br ${meta.gradient} text-white shrink-0`}>
          {meta.icon}
        </div>

        <div className="leading-tight">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-widest">
              Now using
            </span>
          </div>
          <div className="text-sm font-bold text-foreground">{meta.label}</div>
        </div>

        {/* Tagline badge */}
        <div className={`hidden sm:block text-[11px] font-medium rounded-full px-2.5 py-0.5 ${meta.badge}`}>
          {meta.tagline}
        </div>

        {/* Preview badge — shown only for rate-limited preview models */}
        {meta.preview && (
          <div className="hidden sm:flex items-center gap-1 text-[10px] font-semibold rounded-full px-2 py-0.5 bg-amber-50 text-amber-600 border border-amber-200 dark:bg-amber-950 dark:text-amber-400 dark:border-amber-800">
            <FlaskConical className="h-3 w-3" />
            Limited Preview
          </div>
        )}
      </div>
    </div>
  );
}
