import { useCallback, useEffect, useRef, useState } from 'react';
import { apiGet, apiPost, ApiError } from '@/lib/api';
import type { ChatMessage, ChatThread, SendMessageResult } from '@/lib/types';

export interface UseChatResult {
  thread: ChatThread | null;
  messages: ChatMessage[];
  sending: boolean;
  loading: boolean;
  error: string | null;
  sendMessage: (text: string, opts?: { deep?: boolean }) => Promise<void>;
  refresh: () => Promise<void>;
}

/**
 * Manages a single chat thread bound to a chart subject. If the patient has no
 * thread yet a fresh one is created on first use.
 */
export function useChat(chartSubjectId: string | null): UseChatResult {
  const [thread, setThread] = useState<ChatThread | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const initialised = useRef<string | null>(null);

  const ensureThread = useCallback(async (): Promise<ChatThread | null> => {
    if (!chartSubjectId) return null;
    const list = await apiGet<ChatThread[]>(`/api/patients/${chartSubjectId}/threads`);
    if (list.length > 0) {
      return list[0];
    }
    return apiPost<ChatThread, { title: string | null }>(
      `/api/patients/${chartSubjectId}/threads`,
      { title: 'Primary chart thread' },
    );
  }, [chartSubjectId]);

  const loadMessages = useCallback(async (zepThreadId: string) => {
    const items = await apiGet<ChatMessage[]>(`/api/threads/${zepThreadId}/messages`);
    setMessages(items);
  }, []);

  const refresh = useCallback(async () => {
    if (!chartSubjectId) {
      setThread(null);
      setMessages([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const t = await ensureThread();
      setThread(t);
      if (t) {
        await loadMessages(t.zep_thread_id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : (err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [chartSubjectId, ensureThread, loadMessages]);

  useEffect(() => {
    if (!chartSubjectId || initialised.current === chartSubjectId) return;
    initialised.current = chartSubjectId;
    void refresh();
  }, [chartSubjectId, refresh]);

  const sendMessage = useCallback(
    async (text: string, opts?: { deep?: boolean }) => {
      if (!thread || !text.trim()) return;
      setSending(true);
      setError(null);
      try {
        const out = await apiPost<SendMessageResult, { user_input: string; deep: boolean }>(
          `/api/threads/${thread.zep_thread_id}/messages`,
          { user_input: text, deep: !!opts?.deep },
        );
        setMessages((prev) => [...prev, out.user, out.assistant]);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : (err as Error).message);
      } finally {
        setSending(false);
      }
    },
    [thread],
  );

  return { thread, messages, sending, loading, error, sendMessage, refresh };
}
