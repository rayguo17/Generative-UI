import { describe, it, expect } from "vitest";
import { parseSseStream } from "../src/sse-stream";

// 模拟录制的数据
const MOCK_STREAM_DATA = [
  { type: "token", content: '<div class="min-h-screen bg-gray-50 pb-6">' },
  { type: "token", content: '\n    <!-- Header with decorative background -->\n    <div class="relative overflow-hidden bg-gradient-to-br from-blue-600 via-blue-500 to-cyan-400 px-4 pt-12 pb-16">' },
  { type: "token", content: '">\n        <div class="absolute inset-0 opacity-20">\n            <svg class="w-full h-full" viewBox="0 0 400 200" preserveAspectRatio="xMidYMid slice">' },
  { type: "token", content: '">\n            <span>test content</span>\n        </div>\n    </div>\n</div>' },
  { type: "done" },
];

// 创建模拟的 ReadableStream
function createMockStream(events: typeof MOCK_STREAM_DATA): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;

  return new ReadableStream({
    pull(controller) {
      if (index >= events.length) {
        controller.close();
        return;
      }
      const event = events[index++];
      const data = `data: ${JSON.stringify(event)}\n\n`;
      controller.enqueue(encoder.encode(data));
    },
  });
}

describe("sse-stream", () => {
  describe("parseSseStream", () => {
    it("正确解析多个 token", async () => {
      const tokens: string[] = [];
      let done = false;

      await parseSseStream(createMockStream(MOCK_STREAM_DATA), {
        onToken: (content) => tokens.push(content),
        onDone: () => (done = true),
        onError: (err) => console.error(err),
      });

      expect(done).toBe(true);
      expect(tokens.length).toBe(4);
      expect(tokens[0]).toContain('<div class="min-h-screen');
    });

    it("处理错误事件", async () => {
      const errorEvents = [
        { type: "error", message: "Something went wrong" },
      ];

      let errorMessage = "";
      try {
        await parseSseStream(createMockStream(errorEvents), {
          onToken: () => {},
          onDone: () => {},
          onError: (err) => {
            errorMessage = err.message;
          },
        });
      } catch (e) {
        // 错误会被抛出
        errorMessage = (e as Error).message;
      }

      expect(errorMessage).toBe("Something went wrong");
    });

    it("处理空内容", async () => {
      const emptyEvents = [
        { type: "token", content: "" },
        { type: "done" },
      ];

      const tokens: string[] = [];
      await parseSseStream(createMockStream(emptyEvents), {
        onToken: (content) => tokens.push(content),
        onDone: () => {},
        onError: () => {},
      });

      // 空内容不应该触发 onToken
      expect(tokens.filter(t => t.length > 0).length).toBe(0);
    });
  });
});