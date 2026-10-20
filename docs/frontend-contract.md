# 前端数据契约（v1）

本文件是后端 `src/render.py` 与前端 `assets/templates/app.js` 之间的**唯一接口定义**。
两侧任何一方修改字段，必须同步更新本文件。

## 注入方式

`dashboard.html` 中的占位符：

| 占位符 | 内容 |
|---|---|
| `__PAGE_TITLE__` | HTML `<title>` 与左下角标题徽章 `#brandTitle`（已转义，纯文本）；顶栏已不再放标题 |
| `__BRAND_SUB__` | 左下角徽章副标题（已转义，纯文本） |
| `__MERMAID_JS__` | `mermaid.min.js`（UMD 版，3.3MB）内联内容 |
| `__DATA_JSON__` | 下面的 payload JSON（**已把 `<` / `>` / `&` 转义为 `\u003c` / `\u003e` / `\u0026`**） |
| `__APP_JS__` | `assets/templates/app.js` 的内容 |

payload 位于 `<script id="payload" type="application/json">`，前端读取方式：

```js
const DATA = JSON.parse(document.getElementById('payload').textContent);
```

## Payload 结构

```jsonc
{
  "meta": {
    "title": "论文标题",                 // 必填
    "source": "paper.pdf",              // PDF 文件名（不含路径），仅展示用
    "generatedAt": "2026-01-01 12:00:00",
    "pageCount": 12,
    "bodyPages": 10,
    "charCount": 38500,
    "twoColumnPages": [3, 4],           // 疑似多栏的页码；可为空数组
    "model": "deepseek-chat",
    "mode": "api",                      // "api" | "mock"（--mock）| "offline"（--render-from 重渲染）
    "pipelineSeconds": 42.7,
    "usage": { "total_tokens": 8123 }   // 可为 {} 
  },

  "facts": {
    "oneSentenceSummary": "一句话总结",  // 必填
    "researchProblem": {
      "statement": "核心问题",          // 必填
      "whyItMatters": "为什么重要",
      "evidence": "原文摘录",            // 可为 ""
      "page": 1,                         // number | null
      "verify": {                        // 可选，流水线完成溯源校验后才有（结构同 nodes[].verify）
        "found": true, "page": 1, "score": 1.0, "method": "exact", "mismatch": false,
        "context": "命中位置前后各一段原文（简要上下文，可折叠展示）"  // 仅定位成功时写入
      }                                  // 当前 UI 不渲染该 verify（研究问题已是 nodes[] 的一员），保留供调试/后续视图
    },
    "openQuestions": ["..."],           // 可为空数组
    "readingPath": ["..."]              // 可为空数组
  },

  "diagram": {
    "mermaid": "flowchart TD\n  ...",   // 必填，最终渲染用
    "sections": [                       // 展示顺序即数组顺序
      { "key": "root", "title": "论文概览", "color": "#4f46e5" }
    ],
    // 分区显隐由前端 legend 驱动；此字段保留主干链路定义，
    // 但当前版本的主干视图入口（trunkMode）恒为关闭，属未启用功能
    "trunkSections": ["root", "problem", "arguments", "method", "evidence", "conclusions"]
  },

  "nodes": [
    {
      "id": "contributions_1",          // 必填，与 mermaid 中的节点 id 完全一致
      "section": "contributions",       // 必填，对应 diagram.sections[].key
      "sectionTitle": "核心贡献",
      "label": "节点标签",               // 必填
      "detail": "详细说明",              // 可为 ""
      "evidence": "原文摘录",            // 可为 ""
      "page": 3,                        // number | null
      "extra": {                        // 可选，值均为字符串或数字（argument_flow 节点的 "seq" 是序号数字）
        "novelty": "...", "kind": "...", "severity": "高", "analogy": "...", "why": "..."
      },
      "verify": {                       // 可选，引用校验结果
        "found": true,                  // 是否在原文中定位到
        "page": 3,                      // 实际命中的页码
        "score": 1.0,                   // 0~1 匹配度
        "method": "exact",              // exact | squashed | fragment | overlap | not_found | too_short | none
        "mismatch": false,              // 模型标注页码与实际命中页码不一致
        "context": "命中位置前后各一段原文…"  // 仅定位成功时写入（简要上下文，可折叠展示）
      }
    }
  ],

  "critique": {
    "data": "...", "experiment": "...", "method": "...",
    "assumption": "...", "applicationBoundary": "...", "overall": "..."
  },

  "glossary": [
    { "term": "术语", "plain": "大白话解释", "analogy": "...", "why": "...",
      "evidence": "...", "page": 2, "verify": { } }
  ],

  "paper": {                             // 流水线解析 PDF 后写入；离线重渲染同样写入（无 PDF 时可能缺失）
    "pages": [                           // 正文页原文，顺序即阅读顺序；前端「原文」标签页按页折叠展示
      { "page": 1, "text": "第 1 页原文，保留换行与 ## 标题行标记" }
    ]
  },

  "stats": {
    "nodeCount": 28,
    "sectionCount": 9,
    "evidenceCount": 22,      // 全部分析条目中需要回查的证据数，可能多于导图可见节点
    "verifiedCount": 20,      // 在原文中定位成功的证据数
    "mismatchCount": 1,
    "unverifiedCount": 2,
    "pageFixedCount": 3,      // 自动纠正/补全页码的条数；由 pipeline 写入（build_payload 本身不产出）
    "verifiedNodeCount": 18   // verify.found 为真的导图节点数；build_payload 组装时写入，恒在
  },

  "validation": {
    "ok": true,
    "errors": [],
    "warnings": [],
    "repairs": ["补全未闭合的 subgraph"]
  },

  "warnings": ["正文仅提取到 1200 字符，可能是扫描件…"]
}
```

