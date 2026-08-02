import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  CheckCircle2,
  CircleAlert,
  FileAudio,
  HeartHandshake,
  History,
  Loader2,
  Mic,
  ScanLine,
  ShieldCheck,
  Square,
  Upload,
} from 'lucide-react';

import { LiveAudioVisualizer } from '@/components/session/audioVisualizers';
import { ConfirmChanges } from '@/components/session/ConfirmChanges';
import '@/components/session/session.css';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  checkDemoEligibility,
  confirmDemoCheckin,
  createDemoCheckin,
  getDemoReadiness,
  type DemoCheckin,
  type DemoConfirmation,
  type DemoEligibility,
  type DemoReadiness,
  type DemoStatus,
  type PrevisitDraft,
  type ProposedChange,
  type ProviderStatus,
} from '@/lib/demoApi';

interface PreVisitCheckinDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  patientId: string;
  status: DemoStatus | null;
  onSaved: () => void;
}

type Stage = 'capture' | 'processing' | 'review' | 'saving' | 'saved' | 'eligibility';

const PROVIDERS: Array<[
  keyof Pick<DemoStatus, 'deepgram' | 'moss' | 'openai' | 'medplum' | 'stedi' | 'workflow'>,
  string,
]> = [
  ['deepgram', 'Deepgram'],
  ['moss', 'Moss'],
  ['openai', 'OpenAI'],
  ['medplum', 'Medplum'],
  ['stedi', 'Stedi'],
  ['workflow', 'Evidence gate'],
];

function formatSeconds(value: number) {
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60);
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

function ProviderPill({ name, status }: { name: string; status: ProviderStatus | undefined }) {
  const configured = Boolean(status?.configured);
  return (
    <span
      title={configured ? `${name} configured` : `Missing: ${status?.missing.join(', ') || 'status unavailable'}`}
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] font-semibold ${
        configured
          ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
          : 'border-amber-200 bg-amber-50 text-amber-800'
      }`}
    >
      {configured ? <Check size={11} /> : <CircleAlert size={11} />}
      {name}
    </span>
  );
}

function draftMarkdown(draft: PrevisitDraft | null) {
  if (!draft) return '';
  const changes = draft.proposed_changes
    .map(
      (change) =>
        `- **${change.title}:** ${change.proposed_value}\n  - Evidence ${change.evidence_utterance_id}: “${change.evidence_quote}”`,
    )
    .join('\n');
  return `## Proposed chart changes\n${changes || '- No evidence-backed changes extracted.'}\n\n## Clinician verification\n${draft.clinician_verification.map((item) => `- ${item}`).join('\n')}`;
}

