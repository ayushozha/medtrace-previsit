import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Brain, Loader2, Mic, Paperclip, Send, Sparkles, X } from 'lucide-react';
import { useChat } from '@/hooks/useChat';
import { useUploadDocument } from '@/hooks/useUploadDocument';
import { cn } from '@/lib/utils';
import type { ChatMessage } from '@/lib/types';

interface AIChatPanelProps {
  patientId: string;
  patientName: string;
  primaryDoctor: string;
  onUploaded?: () => void;
}

export function AIChatPanel({ patientId, patientName, primaryDoctor, onUploaded }: AIChatPanelProps) {
  const { messages, sending, loading, error, sendMessage } = useChat(patientId);
  const { uploading, error: uploadError, upload } = useUploadDocument();
  const [query, setQuery] = useState('');
  const [deep, setDeep] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length, sending]);

  // Client-side reveal: only the assistant reply that lands right after a send
  // animates. Everything else (thread history, user turns, earlier replies) is
  // added to `revealedIds` as soon as it is seen, so it renders instantly.
  const [revealedIds, setRevealedIds] = useState<ReadonlySet<string>>(() => new Set(['greeting']));
  const seenIds = useRef<Set<string>>(new Set());
  const sendingRef = useRef(false);

  useEffect(() => {
    const fresh = messages.filter((m) => !seenIds.current.has(m.id));
    const arrivedFromSend = sendingRef.current;
    setRevealedIds((prev) => {
      const next = new Set(prev);
      fresh.forEach((m, i) => {
        const isFreshReply = arrivedFromSend && m.role === 'assistant' && i === fresh.length - 1;
        if (!isFreshReply) next.add(m.id);
      });
      return next;
    });
    fresh.forEach((m) => seenIds.current.add(m.id));
    sendingRef.current = sending;
  }, [messages, sending]);

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, []);

  const markRevealed = useCallback((id: string) => {
    setRevealedIds((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }, []);

  const greeting: ChatMessage = {
    id: 'greeting',
    role: 'assistant',
    content: `Hello ${primaryDoctor}. I have ${patientName}'s memory loaded from Zep. What would you like to know?`,
    created_at: null,
    name: 'Assistant',
  };

  const visibleMessages = messages.length > 0 ? messages : [greeting];
  const lastMessage = visibleMessages[visibleMessages.length - 1];
  const animatingId =
    lastMessage.role === 'assistant' && !revealedIds.has(lastMessage.id) ? lastMessage.id : null;

  const handleSend = async () => {
    const text = query.trim();
    if (!text && pendingFiles.length === 0) return;

    if (pendingFiles.length > 0) {
      for (const f of pendingFiles) {
        try {
          await upload(patientId, f);
          onUploaded?.();
        } catch {
          /* surfaced via uploadError */
        }
      }
      setPendingFiles([]);
    }

    if (text) {
      setQuery('');
      await sendMessage(text, { deep });
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;
    setPendingFiles((prev) => [...prev, ...files]);
    e.target.value = '';
  };

  const removePending = (idx: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const banner = error ?? uploadError;

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-white">
      <div ref={scrollRef} className="relative flex-1 overflow-y-auto bg-slate-50 p-3 scroll-smooth">
        <div className="space-y-4 pb-6">
          {loading && messages.length === 0 ? (
            <div className="flex items-center justify-center gap-2 py-4 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
              <Loader2 size={12} className="animate-spin" /> Loading thread...
            </div>
          ) : null}

          {visibleMessages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[92%] rounded-xl px-3 py-2.5 text-[13px] shadow-sm ${
                  msg.role === 'user' ? 'bg-primary text-white' : 'border border-slate-200 bg-white text-slate-800'
                }`}
              >
                {msg.role === 'user' ? (
                  <div className="whitespace-pre-wrap leading-relaxed">{msg.content}</div>
                ) : msg.id === animatingId ? (
                  <StreamingText
                    content={msg.content}
                    onProgress={scrollToBottom}
                    onDone={() => markRevealed(msg.id)}
                  />
                ) : (
                  <div
                    className="
                      leading-relaxed
                      [&>*]:my-1 first:[&>*]:mt-0 last:[&>*]:mb-0
                      [&_p]:my-1
                      [&_strong]:font-semibold [&_strong]:text-slate-900
                      [&_em]:italic
                      [&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-5
                      [&_ol]:my-1 [&_ol]:list-decimal [&_ol]:pl-5
                      [&_li]:my-0.5
                      [&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2
                      [&_code]:rounded [&_code]:bg-slate-100 [&_code]:px-1 [&_code]:py-0.5
                        [&_code]:font-mono [&_code]:text-[11px] [&_code]:text-slate-700
                      [&_pre]:my-1.5 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-slate-900
                        [&_pre]:p-2 [&_pre]:text-[11px] [&_pre]:leading-snug
                      [&_pre>code]:bg-transparent [&_pre>code]:p-0 [&_pre>code]:text-slate-100
                      [&_blockquote]:border-l-2 [&_blockquote]:border-slate-300 [&_blockquote]:pl-2
                        [&_blockquote]:text-slate-600
                      [&_hr]:my-2 [&_hr]:border-slate-200
                      [&_h1]:text-[14px] [&_h1]:font-semibold
                      [&_h2]:text-[13px] [&_h2]:font-semibold
                      [&_h3]:text-[13px] [&_h3]:font-semibold
                      [&_table]:my-2 [&_table]:w-full [&_table]:text-[12px]
                      [&_th]:border [&_th]:border-slate-200 [&_th]:bg-slate-50 [&_th]:px-1.5 [&_th]:py-1 [&_th]:text-left
                      [&_td]:border [&_td]:border-slate-200 [&_td]:px-1.5 [&_td]:py-1
                    "
                  >
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  </div>
                )}
              </div>
            </div>
          ))}

          {sending && (
            <div className="flex justify-start">
              <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-sm">
                <div className="flex gap-1">
                  <div className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-300" />
                  <div className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-300" style={{ animationDelay: '0.2s' }} />
                  <div className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-300" style={{ animationDelay: '0.4s' }} />
                </div>
                <span className="ml-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                  Synthesizing...
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      {banner && (
        <div className="border-t border-red-100 bg-red-50 px-3 py-2 text-[11px] text-red-700">{banner}</div>
      )}

      <div className="z-10 mt-auto border-t border-border bg-white/85 p-3 shadow-[0_-10px_20px_-10px_rgba(15,23,42,0.12)] backdrop-blur">
        {pendingFiles.length > 0 && (
          <div className="flex flex-wrap gap-2 px-1 pb-2">
            {pendingFiles.map((file, i) => (
              <div
                key={`${file.name}-${i}`}
                className="flex items-center gap-1.5 rounded border border-blue-200 bg-blue-50 py-1 pl-2 pr-1 text-[10px] font-semibold text-primary"
              >
                <Paperclip size={10} />
                <span className="truncate max-w-[100px]">{file.name}</span>
                <button
                  type="button"
                  onClick={() => removePending(i)}
                  className="p-0.5 hover:bg-blue-200 rounded text-blue-800 transition-colors"
                >
                  <X size={10} />
                </button>
              </div>
            ))}
          </div>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void handleSend();
          }}
          className="flex items-end gap-2 rounded-3xl border border-slate-200 bg-white/95 p-1 shadow-sm transition-all focus-within:border-primary focus-within:ring-2 focus-within:ring-blue-500/20"
        >
          <div className="flex flex-col w-full">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ask Copilot anything..."
              className="min-h-[44px] resize-none border-none bg-transparent px-4 py-2 text-[13px] shadow-none focus-visible:ring-0"
              disabled={sending}
            />
            <div className="flex items-center justify-between px-2 pb-1 text-slate-400">
              <div className="flex items-center gap-1">
                <input type="file" ref={fileInputRef} className="hidden" onChange={handleFileChange} accept=".pdf,.txt" />
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                  className="rounded-md p-1.5 transition-colors hover:bg-slate-100 hover:text-slate-700 disabled:opacity-60"
                  title="Attach file"
                >
                  {uploading ? <Loader2 size={14} className="animate-spin" /> : <Paperclip size={14} />}
                </button>
                <Link
                  to="/session"
                  className="rounded-md p-1.5 transition-colors hover:bg-slate-100 hover:text-slate-700"
                  title="Record a voice consultation"
                >
                  <Mic size={14} />
                </Link>
                <button
                  type="button"
                  onClick={() => setDeep((v) => !v)}
                  aria-pressed={deep}
                  className={cn(
                    'ml-1 inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] transition-colors',
                    deep
                      ? 'bg-primary/10 text-primary'
                      : 'text-slate-400 hover:bg-slate-100 hover:text-slate-700',
                  )}
                  title={
                    deep
                      ? 'Deep clinical agent: uses Zep graph + PubMed tools. Slower, better grounded.'
                      : 'Fast path: one call with memory context. Click to enable the deep clinical agent.'
                  }
                >
                  <Brain size={12} />
                  Deep
                </button>
              </div>
              <Button
                type="submit"
                size="icon"
                disabled={(!query.trim() && pendingFiles.length === 0) || sending}
                className="h-8 w-8 shrink-0 rounded-full bg-primary hover:bg-blue-700 disabled:bg-slate-300 disabled:text-slate-500"
              >
                {sending ? <Loader2 size={14} className="animate-spin text-white" /> : <Send size={14} className="text-white ml-0.5" />}
              </Button>
            </div>
          </div>
        </form>
        <div className="text-center mt-2">
          <p className="flex items-center justify-center gap-1 text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-400">
            <Sparkles size={8} /> AI generated content may be inaccurate
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * Progressive plain-text reveal for the newest assistant reply. The backend
 * returns the whole message at once, so the "streaming" is client-side: text is
 * appended in chunks and swapped for the rendered markdown once complete.
 * Revealing raw text avoids re-parsing partial markdown (broken **, unclosed
 * code fences) on every tick.
 */
function StreamingText({
  content,
  onProgress,
  onDone,
}: {
  content: string;
  onProgress: () => void;
  onDone: () => void;
}) {
  const [shown, setShown] = useState(0);

  useEffect(() => {
    setShown(0);
    // ~3s for long replies, proportionally faster for short ones.
    const chunk = Math.max(2, Math.ceil(content.length / 180));
    const timer = setInterval(() => {
      setShown((prev) => Math.min(prev + chunk, content.length));
    }, 16);
    return () => clearInterval(timer);
  }, [content]);

  useEffect(() => {
    if (shown > 0) onProgress();
  }, [shown, onProgress]);

  useEffect(() => {
    if (shown >= content.length && content.length > 0) onDone();
  }, [shown, content.length, onDone]);

  return (
    <div className="whitespace-pre-wrap leading-relaxed">
      {content.slice(0, shown)}
      {shown < content.length && <span className="animate-pulse text-primary">▍</span>}
    </div>
  );
}
