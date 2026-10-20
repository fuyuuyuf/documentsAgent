/**
 * 前端无头验证：用 jsdom 真实执行生成的 HTML，确认看板能跑起来。
 *
 * 用法：
 *   node tests/frontend_check.js _verify/smoke_dashboard.html
 *
 * 检查项：
 *   1. payload JSON 能被解析
 *   2. 内嵌 mermaid 运行库存在并被初始化
 *   3. mermaid.render 产出了 <svg>
 *   4. app.js 没有抛出未捕获异常
 *   5. 关键 DOM（图例、统计、源码视图）被正确填充
 *   6. 页面中不存在任何外部网络引用（CDN 检查）
 *   7. 单条顶栏：标签页/工具/导出下拉并入 topbar，旧 .toolbar 移除
 *   8. 画布/顶栏禁文字选区（user-select），正文仍可选
 *   9. 详情面板可收起/展开并写入 localStorage
 *  10. 节点悬停 tooltip 浮出 / 移开消失
 *  11. 标题块（logo+标题+元信息）移至画布左下角 .corner-dock，顶栏不再含标题
 *  12. 左下角信息卡可折叠/展开（图例 + 标题徽章）
 *  13. 图例点击 → 对应分区隐藏/恢复（.sec-hidden 类切换，SVG 不重建 —— 2a 性能优化）
 *  14. topbar 单行校验（有布局引擎时 rowSpan ≈ 最高子元素；jsdom 下结构跳过）
 *  15. 标题徽章可悬停（.brand-dock pointer-events:auto，title=完整标题）—— 1a
 *  16. 拖拽兜底复位：pointerleave / blur 后 pan 状态不残留 —— 1b
 *  17. prefers-reduced-motion 禁用过渡动画；head 内联脚本预置主题防首帧闪白 —— 1c/1d
 *  18. 键盘可达：Esc 逐层收起（tooltip > 导出菜单 > 详情面板）；图例项 Enter/Space 触发 —— 3
 *  19. pan/zoom rAF 节流、tooltip 事件委托、搜索 150ms 防抖 —— 2b/2c
 */
'use strict';

const fs = require('fs');
const path = require('path');

const target = process.argv[2] || path.join(__dirname, '..', '_verify', 'smoke_dashboard.html');
const file = path.resolve(target);

if (!fs.existsSync(file)) {
  console.error(`[fail] 找不到文件：${file}`);
  console.error('       先运行：python tests/smoke_test.py');
  process.exit(1);
}

let JSDOM;
try {
  ({ JSDOM } = require(path.join(__dirname, '..', '_verify', 'node_modules', 'jsdom')));
} catch (err) {
  console.error('[skip] 未安装 jsdom，跳过无头前端验证。');
  console.error('       安装方式：cd _verify && npm install jsdom');
  process.exit(0);
}

const html = fs.readFileSync(file, 'utf8');
const results = [];
const errors = [];

function check(name, condition, detail) {
  results.push({ name, ok: !!condition, detail: detail || '' });
  console.log(`  ${condition ? '✓' : '✗'} ${name}${condition || !detail ? '' : ' — ' + detail}`);
}

// ------------------------------------------------------------------ 静态检查
console.log('='.repeat(66));
console.log('  前端无头验证（jsdom）');
console.log('='.repeat(66));
console.log(`  目标文件：${path.basename(file)}（${(html.length / 1024).toFixed(0)} KB）`);
console.log('\n[静态检查]');

