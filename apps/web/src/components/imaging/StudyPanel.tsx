import { type ChangeEvent, useRef } from 'react';
import { CircleDot, FileImage } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ReviewDecision, Study } from '@/lib/types';

const DECISION_COLOR: Record<ReviewDecision, string> = {
  accepted: 'text-emerald-300',
  'needs-correction': 'text-red-300',
  unreviewed: 'text-amber-300',
};

function StatusDot({ decision }: { decision: ReviewDecision }) {
  return <CircleDot className={cn('mt-0.5 h-4 w-4', DECISION_COLOR[decision])} />;
}

interface StudyPanelProps {
  studies: Study[];
  activeStudyId: string;
  onFiles: (files: File[]) => void;
  onSelectStudy: (studyId: string) => void;
}

export function StudyPanel({ studies, activeStudyId, onFiles, onSelectStudy }: StudyPanelProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length > 0) {
      onFiles(files);
      event.target.value = '';
    }
  };

  return (
    <aside className="flex min-h-0 flex-col border-r border-slate-800 bg-[#080d15] max-lg:min-h-[360px]">
      <header className="border-b border-slate-800 px-4 py-4">
        <input
          ref={fileInputRef}
          className="hidden"
          type="file"
          multiple
          accept=".dcm,.dicom,application/dicom"
          onChange={handleFileInput}
        />
        <button
          className="flex w-full items-center justify-center gap-2 rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-400/15"
          type="button"
          onClick={() => fileInputRef.current?.click()}
        >
          <FileImage className="h-4 w-4" />
          Upload DICOM
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {studies.length === 0 ? (
          <article className="rounded-md border border-slate-800 bg-[#0c1420] p-3 text-sm leading-6 text-slate-400">
            No DICOM uploaded.
          </article>
        ) : (
          <div className="space-y-2">
            {studies.map((study) => (
              <button
                key={study.id}
                className={cn(
                  'w-full rounded-md border px-2.5 py-2 text-left transition',
                  activeStudyId === study.id
                    ? 'border-cyan-300/45 bg-cyan-400/10 shadow-[0_0_22px_rgba(59,130,246,0.36)]'
                    : 'border-slate-800 bg-[#0c1420] hover:border-slate-600',
                )}
                type="button"
                onClick={() => onSelectStudy(study.id)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-white">{study.patient_name}</p>
                    <p className="mt-0.5 truncate text-[11px] text-slate-500">
                      {study.modality} {study.body_part}
                    </p>
                  </div>
                  <StatusDot decision={study.reviewDecision} />
                </div>
                <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-slate-500">
                  <span className="truncate">{study.uploaded_file_name ?? study.id}</span>
                  <span>{study.timestamp}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
