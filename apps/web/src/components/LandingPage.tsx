import { useRef, useState, type CSSProperties, type PointerEvent, type ReactNode } from 'react';
import {
  Activity,
  ArrowRight,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  Circle,
  Clock3,
  Database,
  FileCheck2,
  LockKeyhole,
  MessageSquareText,
  Mic,
  Radio,
  ScanLine,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Zap,
} from 'lucide-react';
import { Link } from 'react-router-dom';

type WorkspaceMode = 'brief' | 'imaging' | 'session';

const WORKSPACES: Array<{
  id: WorkspaceMode;
  label: string;
  detail: string;
  icon: typeof Activity;
}> = [
  { id: 'brief', label: 'Patient brief', detail: 'Longitudinal intelligence', icon: BrainCircuit },
  { id: 'imaging', label: 'Imaging', detail: 'DICOM + segmentation', icon: ScanLine },
  { id: 'session', label: 'Live session', detail: 'Voice-assisted visit', icon: Mic },
];

const SIGNALS = [
  '12 sources synchronized',
  'Medication conflict surfaced',
  '3 trends linked to evidence',
  'FHIR record connected',
  'Clinician review required',
];

const WORKFLOW = [
  {
    label: 'Before the visit',
    title: 'Walk in knowing what changed.',
    body: 'Medtrace reads across encounters, documents, medications, labs, and prior conversations to build a focused pre-visit brief.',
    stat: '18 mo',
    statLabel: 'context condensed',
    icon: BrainCircuit,
  },
  {
    label: 'During the visit',
    title: 'Stay with the patient, not the chart.',
    body: 'Use the live consultation workspace to capture the conversation, retrieve context, and preserve the clinical thread in real time.',
    stat: 'Live',
    statLabel: 'voice context',
    icon: Mic,
  },
  {
    label: 'After the visit',
    title: 'Review every proposed change.',
    body: 'Suggested updates remain visible, source-linked, and unverified until a clinician decides what belongs in the canonical record.',
    stat: '100%',
    statLabel: 'human reviewed',
    icon: FileCheck2,
  },
] as const;