export function PreVisitCheckinDialog({
  open,
  onOpenChange,
  patientId,
  status,
  onSaved,
}: PreVisitCheckinDialogProps) {
  const [stage, setStage] = useState<Stage>('capture');
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [duration, setDuration] = useState(0);
  const [patientSpeaker, setPatientSpeaker] = useState(0);
  const [isRecording, setIsRecording] = useState(false);
  const [activeRecorder, setActiveRecorder] = useState<MediaRecorder | null>(null);
  const [checkin, setCheckin] = useState<DemoCheckin | null>(null);
  const [draft, setDraft] = useState<PrevisitDraft | null>(null);
  const [confirmation, setConfirmation] = useState<DemoConfirmation | null>(null);
  const [eligibility, setEligibility] = useState<DemoEligibility | null>(null);
  const [readiness, setReadiness] = useState<DemoReadiness | null>(null);
  const [accessToken, setAccessToken] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [reviewKey, setReviewKey] = useState(0);

  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const startedAtRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      streamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  useEffect(() => {
    setStage('capture');
    setAudioFile(null);
    setDuration(0);
    setPatientSpeaker(0);
    setCheckin(null);
    setDraft(null);
    setConfirmation(null);
    setEligibility(null);
    setReadiness(null);
    setAccessToken('');
    setError(null);
    setReviewKey((value) => value + 1);
  }, [patientId]);

  const checkinProvidersReady =
    status?.demo_patient_id === patientId &&
    accessToken.length >= 32 &&
    Boolean(
      status.deepgram.configured &&
        status.moss.configured &&
        status.openai.configured &&
        status.medplum.configured &&
        status.workflow.configured,
    );

  const startRecording = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const preferred = 'audio/webm;codecs=opus';
      const recorder = MediaRecorder.isTypeSupported(preferred)
        ? new MediaRecorder(stream, { mimeType: preferred })
        : new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const elapsed = Math.max(0.1, (Date.now() - startedAtRef.current) / 1000);
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        setAudioFile(new File([blob], 'synthetic-previsit.webm', { type: blob.type }));
        setDuration(elapsed);
        setIsRecording(false);
        setActiveRecorder(null);
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        if (timerRef.current) clearInterval(timerRef.current);
        timerRef.current = null;
      };
      startedAtRef.current = Date.now();
      setDuration(0);
      setActiveRecorder(recorder);
      setIsRecording(true);
      recorder.start(250);
      timerRef.current = setInterval(() => {
        setDuration((Date.now() - startedAtRef.current) / 1000);
      }, 250);
    } catch (err) {
      setError((err as Error).message || 'Microphone access failed.');
    }
  };

  const stopRecording = () => {
    if (activeRecorder?.state === 'recording') activeRecorder.stop();
  };

  const processAudio = async () => {
    if (!audioFile) return;
    setStage('processing');
    setError(null);
    try {
      const result = await createDemoCheckin(
        patientId,
        audioFile,
        duration,
        patientSpeaker,
        accessToken,
      );
      setCheckin(result);
      setDraft(result.draft);
      setStage('review');
    } catch (err) {
      setError((err as Error).message);
      setStage('capture');
    }
  };

  const updateChange = (index: number, patch: Partial<ProposedChange>) => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        proposed_changes: current.proposed_changes.map((change, changeIndex) =>
          changeIndex === index ? { ...change, ...patch } : change,
        ),
      };
    });
    setReviewKey((value) => value + 1);
  };

  const saveApproval = async () => {
    if (!checkin || !draft) {
      setError('The provider-produced draft is not ready for approval.');
      setReviewKey((value) => value + 1);
      return;
    }
    setStage('saving');
    setError(null);
    try {
      const saved = await confirmDemoCheckin(patientId, checkin, draft, accessToken);
      setConfirmation(saved);
      setReadiness(await getDemoReadiness(patientId, accessToken));
      setStage('saved');
      onSaved();
    } catch (err) {
      setError((err as Error).message);
      setStage('review');
      setReviewKey((value) => value + 1);
    }
  };

  const loadLatestReconstruction = async () => {
    setStage('processing');
    setError(null);
    try {
      const latest = await getDemoReadiness(patientId, accessToken);
      setReadiness(latest);
      setConfirmation({
        checkin_id: latest.checkin_id,
        approved: true,
        validation_status: latest.validation_status,
        validations: latest.validations,
        resources: latest.resources,
        document_id: latest.document_id,
      });
      setEligibility(latest.eligibility);
      setStage('saved');
    } catch (err) {
      setError((err as Error).message);
      setStage('capture');
    }
  };

  const runEligibility = async () => {
    const checkinId = checkin?.checkin_id ?? readiness?.checkin_id;
    if (!checkinId) return;
    setStage('eligibility');
    setError(null);
    try {
      const result = await checkDemoEligibility(patientId, checkinId, accessToken);
      setEligibility(result);
      setReadiness(await getDemoReadiness(patientId, accessToken));
      setStage('saved');
    } catch (err) {
      setError((err as Error).message);
      setStage('saved');
    }
  };

  const proposedMarkdown = useMemo(() => draftMarkdown(draft), [draft]);

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (nextOpen) onOpenChange(true);
        if (!nextOpen && !isRecording) {
          setStage('capture');
          setAudioFile(null);
          setDuration(0);
          setPatientSpeaker(0);
          setCheckin(null);
          setDraft(null);
          setConfirmation(null);
          setEligibility(null);
          setReadiness(null);
          setAccessToken('');
          setError(null);
          setReviewKey((value) => value + 1);
          onOpenChange(false);
        }
      }}
    >
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-5xl" showCloseButton={!isRecording}>
        <DialogHeader>
          <div className="pr-8">
            <div>
              <DialogTitle>Evidence-linked pre-visit check-in</DialogTitle>
              <DialogDescription className="mt-1">
                No clinical FHIR write occurs until explicit clinician approval.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="flex flex-wrap gap-1.5 border-y border-slate-100 py-3">
          {PROVIDERS.map(([key, name]) => (
            <ProviderPill key={key} name={name} status={status?.[key]} />
          ))}
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">
            {error}
          </div>
        )}

        {(stage === 'capture' || stage === 'processing') && (
          <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="clinical-section-title">Patient conversation</p>
                  <h3 className="mt-1 text-base font-semibold text-slate-900">Record or upload synthetic audio</h3>
                </div>
                <span className="font-mono text-xs font-semibold text-slate-500">{formatSeconds(duration)}</span>
              </div>

              <div className="mt-4 grid min-h-20 place-items-center rounded-lg border border-dashed border-slate-200 bg-white px-3">
                {isRecording && activeRecorder ? (
                  <LiveAudioVisualizer
                    mediaRecorder={activeRecorder}
                    width={520}
                    height={48}
                    barColor="#0052cc"
                  />
                ) : audioFile ? (
                  <div className="flex items-center gap-2 text-sm text-slate-700">
                    <FileAudio size={18} className="text-primary" />
                    <span className="font-medium">{audioFile.name}</span>
                  </div>
                ) : (
                  <p className="max-w-md text-center text-xs leading-5 text-slate-500">
                    Use a brief prerecorded synthetic conversation with two genuine speakers so Deepgram can prove diarization.
                  </p>
                )}
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                {isRecording ? (
                  <Button variant="destructive" onClick={stopRecording}>
                    <Square size={13} /> Stop recording
                  </Button>
                ) : (
                  <Button onClick={() => void startRecording()} disabled={!checkinProvidersReady || stage === 'processing'}>
                    <Mic size={14} /> Record
                  </Button>
                )}
                <label className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-lg border border-border bg-white px-2.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100">
                  <Upload size={14} /> Upload audio
                  <input
                    type="file"
                    accept="audio/*,video/webm"
                    className="hidden"
                    disabled={stage === 'processing'}
                    onChange={(event) => {
                      const selected = event.target.files?.[0];
                      if (selected) {
                        setAudioFile(selected);
                        setDuration(0);
                        setError(null);
                      }
                      event.target.value = '';
                    }}
                  />
                </label>
                <Button
                  variant="secondary"
                  onClick={() => void processAudio()}
                  disabled={!audioFile || !checkinProvidersReady || stage === 'processing'}
                >
                  {stage === 'processing' ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
                  Deepgram → Moss → OpenAI
                </Button>
              </div>
              <label className="mt-4 flex max-w-xs flex-col gap-1 text-xs font-semibold text-slate-700">
                Patient speaker in this recording
                <select
                  className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs"
                  value={patientSpeaker}
                  onChange={(event) => setPatientSpeaker(Number(event.target.value))}
                  disabled={stage === 'processing'}
                >
                  <option value={0}>Speaker 1 (patient speaks first)</option>
                  <option value={1}>Speaker 2</option>
                </select>
              </label>
            </div>

            <aside className="rounded-xl border border-slate-200 bg-white p-4">
              <label htmlFor="demo-operator-token" className="clinical-section-title">
                Demo operator access
              </label>
              <Input
                id="demo-operator-token"
                type="password"
                autoComplete="off"
                spellCheck={false}
                className="mt-2 bg-white font-mono"
                value={accessToken}
                onChange={(event) => setAccessToken(event.target.value)}
                placeholder="Paste the runtime access token"
                disabled={stage === 'processing'}
              />
              <p className="mt-2 text-[10px] leading-4 text-slate-400">
                Held only in this dialog's memory and cleared when it closes. It is never bundled or stored in
                browser storage.
              </p>
              <Button
                type="button"
                variant="outline"
                className="mt-3 w-full"
                disabled={accessToken.length < 32 || !status?.medplum.configured || stage === 'processing'}
                onClick={() => void loadLatestReconstruction()}
              >
                {stage === 'processing' ? <Loader2 className="animate-spin" /> : <History />}
                Open latest saved reconstruction
              </Button>
              <div className="my-4 border-t border-slate-100" />
              <p className="clinical-section-title">Write gate</p>
              <div className="mt-3 space-y-3 text-xs leading-5 text-slate-600">
                <p>1. Deepgram produces exact speaker/time evidence.</p>
                <p>2. Moss retrieves patient context locally; the session is not pushed.</p>
                <p>3. OpenAI proposes a structured draft with exact quotes.</p>
                <p className="font-semibold text-slate-900">4. Medplum receives nothing until clinician approval.</p>
              </div>
              {!checkinProvidersReady && (
                <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-2 text-[11px] leading-5 text-amber-800">
                  {accessToken.length < 32
                    ? 'Enter the runtime operator token, then confirm every provider pill is configured.'
                    : 'Configure the missing server-side values shown above. No mock response will be substituted.'}
                </p>
              )}
            </aside>
          </section>
        )}

        {(stage === 'review' || stage === 'saving') && checkin && draft && (
          <div className="space-y-4">
            <section className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex items-center justify-between gap-2">
                  <p className="clinical-section-title">Deepgram diarization</p>
                  <span className="font-mono text-[10px] text-slate-400">{checkin.deepgram_request_id}</span>
                </div>
                <div className="mt-3 max-h-64 space-y-2 overflow-y-auto pr-1">
                  {checkin.utterances.map((utterance) => (
                    <article key={utterance.id} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                      <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-500">
                        <span>
                          {utterance.speaker === checkin.patient_speaker ? 'Patient' : 'Clinician / other'} · Speaker{' '}
                          {utterance.speaker + 1} · {utterance.id}
                        </span>
                        <span>{utterance.start.toFixed(1)}–{utterance.end.toFixed(1)}s</span>
                      </div>
                      <p className="mt-2 text-xs leading-5 text-slate-800">{utterance.text}</p>
                    </article>
                  ))}
                </div>
              </div>

              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex items-center justify-between gap-2">
                  <p className="clinical-section-title">Moss retrieval evidence</p>
                  <span className="rounded bg-emerald-50 px-2 py-1 text-[10px] font-semibold text-emerald-700">
                    Local · not pushed
                  </span>
                </div>
                <p className="mt-2 text-[11px] text-slate-500">
                  {checkin.moss.time_taken_ms ?? '—'} ms · {checkin.moss.index_name} · OpenAI {checkin.openai_model}
                </p>
                <div className="mt-3 max-h-64 space-y-2 overflow-y-auto pr-1">
                  {checkin.moss.evidence.map((item) => (
                    <article key={item.id} className="rounded-lg border border-blue-100 bg-blue-50 p-3">
                      <div className="flex items-center justify-between text-[10px] font-semibold text-blue-700">
                        <span>{item.id}</span>
                        <span>{item.score.toFixed(3)}</span>
                      </div>
                      <p className="mt-2 line-clamp-3 text-xs leading-5 text-slate-700">{item.text}</p>
                    </article>
                  ))}
                </div>
                {checkin.imaging_evidence.length > 0 && (
                  <div className="mt-3 rounded-lg border border-cyan-100 bg-cyan-50 p-3">
                    <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-cyan-800">
                      <ScanLine size={12} /> Clinician-accepted imaging context
                    </p>
                    {checkin.imaging_evidence.map((item) => (
                      <div key={item.diagnostic_report_id} className="mt-2">
                        <p className="font-mono text-[10px] text-cyan-700">
                          DiagnosticReport/{item.diagnostic_report_id}
                        </p>
                        {item.reviewer_name && (
                          <p className="mt-1 text-[10px] text-cyan-800">
                            Accepted by {item.reviewer_name}
                            {item.report_version_id ? ` · v${item.report_version_id}` : ''}
                          </p>
                        )}
                        <p className="mt-1 line-clamp-3 text-xs leading-5 text-slate-700">{item.summary}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <section className="rounded-xl border border-slate-200 bg-slate-50 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="clinical-section-title">Clinician review</p>
                  <h3 className="mt-1 text-base font-semibold text-slate-900">Approve or correct the draft</h3>
                </div>
                <span className="rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-[10px] text-slate-500">
                  No FHIR write yet
                </span>
              </div>
              <div className="mt-4 grid gap-3">
                <div className="grid gap-3 rounded-lg border border-slate-200 bg-white p-3 lg:grid-cols-2">
                  <div className="lg:col-span-2">
                    <label htmlFor="review-summary" className="text-xs font-semibold text-slate-700">
                      Reviewed summary
                    </label>
                    <Textarea
                      id="review-summary"
                      className="mt-1 min-h-16 bg-white text-xs"
                      value={draft.summary}
                      onChange={(event) => setDraft({ ...draft, summary: event.target.value })}
                    />
                  </div>
                  <div>
                    <label htmlFor="review-unresolved" className="text-xs font-semibold text-slate-700">
                      Unresolved questions, one per line
                    </label>
                    <Textarea
                      id="review-unresolved"
                      className="mt-1 min-h-20 bg-white text-xs"
                      value={draft.unresolved_questions.join('\n')}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          unresolved_questions: event.target.value
                            .split('\n')
                            .map((item) => item.trim())
                            .filter(Boolean),
                        })
                      }
                    />
                  </div>
                  <div>
                    <label htmlFor="review-verification" className="text-xs font-semibold text-slate-700">
                      Clinician verification, one per line
                    </label>
                    <Textarea
                      id="review-verification"
                      className="mt-1 min-h-20 bg-white text-xs"
                      value={draft.clinician_verification.join('\n')}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          clinician_verification: event.target.value
                            .split('\n')
                            .map((item) => item.trim())
                            .filter(Boolean),
                        })
                      }
                    />
                  </div>
                  <label className="flex items-center gap-2 text-xs font-semibold text-slate-700">
                    <input
                      type="checkbox"
                      checked={draft.recommended_visit}
                      onChange={(event) => setDraft({ ...draft, recommended_visit: event.target.checked })}
                    />
                    Recommend a future clinician visit
                  </label>
                  <Input
                    aria-label="Recommended service"
                    value={draft.recommended_service}
                    onChange={(event) => setDraft({ ...draft, recommended_service: event.target.value })}
                    placeholder="Recommended service"
                    disabled={!draft.recommended_visit}
                  />
                </div>
                {draft.proposed_changes.map((change, index) => (
                  <div key={index} className="rounded-lg border border-slate-200 bg-white p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <select
                        aria-label={`Change ${index + 1} type`}
                        className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-800"
                        value={change.kind}
                        onChange={(event) =>
                          updateChange(index, { kind: event.target.value as ProposedChange['kind'] })
                        }
                      >
                        <option value="medication_adherence">Medication adherence</option>
                        <option value="allergy_confirmation">Allergy confirmation</option>
                        <option value="follow_up">Follow-up</option>
                      </select>
                      <span className="text-[10px] font-semibold text-primary">Evidence {change.evidence_utterance_id}</span>
                    </div>
                    <Input
                      aria-label={`Change ${index + 1} title`}
                      className="mt-2 bg-white text-xs font-semibold"
                      value={change.title}
                      onChange={(event) => updateChange(index, { title: event.target.value })}
                    />
                    <Input
                      aria-label={`Change ${index + 1} clinical subject`}
                      className="mt-2 bg-white text-xs"
                      value={change.clinical_subject}
                      onChange={(event) => updateChange(index, { clinical_subject: event.target.value })}
                      placeholder="Medication, allergen, or follow-up topic"
                    />
                    <Textarea
                      id={`change-${index}`}
                      className="mt-2 min-h-16 bg-white text-xs"
                      value={change.proposed_value}
                      onChange={(event) => updateChange(index, { proposed_value: event.target.value })}
                    />
                    <div className="mt-2 grid gap-2 sm:grid-cols-[140px_minmax(0,1fr)]">
                      <Input
                        aria-label={`Change ${index + 1} evidence ID`}
                        className="bg-white font-mono text-xs"
                        value={change.evidence_utterance_id}
                        onChange={(event) => updateChange(index, { evidence_utterance_id: event.target.value })}
                      />
                      <Input
                        aria-label={`Change ${index + 1} exact quote`}
                        className="bg-white text-xs"
                        value={change.evidence_quote}
                        onChange={(event) => updateChange(index, { evidence_quote: event.target.value })}
                      />
                    </div>
                    <Input
                      aria-label={`Change ${index + 1} clinician note`}
                      className="mt-2 bg-white text-xs"
                      value={change.clinician_note}
                      onChange={(event) => updateChange(index, { clinician_note: event.target.value })}
                      placeholder="Clinician note"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      className="mt-2 text-xs text-red-600"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          proposed_changes: draft.proposed_changes.filter((_, changeIndex) => changeIndex !== index),
                        })
                      }
                    >
                      Remove unsupported change
                    </Button>
                    <blockquote className="mt-2 border-l-2 border-blue-200 pl-3 text-[11px] leading-5 text-slate-600">
                      “{change.evidence_quote}”
                    </blockquote>
                  </div>
                ))}
                <p className="rounded-lg border border-blue-100 bg-blue-50 p-3 text-xs leading-5 text-blue-900">
                  Approval is attributed to the server-configured operator identity after the runtime access
                  token is verified; the browser cannot choose the clinician provenance.
                </p>
              </div>
              <div className="mt-4">
                {stage === 'saving' ? (
                  <div className="flex items-center justify-center gap-2 rounded-lg border border-blue-100 bg-blue-50 p-4 text-sm font-semibold text-primary">
                    <Loader2 className="animate-spin" /> Validating first, then writing the FHIR transaction…
                  </div>
                ) : (
                  <ConfirmChanges
                    key={reviewKey}
                    allowWithoutRespond
                    previousMarkdown="No chart changes have been written."
                    proposedMarkdown={proposedMarkdown}
                    onReject={() => setError('Rejected. Nothing was written; edit the draft before confirming.')}
                    onConfirm={() => void saveApproval()}
                  />
                )}
              </div>
            </section>
          </div>
        )}

        {(stage === 'saved' || stage === 'eligibility') && confirmation && (
          <div className="space-y-4">
            <section className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
              <div className="flex items-start gap-3">
                <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
                <div className="min-w-0 flex-1">
                  <p className="clinical-section-title text-emerald-700">Validation passed · saved to Medplum</p>
                  <h3 className="mt-1 text-base font-semibold text-emerald-950">
                    {confirmation.resources.length} validated FHIR resources committed
                  </h3>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {confirmation.resources.map((resource, index) => (
                      <div key={`${resource.resource_type}-${resource.resource_id}-${index}`} className="rounded-lg border border-emerald-200 bg-white p-2.5">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-emerald-700">
                          {resource.resource_type ?? 'FHIR resource'}
                        </p>
                        <p className="mt-1 break-all font-mono text-[11px] text-slate-700">
                          {resource.resource_id ?? resource.location ?? resource.status}
                        </p>
                        {resource.version_id && <p className="mt-1 text-[10px] text-slate-400">v{resource.version_id}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </section>

            {readiness && (
              <section className="rounded-xl border border-blue-100 bg-blue-50 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="clinical-section-title text-blue-700">Saved sponsor evidence</p>
                    <p className="mt-1 text-xs text-slate-600">
                      Reviewed by {readiness.clinician_name} · OpenAI {readiness.openai_model}
                    </p>
                  </div>
                  <div className="space-y-1 text-right font-mono text-[10px] text-slate-500">
                    <p>Deepgram {readiness.deepgram_request_id}</p>
                    <p>OpenAI {readiness.openai_response_id}</p>
                  </div>
                </div>
                <div className="mt-3 grid gap-2 md:grid-cols-3">
                  {readiness.what_changed.map((change) => (
                    <article key={`${change.title}-${change.evidence_utterance_id}`} className="rounded-lg border border-blue-100 bg-white p-3">
                      <p className="text-xs font-semibold text-slate-900">{change.title}</p>
                      <p className="mt-1 text-[11px] leading-5 text-slate-600">{change.proposed_value}</p>
                      <blockquote className="mt-2 border-l-2 border-blue-200 pl-2 text-[10px] leading-4 text-blue-800">
                        {change.evidence_utterance_id}: “{change.evidence_quote}”
                      </blockquote>
                    </article>
                  ))}
                </div>
                {readiness.unresolved_questions.length > 0 && (
                  <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
                    Unresolved: {readiness.unresolved_questions.join(' · ')}
                  </p>
                )}
                {readiness.imaging_evidence.map((item) => (
                  <p key={item.diagnostic_report_id} className="mt-3 flex items-start gap-2 rounded-lg border border-cyan-100 bg-cyan-50 p-2 text-xs text-cyan-950">
                    <ScanLine size={14} className="mt-0.5 shrink-0" />
                    <span className="line-clamp-3">
                      Accepted imaging context · DiagnosticReport/{item.diagnostic_report_id}
                      {item.reviewer_name ? ` · ${item.reviewer_name}` : ''}: {item.summary}
                    </span>
                  </p>
                ))}
              </section>
            )}

            <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <p className="clinical-section-title">Stedi test-mode eligibility</p>
                {eligibility ? (
                  <div className="mt-3 space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`rounded-md px-2 py-1 text-xs font-semibold ${eligibility.coverage_active ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-800'}`}>
                        {eligibility.coverage_active === null
                          ? 'Coverage status not returned'
                          : eligibility.coverage_active
                            ? 'Active coverage reported'
                            : 'Active coverage not confirmed'}
                      </span>
                      <span className="font-mono text-[10px] text-slate-400">{eligibility.transaction_id}</span>
                    </div>
                    <p className="rounded-lg border border-blue-100 bg-blue-50 p-3 text-sm font-semibold leading-6 text-blue-950">
                      {eligibility.patient_responsibility_summary}
                    </p>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {eligibility.benefits.slice(0, 8).map((benefit, index) => (
                        <div key={`${benefit.code}-${benefit.name}-${index}`} className="rounded-lg border border-slate-200 p-2.5 text-xs">
                          <p className="font-semibold text-slate-900">{benefit.name || benefit.code}</p>
                          <p className="mt-1 text-slate-600">
                            {benefit.benefit_amount != null
                              ? `$${benefit.benefit_amount.toFixed(2)}`
                              : benefit.benefit_percent != null
                                ? `${(benefit.benefit_percent * 100).toFixed(0)}%`
                                : 'Benefit detail returned'}
                          </p>
                          <p className="mt-1 text-[10px] leading-4 text-slate-400">
                            STC {benefit.service_type_codes.join(', ') || 'not returned'} · network{' '}
                            {benefit.in_plan_network_indicator_code || 'not returned'} · coverage{' '}
                            {benefit.coverage_level_code || 'not returned'} · time{' '}
                            {benefit.time_qualifier_code || 'not returned'}
                          </p>
                        </div>
                      ))}
                    </div>
                    <p className="text-[11px] leading-5 text-slate-500">{eligibility.disclaimer}</p>
                  </div>
                ) : (
                  <div className="mt-4">
                    <p className="text-xs leading-5 text-slate-600">
                      Uses the configured exact Stedi synthetic subscriber, payer, provider NPI, and service type codes.
                    </p>
                    <Button
                      className="mt-3"
                      onClick={() => void runEligibility()}
                      disabled={!status?.stedi.configured || stage === 'eligibility'}
                    >
                      {stage === 'eligibility' ? <Loader2 className="animate-spin" /> : <HeartHandshake />}
                      Check coverage and patient responsibility
                    </Button>
                  </div>
                )}
              </div>

              <aside className="rounded-xl border border-slate-800 bg-slate-950 p-4 text-slate-100">
                <p className="clinical-section-title text-slate-400">Clinician readiness</p>
                {readiness ? (
                  <div className="mt-3 space-y-4 text-xs leading-5">
                    <div>
                      <p className="font-semibold text-white">What changed today</p>
                      <ul className="mt-1 list-disc space-y-1 pl-4 text-slate-300">
                        {readiness.what_changed.map((change) => <li key={change.title}>{change.proposed_value}</li>)}
                      </ul>
                    </div>
                    <div>
                      <p className="font-semibold text-white">Verify next</p>
                      <ul className="mt-1 list-disc space-y-1 pl-4 text-slate-300">
                        {readiness.clinician_verification.map((item) => <li key={item}>{item}</li>)}
                      </ul>
                    </div>
                    {readiness.unresolved_questions.length > 0 && (
                      <div>
                        <p className="font-semibold text-white">Still unresolved</p>
                        <ul className="mt-1 list-disc space-y-1 pl-4 text-amber-200">
                          {readiness.unresolved_questions.map((item) => <li key={item}>{item}</li>)}
                        </ul>
                      </div>
                    )}
                    <p className="rounded-lg border border-slate-700 bg-slate-900 p-2 text-slate-300">
                      Saved reconstruction: {readiness.checkin_id}<br />
                      {readiness.utterances.length} diarized utterances · DocumentReference/{readiness.document_id}
                    </p>
                  </div>
                ) : (
                  <Loader2 className="mt-4 animate-spin text-slate-400" />
                )}
              </aside>
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