## 前端必须实现的交互（对应评分项"看板可用"）

1. **节点详情面板**：点击节点 → 右侧展示 label / detail / evidence / 页码 / extra / 校验结果。
2. **滚动缩放**：滚轮以鼠标位置为锚点缩放；工具栏 ＋/－/适应/重置；拖动平移。
3. **展开折叠**：默认“清晰概览”每个正文分区显示一个关键节点；“显示全部节点”展开完整导图。分区显隐**仅**通过左下角图例切换（画布内点击分区标题/背景不折叠分区，防止误触）；图例切换实现为 `.sec-hidden` 类切换，**不重建 SVG**。注意工具栏的 ⊞/⊟（概览 / 全部）是另一套机制：它们会重新生成 Mermaid 源码并重渲染。
3b. **原文视图**：「原文」标签页按页展示提取到的论文正文：保留换行与段落排版，`## 标题`行渲染为小标题，每页可折叠（默认展开第一页），并提供「全部展开 / 全部折叠」。payload.paper 缺失时显示提示而非白屏。
3c. **操作指南**：顶栏「📖 指南」按钮打开弹层，集中说明视图切换、缩放平移、图例分区显隐、搜索、证据溯源、原文折叠、导出与键盘操作；Esc / 关闭按钮 / 点击遮罩空白处均可关闭。
4. **搜索**：输入关键字 → 高亮命中节点、其余变暗、自动**居中**（`centerOn`）到第一个命中。Esc 为分层收起：tooltip → 指南 → 导出菜单 → 详情面板 → 搜索，前一层消费掉 Esc 后不会连带清空后面的状态。
5. **引用溯源可读**：`verify.found === false` 要显著提示"未在原文定位到"；`mismatch` 提示页码不符；定位成功时在详情面板以**可折叠**形式展示 `verify.context`（命中位置前后各一段原文，字段缺失时整体隐藏）。
6. **导出**：下载 SVG / PNG / `.mmd` 源文件（全部在前端离线完成，禁止联网）。
7. **主题切换**：深浅色，**仅改 CSS 变量**（`data-theme`），**不重新渲染 Mermaid** —— 重渲染会丢失缩放/平移与选中态，且会闪一次白。
8. **兜底**：Mermaid 渲染失败时，不得白屏——展示错误信息 + 原始 Mermaid 源码 + 节点列表。

## 当前未被前端消费的字段（仅记录，勿据此判断功能缺失）

`meta.bodyPages`、`meta.twoColumnPages`、`meta.usage`、`stats.pageFixedCount`、`stats.verifiedNodeCount`、`facts.researchProblem.verify` 都会写进 payload，供调试与后续视图使用，但当前 UI 不读它们。修改这些字段不会影响看板表现。

## 硬性约束

- **产物（`dashboard.html`）禁止任何外部网络请求**：不得出现 CDN、`fetch`、`import()`、外链字体/图标。注意：`upload.html` 是由本地 `--web` 服务托管的页面，它用 `fetch` 调本地接口，不受此约束。
- 不使用 ES module / `import`；只写普通 `<script>` 可执行的 ES2017 语法。
- 不依赖任何第三方库，只用原生 DOM API。
- 所有 CSS 已写在 `dashboard.html` 的 `<style>` 中，`app.js` **不要**再注入样式表，如需少量动态样式可用 `element.style` 或复用已有 class。
- `'use strict';` 放在文件首行，主体包在 IIFE 内，避免污染全局（首行指令同样作用于 IIFE，不必写在 IIFE 内部）。
- 必须对 `DATA` 字段缺失做防御（本项目用 `str/arr/obj/num` 等 ES5 helper + 默认值；不强制可选链，以兼容老浏览器），任何异常都不能让页面白屏。
