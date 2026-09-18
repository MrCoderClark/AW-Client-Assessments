// Multi-office locations admin client. Same-origin via the Next rewrite.
import { apiFetch } from "./auth";

export type Location = { id: number; code: string; name: string; active: boolean };
export type Alias = { office_value: string; location_id: number };
export type PcLoc = { pc_name: string; host: string; location_id: number };

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.title || `HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}
const jbody = (v: unknown) => ({ headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) });

export async function listLocations(): Promise<Location[]> {
  return json(await apiFetch("/api/locations"));
}
export async function createLocation(code: string, name: string): Promise<Location> {
  return json(await apiFetch("/api/locations", { method: "POST", ...jbody({ code, name }) }));
}
export async function patchLocation(id: number, patch: Partial<Pick<Location, "name" | "active">>): Promise<Location> {
  return json(await apiFetch(`/api/locations/${id}`, { method: "PATCH", ...jbody(patch) }));
}

export async function listAliases(): Promise<Alias[]> {
  return json(await apiFetch("/api/locations/aliases"));
}
export async function addAlias(office_value: string, location_id: number): Promise<Alias> {
  return json(await apiFetch("/api/locations/aliases", { method: "POST", ...jbody({ office_value, location_id }) }));
}
export async function deleteAlias(office_value: string): Promise<unknown> {
  return json(await apiFetch(`/api/locations/aliases/${encodeURIComponent(office_value)}`, { method: "DELETE" }));
}

export async function listLocationPcs(): Promise<PcLoc[]> {
  return json(await apiFetch("/api/locations/pcs"));
}
export async function setPcLocation(pc_name: string, location_id: number): Promise<unknown> {
  return json(await apiFetch(`/api/locations/pcs/${encodeURIComponent(pc_name)}`, { method: "PATCH", ...jbody({ location_id }) }));
}

export async function getUserLocations(userId: string): Promise<{ location_ids: number[] }> {
  return json(await apiFetch(`/api/locations/user/${userId}`));
}
export async function setUserLocations(userId: string, location_ids: number[]): Promise<unknown> {
  return json(await apiFetch(`/api/locations/user/${userId}`, { method: "PUT", ...jbody({ location_ids }) }));
}
