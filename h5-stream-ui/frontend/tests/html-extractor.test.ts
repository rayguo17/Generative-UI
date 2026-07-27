import { describe, it, expect, beforeEach } from 'vitest';
import { createStreamingHtmlExtractor } from '../src/html-extractor';

describe('StreamingHtmlExtractor', () => {
  let extractor: ReturnType<typeof createStreamingHtmlExtractor>;

  beforeEach(() => {
    extractor = createStreamingHtmlExtractor();
  });

  it('空 token 返回空', () => {
    const result = extractor.extract('');
    expect(result.html).toBe('');
    expect(result.hasNewContent).toBe(false);
  });

  it('完整 fence HTML', () => {
    const result = extractor.extract('```html\n<div>test</div>\n```');
    expect(result.html).toBe('<div>test</div>');
    expect(result.hasNewContent).toBe(true);
  });

  it('fence 被分割', () => {
    // 第一次: 打开 fence，不输出
    let r = extractor.extract('```html');
    expect(r.html).toBe('');

    // 第二次: 遇到内容
    r = extractor.extract('\n<div>test</div>');
    expect(r.html).toBe('<div>test</div>');

    // 第三次: 闭合 fence
    r = extractor.extract('\n```');
    expect(r.html).toBe('');
  });

  it('raw html', () => {
    const result = extractor.extract('<div>test</div>');
    expect(result.html).toBe('<div>test</div>');
  });

  it('raw html 被分割 - 累积到完整标签', () => {
    // 第一次: <div - 包含 <，开始累积，等待 >
    let r = extractor.extract('<div');
    expect(r.html).toBe('');
    expect(r.hasNewContent).toBe(false);

    // 第二次: >test, 遇到 >, 释放累积的 html 片段
    r = extractor.extract('>test')
    expect(r.html).toBe('<div>test');
    expect(r.hasNewContent).toBe(true);

    // 第三次: </div> - 包含 >，返回完整标签
    r = extractor.extract('</div>');
    expect(r.html).toBe('</div>');
    expect(r.hasNewContent).toBe(true);
  });

  it('reset 重置状态', () => {
    extractor.extract('```html\n<div>test</div>\n```');
    extractor.reset();
    const result = extractor.extract('```html\n<div>new</div>\n```');
    expect(result.html).toBe('<div>new</div>');
  });

  it('连续多个 fence 块', () => {
    let r = extractor.extract('```html\n<div>first</div>\n```');
    expect(r.html).toBe('<div>first</div>');

    r = extractor.extract('```html\n<div>second</div>\n```');
    expect(r.html).toBe('<div>second</div>');
  });

  it('JSON 块完整', () => {
    const result = extractor.extract('{"html": "<div>test</div>"}');
    expect(result.html).toBe('<div>test</div>');
    expect(result.hasNewContent).toBe(true);
  });

  it('JSON 块被分割 - 增量输出', () => {
    let r = extractor.extract('{"html": "<div');
    expect(r.html).toBe('<div');
    expect(r.hasNewContent).toBe(true);

    r = extractor.extract('>test</div>"}');
    expect(r.html).toBe('>test</div>');
    expect(r.hasNewContent).toBe(true);
  });

  it('JSON 块带转义字符 - 增量输出', () => {
    let r = extractor.extract('{"html": "<div class=\\"');
    expect(r.html).toBe('<div class="');
    expect(r.hasNewContent).toBe(true);

    r = extractor.extract('test\\">content\\nline</div>"}');
    expect(r.html).toBe('test">content\nline</div>');
    expect(r.hasNewContent).toBe(true);
  });

  it('JSON 块结束后忽略后续输入', () => {
    extractor.extract('{"html": "<div>first</div>"}');
    // 后续输入全部忽略
    const r = extractor.extract('```html\n<div>second</div>\n```');
    expect(r.html).toBe('');
    expect(r.hasNewContent).toBe(false);
  });

  it('JSON 块内含未闭合 HTML 标签 - 缓冲尖括号对', () => {
    let r = extractor.extract('{"html": "<span class="');
    expect(r.html).toBe('<span class="');
    expect(r.hasNewContent).toBe(true);

    r = extractor.extract('btn">text</span>"}');
    expect(r.html).toBe('btn">text</span>');
    expect(r.hasNewContent).toBe(true);
  });

  it('JSON 块内含多个未闭合标签', () => {
    let r = extractor.extract('{"html": "<div><span');
    expect(r.html).toBe('<div>');
    expect(r.hasNewContent).toBe(true);

    r = extractor.extract(' class="x">hello</span></div>"}');
    expect(r.html).toBe('<span class="x">hello</span></div>');
    expect(r.hasNewContent).toBe(true);
  });
});