check('无 CDN 外链', !/cdn\.jsdelivr\.net|unpkg\.com|googleapis\.com|cdnjs\.cloudflare\.com/.test(html));
check('无 http(s) 外链脚本', !/<script[^>]+src=["']https?:/i.test(html));
check('无远程样式表', !/<link[^>]+href=["']https?:/i.test(html));
check('Mermaid 已内嵌', html.length > 2_000_000 && /mermaid/i.test(html));
check('存在 payload 脚本块', html.includes('id="payload" type="application/json"'));
check(
  '存在 app.js 逻辑（判据取真实源码片段）',
  html.includes('=== 4. 节点识别') || html.includes('主渲染入口') || html.includes('看板初始化失败'),
  '若前端脚本被替换，请同步更新本判据'
);
check(
  '画布/顶栏禁用文字选区，正文保留可选',
  (() => {
    const m = html.match(/<style>[\s\S]*?<\/style>/);
    return !!m && m[0].includes('user-select: none') && m[0].includes('user-select: text');
  })(),
  'style 块需同时包含 user-select: none 与 user-select: text'
);
check(
  'Mermaid 布局配置已收紧（nodeSpacing/rankSpacing/padding）',
  (() => {
    const appJs = html.slice(html.lastIndexOf('<script>'));   // 只看 app.js（mermaid.min.js 内部默认值不算）
    const ns = /nodeSpacing:\s*(\d+)/.exec(appJs);
    const rs = /rankSpacing:\s*(\d+)/.exec(appJs);
    const pd = /padding:\s*(\d+)\s*\}/.exec(appJs);
    return !!ns && !!rs && !!pd && Number(ns[1]) < 40 && Number(rs[1]) < 60 && Number(pd[1]) < 12;
  })(),
  '应小于旧值 40 / 60 / 12'
);

// --- 前端整体优化新增：静态判据（样式块 / app.js 切片） ---
const styleBlock = (html.match(/<style>[\s\S]*?<\/style>/) || [''])[0];
const appJsSlice = html.slice(html.lastIndexOf('<script>'));
check(
  '1a 标题徽章可悬停（容器 pointer-events:none，.brand-dock 恢复 auto）',
  styleBlock.includes('pointer-events: none') && /\.brand-dock\s*\{[^}]*pointer-events:\s*auto/.test(styleBlock),
  '.brand-dock 需要 pointer-events: auto，否则 title 提示失效'
);
check(
  '1d head 内联脚本在样式后、body 前预设 data-theme（防深色首帧闪白）',
  /<head>[\s\S]*?<script>[\s\S]*?paper-theme[\s\S]*?<\/script>[\s\S]*?<\/head>/.test(html),
  'head 中需有读取 paper-theme 并设置 data-theme 的内联脚本'
);
check(
  '1c 支持 prefers-reduced-motion（禁用过渡/动画）',
  styleBlock.includes('prefers-reduced-motion: reduce'),
  'style 块需包含 prefers-reduced-motion 媒体查询'
);
check(
  '3 所有可聚焦控件有可见焦点环（:focus-visible）',
  styleBlock.includes(':focus-visible'),
  'style 块需包含 :focus-visible 焦点样式'
);
check(
  '2a 分区显隐走 .sec-hidden 类（CSS 隐藏规则就绪）',
  /\.canvas \.sec-hidden\s*\{[^}]*display:\s*none/.test(styleBlock),
  'style 块需包含 .canvas .sec-hidden { display: none }'
);
check(
  '2b pan/zoom 已用 rAF 节流（requestAnimationFrame 写 transform）',
  appJsSlice.includes('requestAnimationFrame'),
  'app.js 需包含 requestAnimationFrame 合帧逻辑'
);
check(
  '2c 搜索输入 150ms 防抖',
  /debounce\(function \(\) \{ runSearch\(el\.search\.value\); \}, 150\)/.test(appJsSlice),
  'onSearchInput 的 debounce 间隔应为 150ms'
);
check(
  '2b tooltip 已改为事件委托（不再逐节点绑定 mouseenter）',
  appJsSlice.includes("'mouseover'") && !appJsSlice.includes("addEventListener('mouseenter'"),
  'app.js 应在容器上委托 mouseover，且不再有逐节点 mouseenter 绑定'
);

// 单独验证 payload 可解析
let payload = null;
try {
  const chunk = html.split('id="payload" type="application/json">')[1].split('</script>')[0];
  payload = JSON.parse(chunk);
  check('payload 可被 JSON.parse', true);
} catch (err) {
  check('payload 可被 JSON.parse', false, err.message);
}

if (payload) {
  check('payload.nodes 非空', Array.isArray(payload.nodes) && payload.nodes.length > 0, `nodes=${payload.nodes && payload.nodes.length}`);
  check('payload.diagram.mermaid 非空', !!(payload.diagram && payload.diagram.mermaid));
  check('payload.diagram.sections 非空', !!(payload.diagram && payload.diagram.sections && payload.diagram.sections.length));
  check('每个节点都有 id 与 label', (payload.nodes || []).every((n) => n.id && n.label));
  const sectionKeys = new Set((payload.diagram.sections || []).map((s) => s.key));
  const orphan = (payload.nodes || []).filter((n) => !sectionKeys.has(n.section));
  check('节点 section 均在 sections 中', orphan.length === 0, orphan.length ? orphan.slice(0, 3).map((n) => n.section).join(',') : '');

  // 溯源信息必须真正落到节点上，否则详情面板显示不出页码与「未在原文定位到」
  const withEvidence = (payload.nodes || []).filter((n) => String(n.evidence || '').trim());
  const withVerify = withEvidence.filter((n) => n.verify && typeof n.verify === 'object');
  check(
    '带证据的节点都携带 verify 字段',
    withEvidence.length > 0 && withVerify.length === withEvidence.length,
    `${withVerify.length}/${withEvidence.length}`
  );
  check(
    'verify 字段键完整',
    withVerify.every((n) => 'found' in n.verify && 'page' in n.verify && 'score' in n.verify && 'method' in n.verify)
  );
  const problemVerify =
    payload.facts && payload.facts.researchProblem ? payload.facts.researchProblem.verify : null;
  check(
    'research_problem 也参与溯源',
    !String(((payload.facts || {}).researchProblem || {}).evidence || '').trim() || !!problemVerify,
    problemVerify ? '' : '缺少 researchProblem.verify'
  );
  const verifiedNodes = (payload.nodes || []).filter((n) => (n.verify || {}).found).length;
  check(
    '已溯源统计覆盖可见节点且不超过证据总数',
    verifiedNodes <= Number((payload.stats || {}).verifiedCount || 0) &&
      Number((payload.stats || {}).verifiedCount || 0) <= Number((payload.stats || {}).evidenceCount || 0),
    `可见节点 ${verifiedNodes}，已溯源 ${(payload.stats || {}).verifiedCount}，总数 ${(payload.stats || {}).evidenceCount}`
  );
  const evidencePages = (payload.nodes || []).filter((n) => String(n.evidence || '').trim() && n.page);
  check(
    '带证据的节点多数带有页码',
    evidencePages.length >= Math.floor(withEvidence.length * 0.8),
    `${evidencePages.length}/${withEvidence.length}`
  );
}

// --------------------------------------------------------------- 动态执行
console.log('\n[动态执行]');

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  url: 'http://localhost/',
  // jsdom 缺失若干现代浏览器 API，这里补齐，否则无法真实执行 mermaid。
  // 注意：这些 API 在 Chrome 98+ / Safari 15.4+ / Firefox 94+ 都已原生支持，
  // 因此补丁只影响本测试环境，不代表产物需要它们。
  beforeParse(window) {
    if (typeof window.structuredClone !== 'function') {
      window.structuredClone = function (value) {
        return JSON.parse(JSON.stringify(value));
      };
    }
    if (typeof window.matchMedia !== 'function') {
      window.matchMedia = function (query) {
        return {
          matches: false,
          media: query,
          onchange: null,
          addListener() {},
          removeListener() {},
          addEventListener() {},
          removeEventListener() {},
          dispatchEvent() { return false; }
        };
      };
    }
    if (!window.SVGElement.prototype.getBBox) {
      window.SVGElement.prototype.getBBox = function () {
        // 粗略估算：按文本长度给出一个稳定但不精确的包围盒，足够驱动布局测试
        const text = (this.textContent || '').trim();
        const width = Math.max(120, Math.min(600, text.length * 9));
        const height = 40;
        return { x: 0, y: 0, width, height, top: 0, left: 0, right: width, bottom: height };
      };
    }
    if (!window.SVGElement.prototype.getComputedTextLength) {
      window.SVGElement.prototype.getComputedTextLength = function () {
        return Math.max(40, ((this.textContent || '').length || 8) * 8);
      };
    }
    if (!window.Element.prototype.scrollIntoView) {
      window.Element.prototype.scrollIntoView = function () {};
    }
    if (typeof window.requestAnimationFrame !== 'function') {
      window.requestAnimationFrame = function (cb) { return setTimeout(() => cb(Date.now()), 16); };
      window.cancelAnimationFrame = function (id) { clearTimeout(id); };
    }
    window.URL.createObjectURL = window.URL.createObjectURL || function () { return 'blob:mock'; };
    window.URL.revokeObjectURL = window.URL.revokeObjectURL || function () {};
    window.HTMLCanvasElement.prototype.getContext = window.HTMLCanvasElement.prototype.getContext || function () {
      return { fillRect() {}, drawImage() {}, fillStyle: '' };
    };
  },
  virtualConsole: (() => {
    const { VirtualConsole } = require(path.join(__dirname, '..', '_verify', 'node_modules', 'jsdom'));
    const vc = new VirtualConsole();
    vc.on('jsdomError', (err) => errors.push(`jsdomError: ${err.message}`));
    vc.on('error', (...args) => errors.push(`console.error: ${args.join(' ')}`));
    vc.on('warn', () => {});
    vc.on('log', () => {});
    return vc;
  })()
});

const { window } = dom;

function finish() {
  const document = window.document;
  const svg = document.querySelector('#mermaidHost svg');
  const fallback = document.querySelector('#mermaidHost .fallback');
  const nodeGroups = document.querySelectorAll('#mermaidHost g.node');
  const boundNodes = document.querySelectorAll('#mermaidHost g.node[data-id]');
  const legendItems = document.querySelectorAll('#legend .item');
  const sourceView = document.getElementById('sourceView');
  const sideBody = document.getElementById('sideBody');
  const zoomLabel = document.getElementById('zoomLabel');
  const overlay = document.getElementById('overlay');

  check('mermaid 运行库已加载', typeof window.mermaid === 'object' && window.mermaid !== null);
  check('未进入兜底视图', !fallback, fallback ? fallback.textContent.slice(0, 120) : '');
  check('渲染出 <svg>', !!svg);
  check('SVG 中至少有节点', nodeGroups.length > 0, `g.node=${nodeGroups.length}`);
  check(
    'app.js 成功给节点打上 data-id',
    boundNodes.length > 0,
    `已绑定 ${boundNodes.length}/${nodeGroups.length}`
  );
  check('节点绑定比例 > 80%', nodeGroups.length > 0 && boundNodes.length / nodeGroups.length > 0.8);
  // securityLevel=strict 下，标签里的 <br/> 必须仍按换行渲染；一旦哪版 mermaid
  // 把它转义成字面文本，长标签会挤成一行（纯视觉回归，其他断言抓不到）。
  const nodeList = Array.from(nodeGroups);
  const literalBr = nodeList.filter((g) => /<br\s*\/?>/i.test(g.textContent || '')).length;
  const wrappedNodes = nodeList.filter((g) => g.querySelectorAll('tspan').length >= 2).length;
  check('标签中的 <br/> 未被转义成字面文本', literalBr === 0, `含字面 <br> 的节点 ${literalBr} 个`);
  check('长标签仍按多行渲染（strict 模式兼容）', wrappedNodes > 0, `多行节点 ${wrappedNodes} 个`);
  check('图例已生成', legendItems.length > 0, `items=${legendItems.length}`);
  check('Mermaid 源码视图已填充', !!sourceView && sourceView.textContent.indexOf('flowchart') >= 0);
  check('概览面板已填充', !!sideBody && sideBody.innerHTML.length > 100);
  check('缩放标签已初始化', !!zoomLabel && /%$/.test(zoomLabel.textContent.trim()), zoomLabel && zoomLabel.textContent);
  check('加载遮罩已隐藏', !!overlay && overlay.classList.contains('hidden'));

  // --- 交互：点击一个节点，右侧应显示详情 ---
  if (boundNodes.length) {
    const first = boundNodes[0];
    const id = first.getAttribute('data-id');
    const before = sideBody.innerHTML;
    first.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
    check('点击节点后有选中态', first.classList.contains('selected'));
    check('点击节点后详情面板更新', sideBody.innerHTML !== before && sideBody.innerHTML.length > 50);
    const nodeData = (payload.nodes || []).find((n) => n.id === id);
    const title = document.getElementById('sideTitle');
    // 面板标题的实现方式有多种（「节点详情」/ 分区名 / 节点标签），只要不是概览且
    // 与该节点相关即算正确
    const titleText = title ? title.textContent.trim() : '';
    const titleOk =
      titleText.length > 0 &&
      titleText !== '论文概览' &&
      (titleText.indexOf('节点详情') >= 0 ||
        (nodeData &&
          ((nodeData.sectionTitle && titleText.indexOf(nodeData.sectionTitle) >= 0) ||
            (nodeData.label && titleText.indexOf(String(nodeData.label).slice(0, 6)) >= 0))));
    check('详情面板标题已切换到该节点', titleOk, titleText);
    check(
      '详情展示了该节点的溯源/说明字段',
      sideBody.textContent.indexOf('溯源') >= 0 ||
        sideBody.textContent.indexOf('说明') >= 0 ||
        sideBody.textContent.indexOf('原文') >= 0
    );
    if (nodeData) {
      check('详情展示的是正确节点', sideBody.textContent.indexOf(String(nodeData.label).slice(0, 6)) >= 0, nodeData.label);
    }
    check('详情面板有关闭按钮', !!document.getElementById('btnCloseDetail'));
  }

  // --- 新布局：单条顶栏（标签页 + 工具 + 导出下拉并入 topbar） ---
  const topbarTab = document.querySelector('.topbar .tab[data-view="map"]');
  const btnExport = document.getElementById('btnExport');
  check(
    '标签页与导出按钮已并入单条顶栏',
    !!topbarTab && !!btnExport && !document.querySelector('.toolbar'),
    topbarTab && btnExport ? '仍残留旧 .toolbar' : 'topbar 内缺少标签页或导出按钮'
  );
  const exportMenu = document.getElementById('exportMenu');
  const exportItems = exportMenu ? exportMenu.querySelectorAll('.menu-item').length : 0;
  let exportToggleOk = false;
  if (exportMenu && btnExport) {
    btnExport.click();
    const opened = !exportMenu.hidden;
    btnExport.click();
    exportToggleOk = opened && exportMenu.hidden;
  }
  check(
    '导出下拉含 SVG / PNG / .mmd 三项且可开合',
    exportItems === 3 && exportToggleOk,
    `items=${exportItems}`
  );

  // --- 新布局：标题块移至画布左下角 .corner-dock（图例在上、标题徽章在下、可整组折叠） ---
  const topbarEl = document.querySelector('.topbar');
  const cornerDock = document.getElementById('cornerDock');
  const brandDockTitle = cornerDock ? cornerDock.querySelector('.brand-dock .brand-title') : null;
  check(
    '顶栏不再含标题块，标题徽章与图例同在左下角 .corner-dock',
    !!topbarEl && !topbarEl.querySelector('.brand, #brandSub') &&
      !!cornerDock && !!brandDockTitle && !!cornerDock.querySelector('#legend'),
    cornerDock ? 'topbar 内仍有标题块或 dock 内缺少图例/标题' : '缺少 .corner-dock'
  );
  check(
    '标题徽章已填充真实标题（无占位符）',
    !!brandDockTitle && brandDockTitle.textContent.trim().length > 0 &&
      brandDockTitle.textContent.indexOf('__PAGE_TITLE__') < 0,
    brandDockTitle ? brandDockTitle.textContent.slice(0, 40) : '缺少 .brand-dock .brand-title'
  );
  // 1a 动态：title 提示 = 显示文本（截断时悬停可见全文），且 app.js 启动时已回填
  const brandTitleEl = document.getElementById('brandTitle');
  check(
    '1a 标题徽章 title 提示为完整标题（与显示文本一致，悬停可见全文）',
    !!brandTitleEl && !!String(brandTitleEl.getAttribute('title') || '').trim() &&
      brandTitleEl.getAttribute('title') === brandTitleEl.textContent.trim(),
    brandTitleEl ? String(brandTitleEl.getAttribute('title')).slice(0, 40) : '缺少 #brandTitle'
  );
  const dockToggle = document.getElementById('btnDockToggle');
  let dockOk = false;
  if (cornerDock && dockToggle) {
    dockToggle.click();
    const collapsed = cornerDock.getAttribute('data-collapsed') === 'true' &&
      window.localStorage.getItem('paper-dock-collapsed') === '1';
    dockToggle.click();
    dockOk = collapsed && cornerDock.getAttribute('data-collapsed') === 'false';
  }
  check(
    '左下角信息卡可折叠/展开且状态写入 localStorage',
    dockOk,
    cornerDock && dockToggle ? '' : '缺少 #cornerDock 或 #btnDockToggle'
  );

  // --- 单行 topbar：rowSpan（子元素垂直跨度）≈ 最高子元素才算一行。
  //     jsdom 无布局引擎（offsetTop/offsetHeight 恒为 0）时跳过，
  //     1600/1280 视口下的量化验证由无头 Chrome（CDP）执行。 ---
  check(
    'topbar 单行（rowSpan ≤ 最高子元素 × 1.25，且无横向溢出）',
    (() => {
      if (!topbarEl) return false;
      const kids = [...topbarEl.children].filter((c) => c.offsetHeight > 0 || c.offsetWidth > 0);
      if (!kids.length) return true;                       // jsdom 无布局：结构检查已覆盖
      const tops = kids.map((c) => c.offsetTop);
      const bottoms = kids.map((c) => c.offsetTop + c.offsetHeight);
      const maxH = Math.max(...kids.map((c) => c.offsetHeight));
      if (maxH === 0) return true;                         // 无布局兜底
      const rowSpan = Math.max(...bottoms) - Math.min(...tops);
      return rowSpan <= maxH * 1.25 + 4 && topbarEl.scrollWidth <= topbarEl.clientWidth + 1;
    })(),
    '子元素跨两行或横向溢出'
  );

  // --- 新布局：详情面板收起 / 展开（localStorage 记忆） ---
  const layoutEl = document.getElementById('layout');
  const sideToggle = document.getElementById('btnToggleSide');
  let sideOk = false;
  if (layoutEl && sideToggle) {
    sideToggle.click();
    const collapsed = layoutEl.classList.contains('side-collapsed') &&
      window.localStorage.getItem('paper-side-collapsed') === '1';
    sideToggle.click();
    sideOk = collapsed && !layoutEl.classList.contains('side-collapsed');
  }
  check(
    '详情面板可收起/展开且状态写入 localStorage',
    sideOk,
    layoutEl && sideToggle ? '' : '缺少 #layout 或 #btnToggleSide'
  );

  // --- 交互：标签页切换 ---
  const sourceTab = document.querySelector('.tab[data-view="source"]');
  if (sourceTab) {
    sourceTab.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    const sourceActive = document.querySelector('.view[data-view="source"]');
    check('标签页可切换到 Mermaid 源码', sourceActive && sourceActive.classList.contains('active'));
    check('切换视图后顶栏导图工具组随 data-view 显隐', document.body.getAttribute('data-view') === 'source',
      `data-view=${document.body.getAttribute('data-view')}`);
  }

  // --- 原文视图：按页折叠展示（payload.paper 缺失的旧产物显示提示，不白屏） ---
  const paperTab = document.querySelector('.tab[data-view="paper"]');
  const paperView = document.getElementById('paperView');
  if (paperTab && paperView) {
    paperTab.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    const paperActive = document.querySelector('.view[data-view="paper"]');
    check('标签页可切换到原文视图', !!paperActive && paperActive.classList.contains('active'));
    const paperPages = payload && payload.paper && Array.isArray(payload.paper.pages) ? payload.paper.pages : [];
    if (paperPages.length) {
      const pageEls = paperView.querySelectorAll('details.paper-page');
      check('原文按页折叠展示', pageEls.length >= 1 && !!paperView.querySelector('summary'), `pages=${pageEls.length}`);
      check('原文内容非空', paperView.textContent.length > 30);
      const firstPage = paperView.querySelector('details.paper-page');
      const secondPage = pageEls.length > 1 ? pageEls[1] : null;
      if (firstPage && secondPage) {
        const openBefore = secondPage.open;
        secondPage.open = !secondPage.open;
        check('原文页可折叠/展开', secondPage.open !== openBefore);
        secondPage.open = openBefore;
      }
    }
    // 切回导图视图，后续检查不受影响
    const mapTab = document.querySelector('.tab[data-view="map"]');
    if (mapTab) mapTab.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  }

  // --- 操作指南弹层：打开 / 内容 / 关闭 ---
  const btnGuide = document.getElementById('btnGuide');
  const guideBackdrop = document.getElementById('guideBackdrop');
  if (btnGuide && guideBackdrop) {
    check('操作指南初始隐藏', guideBackdrop.hidden === true);
    btnGuide.click();
    check('操作指南可打开', !guideBackdrop.hidden && !!guideBackdrop.querySelector('.guide-section'));
    const guideText = guideBackdrop.textContent || '';
    check('操作指南覆盖关键操作说明（图例/原文/键盘）',
      guideText.indexOf('图例') >= 0 && guideText.indexOf('原文') >= 0 && guideText.indexOf('Esc') >= 0,
      '指南内容缺少关键条目');
    const btnGuideClose = document.getElementById('btnGuideClose');
    if (btnGuideClose) {
      btnGuideClose.click();
      check('操作指南可关闭', guideBackdrop.hidden === true);
    }
  }

  // --- 交互：节点悬停 tooltip（300ms 延迟浮出，移开即消失；2b 起为容器 mouseover/mouseout 委托） ---
  const tip = document.getElementById('nodeTip');
  const tipProbe = boundNodes.length ? boundNodes[0] : null;
  if (tip && tipProbe) {
    const tipNode = (payload.nodes || []).find((n) => n.id === tipProbe.getAttribute('data-id'));
    const tipLabel = tipNode ? String(tipNode.label).slice(0, 6) : '';
    // mouseenter/mouseleave 不冒泡，委托实现监听的是容器上的 mouseover/mouseout
    tipProbe.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true }));
    setTimeout(() => {
      check(
        '节点悬停后浮出 tooltip（含标签与页码/溯源）',
        tip.classList.contains('show') &&
          (!tipLabel || tip.textContent.indexOf(tipLabel) >= 0) &&
          tip.textContent.indexOf('页') >= 0,
        tip.className
      );
      tipProbe.dispatchEvent(new window.MouseEvent('mouseout', { bubbles: true }));
      check('移开节点后 tooltip 立即消失', !tip.classList.contains('show'));
      runSearchCheck(payload);
    }, 450);
    return;
  }
  runSearchCheck(payload);
}

