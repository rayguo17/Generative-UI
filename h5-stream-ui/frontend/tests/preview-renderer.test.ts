import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { JSDOM } from "jsdom";
import { PreviewRenderer } from "../src/preview-renderer";

// 模拟录制数据的 chunks
const MOCK_CHUNKS = [
  '<div class="min-h-screen bg-gray-50 pb-6">',
  '\n    <div class="relative overflow-hidden bg-gradient-to-br from-blue-600 via-blue-500 to-cyan-400 px-4 pt-12 pb-16">',
  '">\n        <div class="absolute inset-0 opacity-20">\n            <svg class="w-full h-full" viewBox="0 0 400 200" preserveAspectRatio="xMidYMid slice">',
  '">\n                <defs>\n                    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">',
  '">\n                        <path d="M 40 0 L 0 0 0 40" fill="none" stroke="white" stroke-width="0.5"/>\n                    </pattern>',
  '">\n                </defs>\n                <rect width="400" height="200" fill="url(#grid)"/>\n            </svg>\n        </div>\n    </div>\n</div>',
];

describe("preview-renderer integration", () => {
  let dom: JSDOM;
  let doc: Document;
  let renderer: PreviewRenderer;
  let renderedCount: number;

  beforeEach(() => {
    // 创建 JSDOM
    dom = new JSDOM(`<!DOCTYPE html><html><body><div id="root"></div></body></html>`);
    doc = dom.window.document;
    renderedCount = 0;

    // 创建渲染器
    renderer = new PreviewRenderer(() => doc, { strategy: "inner-html" }, {
      onRendered: () => {
        renderedCount++;
      },
    });
  });

  afterEach(() => {
    renderer.reset();
  });

  describe("完整流式渲染流程", () => {
    it("逐步接收 token 并渲染", () => {
      // 模拟逐步接收 token
      for (const chunk of MOCK_CHUNKS) {
        renderer.onToken(chunk);
      }

      // 调用 onDone 完成渲染
      renderer.onDone();

      // 验证内容已渲染
      const root = doc.getElementById("root");
      expect(root?.innerHTML).toContain('<div class="min-h-screen');
      expect(root?.innerHTML).toContain('bg-gray-50');

      // 验证渲染回调被调用
      expect(renderedCount).toBeGreaterThan(0);

      // 验证诊断信息
      const diagnostics = renderer.getDiagnostics();
      expect(Array.isArray(diagnostics)).toBe(true);
    });

    it("处理空输入", () => {
      renderer.onToken("");
      renderer.onDone();

      const root = doc.getElementById("root");
      expect(root?.innerHTML).toBe("");
    });

    it("重置内部状态", () => {
      renderer.onToken(MOCK_CHUNKS[0]);
      renderer.reset();

      // reset() 重置内部状态，不清理 DOM
      // 再次渲染新内容时应该从空状态开始
      const root = doc.getElementById("root");
      // 第一次渲染的内容还在
      expect(root?.innerHTML).toContain("min-h-screen");
    });
  });

  describe("使用录制数据测试", () => {
    it("使用 720444ad989c49aa.json 的 chunk 数据模拟渲染", () => {
      // 读取部分真实录制数据的 chunk 内容
      const realChunks = [
        '<div class="min-h-screen bg-gray-50 pb-6">\n    <!-- Header with decorative background -->\n    <div class="relative overflow-hidden bg-gradient-to-br from-blue-600 via-blue-500 to-cyan-400 px-4 pt-12 pb-16">',
        '">\n        <div class="absolute inset-0 opacity-20">\n            <svg class="w-full h-full" viewBox="0 0 400 200" preserveAspectRatio="xMidYMid slice">\n                <defs>\n                    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">\n                        <path d="M 40 0 L 0 0 0 40" fill="none" stroke="white" stroke-width="0.5"/>\n                    </pattern>\n                </defs>\n                <rect width="400" height="200" fill="url(#grid)"/>\n            </svg>\n        </div>\n        <div class="relative z-10">\n            <div class="text-center">\n                <span class="inline-block px-3 py-1 bg-white/20 rounded-full text-white text-xs mb-3">2024年五一假期</span>\n                <h1 class="text-2xl font-bold text-white mb-2">南京旅游攻略</h1>',
        '            </div>\n        </div>\n    </div>\n</div>',
      ];

      for (const chunk of realChunks) {
        renderer.onToken(chunk);
      }

      renderer.onDone();

      const root = doc.getElementById("root");
      const html = root?.innerHTML ?? "";

      // 验证关键内容存在
      expect(html).toContain("min-h-screen");
      expect(html).toContain("bg-gray-50");
      expect(html).toContain("南京旅游攻略");
      expect(html).toContain("2024年五一假期");
    });
  });
});