import { useMemo, useState } from 'react';
import { diffWords } from 'diff';

/**
 * Word-level preview of the agent's proposed rewrite.
 *
 * Rendered as React nodes rather than an HTML string: a diff of markdown text cannot be
 * fed back through a markdown renderer, because the inserted tags sit at line starts and
 * stop `#`/`-`/`>` from being parsed as block syntax.
 */
function ChangePreview({ previous, proposed }: { previous: string; proposed: string }) {
  const parts = useMemo(() => diffWords(previous, proposed), [previous, proposed]);

  return (
    <div className="confirm-changes-diff" aria-label="Proposed changes">
      {parts.map((part, idx) =>
        part.added ? (
          <ins key={idx} className="confirm-changes-diff-add">
            {part.value}
          </ins>
        ) : part.removed ? (
          <del key={idx} className="confirm-changes-diff-del">
            {part.value}
          </del>
        ) : (
          <span key={idx}>{part.value}</span>
        ),
      )}
    </div>
  );
}

interface ConfirmChangesProps {
  respond?: ((payload: { accepted: boolean }) => void) | undefined;
  previousMarkdown: string;
  proposedMarkdown: string;
  onReject: () => void;
  onConfirm: () => void;
}

/** Human-in-the-loop gate: the agent's document edits need explicit clinician approval. */
export function ConfirmChanges({
  respond,
  previousMarkdown,
  proposedMarkdown,
  onReject,
  onConfirm,
}: ConfirmChangesProps) {
  const [accepted, setAccepted] = useState<boolean | null>(null);

  const decide = (value: boolean) => {
    if (!respond) return;
    setAccepted(value);
    if (value) onConfirm();
    else onReject();
    respond({ accepted: value });
  };

  const hasDiff = proposedMarkdown.trim().length > 0 && proposedMarkdown !== previousMarkdown;

  return (
    <div data-testid="confirm-changes-modal" className="confirm-changes-modal">
      <h2 className="confirm-changes-title">
        <span aria-hidden="true">⚡</span> Confirm Proposed Changes
      </h2>
      <p className="confirm-changes-body">
        Would you like to accept the new changes proposed by the AI editor?
      </p>
      {accepted === null && hasDiff && (
        <ChangePreview previous={previousMarkdown} proposed={proposedMarkdown} />
      )}
      {accepted === null ? (
        <div className="confirm-changes-actions">
          <button type="button" disabled={!respond} onClick={() => decide(false)}>
            Reject
          </button>
          <button type="button" disabled={!respond} onClick={() => decide(true)}>
            Confirm
          </button>
        </div>
      ) : (
        <div className="confirm-changes-feedback-wrap">
          <div
            className={`confirm-changes-feedback ${
              accepted ? 'confirm-changes-feedback--accepted' : 'confirm-changes-feedback--rejected'
            }`}
          >
            {accepted ? '✓ Accepted' : '✗ Rejected'}
          </div>
        </div>
      )}
    </div>
  );
}
