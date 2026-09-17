// Salesforce push client. Same-origin via the Next rewrite; apiFetch adds auth + CSRF.
import { apiFetch } from "./auth";

export type SfStatus = { configured: boolean };
export type SfPrefill = { case_number: string | null; account_name?: string | null };
export type SfFile = { title: string; ext: string | null; size: number | null };
export type SfResolve = {
  account_id: string;
  account_name: string | null;
  is_person_account: boolean;
  files: SfFile[];
};
export type SfPushResult = {
  status: "uploaded" | "duplicate";
  content_version_id?: string;
  account_name: string | null;
  account_id: string;
};

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.title || `HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}

export async function sfStatus(): Promise<SfStatus> {
  return json(await apiFetch("/api/salesforce/status"));
}

export async function sfPrefill(first: string, last: string): Promise<SfPrefill> {
  const q = new URLSearchParams({ first, last }).toString();
  return json(await apiFetch(`/api/salesforce/prefill?${q}`));
}

export async function sfResolve(caseNumber: string): Promise<SfResolve> {
  return json(await apiFetch("/api/salesforce/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ case_number: caseNumber }),
  }));
}

export async function sfPush(pdfId: number, caseNumber: string): Promise<SfPushResult> {
  return json(await apiFetch(`/api/pdfs/${pdfId}/salesforce`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ case_number: caseNumber }),
  }));
}