function runSearchCheck(payload) {
  const document = window.document;
  const searchInput = document.getElementById('searchInput');
  if (searchInput && payload && payload.nodes && payload.nodes.length) {
    let keyword = '';
    for (const node of payload.nodes) {
      const candidate = String(node.label || '').trim();
      if (candidate.length >= 2) { keyword = candidate.slice(0, 2); break; }
    }
    if (keyword) {
      searchInput.value = keyword;
      searchInput.dispatchEvent(new window.Event('input', { bubbles: true }));
      setTimeout(() => {
        const hit = document.querySelectorAll('#mermaidHost g.node.hit');
        const dim = document.querySelectorAll('#mermaidHost g.node.dim');
        check(`搜索「${keyword}」命中节点`, hit.length > 0, `hit=${hit.length} dim=${dim.length}`);
        runLegendCheck(payload);
      }, 400);
      return;
    }
  }
  runLegendCheck(payload);
}

/**
 * 图例专项测试（2a 新行为）：点击图例项 → 对应分区以 .sec-hidden 类隐藏（SVG 不重建，
 * #mermaidHost svg 保持同一元素）；再次点击恢复。旧实现是重建源码后整图重渲染。
 */
function runLegendCheck(payload) {
  const document = window.document;
  const items = [...document.querySelectorAll('#legend .item')].filter(
    (it) => it.getAttribute('data-section') && it.getAttribute('data-section') !== 'root'
  );
  if (!items.length) {
    check('图例点击显隐分区', false, '无可测的图例项（#legend .item 为空）');
    return report();
  }
  const item = items.find((it) => it.getAttribute('data-section') === 'arguments') || items[0];
  const key = item.getAttribute('data-section');
  const secMeta = ((payload.diagram || {}).sections || []).find((s) => s.key === key) || {};
  const secTitle = String(secMeta.title || key);
  const clusterSel = `#mermaidHost g.cluster[data-section="${key}"]`;
  const stateNow = () => {
    // 注意：toggleSection 每次都会经 buildLegend() 重建图例项，必须按 data-section 重新查询，
    // 不能沿用最初捕获的元素引用（否则会读到已脱离文档的旧节点）
    const live = document.querySelector(`#legend .item[data-section="${key}"]`) || item;
    const cluster = document.querySelector(clusterSel);
    const svg = document.querySelector('#mermaidHost svg');
    return {
      off: live.classList.contains('off'),
      clusterPresent: !!cluster,
      clusterHidden: !!cluster && cluster.classList.contains('sec-hidden'),
      svgNodes: document.querySelectorAll('#mermaidHost g.node').length,
      visibleNodes: document.querySelectorAll('#mermaidHost g.node:not(.sec-hidden)').length,
      svgEl: svg
    };
  };
  const before = stateNow();
  const hadEvidence = before.clusterPresent;
  check(`图例分区「${secTitle}」初始渲染可见`, hadEvidence && !before.clusterHidden, JSON.stringify({
    clusterPresent: before.clusterPresent, clusterHidden: before.clusterHidden
  }));
  item.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  setTimeout(() => {
    const hidden = stateNow();
    check(
      `点击图例「${secTitle}」后分区隐藏（off + .sec-hidden，且 SVG 未重建）`,
      hadEvidence && hidden.off && hidden.clusterPresent && hidden.clusterHidden &&
        hidden.svgEl === before.svgEl && hidden.visibleNodes < before.visibleNodes,
      JSON.stringify({ off: hidden.off, clusterHidden: hidden.clusterHidden, sameSvg: hidden.svgEl === before.svgEl, visibleNodes: `${hidden.visibleNodes}/${before.visibleNodes}` })
    );
    const notice = document.getElementById('mmNotice');
    check('图例操作有反馈提示（已隐藏分区）', !!notice && notice.textContent.indexOf('隐藏') >= 0,
      notice ? notice.textContent : '无 #mmNotice');
    const itemAgain = document.querySelector(`#legend .item[data-section="${key}"]`) || item;
    itemAgain.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
    setTimeout(() => {
      const restored = stateNow();
      check(
        `再次点击图例「${secTitle}」后分区恢复（SVG 仍是同一元素，节点数复原）`,
        !restored.off && restored.clusterPresent && !restored.clusterHidden &&
          restored.svgEl === before.svgEl && restored.visibleNodes === before.visibleNodes,
        JSON.stringify({ off: restored.off, clusterHidden: restored.clusterHidden, sameSvg: restored.svgEl === before.svgEl, visibleNodes: `${restored.visibleNodes}/${before.visibleNodes}` })
      );
      runAccessibilityChecks(payload);
    }, 1800);
  }, 1800);
}

