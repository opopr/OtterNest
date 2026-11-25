import { formatApiError } from "../utils/labels";
import {
  buildApiUrl,
  buildWebSocketUrl,
  normalizeApiBaseUrl,
  resolveApiOrigin
} from "./url";

const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL);
let accessToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

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

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export function getApiOrigin(): string {
  return resolveApiOrigin(API_BASE_URL, window.location.origin);
}

export function websocketUrl(path: string): string {
  return buildWebSocketUrl(getApiOrigin(), path);
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

  const response = await fetch(buildApiUrl(API_BASE_URL, path), {
    ...init,
    headers,
    credentials: "omit"
  });
  const contentType = response.headers.get("content-type") || "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    if (response.status === 401 && token && unauthorizedHandler) {
      unauthorizedHandler();
    }
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? (payload as { detail?: unknown }).detail
        : payload;
    throw new ApiError(formatApiError(detail || response.statusText, response.status), response.status);
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
  const response = await fetch(buildApiUrl(API_BASE_URL, path), {
    ...init,
    headers,
    credentials: "omit"
  });
  if (!response.ok) {
    if (response.status === 401 && token && unauthorizedHandler) {
      unauthorizedHandler();
    }
    const contentType = response.headers.get("content-type") || "";
    const payload: unknown = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? (payload as { detail?: unknown }).detail
        : payload;
    throw new ApiError(formatApiError(detail || response.statusText, response.status), response.status);
  }
  return response;
}

export interface HealthStatus {
  status: string;
}

/** Probe public liveness/readiness endpoints without sending the owner token. */
export async function healthRequest(
  path: "/health/live" | "/health/ready"
): Promise<HealthStatus> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(new URL(path, getApiOrigin()), {
      method: "GET",
      credentials: "omit",
      cache: "no-store",
      signal: controller.signal
    });
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      throw new ApiError("健康检查返回 HTTP " + response.status, response.status);
    }
    if (typeof payload !== "object" || payload === null ||
        !("status" in payload) || typeof payload.status !== "string") {
      throw new Error("健康检查返回了无效响应");
    }
    return payload as HealthStatus;
  } finally {
    window.clearTimeout(timeout);
  }
}

