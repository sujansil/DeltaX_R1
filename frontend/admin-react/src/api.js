export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
export function errorMessage(detail, status) {
  if (Array.isArray(detail))
    return detail
      .map((x) => [x.loc?.slice(1).join("."), x.msg].filter(Boolean).join(": "))
      .join("; ");
  return typeof detail === "object"
    ? detail?.message || `Request failed (${status})`
    : detail || `Request failed (${status})`;
}
export async function api(
  path,
  { token, method = "GET", body, signal, download = false } = {},
) {
  const controller = new AbortController(),
    abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, 20000);
  try {
    const form = body instanceof FormData,
      r = await fetch(path, {
        method,
        cache: "no-store",
        signal: controller.signal,
        headers: {
          ...(token ? { Authorization: "Bearer " + token } : {}),
          ...(body !== undefined && !form
            ? { "Content-Type": "application/json" }
            : {}),
        },
        body:
          body === undefined ? undefined : form ? body : JSON.stringify(body),
      });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new ApiError(
        errorMessage(d.detail, r.status),
        r.status,
        d.detail?.code,
      );
    }
    if (download) {
      const type = r.headers.get("Content-Type") || "";
      if (!type.includes("spreadsheetml"))
        throw new ApiError(
          "The server did not return an Excel workbook.",
          r.status,
        );
      return {
        blob: await r.blob(),
        name:
          r.headers
            .get("Content-Disposition")
            ?.match(/filename="([^"]+)"/)?.[1] || "round1-results.xlsx",
      };
    }
    return r.status === 204 ? {} : await r.json();
  } catch (e) {
    if (e.name === "AbortError")
      throw new ApiError(
        "Request timed out. Refresh to check whether the action was applied before retrying.",
        0,
      );
    if (e instanceof TypeError)
      throw new ApiError(
        "Cannot reach the server. Check that start.sh is running.",
        0,
      );
    throw e;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
export const date = (v) => (v ? new Date(v).toLocaleString() : "—");
export const points = (v) => Number(v ?? 0).toLocaleString("en-IN");
export function time(ms) {
  const s = Math.ceil(Math.max(0, ms || 0) / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
export const roundName = (r) =>
  r === "R1A" ? "WizTime · R1A" : "Math Rally · R1B";
export function roundActions(r) {
  if (!r) return [];
  if (r.status === "DRAFT") return ["validate", "start"];
  if (r.status === "LIVE") return ["pause", "end"];
  if (r.status === "PAUSED") return ["resume", "end"];
  return [];
}
export const text = (value) =>
  value == null || value === "" ? "—" : String(value);
