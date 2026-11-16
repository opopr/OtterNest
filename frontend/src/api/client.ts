const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "");
let accessToken: string | null = null;

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getApiOrigin(): string {
  return API_BASE_URL || window.location.origin;
}

export function websocketUrl(path: string): string {
  const url = new URL(path, getApiOrigin());
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  tokenOverride?: string | null
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = tokenOverride === undefined ? accessToken : tokenOverride;
  if (token) headers.set("Authorization", "Bearer " + token);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const response = await fetch(API_BASE_URL + "/api/v1" + path, {
    ...init,
    headers,
    credentials: "omit"
  });
  const contentType = response.headers.get("content-type") || "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? (payload as { detail?: unknown }).detail
        : payload;
    const message = typeof detail === "string"
      ? detail
      : detail ? JSON.stringify(detail) : response.statusText;
    throw new ApiError(message || "请求失败", response.status);
  }
  return payload as T;
}


/** Authenticated raw-body request for binary uploads/downloads; unlike request(), it never assumes JSON. */
export async function binaryRequest(
  path: string,
  init: RequestInit = {},
  tokenOverride?: string | null
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = tokenOverride === undefined ? accessToken : tokenOverride;
  if (token) headers.set("Authorization", "Bearer " + token);
  const response = await fetch(API_BASE_URL + "/api/v1" + path, {
    ...init,
    headers,
    credentials: "omit"
  });
  if (!response.ok) {
    const contentType = response.headers.get("content-type") || "";
    const payload: unknown = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? (payload as { detail?: unknown }).detail
        : payload;
    const message = typeof detail === "string"
      ? detail
      : detail ? JSON.stringify(detail) : response.statusText;
    throw new ApiError(message || "文件请求失败", response.status);
  }
  return response;
}
