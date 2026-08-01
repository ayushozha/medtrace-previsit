import { useEffect, useState } from 'react';
import { MessageSquareText } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import type { Study } from '@/lib/types';

interface FeedbackDialogProps {
  open: boolean;
  study: Study;
  onOpenChange: (open: boolean) => void;
  onSave: (note: string) => void;
}

/**
 * Correction note for a draft report. Uses the shared Dialog primitive rather than the
 * hand-rolled modal the standalone radiology app carried.
 */
export function FeedbackDialog({ open, study, onOpenChange, onSave }: FeedbackDialogProps) {
  const [note, setNote] = useState(study.reviewNote ?? '');

  // Re-seed from the study whenever the dialog is (re)opened.
  useEffect(() => {
    if (open) setNote(study.reviewNote ?? '');
  }, [open, study.reviewNote]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <MessageSquareText className="h-5 w-5 text-amber-500" />
            Doctor feedback
          </DialogTitle>
          <DialogDescription>
            {study.patient_name} • {study.modality} {study.body_part}
          </DialogDescription>
        </DialogHeader>

        <Textarea
          className="h-36 resize-none"
          placeholder="Record what needs correction before this can be accepted..."
          value={note}
          autoFocus
          onChange={(event) => setNote(event.target.value)}
        />

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              onSave(note.trim());
              onOpenChange(false);
            }}
          >
            Save note
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
