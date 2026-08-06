import type { Pdf } from "./app-provider";

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtDate(s: string): string {
  const iso = s.includes("T") ? s : s.replace(" ", "T") + "Z";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function fmtRelative(s: string): string {
  // ponytail: backend now always sends ISO with T separator and offset
  // (e.g. "2026-08-01T21:23:36-04:00"). If a legacy string sneaks through
  // without a zone marker, treat it as America/New_York local — the old
  // `+ "Z"` hack tagged it as UTC and shifted the display by 4-5 hours.
  const iso = s.includes("T") ? s : s.replace(" ", "T");
  const then = new Date(iso).getTime();
  const now = Date.now();
  const delta = Math.max(0, now - then) / 1000;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)}h ago`;
  return `${Math.round(delta / 86400)}d ago`;
}

export function ftypeClass(assessment: string | null | undefined): string {
  if (!assessment) return "ftype-etc";
  if (assessment.startsWith("O_NET")) return "ftype-onet";
  if (assessment.startsWith("VIA")) return "ftype-via";
  return "ftype-etc";
}

export function ftypeLabel(assessment: string | null | undefined): string {
  if (!assessment) return "?";
  if (assessment.startsWith("O_NET")) return "ONET";
  if (assessment.startsWith("VIA")) return "VIA";
  return "PDF";
}

export function displayName(p: Pdf): string {
  const n = [p.first_name, p.last_name].filter(Boolean).join(" ");
  return n || "Unknown Client";
}

/** Turn a raw assessment_type from the classifier (e.g. "O_NET_Interest_Profiler")
 *  into a human-friendly label ("O*NET Interest Profiler"). */
export function formatAssessmentType(t: string | null | undefined): string {
  if (!t) return "Unclassified";
  return t.replace(/_/g, " ").replace(/^O NET/, "O*NET");
}