/** 3 可达性：Esc 逐层收起（tooltip > 导出菜单 > 详情面板）；图例项 Enter / Space 触发 */
function runAccessibilityChecks(payload) {
  const document = window.document;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const escKey = () => document.dispatchEvent(
    new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
  );
  (async () => {
    const tip = document.getElementById('nodeTip');
    // 委托实现只对「已匹配 payload」的节点出提示，探针必须选这类节点
    const probe = [...document.querySelectorAll('#mermaidHost g.node[data-id]')].find(
      (g) => (payload.nodes || []).some((n) => n.id === g.getAttribute('data-id'))
    );
    const selectedBefore = !!document.querySelector('#mermaidHost g.node.selected');

    // 第 1 层：tooltip 显示时 Esc 只关 tooltip
    if (tip && probe) {
      probe.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true }));
      await sleep(420);                                          // tooltip 延迟 300ms
      const tipShown = tip.classList.contains('show');
      escKey();
      check('Esc 第一层：tooltip 显示时按 Esc 只关 tooltip', tipShown && !tip.classList.contains('show'));
    } else {
      check('Esc 第一层：tooltip 显示时按 Esc 只关 tooltip', false, '缺少 #nodeTip 或可测节点');
    }

    // 第 2 层：导出菜单打开时 Esc 只关菜单，详情选中态保留
    const btnExport = document.getElementById('btnExport');
    const exportMenu = document.getElementById('exportMenu');
    if (btnExport && exportMenu) {
      btnExport.click();
      escKey();
      const selectionKept = selectedBefore === !!document.querySelector('#mermaidHost g.node.selected');
      check('Esc 第二层：导出菜单打开时按 Esc 只关菜单、保留详情选中', exportMenu.hidden && selectionKept,
        selectionKept ? '' : '详情选中态被误清');
    } else {
      check('Esc 第二层：导出菜单打开时按 Esc 只关菜单、保留详情选中', false, '缺少导出菜单');
    }

    // 第 3 层：再次 Esc 关闭详情面板（清除选中）
    escKey();
    check('Esc 第三层：再次按 Esc 关闭详情面板（清除选中态）',
      !document.querySelector('#mermaidHost g.node.selected'),
      selectedBefore ? '' : '前置条件：此前应已有选中节点（若前置缺失请检查上游交互测试）');

    // 图例项键盘触发
    const legendItem = [...document.querySelectorAll('#legend .item')].find(
      (it) => it.getAttribute('data-section') && it.getAttribute('data-section') !== 'root'
    );
    if (legendItem) {
      const key = legendItem.getAttribute('data-section');
      legendItem.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
      const offAfterEnter = (document.querySelector(`#legend .item[data-section="${key}"]`) || {}).classList?.contains('off');
      const live = document.querySelector(`#legend .item[data-section="${key}"]`) || legendItem;
      live.dispatchEvent(new window.KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true }));
      const restored = document.querySelector(`#legend .item[data-section="${key}"]`);
      check('图例项支持 Enter / Space 键盘触发（role=button + tabindex=0）',
        legendItem.getAttribute('role') === 'button' && String(legendItem.getAttribute('tabindex')) === '0' &&
          !!offAfterEnter && !!restored && !restored.classList.contains('off'),
        `role=${legendItem.getAttribute('role')} tabindex=${legendItem.getAttribute('tabindex')} enterOff=${offAfterEnter}`);
    } else {
      check('图例项支持 Enter / Space 键盘触发（role=button + tabindex=0）', false, '无可测图例项');
    }
    runPanChecks();
  })().catch((err) => {
    check('可达性检查执行完成', false, err && err.message ? err.message : String(err));
    runPanChecks();
  });
}

