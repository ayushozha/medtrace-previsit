import { FileSymlink, Loader2, Upload } from 'lucide-react';
import { useRef } from 'react';
import { useUploadDocument } from '@/hooks/useUploadDocument';
import type { DocumentRecord } from '@/lib/types';

interface DocumentLibraryProps {
  documents: DocumentRecord[];
  patientId: string;
  onUploaded?: () => void;
}

export function DocumentLibrary({ documents, patientId, onUploaded }: DocumentLibraryProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { uploading, error, upload } = useUploadDocument();

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';
    try {
      await upload(patientId, file);
      onUploaded?.();
    } catch {
      /* error surfaced via state below */
    }
  };

  return (
    <section className="clinical-panel flex flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border p-4">
        <h2 className="clinical-section-title">Memory Sources</h2>
        <div className="flex items-center gap-2">
          <span className="rounded-md bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-500">
            {documents.length}
          </span>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-semibold text-slate-600 hover:border-blue-200 hover:bg-blue-50 hover:text-primary disabled:opacity-60"
          >
            {uploading ? <Loader2 size={11} className="animate-spin" /> : <Upload size={11} />}
            Upload
          </button>
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFile}
            className="hidden"
            accept=".pdf,.txt"
          />
        </div>
      </div>

      {error && (
        <div className="border-b border-red-100 bg-red-50 px-4 py-2 text-[11px] text-red-700">{error}</div>
      )}

      <div className="space-y-1 p-2">
        {documents.length === 0 ? (
          <p className="rounded-md border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-[11px] text-slate-500">
            No documents uploaded yet. Click <span className="font-semibold">Upload</span> to ingest a PDF or note.
          </p>
        ) : (
          documents.map((doc, i) => {
            const filenameDisplay = doc.filename;
            const reviewBadgeClass =
              doc.review_status === 'Approved'
                ? 'border-green-200 bg-green-50 text-green-700'
                : 'border-yellow-200 bg-yellow-50 text-yellow-800';
            const iconClass = doc.document_kind.includes('clinical')
              ? 'bg-blue-50 text-primary'
              : doc.document_kind.includes('radiology')
                ? 'bg-yellow-50 text-yellow-800'
                : 'bg-slate-50 text-slate-600';
            const Wrapper: React.ElementType = doc.storage_url ? 'a' : 'div';
            const wrapperProps: Record<string, unknown> = doc.storage_url
              ? { href: doc.storage_url, target: '_blank', rel: 'noopener noreferrer' }
              : {};
            return (
              <Wrapper
                key={doc.doc_id}
                {...wrapperProps}
                className={`flex cursor-pointer gap-3 rounded-lg border border-transparent p-2.5 transition-colors hover:border-slate-100 hover:bg-slate-50 ${
                  i > 1 ? 'opacity-80' : ''
                }`}
              >
                <div
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded ${iconClass}`}
                >
                  <FileSymlink size={16} />
                </div>
                <div className="truncate flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-xs font-semibold text-slate-800">{filenameDisplay}</p>
                      <p className="text-[10px] text-slate-400">
                        {doc.uploaded_at} - {doc.document_kind}
                      </p>
                    </div>
                    <span
                      className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[9px] font-semibold ${reviewBadgeClass}`}
                    >
                      {doc.review_status}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center justify-between text-[10px] text-slate-500">
                    <span>{doc.episode_count} facts extracted</span>
                    <span>{doc.status}</span>
                  </div>
                </div>
              </Wrapper>
            );
          })
        )}
      </div>
    </section>
  );
}
