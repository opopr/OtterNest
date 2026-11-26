export function normalizeApiBaseUrl(raw: string | undefined): string {
  return (raw || "").trim().replace(/\/+$/, "");
}

export function resolveApiOrigin(baseUrl: string, fallbackOrigin: string): string {
  return baseUrl || fallbackOrigin;
}

export function buildApiUrl(baseUrl: string, path: string): string {
  const normalizedPath = path.startsWith("/") ? path : "/" + path;
  return baseUrl + "/api/v1" + normalizedPath;
}

export function buildWebSocketUrl(apiOrigin: string, path: string): string {
  const url = new URL(path, apiOrigin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function formatAuthConnectionError(reason: unknown, apiOrigin: string): string {
  if (reason instanceof Error) {
    if (reason.name === "TypeError" || /failed to fetch|networkerror|load failed/i.test(reason.message)) {
      return `无法连接到后端认证接口 (${apiOrigin}/api/v1/auth/status)。这通常是服务端 CORS 未放行当前前端来源地址（Origin），或后端服务暂时不可达。`;
    }
    return reason.message || "无法连接 OtterNest 后端服务";
  }
  return "无法连接 OtterNest 后端服务";
}