/** 1b 拖拽兜底：pointerleave / blur 后 pan 状态不残留；正常拖拽仍可用 */
function runPanChecks() {
  const document = window.document;
  const wrap = document.getElementById('canvasWrap');
  const canvas = document.getElementById('canvas');
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const PEv = (type, x, y) => new window.MouseEvent(type, {
    bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y
  });
  (async () => {
    if (!wrap || !canvas) {
      check('1b 拖拽兜底复位（pointerleave / blur）', false, '缺少 #canvasWrap / #canvas');
      return report();
    }
    await sleep(60);                                           // 让挂起的 rAF 先落地
    // 正向回归：正常拖拽仍可平移，松开后拖拽态复位
    const t0 = canvas.style.transform;
    wrap.dispatchEvent(PEv('pointerdown', 100, 100));
    wrap.dispatchEvent(PEv('pointermove', 160, 130));
    await sleep(80);                                           // 等 rAF 写 transform
    const draggedOk = canvas.style.transform !== t0 && wrap.classList.contains('dragging');
    wrap.dispatchEvent(PEv('pointerup', 160, 130));
    check('正常拖拽仍可平移画布并在松开后复位拖拽态',
      draggedOk && !wrap.classList.contains('dragging'));
    // 1b-a：按下未越阈值就移出画布 → pointerleave 复位，回窗后不误拖
    wrap.dispatchEvent(PEv('pointerdown', 100, 100));
    wrap.dispatchEvent(new window.MouseEvent('pointerleave'));
    const t2 = canvas.style.transform;
    wrap.dispatchEvent(PEv('pointermove', 300, 300));
    await sleep(80);
    check('1b 按下后移出画布（pointerleave）pan 状态不残留',
      canvas.style.transform === t2 && !wrap.classList.contains('dragging'),
      canvas.style.transform === t2 ? '' : 'transform 被意外修改');
    // 1b-b：窗口失焦兜底复位
    wrap.dispatchEvent(PEv('pointerdown', 100, 100));
    window.dispatchEvent(new window.Event('blur'));
    const t3 = canvas.style.transform;
    wrap.dispatchEvent(PEv('pointermove', 300, 300));
    await sleep(80);
    check('1b 窗口失焦（blur）后 pan 状态复位',
      canvas.style.transform === t3 && !wrap.classList.contains('dragging'));
    report();
  })().catch((err) => {
    check('拖拽兜底检查执行完成', false, err && err.message ? err.message : String(err));
    report();
  });
}

function report() {
  check('执行期间无未捕获异常', errors.length === 0, errors.slice(0, 3).join(' | '));

  const failed = results.filter((r) => !r.ok);
  console.log('\n' + '='.repeat(66));
  console.log(`  通过 ${results.length - failed.length} 项，失败 ${failed.length} 项`);
  if (failed.length) {
    for (const item of failed) {
      console.log(`    ✗ ${item.name}${item.detail ? ' — ' + item.detail : ''}`);
    }
    console.log('='.repeat(66));
    process.exit(1);
  }
  console.log('  ✓ 前端无头验证全部通过');
  console.log('='.repeat(66));
  process.exit(0);
}

// 给 mermaid 的异步渲染留出时间；renderDiagram 返回 Promise，boot 里有 setTimeout
setTimeout(finish, 2500);

// 兜底：任何未捕获异常都不能让脚本静默挂住
process.on('uncaughtException', (err) => {
  console.error('[fail] 未捕获异常：', err && err.stack ? err.stack : err);
  process.exit(1);
});
