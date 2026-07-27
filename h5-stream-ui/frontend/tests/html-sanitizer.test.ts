import { describe, it, expect, beforeEach } from 'vitest';
import { createStreamingHtmlSanitizer, sanitizeHtml } from '../src/html-sanitizer';

describe('StreamingHtmlSanitizer', () => {
  let sanitizer: ReturnType<typeof createStreamingHtmlSanitizer>;

  beforeEach(() => {
    sanitizer = createStreamingHtmlSanitizer();
  });

  describe('批量清理 - sanitizeHtml', () => {
    it('移除完整的 script 块', () => {
      const result = sanitizeHtml('<script>alert(1)</script><div>test</div>');
      expect(result).toBe('<div>test</div>');
    });

    it('移除完整的 style 块', () => {
      const result = sanitizeHtml('<style>.x{color:red}</style><div>test</div>');
      expect(result).toBe('<div>test</div>');
    });

    it('移除事件监听器', () => {
      const result = sanitizeHtml('<div onclick="alert(1)">test</div>');
      expect(result).toBe('<div>test</div>');
    });

    it('同时移除 script 和 style', () => {
      const input = '<script>evil()</script><style>.x{}</style><div>test</div>';
      const result = sanitizeHtml(input);
      expect(result).toBe('<div>test</div>');
    });

    it('事件监听器 - onerror', () => {
      const result = sanitizeHtml('<img onerror="evil()" src="x">');
      expect(result).toBe('<img src="x">');
    });

    it('事件监听器 - onload', () => {
      const result = sanitizeHtml('<img onload="evil()" src="x">');
      expect(result).toBe('<img src="x">');
    });

    it('安全内容不受影响', () => {
      const result = sanitizeHtml('<div class="test">Hello</div><p>Paragraph</p>');
      expect(result).toBe('<div class="test">Hello</div><p>Paragraph</p>');
    });
  });

  describe('增量清理', () => {
    it('空 token 返回空', () => {
      const result = sanitizer.sanitize('');
      expect(result.html).toBe('');
      expect(result.hasNewContent).toBe(false);
    });

    it('安全内容直接通过', () => {
      const result = sanitizer.sanitize('<div>test</div>');
      expect(result.html).toBe('<div>test</div>');
      expect(result.hasNewContent).toBe(true);
    });

    it('完整 script 块被移除', () => {
      const result = sanitizer.sanitize('<script>alert(1)</script><div>test</div>');
      expect(result.html).toBe('<div>test</div>');
      expect(result.hasNewContent).toBe(true);
    });

    it('增量 - script 开始被缓冲，等待闭合', () => {
      // 第一个分片，script 未闭合，缓冲
      let result = sanitizer.sanitize('<script>alert()');
      expect(result.html).toBe('');
      expect(result.hasNewContent).toBe(false);

      // 第二个分片，script 闭合，完成清理
      result = sanitizer.sanitize('</script><div>test</div>');
      expect(result.html).toBe('<div>test</div>');
      expect(result.hasNewContent).toBe(true);
    });

    it('增量 - style 开始被缓冲，等待闭合', () => {
      let result = sanitizer.sanitize('<style>.x{}');
      expect(result.html).toBe('');
      expect(result.hasNewContent).toBe(false);

      result = sanitizer.sanitize('</style><span>OK</span>');
      expect(result.html).toBe('<span>OK</span>');
      expect(result.hasNewContent).toBe(true);
    });

    it('增量 - 事件处理器被移除', () => {
      let result = sanitizer.sanitize('<div onclick="alert(1)">test</div>');
      expect(result.html).toBe('<div>test</div>');
      expect(result.hasNewContent).toBe(true);
    });

    it('增量 - 事件处理器在字符串中被分割', () => {
      // 事件处理器被分割在不同 token 中
      let result = sanitizer.sanitize('<button onclick="aler');
      // 第一个分片可能被保留

      result = sanitizer.sanitize('t(1)">Click</button>');
      // 需要能正确处理
      expect(result.hasNewContent).toBe(true);
    });

    it('reset 重置状态', () => {
      sanitizer.sanitize('<script>evil()</script>');
      sanitizer.reset();
      const result = sanitizer.sanitize('<div>new</div>');
      expect(result.html).toBe('<div>new</div>');
      expect(result.hasNewContent).toBe(true);
    });

    it('多个增量块连续输入', () => {
      let result = sanitizer.sanitize('<div>');
      expect(result.html).toBe('<div>');

      result = sanitizer.sanitize('hello');
      expect(result.html).toBe('hello');

      result = sanitizer.sanitize('</div>');
      expect(result.html).toBe('</div>');
      expect(result.hasNewContent).toBe(true);
    });
  });
});