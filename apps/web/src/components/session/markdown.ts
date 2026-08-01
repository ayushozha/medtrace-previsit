/**
 * Markdown ⇄ HTML conversion for the tiptap clinical canvas.
 *
 * The agent's `state.document` is markdown; the editor speaks HTML. Both directions are
 * needed so the document survives a round trip: `editor.getText()` is not an option
 * because it strips every markdown marker, which turns the next agent turn into a diff
 * of prose against syntax.
 */
import MarkdownIt from 'markdown-it';
import TurndownService from 'turndown';

const md = new MarkdownIt({ typographer: true, html: true });

const turndown = new TurndownService({
  headingStyle: 'atx',
  bulletListMarker: '-',
  codeBlockStyle: 'fenced',
  emDelimiter: '*',
});

/** Markdown → HTML string for tiptap's `setContent`, which needs a string, not React nodes. */
export function fromMarkdown(text: string): string {
  return md.render(text);
}

/** Editor HTML → markdown, for persisting clinician edits and handing state to the agent. */
export function toMarkdown(html: string): string {
  return turndown.turndown(html).trim();
}
