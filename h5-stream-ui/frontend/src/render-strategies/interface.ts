/**
 * 渲染策略接口 - 定义渲染策略的统一接口
 */

export interface RenderContext {
  /** 渲染目标元素（如 #root） */
  rootElement: HTMLElement | null;
  /** 完整的 Document（用于创建节点等） */
  ownerDocument: Document | null;
}

export interface RenderStrategy {
  name: string;

  /** 接收 token，内部处理累积/提取/清理/裁剪/渲染 */
  onToken(token: string, context: RenderContext): boolean;

  /** 流结束，内部处理最终渲染（不裁剪） */
  onDone(context: RenderContext): void;

  /** 重置策略内部状态 */
  reset(): void;

  /** 获取最后渲染的 HTML（用于诊断等） */
  getLastRenderedHtml?(context: RenderContext): string;
}