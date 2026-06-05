'use client';

/**
 * VoiceChatOverlay — full-screen voice chat UI.
 *
 * State machine:
 *   idle → listening → processing → (speaking) → listening (loop)
 *
 * The overlay manages its own SpeechRecognition instance so it doesn't
 * interfere with ChatInput's mic. sendMessage is called via the store.
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import { X, Mic, MicOff, Volume2, VolumeX, ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useChatStore } from '@/lib/store';

type VoiceState = 'idle' | 'listening' | 'processing' | 'speaking';

const VOICE_OPTIONS = [
  { id: 'en-US-AriaNeural', label: 'Aria (US)' },
  { id: 'en-US-JennyNeural', label: 'Jenny (US)' },
  { id: 'en-GB-SoniaNeural', label: 'Sonia (UK)' },
  { id: 'en-US-GuyNeural', label: 'Guy (US)' },
  { id: 'en-US-DavisNeural', label: 'Davis (US)' },
  { id: 'en-GB-RyanNeural', label: 'Ryan (UK)' },
];

export function VoiceChatOverlay() {
  const {
    isStreaming,
    isPlayingAudio,
    sendMessage,
    stopAudio,
    stopStreaming,
    setVoiceModeEnabled,
    selectedVoice,
    setSelectedVoice,
  } = useChatStore();

  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [interimText, setInterimText] = useState('');
  const [finalText, setFinalText] = useState('');
  const [voiceMenuOpen, setVoiceMenuOpen] = useState(false);
  const [speechSupported, setSpeechSupported] = useState(true);

  const recognitionRef = useRef<any>(null);
  const shouldAutoListenRef = useRef(true);
  const isMountedRef = useRef(true);
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Speech Recognition setup ────────────────────────────────────────────

  const clearSilenceTimer = useCallback(() => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
  }, []);

  const stopRecognition = useCallback(() => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    if (recognitionRef.current) {
      try { recognitionRef.current.stop(); } catch { /* ignore */ }
      recognitionRef.current = null;
    }
  }, []);

  const startListening = useCallback(() => {
    if (!isMountedRef.current) return;

    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      setSpeechSupported(false);
      return;
    }

    stopRecognition();
    clearSilenceTimer();

    const rec = new SR() as any;
    rec.lang = 'en-US';
    rec.continuous = true;      // keep mic open — no artificial 1-2s wait
    rec.interimResults = true;
    recognitionRef.current = rec;

    let accumulated = '';  // all final results concatenated
    let submitted = false;

    const submit = () => {
      if (submitted || !isMountedRef.current) return;
      submitted = true;
      clearSilenceTimer();
      const text = accumulated.trim();
      try { rec.stop(); } catch { /* ignore */ }
      if (text) {
        setVoiceState('processing');
        setInterimText('');
        sendMessage(text).catch(() => {});
      } else {
        setVoiceState('idle');
        setInterimText('');
      }
    };

    rec.onstart = () => {
      if (isMountedRef.current) setVoiceState('listening');
    };

    rec.onresult = (e: any) => {
      if (!isMountedRef.current || submitted) return;
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) {
          accumulated += (accumulated ? ' ' : '') + t.trim();
        } else {
          interim += t;
        }
      }
      setInterimText(interim || accumulated);
      if (accumulated) setFinalText(accumulated);

      // Reset 500 ms silence timer on every new speech segment
      clearSilenceTimer();
      silenceTimerRef.current = setTimeout(submit, 500);
    };

    rec.onend = () => {
      recognitionRef.current = null;
      if (!isMountedRef.current) return;
      // onend fires after rec.stop() — submit if not already done
      if (!submitted) {
        submitted = true;
        clearSilenceTimer();
        const text = accumulated.trim();
        if (text) {
          setVoiceState('processing');
          setInterimText('');
          sendMessage(text).catch(() => {});
        } else {
          setVoiceState('idle');
          setInterimText('');
        }
      }
    };

    rec.onerror = (e: any) => {
      recognitionRef.current = null;
      clearSilenceTimer();
      if (!isMountedRef.current) return;
      setVoiceState('idle');
      setInterimText('');
    };

    try {
      rec.start();
    } catch {
      setVoiceState('idle');
    }
  }, [sendMessage, stopRecognition, clearSilenceTimer]);

  // ── Auto-listen cycle: after AI finishes speaking/processing, re-listen ──

  useEffect(() => {
    if (!shouldAutoListenRef.current) return;

    if (!isStreaming && !isPlayingAudio && voiceState === 'processing') {
      // AI done responding — back to listening
      setVoiceState('idle');
      setFinalText('');
    }

    if (!isStreaming && !isPlayingAudio && voiceState === 'speaking') {
      // Audio finished — back to listening
      setVoiceState('idle');
      setFinalText('');
    }
  }, [isStreaming, isPlayingAudio, voiceState]);

  // Track when speaking starts
  useEffect(() => {
    if (isPlayingAudio && voiceState === 'processing') {
      setVoiceState('speaking');
    }
  }, [isPlayingAudio, voiceState]);

  // Auto-start listening when idle
  useEffect(() => {
    if (voiceState === 'idle' && shouldAutoListenRef.current && speechSupported) {
      const timer = setTimeout(() => {
        if (isMountedRef.current && voiceState === 'idle') {
          startListening();
        }
      }, 400);
      return () => clearTimeout(timer);
    }
  }, [voiceState, speechSupported, startListening]);

  // ── Cleanup on unmount ─────────────────────────────────────────────────

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      shouldAutoListenRef.current = false;
      stopRecognition();
    };
  }, [stopRecognition]);

  // ── Handlers ──────────────────────────────────────────────────────────

  const handleOrbClick = () => {
    if (voiceState === 'speaking') {
      // Interrupt AI
      stopAudio();
      setVoiceState('idle');
    } else if (voiceState === 'listening') {
      stopRecognition();
      shouldAutoListenRef.current = false;
      setVoiceState('idle');
      setInterimText('');
    } else if (voiceState === 'idle') {
      shouldAutoListenRef.current = true;
      startListening();
    }
  };

  const handleMicButton = () => {
    if (voiceState === 'listening') {
      stopRecognition();
      shouldAutoListenRef.current = false;
      setVoiceState('idle');
      setInterimText('');
    } else {
      shouldAutoListenRef.current = true;
      startListening();
    }
  };

  const handleClose = () => {
    shouldAutoListenRef.current = false;
    stopRecognition();
    stopAudio();
    setVoiceModeEnabled(false);
  };

  const handleStopAI = () => {
    if (isStreaming) stopStreaming();
    if (isPlayingAudio) stopAudio();
    setVoiceState('idle');
  };

  // ── Orb appearance ────────────────────────────────────────────────────

  const orbConfig = {
    idle: {
      gradient: 'from-slate-400 to-slate-600',
      ring: 'ring-slate-300 dark:ring-slate-600',
      pulse: false,
      scale: 'scale-100',
    },
    listening: {
      gradient: 'from-blue-500 via-indigo-500 to-purple-600',
      ring: 'ring-blue-400/50 dark:ring-blue-500/50',
      pulse: true,
      scale: 'scale-110',
    },
    processing: {
      gradient: 'from-amber-400 via-orange-500 to-rose-500',
      ring: 'ring-amber-300/50',
      pulse: true,
      scale: 'scale-105',
    },
    speaking: {
      gradient: 'from-emerald-400 via-teal-500 to-cyan-600',
      ring: 'ring-emerald-400/50 dark:ring-emerald-500/50',
      pulse: true,
      scale: 'scale-110',
    },
  };

  const orb = orbConfig[voiceState];

  const statusLabel = {
    idle: 'Tap to speak',
    listening: 'Listening…',
    processing: 'Processing…',
    speaking: 'Speaking…',
  }[voiceState];

  const displayText = interimText || finalText;

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-background/95 backdrop-blur-md">
      {/* Top bar */}
      <div className="absolute top-0 left-0 right-0 flex items-center justify-between px-6 py-4">
        <button
          onClick={handleClose}
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <X className="h-5 w-5" />
          <span>Exit voice chat</span>
        </button>

        {/* Voice selector */}
        <div className="relative">
          <button
            onClick={() => setVoiceMenuOpen((o) => !o)}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-3 py-1.5 rounded-lg border border-border/50 hover:border-border"
          >
            <Volume2 className="h-3.5 w-3.5" />
            <span>{VOICE_OPTIONS.find((v) => v.id === selectedVoice)?.label ?? selectedVoice}</span>
            <ChevronDown className="h-3 w-3" />
          </button>

          {voiceMenuOpen && (
            <div className="absolute right-0 top-full mt-1 w-44 rounded-xl border border-border bg-background shadow-lg z-10 overflow-hidden">
              {VOICE_OPTIONS.map((v) => (
                <button
                  key={v.id}
                  onClick={() => {
                    setSelectedVoice(v.id);
                    setVoiceMenuOpen(false);
                  }}
                  className={cn(
                    'w-full text-left px-4 py-2.5 text-sm transition-colors hover:bg-muted',
                    selectedVoice === v.id && 'font-semibold text-primary',
                  )}
                >
                  {v.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Main content */}
      <div className="flex flex-col items-center gap-8 px-6 text-center">
        {/* Animated orb */}
        <button
          onClick={handleOrbClick}
          aria-label={statusLabel}
          className={cn(
            'relative h-40 w-40 rounded-full transition-all duration-500 cursor-pointer',
            'focus:outline-none focus-visible:ring-4 focus-visible:ring-primary/40',
            orb.scale,
          )}
        >
          {/* Outer glow ring */}
          <span
            className={cn(
              'absolute inset-0 rounded-full ring-8 transition-all duration-500',
              orb.ring,
            )}
          />

          {/* Pulse rings (listening / speaking) */}
          {orb.pulse && (
            <>
              <span
                className={cn(
                  'absolute inset-0 rounded-full opacity-30 animate-ping',
                  `bg-gradient-to-br ${orb.gradient}`,
                )}
              />
              <span
                className={cn(
                  'absolute inset-[-12px] rounded-full opacity-15 animate-ping',
                  `bg-gradient-to-br ${orb.gradient}`,
                )}
                style={{ animationDelay: '150ms' }}
              />
            </>
          )}

          {/* Core gradient circle */}
          <span
            className={cn(
              'absolute inset-0 rounded-full shadow-2xl transition-all duration-500',
              `bg-gradient-to-br ${orb.gradient}`,
            )}
          />

          {/* Icon overlay */}
          <span className="absolute inset-0 flex items-center justify-center">
            {voiceState === 'listening' ? (
              <Mic className="h-10 w-10 text-white drop-shadow" />
            ) : voiceState === 'speaking' ? (
              <VolumeX className="h-10 w-10 text-white drop-shadow" />
            ) : voiceState === 'processing' ? (
              <svg
                className="h-10 w-10 text-white animate-spin"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
              </svg>
            ) : (
              <Mic className="h-10 w-10 text-white/70 drop-shadow" />
            )}
          </span>
        </button>

        {/* Status label */}
        <div className="flex flex-col items-center gap-2 min-h-[3rem]">
          <p className="text-lg font-medium tracking-wide transition-all duration-300">
            {statusLabel}
          </p>
          {displayText && (
            <p className="text-sm text-muted-foreground max-w-xs italic leading-relaxed">
              &ldquo;{displayText}&rdquo;
            </p>
          )}
          {!speechSupported && (
            <p className="text-sm text-destructive">
              Voice input not supported in this browser.
            </p>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-4">
          {/* Mic toggle */}
          <button
            onClick={handleMicButton}
            disabled={voiceState === 'processing' || voiceState === 'speaking'}
            className={cn(
              'flex items-center gap-2 px-5 py-2.5 rounded-full text-sm font-medium transition-all',
              voiceState === 'listening'
                ? 'bg-red-500 hover:bg-red-600 text-white shadow-lg shadow-red-500/30'
                : 'bg-primary hover:bg-primary/90 text-primary-foreground shadow-lg shadow-primary/30',
              'disabled:opacity-40 disabled:cursor-not-allowed',
            )}
          >
            {voiceState === 'listening' ? (
              <>
                <MicOff className="h-4 w-4" />
                Stop listening
              </>
            ) : (
              <>
                <Mic className="h-4 w-4" />
                Tap to speak
              </>
            )}
          </button>

          {/* Stop AI button — shown when AI is active */}
          {(isStreaming || isPlayingAudio) && (
            <button
              onClick={handleStopAI}
              className="flex items-center gap-2 px-5 py-2.5 rounded-full text-sm font-medium bg-muted hover:bg-muted/80 text-muted-foreground transition-all"
            >
              <VolumeX className="h-4 w-4" />
              Stop AI
            </button>
          )}
        </div>

        {/* Hint */}
        <p className="text-xs text-muted-foreground/60 mt-2">
          {voiceState === 'speaking' ? 'Tap the orb to interrupt' : 'Tap the orb or button to speak'}
        </p>
      </div>
    </div>
  );
}
