import { parseWorkflowDocument, type WorkflowDocument } from '@shared/lib/workflow';

export type WorkflowScriptResult =
  | { ok: true; workflow: WorkflowDocument }
  | { ok: false; errors: string[] };

/**
 * Read a workflow "script" (a JSON package) and validate it before it can reach
 * the canvas or the server. Validation is the shared one, so a script that loads
 * is exactly a script that may run (plan Review Focus 3).
 */
export function loadWorkflowScript(raw: string): WorkflowScriptResult {
  if (typeof raw !== 'string' || raw.trim() === '') {
    return { ok: false, errors: ['workflow script is empty'] };
  }
  return parseWorkflowDocument(raw);
}

/** File wrapper used by the page's file picker. */
export async function loadWorkflowScriptFile(file: File): Promise<WorkflowScriptResult> {
  if (!file) return { ok: false, errors: ['no file selected'] };
  if (file.size > 512 * 1024) {
    return { ok: false, errors: ['workflow script is larger than 512 KB'] };
  }
  try {
    return loadWorkflowScript(await file.text());
  } catch (error) {
    return { ok: false, errors: [`could not read the file: ${(error as Error).message}`] };
  }
}
