/**
 * SSE 流解析器 - 解析后端 SSE 流
 */

export type SseEvent =
  | { type: "start" }
  | { type: "token"; content: string }
  | { type: "done" }
  | { type: "error"; message: string };

export interface SseHandler {
  onToken: (content: string) => void;
  onDone: () => void;
  onError: (error: Error) => void;
}

/**
 * 解析 SSE 流
 */
export async function parseSseStream(
  body: ReadableStream<Uint8Array> | null,
  handler: SseHandler
): Promise<void> {
  if (!body) throw new Error("No response body");
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const rawLine = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (!rawLine.startsWith("data:")) continue;
      const jsonStr = rawLine.slice(5).trim();
      let ev: SseEvent;
      try {
        ev = JSON.parse(jsonStr) as SseEvent;
      } catch {
        continue;
      }
      if (ev.type === "token" && ev.content) handler.onToken(ev.content);
      if (ev.type === "error") handler.onError(new Error(ev.message));
    }
  }
  handler.onDone();
}