export function LandingPage() {
  const [workspace, setWorkspace] = useState<WorkspaceMode>('brief');
  const [workflowStep, setWorkflowStep] = useState(0);
  const heroRef = useRef<HTMLElement>(null);

  const handlePointerMove = (event: PointerEvent<HTMLElement>) => {
    const rect = heroRef.current?.getBoundingClientRect();
    if (!rect || !heroRef.current) return;
    heroRef.current.style.setProperty('--pointer-x', `${event.clientX - rect.left}px`);
    heroRef.current.style.setProperty('--pointer-y', `${event.clientY - rect.top}px`);
  };

  return (
    <div className="modern-landing overflow-hidden bg-[#050912] text-white">
      <section
        ref={heroRef}
        onPointerMove={handlePointerMove}
        className="modern-hero relative border-b border-white/[0.08]"
      >
        <div className="modern-hero-grid pointer-events-none absolute inset-0" aria-hidden="true" />
        <div className="modern-pointer-glow pointer-events-none absolute inset-0" aria-hidden="true" />
        <div className="modern-orb modern-orb-one" aria-hidden="true" />
        <div className="modern-orb modern-orb-two" aria-hidden="true" />

        <div className="relative mx-auto flex min-h-[calc(100svh-3.5rem)] w-full max-w-[1480px] flex-col px-5 pb-10 pt-16 sm:px-8 lg:px-12 lg:pb-14 lg:pt-20 xl:px-16">
          <div className="mx-auto max-w-5xl text-center">
            <div className="modern-enter inline-flex items-center gap-2 rounded-full border border-[#3b82f6]/25 bg-[#3b82f6]/[0.08] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#93c5fd] shadow-[inset_0_0_20px_rgba(59,130,246,0.05)]">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#3b82f6] opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-[#3b82f6]" />
              </span>
              Clinical intelligence · online
            </div>

            <h1 className="modern-display modern-enter mt-7 text-[clamp(3.2rem,8vw,7.7rem)] font-semibold leading-[0.88] tracking-[-0.072em] [animation-delay:80ms]">
              Know the patient.
              <span className="modern-gradient-text mt-2 block">Before the room.</span>
            </h1>

            <p className="modern-enter mx-auto mt-7 max-w-2xl text-[15px] leading-7 text-[#98a6bb] sm:text-lg sm:leading-8 [animation-delay:160ms]">
              One clinical workspace that turns fragmented records, imaging, and conversation into
              a source-linked brief you can actually act on.
            </p>

            <div className="modern-enter mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row [animation-delay:240ms]">
              <Link
                to="/patients"
                className="modern-primary-button group inline-flex h-12 w-full items-center justify-center gap-2.5 rounded-xl bg-[#0052cc] px-5 text-sm font-bold text-white shadow-[0_0_0_1px_rgba(59,130,246,0.45),0_16px_45px_-15px_rgba(59,130,246,0.7)] transition duration-300 hover:-translate-y-0.5 hover:bg-[#2563eb] sm:w-auto"
              >
                Launch workspace
                <ArrowRight size={16} className="transition-transform group-hover:translate-x-1" />
              </Link>
              <a
                href="#platform"
                className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.045] px-5 text-sm font-semibold text-white/80 backdrop-blur transition hover:border-white/20 hover:bg-white/[0.08] hover:text-white sm:w-auto"
              >
                Explore the platform
                <ChevronRight size={15} />
              </a>
            </div>
          </div>

          <div className="modern-enter relative mx-auto mt-12 w-full max-w-[1220px] [animation-delay:320ms] lg:mt-14">
            <div className="absolute -inset-x-8 bottom-0 top-1/3 -z-10 bg-[#3b82f6]/15 blur-[100px]" aria-hidden="true" />
            <ProductStage workspace={workspace} onWorkspaceChange={setWorkspace} />
          </div>
        </div>
      </section>

      <div className="relative overflow-hidden border-b border-white/[0.08] bg-[#080e19] py-3.5">
        <div className="modern-signal-track flex w-max items-center">
          {[...SIGNALS, ...SIGNALS].map((signal, index) => (
            <div key={`${signal}-${index}`} className="flex items-center gap-6 px-5 text-[10px] font-semibold uppercase tracking-[0.15em] text-[#728096]">
              <span className="flex items-center gap-2 whitespace-nowrap">
                <Circle size={6} className="fill-[#3b82f6] text-[#3b82f6]" />
                {signal}
              </span>
              <span className="h-px w-12 bg-white/10" />
            </div>
          ))}
        </div>
      </div>

      <section id="platform" className="relative bg-[#070c15] px-5 py-20 sm:px-8 lg:px-12 lg:py-28">
        <div className="mx-auto w-full max-w-[1340px]">
          <SectionHeading
            eyebrow="One connected clinical system"
            title={<>Every signal.<br /><span className="text-white/35">One workspace.</span></>}
            body="Medtrace joins the patient timeline, evidence, imaging, and visit conversation without hiding where information came from."
          />

          <div className="mt-12 grid gap-4 lg:grid-cols-12 lg:grid-rows-[300px_300px]">
            <article className="modern-bento group relative overflow-hidden rounded-3xl border border-white/[0.09] bg-[#0b1220] p-6 lg:col-span-7 lg:row-span-2 sm:p-8">
              <BentoGlow />
              <div className="relative z-10 flex h-full flex-col">
                <div className="flex items-start justify-between gap-4">
                  <FeatureLabel icon={BrainCircuit} text="Longitudinal intelligence" />
                  <span className="rounded-full border border-[#3b82f6]/25 bg-[#3b82f6]/[0.08] px-2.5 py-1 font-mono text-[9px] uppercase tracking-widest text-[#60a5fa]">Live graph</span>
                </div>
                <h3 className="modern-display mt-8 max-w-xl text-3xl font-semibold tracking-[-0.04em] sm:text-5xl">
                  The chart becomes a clinical thread.
                </h3>
                <p className="mt-4 max-w-xl text-sm leading-7 text-[#8795aa]">
                  Relevant history, recent changes, care gaps, and conflicting evidence surface together—with a path back to the source.
                </p>
                <div className="mt-9 grid min-h-0 flex-1 grid-cols-[34px_1fr] gap-4 overflow-hidden rounded-2xl border border-white/[0.08] bg-[#070c15]/80 p-4 sm:grid-cols-[120px_1fr] sm:p-5">
                  <div className="relative flex flex-col justify-between before:absolute before:bottom-4 before:left-[5px] before:top-3 before:w-px before:bg-gradient-to-b before:from-[#3b82f6] before:to-white/5">
                    {['Today', '3 mo', '8 mo', '18 mo'].map((date, index) => (
                      <div key={date} className="relative z-10 flex items-center gap-3">
                        <span className={`h-[11px] w-[11px] shrink-0 rounded-full border-2 ${index === 0 ? 'border-[#3b82f6] bg-[#3b82f6]/30 shadow-[0_0_12px_#3b82f6]' : 'border-[#465267] bg-[#0b1220]'}`} />
                        <span className="hidden text-[10px] font-semibold text-[#738198] sm:inline">{date}</span>
                      </div>
                    ))}
                  </div>
                  <div className="space-y-2.5">
                <SignalRow tone="brand" label="New signal" value="A1c increased across 2 observations" />
                    <SignalRow tone="blue" label="Source found" value="Exertional symptoms in discharge note" />
                    <SignalRow tone="amber" label="Needs review" value="Medication reconciliation incomplete" />
                  </div>
                </div>
              </div>
            </article>

            <article className="modern-bento group relative overflow-hidden rounded-3xl border border-white/[0.09] bg-[#0b1220] p-6 lg:col-span-5 sm:p-8">
              <BentoGlow blue />
              <div className="relative z-10">
                <FeatureLabel icon={ScanLine} text="Imaging workspace" />
                <h3 className="modern-display mt-7 text-3xl font-semibold tracking-[-0.04em]">See beyond the report.</h3>
                <p className="mt-3 max-w-md text-sm leading-6 text-[#8795aa]">DICOM review, ROI prompts, and segmentation-assisted draft reporting in one dark-room-ready view.</p>
              </div>
              <div className="absolute -bottom-20 right-[-8%] h-64 w-64 rounded-full border border-[#7da8ff]/25 bg-[radial-gradient(circle_at_48%_47%,#bfd4ff_0%,#526a94_8%,#111a2a_28%,#070b12_64%)] shadow-[0_0_70px_rgba(84,124,202,0.18)] transition duration-700 group-hover:-translate-x-3 group-hover:-translate-y-3 group-hover:scale-105">
                <div className="modern-scan-line absolute inset-x-6 top-1/2 h-px bg-[#7da8ff] shadow-[0_0_12px_#7da8ff]" />
                <div className="absolute left-[39%] top-[34%] h-12 w-14 rounded-[50%] border border-[#3b82f6] shadow-[0_0_20px_rgba(59,130,246,0.7)]" />
              </div>
            </article>

            <article className="modern-bento group relative overflow-hidden rounded-3xl border border-white/[0.09] bg-[#0b1220] p-6 lg:col-span-5 sm:p-8">
              <BentoGlow />
              <div className="relative z-10 flex h-full flex-col">
                <FeatureLabel icon={Mic} text="Live consultation" />
                <h3 className="modern-display mt-7 text-3xl font-semibold tracking-[-0.04em]">Capture context, not just audio.</h3>
                <div className="mt-auto flex h-16 items-center gap-1.5 pt-6">
                  {Array.from({ length: 22 }).map((_, index) => (
                    <span
                      key={index}
                      className="modern-wave-bar flex-1 rounded-full bg-gradient-to-t from-[#0052cc] to-[#60a5fa]"
                      style={{ '--bar-index': index } as CSSProperties}
                    />
                  ))}
                </div>
              </div>
            </article>
          </div>
        </div>
      </section>

      <section className="border-y border-white/[0.08] bg-[#050912] px-5 py-20 sm:px-8 lg:px-12 lg:py-28">
        <div className="mx-auto w-full max-w-[1340px]">
          <SectionHeading
            eyebrow="Built around the encounter"
            title={<>Context that moves<br /><span className="modern-gradient-text">with the patient.</span></>}
            body="Choose a moment in the care journey to see how Medtrace keeps the clinical thread intact."
          />

          <div className="mt-12 grid overflow-hidden rounded-3xl border border-white/[0.09] bg-[#0a101c] lg:grid-cols-[0.72fr_1.28fr]">
            <div className="border-b border-white/[0.08] p-3 lg:border-b-0 lg:border-r">
              {WORKFLOW.map((step, index) => {
                const Icon = step.icon;
                const active = workflowStep === index;
                return (
                  <button
                    type="button"
                    key={step.label}
                    onClick={() => setWorkflowStep(index)}
                    className={`group flex w-full items-center gap-4 rounded-2xl border px-4 py-4 text-left transition duration-300 sm:px-5 ${active ? 'border-[#3b82f6]/25 bg-[#3b82f6]/[0.09]' : 'border-transparent hover:bg-white/[0.035]'}`}
                  >
                    <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl transition ${active ? 'bg-[#0052cc] text-white' : 'bg-white/[0.06] text-[#7c899e] group-hover:text-white'}`}>
                      <Icon size={17} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className={`block text-sm font-semibold ${active ? 'text-white' : 'text-[#8b98ad]'}`}>{step.label}</span>
                      <span className="mt-1 block truncate text-[11px] text-[#59667a]">0{index + 1} / Clinical workflow</span>
                    </span>
                    <ChevronRight size={16} className={`transition ${active ? 'translate-x-0 text-[#60a5fa]' : '-translate-x-1 text-[#445064] group-hover:translate-x-0'}`} />
                  </button>
                );
              })}
            </div>

            <div key={workflowStep} className="modern-panel-enter relative min-h-[430px] overflow-hidden p-7 sm:p-10 lg:p-14">
              <div className="absolute right-[-5%] top-[-20%] h-80 w-80 rounded-full bg-[#526fff]/10 blur-[90px]" aria-hidden="true" />
              <div className="relative z-10 flex h-full flex-col">
                <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-[#60a5fa]">{WORKFLOW[workflowStep].label}</p>
                <h3 className="modern-display mt-5 max-w-2xl text-4xl font-semibold leading-[1.04] tracking-[-0.045em] sm:text-5xl">{WORKFLOW[workflowStep].title}</h3>
                <p className="mt-6 max-w-2xl text-sm leading-7 text-[#8795aa] sm:text-base">{WORKFLOW[workflowStep].body}</p>
                <div className="mt-auto grid gap-3 pt-12 sm:grid-cols-[0.7fr_1.3fr]">
                  <div className="rounded-2xl border border-[#3b82f6]/20 bg-[#3b82f6]/[0.07] p-5">
                    <p className="modern-display text-4xl font-semibold text-[#93c5fd]">{WORKFLOW[workflowStep].stat}</p>
                    <p className="mt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-[#657389]">{WORKFLOW[workflowStep].statLabel}</p>
                  </div>
                  <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-5">
                    <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-[#6f7d92]">
                      <ShieldCheck size={13} className="text-[#60a5fa]" />
                      Guardrail active
                    </div>
                    <p className="mt-4 text-sm leading-6 text-[#a0acbd]">Every output remains source-linked and explicitly framed as non-diagnostic decision support.</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="relative bg-[#070c15] px-5 py-20 sm:px-8 lg:px-12 lg:py-28">
        <div className="mx-auto grid w-full max-w-[1340px] overflow-hidden rounded-[2rem] border border-white/[0.09] bg-[#0b1220] lg:grid-cols-[1.08fr_0.92fr]">
          <div className="relative overflow-hidden border-b border-white/[0.08] p-7 sm:p-10 lg:border-b-0 lg:border-r lg:p-14">
            <div className="absolute -left-32 -top-32 h-80 w-80 rounded-full bg-[#3b82f6]/15 blur-[90px]" />
            <div className="relative">
              <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.18em] text-[#60a5fa]">
                <LockKeyhole size={13} />
                Clinician-controlled by design
              </div>
              <h2 className="modern-display mt-6 max-w-2xl text-4xl font-semibold leading-[1.02] tracking-[-0.045em] sm:text-6xl">
                Intelligence with a visible boundary.
              </h2>
              <p className="mt-6 max-w-xl text-sm leading-7 text-[#8795aa]">
                Medtrace is an educational demo and cognitive aid—not a certified medical device. AI and vision output can be incomplete or wrong and must be reviewed by a clinician.
              </p>
            </div>
          </div>
          <div className="grid sm:grid-cols-2">
            <TrustTile icon={Database} title="FHIR canonical" text="Clinical data remains in Medplum FHIR R4." />
            <TrustTile icon={FileCheck2} title="Source-linked" text="Evidence stays inspectable and traceable." />
            <TrustTile icon={ShieldCheck} title="Human review" text="Generated facts remain visibly unverified." />
            <TrustTile icon={CheckCircle2} title="Graceful fallback" text="Core views work when AI services are offline." />
          </div>
        </div>
      </section>

      <footer className="border-t border-white/[0.08] bg-[#050912] px-5 pb-8 pt-20 sm:px-8 lg:px-12 lg:pt-28">
        <div className="mx-auto w-full max-w-[1340px]">
          <div className="flex flex-col gap-10 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="flex items-center gap-2 text-xs font-semibold text-[#60a5fa]">
                <Sparkles size={14} />
                Ready when the chart isn’t.
              </p>
              <h2 className="modern-display mt-5 max-w-4xl text-5xl font-semibold leading-[0.95] tracking-[-0.055em] sm:text-7xl">
                See the story.<br /><span className="text-white/35">Start prepared.</span>
              </h2>
            </div>
            <Link
              to="/patients"
              className="group inline-flex h-14 shrink-0 items-center justify-center gap-3 self-start rounded-xl bg-[#0052cc] px-6 text-sm font-bold text-white shadow-[0_18px_50px_-20px_rgba(59,130,246,0.8)] transition hover:-translate-y-1 hover:bg-[#2563eb] lg:self-auto"
            >
              Open Medtrace
              <ArrowRight size={17} className="transition-transform group-hover:translate-x-1" />
            </Link>
          </div>
          <div className="mt-20 flex flex-col gap-4 border-t border-white/[0.08] pt-6 text-[10px] uppercase tracking-[0.12em] text-[#4f5c70] sm:flex-row sm:items-center sm:justify-between">
            <span className="flex items-center gap-2 font-bold text-white/60"><Stethoscope size={14} className="text-[#60a5fa]" />Medtrace clinical intelligence</span>
            <span>Demo system · Non-diagnostic cognitive aid</span>
          </div>
        </div>
      </footer>
    </div>
  );
}

function ProductStage({ workspace, onWorkspaceChange }: { workspace: WorkspaceMode; onWorkspaceChange: (mode: WorkspaceMode) => void }) {
  return (
    <div className="overflow-hidden rounded-[1.35rem] border border-white/[0.12] bg-[#090f1b]/95 shadow-[0_45px_120px_-35px_rgba(0,0,0,0.9),0_0_0_1px_rgba(59,130,246,0.08)] backdrop-blur-xl">
      <div className="flex h-11 items-center justify-between border-b border-white/[0.08] bg-white/[0.025] px-4">
        <div className="flex gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-[#ff6b69]/70" /><span className="h-2.5 w-2.5 rounded-full bg-[#ffc75d]/70" /><span className="h-2.5 w-2.5 rounded-full bg-[#3b82f6]/80" /></div>
        <div className="flex items-center gap-2 rounded-md border border-white/[0.07] bg-black/20 px-3 py-1 text-[9px] font-medium text-[#69768a]"><LockKeyhole size={9} />medtrace.local / clinical-workspace</div>
        <div className="flex items-center gap-1.5 text-[9px] font-semibold text-[#60a5fa]"><Radio size={10} /> LIVE</div>
      </div>

      <div className="grid min-h-[440px] lg:grid-cols-[225px_1fr]">
        <aside className="border-b border-white/[0.08] bg-[#070c15] p-3 lg:border-b-0 lg:border-r">
          <div className="flex items-center gap-2.5 px-2 py-2 text-xs font-bold"><span className="grid h-7 w-7 place-items-center rounded-lg bg-[#0052cc] text-white"><Stethoscope size={14} /></span>Medtrace</div>
          <div className="mt-5 grid grid-cols-3 gap-1 lg:grid-cols-1">
            {WORKSPACES.map(({ id, label, detail, icon: Icon }) => {
              const active = id === workspace;
              return (
                <button key={id} type="button" onClick={() => onWorkspaceChange(id)} className={`group flex min-w-0 items-center gap-3 rounded-xl border px-2.5 py-2.5 text-left transition duration-300 ${active ? 'border-[#3b82f6]/25 bg-[#3b82f6]/[0.1]' : 'border-transparent hover:bg-white/[0.04]'}`}>
                  <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${active ? 'bg-[#0052cc] text-white' : 'bg-white/[0.06] text-[#647187] group-hover:text-white'}`}><Icon size={14} /></span>
                  <span className="hidden min-w-0 lg:block"><span className={`block truncate text-[11px] font-semibold ${active ? 'text-white' : 'text-[#7c899d]'}`}>{label}</span><span className="mt-0.5 block truncate text-[8px] text-[#4e5b6d]">{detail}</span></span>
                </button>
              );
            })}
          </div>
          <div className="mt-8 hidden rounded-xl border border-white/[0.07] bg-white/[0.025] p-3 lg:block">
            <div className="flex items-center gap-2 text-[9px] font-semibold text-[#91a0b4]"><ShieldCheck size={12} className="text-[#60a5fa]" />FHIR connection</div>
            <div className="mt-3 h-1 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full w-[88%] rounded-full bg-gradient-to-r from-[#0052cc] to-[#60a5fa]" /></div>
            <p className="mt-2 font-mono text-[8px] text-[#4e5b6d]">12 / 12 resources synced</p>
          </div>
        </aside>
        <main key={workspace} className="modern-panel-enter min-w-0 bg-[#0a101c] p-3 sm:p-5">
          {workspace === 'brief' ? <BriefWorkspace /> : workspace === 'imaging' ? <ImagingWorkspacePreview /> : <SessionWorkspacePreview />}
        </main>
      </div>
    </div>
  );
}

