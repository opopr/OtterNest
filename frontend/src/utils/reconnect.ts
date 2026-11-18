/**
 * Pure exponential backoff calculation for WebSocket reconnection attempts.
 */
export function computeReconnectDelayMs(
  attempt: number,
  baseMs = 2000,
  maxMs = 30000
): number {
  const safeAttempt = Math.max(0, Math.floor(attempt));
  const safeBase = Math.max(250, baseMs);
  const safeMax = Math.max(safeBase, maxMs);
  const exponential = safeBase * Math.pow(2, Math.min(safeAttempt, 10));
  return Math.min(safeMax, Math.round(exponential));
}
