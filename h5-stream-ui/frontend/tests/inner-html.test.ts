import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { JSDOM } from "jsdom";
import { PreviewRenderer } from "../src/preview-renderer";

describe("innerHtmlStrategy 完整链路测试", () => {
  let dom: JSDOM;
  let doc: Document;
  let renderer: PreviewRenderer;

  beforeEach(() => {
    dom = new JSDOM(`<!DOCTYPE html><html><body><div id="root"></div></body></html>`);
    doc = dom.window.document;

    renderer = new PreviewRenderer(() => doc, { strategy: "inner-html" });
  });

  afterEach(() => {
    renderer.reset();
  });

  describe("累积 + 渲染", () => {
    it("空 token 不渲染", () => {
      renderer.onToken("");
      const root = doc.getElementById("root");
      expect(root?.innerHTML).toBe("");
    });

    it("多 token 累积后渲染", () => {
      renderer.onToken("<div>");
      renderer.onToken("hello");
      renderer.onToken("</div>");

      const root = doc.getElementById("root");
      expect(root?.innerHTML).toBe("<div>hello</div>");
    });

    it("累积相同内容会产生累积效果", () => {
      renderer.onToken("<div>hello</div>");
      const root = doc.getElementById("root");
      const firstRender = root?.innerHTML ?? "";

      // 再次累积相同内容会追加
      renderer.onToken("<span>world</span>");
      const secondRender = root?.innerHTML ?? "";

      // 第二次渲染会包含累积内容
      expect(secondRender).toContain("<div>hello</div>");
      expect(secondRender).toContain("<span>world</span>");
    });
  });

  describe("extract 逻辑", () => {
    it("markdown fence 提取", () => {
      renderer.onToken('```html\n<div>hello</div>\n```');
      const root = doc.getElementById("root");
      expect(root?.innerHTML).toContain("<div>hello</div>");
    });

    it("json 格式提取", () => {
      renderer.onToken('{"html":"<span>world</span>"}');
      const root = doc.getElementById("root");
      expect(root?.innerHTML).toContain("<span>world</span>");
    });

    it("纯 HTML 提取", () => {
      renderer.onToken("<p>plain</p>");
      const root = doc.getElementById("root");
      expect(root?.innerHTML).toBe("<p>plain</p>");
    });
  });

  describe("sanitize 逻辑", () => {
    it("移除 script 标签", () => {
      renderer.onToken('<div><script>alert(1)</script>hello</div>');
      const root = doc.getElementById("root");
      expect(root?.innerHTML).not.toContain("<script");
      expect(root?.innerHTML).toContain("hello");
    });

    it("移除内联事件属性", () => {
      renderer.onToken('<div onclick="alert(1)">test</div>');
      const root = doc.getElementById("root");
      expect(root?.innerHTML).not.toContain("onclick");
    });
  });

  describe("流式裁剪", () => {
    it("末尾不完整标签会被裁剪", () => {
      // `<div` 没有 `>`，是不完整标签，会被裁剪掉
      renderer.onToken("<div");
      const root = doc.getElementById("root");
      expect(root?.innerHTML).toBe("");
    });
  });

  describe("onDone", () => {
    it("flush 渲染完整内容（包含之前裁剪的部分）", () => {
      renderer.onToken("<div>partial");
      renderer.onDone();

      const root = doc.getElementById("root");
      // onDone 不裁剪，保留完整内容
      expect(root?.innerHTML).toContain("<div>partial");
    });
  });

  describe("reset", () => {
    it("清除累积状态", () => {
      renderer.onToken("<div>first</div>");
      renderer.reset();
      renderer.onToken("<div>second</div>");

      const root = doc.getElementById("root");
      // 应该是 second，不是 first + second
      expect(root?.innerHTML).toBe("<div>second</div>");
    });
  });

  describe("完整链路", () => {
    it("端到端 token → done", () => {
      const chunks = [
        '```html\n<div class="test">\n',
        '<p>Hello ',
        'World</p>\n',
        '</div>\n```',
      ];

      for (const chunk of chunks) {
        renderer.onToken(chunk);
      }

      renderer.onDone();

      const root = doc.getElementById("root");
      expect(root?.innerHTML).toContain('class="test"');
      expect(root?.innerHTML).toContain("Hello");
      expect(root?.innerHTML).toContain("World");
    });
  });
});