import { useCallback, useEffect, useRef, useState } from 'react';
import { CopilotKit } from '@copilotkit/react-core';
import {
  CopilotChatConfigurationProvider,
  useAgent,
  UseAgentUpdate,
  useCopilotKit,
  useHumanInTheLoop,
  useRenderToolCall,
} from '@copilotkit/react-core/v2';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import {
  FileText,
  Loader2,
  Mic,
  Pause,
  Play,
  Send,
  Sparkles,
  Square,
  TriangleAlert,
  Upload,
  User,
} from 'lucide-react';

import '@copilotkit/react-core/v2/styles.css';
import './session.css';

import { AudioVisualizer, LiveAudioVisualizer, base64ToBlob } from './audioVisualizers';
import { ConfirmChanges } from './ConfirmChanges';
import { fromMarkdown, toMarkdown } from './markdown';
import { createSession, generateReport, listSessions, type SessionRecord } from './sessionApi';

const AGENT_ID = 'predictive_state_updates';
const extensions = [StarterKit];

const DRAFT_SESSION: SessionRecord = {
  id: 'local-draft-session',
  timestamp: '',
  duration: '0:00',
  transcript: '',
  report: '',
  audio_base64: '',
};

interface AgentState {
  document: string;
}

function formatTime(secs: number): string {
  const minutes = Math.floor(secs / 60);
  const seconds = secs % 60;
  return `${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;
}

interface TranscriptLine {
  speaker: 'Clinician' | 'Patient';
  text: string;
}

/**
 * Split a diarized transcript into speaker turns.
 *
 * Falls back to alternating speakers when a line carries no prefix. No timestamps: the
 * backend returns no per-line offsets, and the previous version invented them from the
 * line index.
 */
function parseTranscript(rawText: string): TranscriptLine[] {
  return rawText
    .split('\n')
    .filter((line) => line.trim().length > 0)
    .map((line, idx) => {
      const lower = line.toLowerCase();
      const isClinician = lower.startsWith('clinician:') || lower.startsWith('doctor:');
      const isPatient = lower.startsWith('patient:');
      return {
        speaker: isClinician
          ? 'Clinician'
          : isPatient
            ? 'Patient'
            : idx % 2 === 0
              ? 'Clinician'
              : 'Patient',
        text: line.replace(/^(clinician:|doctor:|patient:)\s*/i, ''),
      } as TranscriptLine;
    });
}

function DocumentEditor() {
  // `currentDocument` is the accepted document as **markdown** — the editor is only a view of
  // it. Deriving it from the editor instead would strip the markers the agent needs.
  const [currentDocument, setCurrentDocument] = useState('');

  const editor = useEditor({
    extensions,
    immediatelyRender: false,
    editorProps: { attributes: { class: 'tiptap' } },
    onUpdate: ({ editor: instance }) => {
      setCurrentDocument(toMarkdown(instance.getHTML()));
    },
  });

  /**
   * Render markdown into the canvas without touching `currentDocument`.
   *
   * `setContent` marks its transaction `preventUpdate`, so this never fires `onUpdate` —
   * which is what lets the agent's unconfirmed proposal be displayed without being
   * recorded as accepted.
   */
  const applyMarkdown = useCallback(
    (markdown: string) => {
      editor?.commands.setContent(fromMarkdown(markdown));
    },
    [editor],
  );

  const [sessions, setSessions] = useState<SessionRecord[]>([DRAFT_SESSION]);
  const [activeSessionId, setActiveSessionId] = useState<string>(DRAFT_SESSION.id);
  const [playingSessionId, setPlayingSessionId] = useState<string | null>(null);
  const [commandText, setCommandText] = useState('');
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const [activeAudioBlob, setActiveAudioBlob] = useState<Blob | null>(null);
  const [playbackTime, setPlaybackTime] = useState(0);

  const [isRecording, setIsRecording] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [errorMsg, setErrorMsg] = useState('');
  const [aiBubbleDismissed, setAiBubbleDismissed] = useState(false);
  const [isGeneratingReport, setIsGeneratingReport] = useState(false);
  const [reportActionMsg, setReportActionMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(
    null,
  );

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef<number>(0);

  const renderToolCall = useRenderToolCall();
  const { copilotkit, executingToolCallIds } = useCopilotKit();
  const { agent } = useAgent({
    agentId: AGENT_ID,
    updates: [
      UseAgentUpdate.OnMessagesChanged,
      UseAgentUpdate.OnStateChanged,
      UseAgentUpdate.OnRunStatusChanged,
    ],
  });

  const agentState = agent.state as AgentState | undefined;
  const setAgentState = useCallback((s: AgentState) => agent.setState(s), [agent]);
  const isLoading = agent.isRunning;

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const loadSession = useCallback(
    (session: SessionRecord) => {
      setActiveSessionId(session.id);
      applyMarkdown(session.report);
      setCurrentDocument(session.report);
      setAgentState({ document: session.report });
    },
    [applyMarkdown, setAgentState],
  );

  useEffect(() => {
    const controller = new AbortController();
    listSessions(controller.signal)
      .then((data) => {
        setSessions([DRAFT_SESSION, ...data]);
        loadSession(DRAFT_SESSION);
      })
      .catch(() => {
        setSessions([DRAFT_SESSION]);
        loadSession(DRAFT_SESSION);
      });
    return () => controller.abort();
    // Mount-only: loadSession is recreated whenever the editor instance changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSessionCreated = (newSession: SessionRecord) => {
    setSessions((prev) => [newSession, ...prev.filter((s) => s.id !== DRAFT_SESSION.id)]);
    loadSession(newSession);
  };

  const uploadRecording = async (blob: Blob, duration: string) => {
    setIsUploading(true);
    setErrorMsg('');
    try {
      const base64Audio = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result as string);
        reader.onerror = () => reject(new Error('Could not read the audio file.'));
        reader.readAsDataURL(blob);
      });
      handleSessionCreated(await createSession(base64Audio, duration));
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to transcribe audio.');
    } finally {
      setIsUploading(false);
    }
  };

  const startRecording = async () => {
    setErrorMsg('');
    audioChunksRef.current = [];
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data);
      };
      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
        const durationSecs = Math.round((Date.now() - startTimeRef.current) / 1000);
        stream.getTracks().forEach((track) => track.stop());
        await uploadRecording(audioBlob, formatTime(durationSecs));
      };

      setIsRecording(true);
      setRecordingTime(0);
      startTimeRef.current = Date.now();
      timerRef.current = setInterval(() => setRecordingTime((prev) => prev + 1), 1000);
      mediaRecorder.start();
    } catch (err) {
      console.error('Failed to start recording:', err);
      setErrorMsg('Microphone access denied or unsupported.');
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      if (timerRef.current) clearInterval(timerRef.current);
      setIsRecording(false);
    }
  };

  const handleAudioUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    event.target.value = '';

    let duration = '0:00';
    try {
      const Ctor =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const audioCtx = new Ctor();
      const audioBuffer = await audioCtx.decodeAudioData(await file.arrayBuffer());
      duration = formatTime(Math.round(audioBuffer.duration));
      await audioCtx.close();
    } catch {
      // Undecodable container — the backend still transcribes it; duration stays unknown.
    }
    await uploadRecording(file, duration);
  };

  const activeSession = sessions.find((s) => s.id === activeSessionId) ?? DRAFT_SESSION;
  const sessionHasAudio = Boolean(activeSession.audio_base64?.trim());

  const handleGenerateReport = async () => {
    if (!editor || isGeneratingReport || isLoading) return;
    const currentText = toMarkdown(editor.getHTML()) || currentDocument;
    if (!currentText.trim()) return;

    setIsGeneratingReport(true);
    setReportActionMsg(null);
    try {
      const data = await generateReport({
        session_id: activeSession.id,
        transcript: activeSession.transcript || '',
        current_report_text: currentText,
        regenerate: true,
      });
      const reportText = typeof data.report === 'string' ? data.report : currentText;
      applyMarkdown(reportText);
      setCurrentDocument(reportText);
      setAgentState({ document: reportText });
      setSessions((prev) =>
        prev.map((s) => (s.id === activeSession.id ? { ...s, report: reportText } : s)),
      );
      setReportActionMsg({
        kind: 'ok',
        text: `${data.regenerated ? 'Report generated from transcript.' : 'Report exported.'}${
          data.database_updated ? ' Saved to the session database.' : ''
        }${data.filename ? ` File: ${data.filename}.` : ''}`,
      });
    } catch (e) {
      setReportActionMsg({
        kind: 'err',
        text: e instanceof Error ? e.message : 'Request failed',
      });
    } finally {
      setIsGeneratingReport(false);
    }
  };

  const playAudio = (session: SessionRecord) => {
    if (!session.audio_base64?.trim()) return;

    if (playingSessionId === session.id) {
      audioRef.current?.pause();
      setPlayingSessionId(null);
      return;
    }
    audioRef.current?.pause();
    const audio = new Audio(session.audio_base64);
    audioRef.current = audio;
    setPlayingSessionId(session.id);
    void audio.play();
    audio.ontimeupdate = () => setPlaybackTime(audio.currentTime);
    audio.onended = () => {
      setPlayingSessionId(null);
      setPlaybackTime(0);
    };
  };

  const handleSendCommand = async () => {
    if (!commandText.trim()) return;
    const textToSend = commandText;
    setCommandText('');
    try {
      // Hand the agent the markdown the clinician can currently see, including unsent edits.
      const markdown = editor ? toMarkdown(editor.getHTML()) : currentDocument;
      setCurrentDocument(markdown);
      setAgentState({ document: markdown });
      agent.addMessage({
        id: Math.random().toString(36).substring(7),
        role: 'user',
        content: textToSend,
      });
      await agent.runAgent();
    } catch (err) {
      console.error('Error sending command:', err);
    }
  };

  const assistantMessages = agent.messages.filter((m) => m.role === 'assistant' && m.content);
  const latestAssistantMessage = assistantMessages[assistantMessages.length - 1];
  const hasBubble = isLoading || !!latestAssistantMessage;

  type ToolCall = Parameters<typeof renderToolCall>[0]['toolCall'];
  const pending = (tc: ToolCall) =>
    !agent.messages.some(
      (tm) => tm.role === 'tool' && (tm as { toolCallId?: string }).toolCallId === tc.id,
    );

  const activeToolCallMessage = agent.messages.find(
    (m) => m.role === 'assistant' && ((m as { toolCalls?: ToolCall[] }).toolCalls ?? []).some(pending),
  );
  const activeToolCall = (
    (activeToolCallMessage as { toolCalls?: ToolCall[] } | undefined)?.toolCalls ?? []
  ).find(pending);

  const renderedTool = activeToolCall ? renderToolCall({ toolCall: activeToolCall }) : null;
  const hasHITL = !!renderedTool;

  useEffect(() => {
    if (isLoading) setAiBubbleDismissed(false);
  }, [isLoading]);

  useEffect(() => {
    // emitUpdate=false: the synthetic update event would otherwise run the editor→markdown
    // sync over the agent's proposal and record it as the accepted document.
    editor?.setEditable(!isLoading, false);
  }, [isLoading, editor]);

  // The agent streams markdown into `state.document`; the canvas mirrors it as rendered
  // markdown. The word-level diff against the accepted document lives in the HITL modal,
  // because diff markers inside markdown text destroy its block structure.
  useEffect(() => {
    if (!isLoading) return;
    applyMarkdown(agentState?.document ?? '');
  }, [agentState?.document, isLoading, applyMarkdown]);

  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !isLoading) {
      const proposed = agentState?.document ?? '';
      if (proposed.trim().length > 0 && proposed !== currentDocument) applyMarkdown(proposed);
    }
    wasRunning.current = isLoading;
  }, [isLoading, agentState?.document, currentDocument, applyMarkdown]);

  const canvasHasContent = !!editor && !editor.isEmpty;

  const currentDocumentRef = useRef(currentDocument);
  const agentStateRef = useRef(agentState);
  const applyMarkdownRef = useRef(applyMarkdown);
  const agentRef = useRef(agent);
  currentDocumentRef.current = currentDocument;
  agentStateRef.current = agentState;
  applyMarkdownRef.current = applyMarkdown;
  agentRef.current = agent;

  // HITL: keep deps stable ([]). CopilotKit's useFrontendTool re-runs removeTool/addTool when deps
  // change; streaming document updates were unregistering confirm_changes during processAgentResult,
  // so the tool never reached "executing" and respond stayed undefined (disabled buttons).
  const confirmChangesRender = useCallback(
    ({ respond }: { respond?: (payload: { accepted: boolean }) => void }) => (
      <ConfirmChanges
        respond={respond}
        previousMarkdown={currentDocumentRef.current}
        proposedMarkdown={agentStateRef.current?.document ?? ''}
        onReject={() => {
          const doc = currentDocumentRef.current;
          applyMarkdownRef.current(doc);
          agentRef.current.setState({ document: doc });
        }}
        onConfirm={() => {
          const doc = agentStateRef.current?.document || '';
          applyMarkdownRef.current(doc);
          setCurrentDocument(doc);
          agentRef.current.setState({ document: doc });
        }}
      />
    ),
    [],
  );

  // Omit agentId so getTool() resolves via global fallback (name match, !tool.agentId).
  // Scoped registration + HttpAgent agentId sometimes missed the tool during processAgentResult.
  useHumanInTheLoop({ name: 'confirm_changes', render: confirmChangesRender }, []);

  // LangGraph + HttpAgent sometimes omits the synthetic confirm assistant message from
  // runAgentResult.newMessages, so processAgentResult never runs the frontend tool and
  // Confirm stays disabled. Re-drive processAgentResult once per pending toolCall id.
  const confirmKickAttemptedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const tc = activeToolCall;
    const msg = activeToolCallMessage as { id?: string } | undefined;
    if (!tc?.id || tc.function?.name !== 'confirm_changes' || !msg?.id) return;
    if (isLoading || executingToolCallIds.has(tc.id)) return;

    const toolCallId = tc.id;
    const hasToolResult = agent.messages.some(
      (tm) => tm.role === 'tool' && (tm as { toolCallId?: string }).toolCallId === toolCallId,
    );
    if (hasToolResult) {
      confirmKickAttemptedRef.current.delete(toolCallId);
      return;
    }
    if (confirmKickAttemptedRef.current.has(toolCallId)) return;
    confirmKickAttemptedRef.current.add(toolCallId);

    const runHandler = (
      copilotkit as unknown as {
        runHandler?: {
          processAgentResult: (p: {
            runAgentResult: { newMessages: unknown[] };
            agent: typeof agent;
          }) => Promise<unknown>;
        };
      }
    ).runHandler;
    if (!runHandler?.processAgentResult) {
      confirmKickAttemptedRef.current.delete(toolCallId);
      return;
    }

    void runHandler
      .processAgentResult({ runAgentResult: { newMessages: [msg] }, agent })
      .catch((err: unknown) => {
        console.error('[HITL] confirm_changes processAgentResult kick failed:', err);
        confirmKickAttemptedRef.current.delete(toolCallId);
      });
  }, [activeToolCall, activeToolCallMessage, agent, copilotkit, executingToolCallIds, isLoading]);

  const waveformHostRef = useRef<HTMLDivElement | null>(null);
  const [waveDims, setWaveDims] = useState({ width: 280, height: 32 });

  useEffect(() => {
    const el = waveformHostRef.current;
    if (!el) return;
    const measure = () => {
      const w = Math.max(160, Math.floor(el.clientWidth));
      setWaveDims((d) => (d.width === w ? d : { ...d, width: w }));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    setActiveAudioBlob(activeSession.audio_base64 ? base64ToBlob(activeSession.audio_base64) : null);
  }, [activeSessionId, activeSession.audio_base64]);

  const parsedTranscript = parseTranscript(activeSession.transcript);

  return (
    <div className="document-editor-workspace">
      <div className="stitch-outer-frame-card">
        <div className="stitch-columns-grid relative">
          <div className="stitch-left-stack">
            <div className="stitch-consultation-header">
              <div className="consultation-header-toolbar">
                <div className="consultation-header-status">
                  {errorMsg && <p className="consultation-error">{errorMsg}</p>}
                  {!errorMsg && !sessionHasAudio && !isRecording && (
                    <p className="consultation-hint">
                      No audio for this session — the waveform appears after you record or upload.
                    </p>
                  )}
                </div>
                <div className="consultation-header-actions">
                  {isRecording ? (
                    <button
                      type="button"
                      className="stitch-action-pill stop-rec inline-flex items-center gap-1.5"
                      onClick={stopRecording}
                    >
                      <Square size={11} fill="currentColor" /> Stop ({formatTime(recordingTime)})
                    </button>
                  ) : isUploading ? (
                    <button
                      type="button"
                      className="stitch-action-pill stop-rec inline-flex items-center gap-1.5"
                      disabled
                    >
                      <Loader2 size={12} className="animate-spin" /> Transcribing…
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="stitch-action-pill start-rec inline-flex items-center gap-1.5"
                      onClick={startRecording}
                    >
                      <Mic size={13} /> Record
                    </button>
                  )}
                  <label className="stitch-action-pill outline cursor-pointer inline-flex items-center gap-1.5">
                    <Upload size={13} /> Upload
                    <input
                      type="file"
                      accept="audio/*"
                      style={{ display: 'none' }}
                      onChange={handleAudioUpload}
                      disabled={isRecording || isUploading}
                    />
                  </label>
                </div>
              </div>

              <div className="stitch-playback-row">
                <div className="playback-controls">
                  <button
                    type="button"
                    className={`playback-play-btn ${
                      playingSessionId === activeSession.id ? 'playing' : ''
                    } ${!sessionHasAudio && !isRecording ? 'disabled' : ''}`}
                    onClick={() => playAudio(activeSession)}
                    disabled={!sessionHasAudio && !isRecording}
                    title={
                      !sessionHasAudio && !isRecording ? 'Add audio to enable playback' : undefined
                    }
                  >
                    {playingSessionId === activeSession.id ? (
                      <Pause size={12} fill="currentColor" />
                    ) : (
                      <Play size={12} fill="currentColor" className="ml-px" />
                    )}
                  </button>
                </div>

                <div className="waveform-timeline-host" ref={waveformHostRef}>
                  <div className="waveform-timeline-container">
                    {isRecording && mediaRecorderRef.current ? (
                      <LiveAudioVisualizer
                        mediaRecorder={mediaRecorderRef.current}
                        width={waveDims.width}
                        height={waveDims.height}
                        barColor="#0052cc"
                      />
                    ) : activeAudioBlob ? (
                      <AudioVisualizer
                        blob={activeAudioBlob}
                        width={waveDims.width}
                        height={waveDims.height}
                        barWidth={1.5}
                        gap={1}
                        barColor="#cbd5e1"
                        barPlayedColor="#0052cc"
                        currentTime={playbackTime}
                      />
                    ) : (
                      <div
                        className="waveform-empty-placeholder"
                        role="status"
                        aria-label="No audio waveform for this session"
                      >
                        <span className="waveform-empty-line" aria-hidden />
                        <span className="waveform-empty-label">No waveform</span>
                      </div>
                    )}
                  </div>
                </div>
                <span className="waveform-duration-badge">{activeSession.duration}</span>
              </div>
            </div>

            <div className="stitch-transcript-panel">
              <div className="panel-header-row panel-header-row--simple">
                <h3 className="panel-section-title">Transcript</h3>
              </div>
              <div className="transcript-bubbles-scroller">
                {parsedTranscript.length === 0 ? (
                  <div
                    className="transcript-empty-state"
                    role="status"
                    aria-live="polite"
                    aria-label="Transcript is empty until audio is processed"
                  >
                    <div className="transcript-empty-visual" aria-hidden>
                      <span className="transcript-empty-line" />
                    </div>
                    <p className="transcript-empty-kicker">Diarized dialogue</p>
                    <p className="transcript-empty-title">No transcript yet</p>
                    <p className="transcript-empty-hint">
                      Record a consultation or upload audio. Speaker turns will appear here after
                      transcription.
                    </p>
                  </div>
                ) : (
                  parsedTranscript.map((line, idx) => (
                    <div
                      key={idx}
                      className={`transcript-bubble-wrapper ${line.speaker.toLowerCase()}`}
                    >
                      <div className="bubble-meta">
                        <div className="bubble-avatar inline-flex items-center justify-center">
                          {line.speaker === 'Clinician' ? <Sparkles size={10} /> : <User size={10} />}
                        </div>
                        <span className="speaker-name">{line.speaker}</span>
                      </div>
                      <div className="bubble-content-card">
                        <p>{line.text}</p>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          <div className="stitch-insights-panel">
            <div className="panel-header-row">
              <h3 className="panel-section-title">AI Insights</h3>
              <button
                type="button"
                className="stitch-action-pill start-rec inline-flex items-center gap-1.5"
                onClick={handleGenerateReport}
                disabled={isGeneratingReport || isLoading || !editor || !canvasHasContent}
                title={
                  !canvasHasContent
                    ? 'Add report text in the editor (e.g. from chat or after recording) before generating.'
                    : undefined
                }
              >
                {isGeneratingReport ? (
                  <>
                    <Loader2 size={13} className="animate-spin" /> Working…
                  </>
                ) : (
                  <>
                    <FileText size={13} /> Generate report
                  </>
                )}
              </button>
            </div>
            {reportActionMsg && (
              <p
                className={`insights-report-feedback ${
                  reportActionMsg.kind === 'err' ? 'is-error' : 'is-ok'
                }`}
                role="status"
              >
                {reportActionMsg.text}
              </p>
            )}
            <div className="tiptap-clinical-container">
              <EditorContent editor={editor} />
            </div>

            <div className="stitch-disclaimer-banner">
              <span className="disclaimer-icon inline-flex items-center">
                <TriangleAlert size={14} />
              </span>
              <p className="disclaimer-text">
                This demonstration is for illustrative purposes only. It does not represent a
                finished or approved product and is not intended to diagnose or suggest treatment.
              </p>
            </div>
          </div>
        </div>

        {renderedTool && <div className="stitch-hitl-container">{renderedTool}</div>}

        {hasBubble && !hasHITL && !aiBubbleDismissed && (
          <div className="stitch-ai-response-bubble">
            <button
              type="button"
              className="bubble-close-btn"
              aria-label="Dismiss AI message"
              onClick={() => setAiBubbleDismissed(true)}
            >
              ×
            </button>
            <div className="bubble-header">
              <span className="bubble-sparkle-icon inline-flex items-center">
                <Sparkles size={12} />
              </span>
              <span className="bubble-speaker-title">AI CONSULTANT</span>
              {isLoading && <span className="bubble-typing-indicator">Drafting report…</span>}
            </div>
            <div className="bubble-text-content">
              {isLoading && !latestAssistantMessage ? (
                <div className="pulse-text">Updating clinical document in real time…</div>
              ) : typeof latestAssistantMessage?.content === 'string' ? (
                latestAssistantMessage.content
              ) : Array.isArray(latestAssistantMessage?.content) ? (
                latestAssistantMessage.content
                  .map((c: { type?: string; text?: string }) => (c.type === 'text' ? c.text : ''))
                  .join(' ')
              ) : (
                ''
              )}
            </div>
          </div>
        )}
      </div>

      <div className="stitch-docked-command-bar">
        <span className="command-sparkle inline-flex items-center">
          <Sparkles size={13} />
        </span>
        <input
          type="text"
          className="command-input"
          placeholder={isLoading ? 'AI is processing…' : 'Ask the AI to refine the clinical report…'}
          value={commandText}
          onChange={(e) => setCommandText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void handleSendCommand();
          }}
          disabled={isLoading}
        />
        <button
          type="button"
          className="command-send-btn"
          onClick={handleSendCommand}
          disabled={isLoading}
        >
          {isLoading ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
        </button>
      </div>
    </div>
  );
}

/** Voice consultation → diarized transcript → agent-assisted clinical report. */
export function SessionWorkspace() {
  return (
    <CopilotKit
      runtimeUrl="/api/copilotkit"
      useSingleEndpoint={false}
      showDevConsole={import.meta.env.DEV}
      agent={AGENT_ID}
    >
      <CopilotChatConfigurationProvider agentId={AGENT_ID}>
        <div className="clinical-app-layout">
          <main className="clinical-main-content">
            <DocumentEditor />
          </main>
        </div>
      </CopilotChatConfigurationProvider>
    </CopilotKit>
  );
}
