/**
 * API client for communicating with the BOM Validator backend.
 *
 * All functions return promises. The Vite dev server proxies
 * /api/* requests to http://localhost:8000.
 */

const BASE = "/api";

export async function uploadDocuments(csFile, bomFile, sapFile) {
  const form = new FormData();
  form.append("cs_pdf", csFile);
  form.append("bom_xlsx", bomFile);
  form.append("sap_pdf", sapFile);

  const res = await fetch(`${BASE}/upload`, { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Upload failed");
  }
  return res.json();
}

export async function triggerExtraction(identifier) {
  const res = await fetch(`${BASE}/extract/${identifier}`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Extraction failed");
  }
  return res.json();
}

export async function getResults(identifier) {
  const res = await fetch(`${BASE}/results/${identifier}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to fetch results");
  }
  return res.json();
}

export function getDocumentUrl(identifier, docType, download = false) {
  const params = download ? "?download=true" : "";
  return `${BASE}/documents/${identifier}/${docType}${params}`;
}