function BriefWorkspace() {
  return (
    <div className="grid h-full gap-3 xl:grid-cols-[1.35fr_0.65fr]">
      <div className="space-y-3">
        <div className="flex flex-col justify-between gap-3 rounded-2xl border border-white/[0.08] bg-[#0e1625] p-4 sm:flex-row sm:items-center">
          <div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-[#0052cc]/40 to-[#60a5fa]/20 text-sm font-bold text-[#bfdbfe]">AP</span><div><p className="text-xs font-bold">Sample patient · Cardiology follow-up</p><p className="mt-1 text-[9px] text-[#657288]">64 years · 18-month clinical timeline</p></div></div>
          <div className="flex items-center gap-2 rounded-full border border-[#3b82f6]/20 bg-[#3b82f6]/[0.08] px-2.5 py-1 text-[8px] font-bold uppercase tracking-wider text-[#60a5fa]"><Sparkles size={9} />Brief ready</div>
        </div>
        <div className="grid grid-cols-3 gap-2"><MiniMetric value="12" label="Sources" /><MiniMetric value="04" label="Changes" /><MiniMetric value="03" label="Open items" alert /></div>
        <div className="rounded-2xl border border-white/[0.08] bg-[#0e1625] p-4">
          <div className="flex items-center justify-between"><p className="text-[9px] font-bold uppercase tracking-[0.16em] text-[#69768b]">Priority signals</p><span className="font-mono text-[8px] text-[#4e5b6e]">Updated now</span></div>
          <div className="mt-3 space-y-2"><SignalRow tone="brand" label="Trend" value="A1c rose across two recent results" /><SignalRow tone="blue" label="Evidence" value="New exertional symptoms documented" /><SignalRow tone="amber" label="Review" value="Medication reconciliation incomplete" /></div>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
        <div className="rounded-2xl border border-white/[0.08] bg-[#0e1625] p-4"><p className="text-[9px] font-bold uppercase tracking-[0.16em] text-[#69768b]">Risk focus</p><div className="mt-5 flex items-end justify-between"><div><p className="modern-display text-4xl font-semibold">3</p><p className="mt-1 text-[9px] text-[#657288]">items to review</p></div><div className="grid h-14 w-14 place-items-center rounded-full border-[5px] border-[#3b82f6]/20 border-t-[#3b82f6] text-[10px] font-bold text-[#93c5fd]">68%</div></div></div>
        <div className="rounded-2xl border border-white/[0.08] bg-[#0e1625] p-4"><p className="text-[9px] font-bold uppercase tracking-[0.16em] text-[#69768b]">Next visit</p><div className="mt-5 flex items-center gap-3"><span className="grid h-9 w-9 place-items-center rounded-lg bg-[#526fff]/15 text-[#8da8ff]"><Clock3 size={15} /></span><div><p className="text-[11px] font-semibold">Today · 2:30 PM</p><p className="mt-1 text-[8px] text-[#657288]">Preparation complete</p></div></div></div>
      </div>
    </div>
  );
}

function ImagingWorkspacePreview() {
  return (
    <div className="grid h-full gap-3 lg:grid-cols-[1fr_250px]">
      <div className="relative min-h-[330px] overflow-hidden rounded-2xl border border-white/[0.08] bg-[#03060b]">
        <div className="absolute left-4 top-4 z-10 rounded-lg border border-white/10 bg-black/50 px-2.5 py-1.5 font-mono text-[8px] text-white/60 backdrop-blur">AXIAL · SLICE 34 / 72</div>
        <div className="absolute inset-8 rounded-full bg-[radial-gradient(ellipse_at_center,#d0d7e3_0%,#6e7787_9%,#252c37_26%,#0b0f16_48%,transparent_68%)] opacity-90" />
        <div className="absolute left-[47%] top-[42%] h-20 w-24 rounded-[50%] border-2 border-[#3b82f6] bg-[#3b82f6]/10 shadow-[0_0_35px_rgba(59,130,246,0.45)]" />
        <div className="modern-scan-line absolute inset-x-0 top-1/2 h-px bg-[#3b82f6]/70 shadow-[0_0_15px_#3b82f6]" />
        <div className="absolute bottom-4 left-4 flex gap-2"><span className="rounded-md bg-[#526fff] px-2 py-1 text-[8px] font-bold">ROI 01</span><span className="rounded-md border border-white/10 bg-black/50 px-2 py-1 text-[8px] text-white/60">Confidence 94%</span></div>
      </div>
      <div className="space-y-3"><div className="rounded-2xl border border-white/[0.08] bg-[#0e1625] p-4"><p className="text-[9px] font-bold uppercase tracking-[0.16em] text-[#69768b]">Segmentation</p><div className="mt-4 flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-xl bg-[#3b82f6]/10 text-[#60a5fa]"><ScanLine size={17} /></span><div><p className="text-[11px] font-semibold">MedSAM2 mask</p><p className="mt-1 text-[8px] text-[#657288]">Draft · review required</p></div></div></div><div className="rounded-2xl border border-white/[0.08] bg-[#0e1625] p-4"><p className="text-[9px] font-bold uppercase tracking-[0.16em] text-[#69768b]">Study</p><p className="mt-4 text-sm font-semibold">MR · Brain</p><p className="mt-2 text-[9px] leading-5 text-[#657288]">72 slices<br />1 active region<br />Draft report pending</p></div></div>
    </div>
  );
}

function SessionWorkspacePreview() {
  return (
    <div className="grid h-full gap-3 lg:grid-cols-[1.15fr_0.85fr]">
      <div className="flex min-h-[330px] flex-col rounded-2xl border border-white/[0.08] bg-[#0e1625] p-5"><div className="flex items-center justify-between"><p className="flex items-center gap-2 text-[10px] font-semibold"><span className="h-2 w-2 animate-pulse rounded-full bg-[#ff6b69]" />Recording consultation</p><span className="font-mono text-[9px] text-[#657288]">08:42</span></div><div className="my-auto flex h-24 items-center gap-1.5">{Array.from({ length: 34 }).map((_, index) => <span key={index} className="modern-wave-bar flex-1 rounded-full bg-gradient-to-t from-[#0052cc] to-[#60a5fa]" style={{ '--bar-index': index } as CSSProperties} />)}</div><div className="rounded-xl border border-white/[0.07] bg-black/20 p-4"><p className="flex items-center gap-2 text-[8px] font-bold uppercase tracking-wider text-[#60a5fa]"><MessageSquareText size={11} />Live transcript</p><p className="mt-3 text-[11px] leading-5 text-[#a2aec0]">“The symptoms started after the medication change, mostly when walking uphill...”</p></div></div>
      <div className="space-y-3"><div className="rounded-2xl border border-[#3b82f6]/20 bg-[#3b82f6]/[0.07] p-4"><p className="text-[9px] font-bold uppercase tracking-[0.16em] text-[#60a5fa]">Context match</p><p className="mt-4 text-xs font-semibold">Medication change · 3 months ago</p><p className="mt-2 text-[9px] leading-5 text-[#718096]">Linked from the patient timeline while the conversation continues.</p></div><div className="rounded-2xl border border-white/[0.08] bg-[#0e1625] p-4"><p className="text-[9px] font-bold uppercase tracking-[0.16em] text-[#69768b]">Session state</p><div className="mt-4 space-y-3"><StatusLine label="Audio" value="Connected" /><StatusLine label="Transcript" value="Live" /><StatusLine label="Report" value="Drafting" /></div></div></div>
    </div>
  );
}

function SectionHeading({ eyebrow, title, body }: { eyebrow: string; title: ReactNode; body: string }) {
  return <div className="grid gap-7 lg:grid-cols-[1fr_0.62fr] lg:items-end"><div><p className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.2em] text-[#60a5fa]"><Zap size={12} />{eyebrow}</p><h2 className="modern-display mt-5 text-5xl font-semibold leading-[0.96] tracking-[-0.055em] sm:text-7xl">{title}</h2></div><p className="max-w-xl text-sm leading-7 text-[#7f8ca0] lg:justify-self-end">{body}</p></div>;
}

function FeatureLabel({ icon: Icon, text }: { icon: typeof Activity; text: string }) {
  return <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.16em] text-[#7f8da2]"><span className="grid h-8 w-8 place-items-center rounded-lg border border-white/[0.08] bg-white/[0.04] text-[#60a5fa]"><Icon size={14} /></span>{text}</div>;
}

function SignalRow({ tone, label, value }: { tone: 'brand' | 'blue' | 'amber'; label: string; value: string }) {
  const styles = { brand: 'bg-[#3b82f6]/12 text-[#60a5fa]', blue: 'bg-[#0052cc]/15 text-[#93c5fd]', amber: 'bg-[#ffbd66]/10 text-[#ffbd66]' };
  return <div className="group flex items-center gap-3 rounded-xl border border-white/[0.065] bg-white/[0.025] p-2.5 transition hover:border-white/[0.13] hover:bg-white/[0.045]"><span className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg ${styles[tone]}`}><Activity size={12} /></span><div className="min-w-0"><p className="text-[8px] font-bold uppercase tracking-[0.14em] text-[#59667a]">{label}</p><p className="mt-1 truncate text-[10px] font-medium text-[#a6b1c1]">{value}</p></div></div>;
}

function MiniMetric({ value, label, alert = false }: { value: string; label: string; alert?: boolean }) {
  return <div className="rounded-xl border border-white/[0.07] bg-[#0e1625] p-3"><p className={`modern-display text-2xl font-semibold ${alert ? 'text-[#ffbd66]' : 'text-white'}`}>{value}</p><p className="mt-1 text-[7px] font-bold uppercase tracking-[0.12em] text-[#586579]">{label}</p></div>;
}

function StatusLine({ label, value }: { label: string; value: string }) {
  return <div className="flex items-center justify-between text-[9px]"><span className="text-[#657288]">{label}</span><span className="flex items-center gap-1.5 font-semibold text-[#a1adbe]"><span className="h-1.5 w-1.5 rounded-full bg-[#3b82f6] shadow-[0_0_8px_#3b82f6]" />{value}</span></div>;
}

function BentoGlow({ blue = false }: { blue?: boolean }) {
  return <div className={`pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full opacity-0 blur-[80px] transition-opacity duration-500 group-hover:opacity-100 ${blue ? 'bg-[#2563eb]/25' : 'bg-[#3b82f6]/20'}`} aria-hidden="true" />;
}

function TrustTile({ icon: Icon, title, text }: { icon: typeof Activity; title: string; text: string }) {
  return <div className="group min-h-[190px] border-b border-white/[0.08] p-6 transition hover:bg-white/[0.025] sm:[&:nth-child(odd)]:border-r sm:[&:nth-child(n+3)]:border-b-0 lg:p-8"><span className="grid h-10 w-10 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.04] text-[#60a5fa] transition group-hover:scale-110 group-hover:bg-[#0052cc] group-hover:text-white"><Icon size={17} /></span><h3 className="mt-6 text-sm font-semibold">{title}</h3><p className="mt-2 text-xs leading-5 text-[#6f7c90]">{text}</p></div>;
}
