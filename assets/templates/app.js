'use strict';
/* ==========================================================================
 * 论文阅读助手 · 思维导图看板 —— 前端交互逻辑（完全离线，零网络请求）
 * 数据来源：页面内 <script id="payload" type="application/json">；契约：docs/frontend-contract.md
 * 约束：ES2017、无 import/export、无第三方库、无 CDN
 * ========================================================================== */
(function () {
  /* ==================== 0. 通用工具 ==================== */
  function $(id) { return document.getElementById(id); }
  /** HTML 转义：所有拼入 innerHTML 的值都必须先过这里 */
  function esc(v) {
    if (v === null || v === undefined) return '';
    return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function str(v, d) { return (v === null || v === undefined || v === '') ? (d === undefined ? '' : d) : String(v); }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function obj(v) { return (v && typeof v === 'object' && !Array.isArray(v)) ? v : {}; }
  function num(v, d) { return (typeof v === 'number' && isFinite(v)) ? v : d; }
  function clamp(n, lo, hi) { return n < lo ? lo : (n > hi ? hi : n); }
  /** 任意值转可读文本（extra 里可能是数组或对象） */
  function text(v) {
    if (v === null || v === undefined) return '';
    if (Array.isArray(v)) return v.map(text).filter(Boolean).join('、');
    if (typeof v === 'object') { try { return JSON.stringify(v); } catch (e) { return ''; } }
    return String(v);
  }
  function debounce(fn, ms) {
    var t = 0;
    return function () {
      var self = this, a = arguments;
      if (t) clearTimeout(t);
      t = setTimeout(function () { t = 0; fn.apply(self, a); }, ms);
    };
  }
  /** 是否以拉丁字母为主（决定英文摘录是否用斜体） */
  function isLatin(s) {
    var t = str(s, ''), latin = (t.match(/[A-Za-z]/g) || []).length, cjk = (t.match(/[\u3400-\u9fff]/g) || []).length;
    return latin > 0 && (cjk === 0 ? latin > 6 : latin > cjk * 4);
  }
  /** 归一化匹配键：去空白与常见标点、统一小写 */
  function normKey(s) {
    return str(s, '').replace(/[\s\u3000]+/g, '').replace(/[「」【】《》()（）\[\]{}"'`·,，.。:：;；!！?？\-—_/\\]/g, '').toLowerCase();
  }
  function safeName(s, d) {
    var t = str(s, '').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').replace(/\s+/g, ' ').trim();
    return (t || d || 'mindmap').slice(0, 80);
  }
  /* ==================== 1. 读取并归一化 payload（缺字段也不白屏） ==================== */
  var DATA = {}, payloadError = '';
  try {
    var rawEl = $('payload');
    DATA = rawEl ? (JSON.parse(rawEl.textContent || '{}') || {}) : {};
    if (typeof DATA !== 'object' || DATA === null || Array.isArray(DATA)) { payloadError = 'payload 不是 JSON 对象，已按空数据继续。'; DATA = {}; }
  } catch (e) { payloadError = 'payload JSON 解析失败：' + (e && e.message ? e.message : e); DATA = {}; }
  var META = obj(DATA.meta), FACTS = obj(DATA.facts), PROBLEM = obj(FACTS.researchProblem);
  var DIAGRAM = obj(DATA.diagram), CRITIQUE = obj(DATA.critique), VALIDATION = obj(DATA.validation);
  var WARNINGS = arr(DATA.warnings).map(text).filter(Boolean);
  var PAPER_TITLE = str(META.title, str(document.title, '论文导图'));
  var PRISTINE = str(DIAGRAM.mermaid, '');          // 原始源码，永不改写
  var FONT_STACK = '"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif';
  /* 节点归一化 + id / 标签索引 */
  var NODES = [], NODE_BY_ID = {}, NODE_BY_LABEL = {};
  (function () {
    var src = arr(DATA.nodes);
    for (var i = 0; i < src.length; i++) {
      var r = obj(src[i]);
      var n = { id: str(r.id, 'n_' + i), section: str(r.section, ''), sectionTitle: str(r.sectionTitle, ''),
        label: str(r.label, str(r.id, '未命名节点')), detail: str(r.detail, ''), evidence: str(r.evidence, ''),
        page: num(r.page, null), extra: obj(r.extra), verify: obj(r.verify), __el: null };
      NODES.push(n);
      if (!NODE_BY_ID[n.id]) NODE_BY_ID[n.id] = n;
      var k1 = normKey(n.label), k2 = normKey(n.label.replace(/^(节点|node)\s*/i, ''));
      if (k1 && !NODE_BY_LABEL[k1]) NODE_BY_LABEL[k1] = n;
      if (k2 && !NODE_BY_LABEL[k2]) NODE_BY_LABEL[k2] = n;
    }
  })();
  /* 分区归一化：sections 缺失时用节点的 section 补齐，保证图例完整 */
  var PALETTE = ['#4f46e5', '#0891b2', '#16a34a', '#d97706', '#dc2626', '#7c3aed',
    '#db2777', '#0ea5e9', '#65a30d', '#f59e0b', '#14b8a6', '#8b5cf6'];
  var SECTIONS = [], SECTION_KEYS = {}, ID_SECTION = {};
  (function () {
    var src = arr(DIAGRAM.sections), i, o, key;
    for (i = 0; i < src.length; i++) {
      o = obj(src[i]); key = str(o.key, '');
      if (!key || SECTION_KEYS[key]) continue;
      SECTION_KEYS[key] = true;
      SECTIONS.push({ key: key, title: str(o.title, key), color: str(o.color, PALETTE[SECTIONS.length % PALETTE.length]) });
    }
    for (i = 0; i < NODES.length; i++) {
      ID_SECTION[NODES[i].id] = NODES[i].section || '';
      key = NODES[i].section;
      if (key && !SECTION_KEYS[key]) {
        SECTION_KEYS[key] = true;
        SECTIONS.push({ key: key, title: str(NODES[i].sectionTitle, key), color: PALETTE[SECTIONS.length % PALETTE.length] });
      }
    }
    if (!SECTIONS.length) SECTIONS.push({ key: 'root', title: '论文概览', color: PALETTE[0] });
  })();
  var TRUNK = arr(DIAGRAM.trunkSections).map(function (k) { return str(k, ''); }).filter(Boolean);
  if (!TRUNK.length) TRUNK = ['root'];
  /* 统计归一化：stats 缺失时按 nodes 现算 */
  var STATS = (function () {
    var s = obj(DATA.stats), ev = 0, ok = 0, mm = 0, un = 0, n;
    for (var i = 0; i < NODES.length; i++) {
      n = NODES[i];
      if (n.evidence) ev++;
      if (n.verify && n.verify.found === true) ok++; else un++;
      if (n.verify && n.verify.mismatch === true) mm++;
    }
    return { nodeCount: num(s.nodeCount, NODES.length), sectionCount: num(s.sectionCount, SECTIONS.length),
      evidenceCount: num(s.evidenceCount, ev), verifiedCount: num(s.verifiedCount, ok),
      mismatchCount: num(s.mismatchCount, mm), unverifiedCount: num(s.unverifiedCount, un) };
  })();
  var GLOSSARY = arr(DATA.glossary).map(function (g) {
    var o = obj(g);
    return { term: str(o.term, ''), plain: str(o.plain, ''), analogy: str(o.analogy, ''), why: str(o.why, ''),
      evidence: str(o.evidence, ''), page: num(o.page, null), verify: obj(o.verify) };
  }).filter(function (g) { return g.term || g.plain; });
  /* 原文全文：按页折叠展示（旧产物 / 离线重渲染缺少 PDF 时为空，前端给提示，不白屏） */
  var PAPER = obj(DATA.paper);
  var PAPER_PAGES = arr(PAPER.pages).map(function (p) {
    var o = obj(p);
    return { page: num(o.page, null), text: str(o.text, '') };
  }).filter(function (p) { return p.text; });
  /* ==================== 2. DOM 引用与运行状态 ==================== */
  var el = {
    overlay: $('overlay'), statusBadge: $('statusBadge'), brandSub: $('brandSub'), brandTitle: $('brandTitle'),
    search: $('searchInput'), suggestions: $('searchSuggestions'),
    btnHome: $('btnHome'), btnTheme: $('btnTheme'), btnPrint: $('btnPrint'), tabs: $('tabs'), legend: $('legend'),
    wrap: $('canvasWrap'), canvas: $('canvas'), host: $('mermaidHost'), zoomLabel: $('zoomLabel'),
    btnZoomIn: $('btnZoomIn'), btnZoomOut: $('btnZoomOut'), btnFit: $('btnFit'), btnReset: $('btnReset'),
    btnExpandAll: $('btnExpandAll'), btnCollapseAll: $('btnCollapseAll'), btnSvg: $('btnDownloadSvg'),
    btnPng: $('btnDownloadPng'), btnMm: $('btnDownloadMm'), sideTitle: $('sideTitle'), sideBody: $('sideBody'),
    btnCloseDetail: $('btnCloseDetail'), glossaryView: $('glossaryView'),
    critiqueView: $('critiqueView'), sourceView: $('sourceView'), paperView: $('paperView'),
    layout: $('layout'), btnToggleSide: $('btnToggleSide'), sidePanel: $('sidePanel'),
    cornerDock: $('cornerDock'), btnDockToggle: $('btnDockToggle'),
    btnExport: $('btnExport'), exportMenu: $('exportMenu'), tip: $('nodeTip'),
    btnGuide: $('btnGuide'), guideBackdrop: $('guideBackdrop'),
    btnGuideClose: $('btnGuideClose'), guideBody: $('guideBody') };
  var state = { x: 0, y: 0, s: 1,                        // 画布平移 / 缩放
    nodeEls: [], edgeEls: [],                            // DOM 缓存，每次重绘后重建
    hits: null, selectedId: null,                        // 搜索命中表（null = 无搜索）/ 当前选中节点
    legendOff: {},                                       // 图例关闭的分区（独立保存，不因主干模式丢失）
    trunkMode: false, overviewMode: true, collapsedNodes: {}, renderOk: false, renderSeq: 0, lastDragEnd: 0,
    sideCollapsed: false,                                // 右侧详情面板是否收起
    dockCollapsed: false,                                // 左下角信息卡（图例+标题徽章）是否折叠
    tipTimer: 0, tipNode: null };                        // 悬停提示的定时器 / 目标节点
  var pan = { active: false, moved: false, sx: 0, sy: 0, ox: 0, oy: 0, onNode: false, fromLegend: false, pointerId: 0 };
  /* ==================== 3. Mermaid 初始化与渲染 ==================== */
  function isDark() { return document.documentElement.getAttribute('data-theme') === 'dark'; }
  function initMermaid() {
    if (!window.mermaid) return false;
    var dark = isDark(), vars = { fontFamily: FONT_STACK, fontSize: '14px' };
    if (dark) {
      // Mermaid 默认暗色主题会把分区与边标签渲染成灰块；统一使用明确的蓝绿色暗色板。
      vars.primaryColor = '#19414a'; vars.primaryTextColor = '#eef7f4'; vars.primaryBorderColor = '#5b999b';
      vars.lineColor = '#b1d5d0'; vars.secondaryColor = '#173b44'; vars.tertiaryColor = '#102b33';
      vars.clusterBkg = '#173b44'; vars.clusterBorder = '#3a6970'; vars.titleColor = '#f2f0e6';
      vars.edgeLabelBackground = '#12313a';
    } else {   // base 主题下补一点中性配色，保证浅色底可读
      vars.primaryColor = '#e0f3ef'; vars.primaryTextColor = '#15313a'; vars.primaryBorderColor = '#8bc9c0';
      vars.lineColor = '#405b5a'; vars.secondaryColor = '#f5f3ee'; vars.tertiaryColor = '#eeece6';
      vars.clusterBkg = '#f9f6ed'; vars.clusterBorder = '#d9dfd8'; vars.titleColor = '#15313a';
      vars.edgeLabelBackground = '#f5f2e9';
    }
    try {
      window.mermaid.initialize({
        // strict：由 mermaid 自身再净化一次标签文本（纵深防御）；节点标签本就是
        // Python 侧 sanitize_label 清洗过的纯文本 + <br/>，strict 不影响折行。
        startOnLoad: false, securityLevel: 'strict', fontFamily: FONT_STACK, themeVariables: vars,
        theme: 'base',                                        // 两种模式共用可控主题变量，避免默认暗色灰阶覆盖
        // 紧凑布局：间距 / 内边距整体调小（标签换行由 Python 端负责，前端不改写标签）
        flowchart: { htmlLabels: false, curve: 'basis', nodeSpacing: 34, rankSpacing: 56, useMaxWidth: false, padding: 10 } });
      return true;
    } catch (e) { console.log('[看板] mermaid.initialize 失败：', e); return false; }
  }
  /** 清理 mermaid 在 body 上留下的临时节点（失败时可能残留 #d<id>） */
  function cleanupTemp(id) {
    var ids = [id, 'd' + id], n;
    for (var i = 0; i < ids.length; i++) {
      n = document.getElementById(ids[i]);
      if (n && n.parentNode === document.body) n.parentNode.removeChild(n);
    }
  }
  /** 从确定性生成的 Mermaid 源码提取节点关系，供双击折叠子节点使用。 */
  var CHILDREN = (function () {
    var graph = {}, lines = PRISTINE.split(/\r?\n/), match;
    for (var i = 0; i < lines.length; i++) {
      match = lines[i].match(/^\s*([A-Za-z][\w]*)\s*-->\s*(?:\|[^|]*\|\s*)?([A-Za-z][\w]*)/);
      if (match && NODE_BY_ID[match[1]] && NODE_BY_ID[match[2]]) {
        if (!graph[match[1]]) graph[match[1]] = [];
        graph[match[1]].push(match[2]);
      }
    }
    return graph;
  })();
  function hiddenNodes() {
    var hidden = {}, starts = Object.keys(state.collapsedNodes), visited = {}, queue, id, children, i, j;
    for (i = 0; i < starts.length; i++) {
      queue = (CHILDREN[starts[i]] || []).slice(); visited = {};
      while (queue.length) {
        id = queue.shift();
        if (visited[id] || id === starts[i]) continue;
        visited[id] = true; hidden[id] = true;
        children = CHILDREN[id] || [];
        for (j = 0; j < children.length; j++) queue.push(children[j]);
      }
    }
    return hidden;
  }
  /** 首屏只呈现论文的关键分区节点，完整关系可通过「显示全部」展开。 */
  function overviewSource() {
    var chosen = {}, counts = {}, section, node, i, label, lines = ['flowchart LR'];
    function clean(value) {
      return str(value, '').replace(/[<>"|\[\]{}&]/g, ' ').replace(/\s+/g, ' ').trim();
    }
    function parent() {
      for (var j = 0; j < arguments.length; j++) if (chosen[arguments[j]]) return arguments[j];
      return '';
    }
    function connect(from, to) {
      if (from && to && chosen[from] && chosen[to] && from !== to)
        lines.push('  ' + chosen[from].id + ' --> ' + chosen[to].id);
    }
    for (i = 0; i < NODES.length; i++) {
      node = NODES[i]; section = node.section;
      counts[section] = (counts[section] || 0) + 1;
      if (!chosen[section]) chosen[section] = node;
    }
    for (i = 0; i < SECTIONS.length; i++) {
      section = SECTIONS[i].key; node = chosen[section];
      if (!node || section === 'glossary') continue;
      label = clean(SECTIONS[i].title);
      label += '<br/>' + clean(node.label).slice(0, 12);
      if (counts[section] > 1) label += ' · ' + counts[section] + ' 项';
      lines.push('  ' + node.id + '["' + label + '"]');
    }
    connect('problem', 'arguments');
    connect('problem', 'method');
    connect(parent('arguments', 'method', 'problem'), 'contributions');
    connect(parent('method', 'arguments', 'problem'), 'evidence');
    connect(parent('evidence', 'method', 'problem'), 'conclusions');
    connect(parent('method', 'evidence', 'problem'), 'limitations');
    connect(parent('conclusions', 'evidence', 'problem'), 'critique');
    return lines.join('\n');
  }
  /** 主干或节点折叠时过滤源码，交给 Mermaid 重新计算节点、分区与连线的位置。 */
  function visibleSource() {
    if (state.overviewMode) return overviewSource();
    if (!state.trunkMode && !Object.keys(state.collapsedNodes).length) return PRISTINE;
    var lines = PRISTINE.split(/\r?\n/), output = [], hidden = hiddenNodes();
    var inSection = '', skipSection = false, line, m, endpointA, endpointB, section;
    for (var i = 0; i < lines.length; i++) {
      line = lines[i];
      m = line.match(/^\s*subgraph\s+([A-Za-z][\w]*)/);
      if (m) {
        inSection = sectionOfGroup(m[1]) || '';
        skipSection = !!(state.trunkMode && inSection && inSection !== 'root' && TRUNK.indexOf(inSection) < 0);
        if (!skipSection) output.push(line);
        continue;
      }
      if (inSection && /^\s*end\s*$/.test(line)) {
        if (!skipSection) output.push(line);
        inSection = ''; skipSection = false; continue;
      }
      if (skipSection) continue;
      m = line.match(/^\s*([A-Za-z][\w]*)\s*-->\s*(?:\|[^|]*\|\s*)?([A-Za-z][\w]*)/);
      if (m) {
        endpointA = m[1]; endpointB = m[2];
        if (hidden[endpointA] || hidden[endpointB]) continue;
        section = sectionOfGroup(endpointA) || sectionOfId(endpointA);
        if (state.trunkMode && section && section !== 'root' && TRUNK.indexOf(section) < 0) continue;
        section = sectionOfGroup(endpointB) || sectionOfId(endpointB);
        if (state.trunkMode && section && section !== 'root' && TRUNK.indexOf(section) < 0) continue;
      } else if (inSection) {
        m = line.match(/^\s*([A-Za-z][\w]*)\s*[\[({]/);
        if (m && hidden[m[1]]) continue;
      }
      output.push(line);
    }
    return output.join('\n');
  }
  /** 主渲染入口：成功注入 SVG，失败进入兜底视图（绝不白屏） */
  async function renderDiagram(animate, anchorId) {
    hideTip(true);                                               // 重绘后旧节点引用失效，先收提示
    if (!PRISTINE) { state.renderOk = false; return showFallback('payload.diagram.mermaid 为空，无法渲染导图。', ''); }
    if (!initMermaid()) { state.renderOk = false; return showFallback('页面中未找到 Mermaid 运行库（window.mermaid），已切换为文字兜底视图。', PRISTINE); }
    var seq = ++state.renderSeq, id = 'mmd' + seq + 'x' + Date.now().toString(36);
    var src = visibleSource(), i, anchor = NODE_BY_ID[anchorId], anchorRect;
    if (anchor && anchor.__el) {
      anchorRect = anchor.__el.getBoundingClientRect();
      anchorRect = { x: anchorRect.left + anchorRect.width / 2, y: anchorRect.top + anchorRect.height / 2 };
    }
    for (i = 0; i < NODES.length; i++) NODES[i].__el = null;    // 清掉旧 DOM 引用
    state.nodeEls = []; state.edgeEls = [];
    try {
      var tRender0 = performance.now();
      var res = await window.mermaid.render(id, src);
      var tRender1 = performance.now();
      if (seq !== state.renderSeq) return;                      // 旧异步结果不能覆盖新视图
      var svg = (res && res.svg) ? res.svg : (typeof res === 'string' ? res : '');
      if (!svg) throw new Error('Mermaid 未返回 SVG 内容');
      el.host.innerHTML = svg;
      state.renderOk = true;
      try { afterRender(animate, anchorId, anchorRect); } catch (e2) { console.log('[看板] 渲染后处理异常：', e2); }
      // 分阶段耗时（控制台可查）：布局渲染 / 后处理，用于导图性能调优
      window.__renderPerf = { srcChars: src.length, mermaidMs: Math.round(tRender1 - tRender0),
        postMs: Math.round(performance.now() - tRender1), totalMs: Math.round(performance.now() - tRender0) };
    } catch (err) {
      if (seq !== state.renderSeq) return;
      state.renderOk = false;
      showFallback('Mermaid 渲染失败：' + (err && err.message ? err.message : String(err)), src);
    } finally { cleanupTemp(id); }
  }
  /** 渲染成功后收尾：打标签、缓存元素、恢复分区隐藏与高亮选中、适应窗口 */
  function afterRender(animate, anchorId, anchorRect) {
    var matched = tagNodes(), n;
    collectElements();
    applySectionVisibility();                                    // 恢复图例 / 主干模式下的分区隐藏状态（主题切换重绘也不丢）
    applySearchDim(state.hits);
    if (state.selectedId) { n = NODE_BY_ID[state.selectedId]; if (n && n.__el) n.__el.classList.add('selected'); }
    if (!matched) console.log('[看板] 未匹配到任何节点；图例与搜索仍可用。');
    // 折叠节点时保留原缩放与该节点在屏幕上的位置，避免每次重排都切换视角。
    n = NODE_BY_ID[anchorId];
    if (anchorRect && n && n.__el) {
      var nextRect = n.__el.getBoundingClientRect();
      state.x += anchorRect.x - (nextRect.left + nextRect.width / 2);
      state.y += anchorRect.y - (nextRect.top + nextRect.height / 2);
      applyTransform();
    } else fitView(true, animate, state.trunkMode ? 1.1 : 1);
  }
  /* ==================== 4. 节点识别：SVG .node ↔ payload.nodes ==================== */
  function idFromElement(g) {
    var raw = str(g.getAttribute('id'), ''), m;
    if (!raw) return '';
    m = raw.match(/^flowchart-(.+?)-\d+$/);              // mermaid v10: flowchart-<id>-<n>
    return m ? m[1] : (raw.indexOf('flowchart-') === 0 ? raw.slice(10) : raw);
  }
  function labelOfElement(g) {
    var n = g.querySelector('.nodeLabel') || g.querySelector('foreignObject');
    return String(n ? n.textContent : (g.textContent || '')).replace(/\s+/g, ' ').trim();
  }
  /** 给 SVG 节点打 data-id；id 反查失败时按标签文本回退。
      交互（悬停 tooltip / 点击选中 / 分区折叠）统一在 #mermaidHost 上事件委托（见 bindHostInteractions）。 */
  function tagNodes() {
    var svg = el.host.querySelector('svg');
    if (!svg) return 0;
    var groups = svg.querySelectorAll('g.node'), matched = 0, byLabel = 0, unmatched = 0, g, node, k, rid, i;
    for (i = 0; i < groups.length; i++) {
      g = groups[i]; node = NODE_BY_ID[idFromElement(g)] || null;
      if (!node) { k = normKey(labelOfElement(g)); if (k && NODE_BY_LABEL[k]) { node = NODE_BY_LABEL[k]; byLabel++; } }
      if (node) {
        g.setAttribute('data-id', node.id); node.__el = g; matched++;
        g.setAttribute('tabindex', '0'); g.setAttribute('role', 'button');
        g.setAttribute('aria-label', node.label + (CHILDREN[node.id] && CHILDREN[node.id].length ? '，双击折叠或展开子节点' : '，点击查看详情'));
      }
      else { unmatched++; rid = g.getAttribute('id'); if (rid) g.setAttribute('data-id', rid); }
    }
    var clusters = svg.querySelectorAll('g.cluster'), sec;     // 子图也标记分区（分区显隐切类时定位用；切换入口仅限左下角图例）
    for (i = 0; i < clusters.length; i++) {
      sec = sectionOfGroup(idFromElement(clusters[i]));
      if (sec) clusters[i].setAttribute('data-section', sec);
    }
    console.log('[看板] 节点匹配 ' + matched + '/' + groups.length + '（按标签回退 ' + byLabel + ' 个，未匹配 ' + unmatched + ' 个）');
    return matched;
  }
  /** 缓存节点 / 连线元素，供悬停、搜索、导出复用 */
  function collectElements() {
    var svg = el.host.querySelector('svg'), edges;
    if (!svg) { state.nodeEls = []; state.edgeEls = []; return; }
    state.nodeEls = Array.prototype.slice.call(svg.querySelectorAll('g.node'));
    edges = svg.querySelectorAll('.edgePath, .flowchart-link');
    state.edgeEls = Array.prototype.filter.call(edges, function (e, i) { return edges[i] === e; });
  }
  /** 2b：节点 / 分区交互统一事件委托 —— 原来每节点绑 click/mouseenter/mouseleave 共 3 个监听，
      40+ 节点就是 120+ 个；现在整个 SVG 只挂 3 个监听在 #mermaidHost 上。 */
  function bindHostInteractions() {
    var hoverEl = null;
    function nodeOf(g) { return g ? (NODE_BY_ID[g.getAttribute('data-id')] || null) : null; }
    el.host.addEventListener('mouseover', function (ev) {
      var g = ev.target && ev.target.closest ? ev.target.closest('g.node') : null;
      if (g === hoverEl) return;                                 // 同一节点内部子元素间移动不重触发
      hoverEl = g;
      var node = nodeOf(g);
      if (!node) return;                                         // 未匹配节点保持无提示行为
      highlightOnly(g);
      scheduleTip(node);                                         // 悬停 300ms 后浮出提示
    });
    el.host.addEventListener('mouseout', function (ev) {
      if (!hoverEl) return;
      var to = ev.relatedTarget;
      if (to && to.closest && to.closest('g.node') === hoverEl) return;   // 仍停留在原节点内
      hoverEl = null;
      clearHighlight();
      hideTip(true);
    });
    el.host.addEventListener('click', function (ev) {
      if (Date.now() - state.lastDragEnd < 300) return;          // 刚拖拽完，忽略误触
      var t = (ev.target && ev.target.closest) ? ev.target : null;
      if (!t) return;
      var ng = t.closest('g.node');
      if (ng) {
        var node = nodeOf(ng);
        if (!node) return;
        ev.stopPropagation();                                    // 不落入画布空白点击（clearSelection）
        selectNode(ng, node);
      }
      // 分区显隐不再响应画布内点击（此前点击分区标题/背景会单点隐藏分区，容易误触）；
      // 该功能仅限于左下角图例（见 bindEvents 里 #legend 的委托处理）。
    });
    el.host.addEventListener('dblclick', function (ev) {
      var g = ev.target && ev.target.closest ? ev.target.closest('g.node') : null;
      var node = nodeOf(g);
      if (!node || !CHILDREN[node.id] || !CHILDREN[node.id].length) return;
      ev.preventDefault(); ev.stopPropagation();
      toggleNodeChildren(node);
    });
    el.host.addEventListener('keydown', function (ev) {
      var g = ev.target && ev.target.closest ? ev.target.closest('g.node') : null;
      var node = nodeOf(g);
      if (!node || (ev.key !== 'Enter' && ev.key !== ' ')) return;
      ev.preventDefault();
      if (ev.shiftKey && CHILDREN[node.id] && CHILDREN[node.id].length) toggleNodeChildren(node);
      else selectNode(g, node);
    });
  }
  function toggleNodeChildren(node) {
    if (state.overviewMode) {
      state.overviewMode = false;
      showNotice('已展开完整导图。');
      renderDiagram(true, node.id);
      return;
    }
    if (state.collapsedNodes[node.id]) delete state.collapsedNodes[node.id];
    else state.collapsedNodes[node.id] = true;
    showNotice((state.collapsedNodes[node.id] ? '已折叠' : '已展开') + '「' + node.label + '」的后续节点。');
    if (state.selectedId && hiddenNodes()[state.selectedId]) clearSelection();
    renderDiagram(true, node.id);
  }
  /* ==================== 5. 分区识别与可见性控制（2a：切类，不重建 SVG） ==================== */
  /** subgraph 名 → 分区 key（如 contributions_grp → contributions） */
  function sectionOfGroup(name) {
    var n = str(name, '').replace(/^flowchart-/, '').replace(/-\d+$/, ''), base;
    if (!n) return null;
    base = n.replace(/_grp$/i, '');
    return SECTION_KEYS[base] ? base : (SECTION_KEYS[n] ? n : null);
  }
  /** 节点 id → 分区 key（优先查 payload.nodes，其次按 <section>_ 前缀） */
  function sectionOfId(id) {
    var n = str(id, ''), i;
    if (!n) return null;
    if (ID_SECTION[n]) return ID_SECTION[n];
    i = n.indexOf('_');
    return (i > 0 && SECTION_KEYS[n.slice(0, i)]) ? n.slice(0, i) : null;
  }
  /** 分区是否应隐藏：图例开关 ∪ 主干模式（root 概览永不隐藏）。
      搜索过滤（computeHidden）与可见性打类（applySectionVisibility）共用这一份判断，
      「仅看主干」与图例隐藏天然叠加一致。 */
  function sectionHidden(key) {
    if (!key || key === 'root') return false;
    if (state.legendOff[key]) return true;
    return state.trunkMode && TRUNK.indexOf(key) < 0;
  }
  /** 当前隐藏分区集合（搜索时跳过这些分区用） */
  function computeHidden() {
    var hidden = {}, i;
    for (i = 0; i < SECTIONS.length; i++) {
      if (sectionHidden(SECTIONS[i].key)) hidden[SECTIONS[i].key] = true;
    }
    return hidden;
  }
  /** 一条连线是否随分区隐藏：端点（LS-/LE- 类，或 id 的 L-<a>-<b>-<n> 兜底解析）
      任一落在被隐藏分区即隐去；端点无法解析时保守保留。 */
  function edgeHidden(edgeEl) {
    var ends = [], cls = edgeEl.classList, i, m, id, toks;
    for (i = 0; i < cls.length; i++) {
      if ((m = cls[i].match(/^LS-(.+)$/)) || (m = cls[i].match(/^LE-(.+)$/))) ends.push(m[1]);
    }
    id = str(edgeEl.getAttribute('id'), '');
    if (!ends.length && /^L-/.test(id)) {
      toks = id.split('-');
      if (toks.length > 2 && /^\d+$/.test(toks[toks.length - 1])) ends = toks.slice(1, -1);
    }
    for (i = 0; i < ends.length; i++) {
      var sec = sectionOfGroup(ends[i]) || sectionOfId(ends[i]);
      if (sec && sectionHidden(sec)) return true;
    }
    return false;
  }
  /** 2a 核心：只切换 .sec-hidden 类实现分区显隐，SVG 不重建（毫秒级，替代旧的全量 mermaid 重渲染）。
      覆盖 cluster 背景、分区内节点、端点涉及被隐藏分区的连线，以及与连线同序的边标签；
      布局按全图计算保持稳定，隐藏分区位置留白，再次展开不跳动。 */
  function applySectionVisibility() {
    var svg = el.host.querySelector('svg');
    if (!svg) return;
    var clusters = svg.querySelectorAll('g.cluster[data-section]'), i, sec;
    for (i = 0; i < clusters.length; i++) {
      clusters[i].classList.toggle('sec-hidden', sectionHidden(clusters[i].getAttribute('data-section')));
    }
    var nodes = svg.querySelectorAll('g.node');
    for (i = 0; i < nodes.length; i++) {
      sec = sectionOfId(nodes[i].getAttribute('data-id') || idFromElement(nodes[i]));
      nodes[i].classList.toggle('sec-hidden', !!sec && sectionHidden(sec));
    }
    var links = svg.querySelectorAll('path.flowchart-link');
    var labels = svg.querySelectorAll('g.edgeLabels > g.edgeLabel');
    var zip = links.length > 0 && links.length === labels.length;   // mermaid 按同一顺序生成连线与边标签
    for (i = 0; i < links.length; i++) {
      var hid = edgeHidden(links[i]);
      links[i].classList.toggle('sec-hidden', hid);
      if (zip) labels[i].classList.toggle('sec-hidden', hid);
    }
  }
  /* ==================== 6. 图例（分区显隐） ==================== */
  function sectionTitle(key) {
    for (var i = 0; i < SECTIONS.length; i++) if (SECTIONS[i].key === key) return SECTIONS[i].title;
    return key;
  }
  function buildLegend() {
    if (!el.legend) return;
    el.legend.innerHTML = '';
    for (var i = 0; i < SECTIONS.length; i++) {
      var s = SECTIONS[i], item, dot, tx;
      if (s.key === 'root') continue;                           // root 不入图例
      item = document.createElement('div'); dot = document.createElement('span'); tx = document.createElement('span');
      item.className = 'item' + (state.legendOff[s.key] ? ' off' : '');
      item.setAttribute('data-section', s.key);
      // 键盘可达（3）：图例项以 button 语义暴露，Tab 可聚焦，Enter / Space 触发
      //（click / keydown 事件在 #legend 上统一委托，见 bindEvents —— 重建图例不影响监听）
      item.setAttribute('role', 'button');
      item.tabIndex = 0;
      item.setAttribute('aria-pressed', state.legendOff[s.key] ? 'true' : 'false');
      item.title = '点击显示 / 隐藏「' + s.title + '」分区';
      dot.className = 'dot'; dot.style.background = s.color; tx.textContent = s.title;
      item.appendChild(dot); item.appendChild(tx);
      el.legend.appendChild(item);
    }
    el.legend.style.display = SECTIONS.length > 1 ? '' : 'none';
  }
  function toggleSection(key) {
    if (!key || key === 'root') return;
    if (state.legendOff[key]) delete state.legendOff[key]; else state.legendOff[key] = true;
    buildLegend(); setStatus();
    showNotice('已' + (state.legendOff[key] ? '隐藏' : '显示') + '分区：' + sectionTitle(key));
    applySectionVisibility();                                    // 2a：切类即可，不重建 SVG
    if (el.search && el.search.value) runSearch(el.search.value); // 隐藏分区不再参与搜索，命中集合同步刷新
  }
  /* ==================== 7. 缩放 / 平移 ==================== */
  var transformRaf = 0;
  /** 2b：拖拽 / 滚轮等高频路径用 rAF 合帧 —— 一帧最多写一次 transform（2b） */
  function requestTransform() {
    if (transformRaf) return;
    transformRaf = window.requestAnimationFrame(function () {
      transformRaf = 0;
      applyTransform();
    });
  }
  function applyTransform() {
    el.canvas.style.transform = 'translate(' + state.x + 'px,' + state.y + 'px) scale(' + state.s + ')';
    if (el.zoomLabel) el.zoomLabel.textContent = Math.round(state.s * 100) + '%';
  }
  /** 以视口内某点为锚点缩放（滚轮），比例限制在 0.2 ~ 3.0；deferApply = 高频路径合帧写 */
  function zoomAt(clientX, clientY, factor, deferApply) {
    var r = el.wrap.getBoundingClientRect(), px = clientX - r.left, py = clientY - r.top;
    var ns = clamp(state.s * factor, 0.2, 3.0), k = ns / state.s;
    state.x = px - (px - state.x) * k;
    state.y = py - (py - state.y) * k;
    state.s = ns;
    if (deferApply) requestTransform(); else applyTransform();
  }
  function zoomCenter(factor) {
    var r = el.wrap.getBoundingClientRect(), n = NODE_BY_ID[state.selectedId], nr;
    // 选中节点后使用按钮缩放，以节点当前位置为锚点，画面不会跳往另一处。
    if (n && n.__el) {
      nr = n.__el.getBoundingClientRect();
      zoomAt(nr.left + nr.width / 2, nr.top + nr.height / 2, factor);
    } else zoomAt(r.left + r.width / 2, r.top + r.height / 2, factor);
  }
  function resetView() { state.s = 1; state.x = 0; state.y = 0; applyTransform(); }
  /** 让整张图适应视口（留边距） */
  var viewAnimationTimer = 0;
  function animateView() {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    if (viewAnimationTimer) clearTimeout(viewAnimationTimer);
    el.canvas.style.transition = 'transform 360ms cubic-bezier(.2,.8,.2,1)';
    viewAnimationTimer = setTimeout(function () { el.canvas.style.transition = ''; viewAnimationTimer = 0; }, 390);
  }
  function fitView(silent, animate, magnify) {
    var svg = el.host.querySelector('svg'), bb, wr, s0, sr, lw, lh, cb, lx, ly, margin, availW, availH, ns;
    if (!svg) return;
    wr = el.wrap.getBoundingClientRect();
    if (wr.width < 10 || wr.height < 10) return;                // 地图页不可见时不处理
    s0 = state.s || 1; sr = svg.getBoundingClientRect(); lw = sr.width / s0; lh = sr.height / s0;
    if (!lw || !lh) { try { bb = svg.getBBox(); lw = bb.width; lh = bb.height; } catch (e) { return; } }
    if (!lw || !lh) return;
    cb = el.canvas.getBoundingClientRect(); lx = (sr.left - cb.left) / s0; ly = (sr.top - cb.top) / s0; margin = 24;
    availW = Math.max(40, wr.width - margin * 2); availH = Math.max(40, wr.height - margin * 2);
    ns = clamp(Math.min(availW / lw, availH / lh) * (magnify || 1), 0.2, 3.0);
    state.s = ns; state.x = margin + (availW - lw * ns) / 2 - lx * ns; state.y = margin + (availH - lh * ns) / 2 - ly * ns;
    if (animate) animateView();
    applyTransform();
    if (!silent) console.log('[看板] 适应窗口，缩放 ' + Math.round(ns * 100) + '%');
  }
  /** 把某个节点元素移到视口中心 */
  function centerOn(target) {
    var wr, cb, nb;
    if (!target || !target.getBoundingClientRect) return;
    wr = el.wrap.getBoundingClientRect();
    if (wr.width < 10) return;
    cb = el.canvas.getBoundingClientRect(); nb = target.getBoundingClientRect();
    if (!nb.width && !nb.height) return;
    state.x = wr.width / 2 - (nb.left + nb.width / 2 - cb.left);   // 局部坐标 × s0 后与 s0 约掉
    state.y = wr.height / 2 - (nb.top + nb.height / 2 - cb.top);
    applyTransform();
  }
  /** 清掉浏览器残留的文字选区（拖动画布时绝不能带选区） */
  function clearDomSelection() {
    try {
      var sel = window.getSelection();
      if (sel && sel.rangeCount) sel.removeAllRanges();
    } catch (e) { /* 老浏览器忽略 */ }
  }
  function bindPanZoom() {
    el.wrap.style.touchAction = 'none';                         // 触屏手势由脚本接管
    el.wrap.addEventListener('wheel', function (ev) {
      ev.preventDefault();
      hideTip(true);                                            // 缩放时立即收起提示
      zoomAt(ev.clientX, ev.clientY, clamp(Math.pow(1.0015, -ev.deltaY), 0.4, 2.5), true);   // rAF 合帧
    }, { passive: false });
    el.wrap.addEventListener('pointerdown', function (ev) {
      if (ev.button !== 0) return;
      var t = ev.target;
      pan.active = true; pan.moved = false;
      pan.sx = ev.clientX; pan.sy = ev.clientY; pan.ox = state.x; pan.oy = state.y;
      pan.onNode = !!(t && t.closest && t.closest('g.node'));    // 节点上不起拖拽，保证点击可用
      pan.fromLegend = !!(t && t.closest && t.closest('.legend'));
      pan.pointerId = ev.pointerId;
      hideTip(true);                                            // 按下即收提示，避免挡住拖拽
      clearDomSelection();                                      // 清掉上一次拖拽可能残留的选区
      // 交互元素（图例项 / 节点 / 控件）不阻止默认行为，也绝不能在此 setPointerCapture：
      // pointerdown 时捕获指针会把后续 pointerup（及 click）重定向到画布容器，
      // click 落在容器而非图例项 / 节点上 —— 这正是图例（与节点）点击失效的根因。
      // 因此改为：真正开始拖拽（移动超阈值）时才捕获（见下方 pointermove）。
      var interactive = !!(t && t.closest && t.closest('.legend, g.node, input, textarea, select, button, a'));
      if (!interactive && ev.cancelable) ev.preventDefault();   // 仅空白画布上阻止文字选区 / 原生拖放
    });
    el.wrap.addEventListener('pointermove', function (ev) {
      var dx, dy;
      if (!pan.active) return;
      dx = ev.clientX - pan.sx; dy = ev.clientY - pan.sy;
      if (!pan.moved) {
        if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return;       // 4px 阈值区分点击与拖拽
        if (pan.onNode || pan.fromLegend) { pan.active = false; return; }
        pan.moved = true;
        try { el.wrap.setPointerCapture(pan.pointerId); } catch (e) { /* 忽略 */ }   // 拖拽真正开始才捕获，出窗仍能收尾
        el.wrap.classList.add('dragging');
        hideTip(true);
        clearDomSelection();                                    // 进入拖拽先清一次选区
      }
      state.x = pan.ox + dx; state.y = pan.oy + dy;
      requestTransform();                                       // 2b：rAF 合帧写 transform
      clearDomSelection();                                      // 拖拽中持续清除（按住起点拖出的选区）
      if (ev.cancelable) ev.preventDefault();
    });
    /** 1b：拖拽状态兜底复位 —— markDragEnd 时按拖拽结束计（抑制误触点击） */
    function resetPan(markDragEnd) {
      if (markDragEnd && pan.moved) state.lastDragEnd = Date.now();
      pan.active = false; pan.moved = false;
      el.wrap.classList.remove('dragging');
    }
    function endPan(ev) {
      resetPan(true);
      clearDomSelection();                                      // 收尾再清一次，选区不残留
      try { el.wrap.releasePointerCapture(ev.pointerId); } catch (e) { /* 忽略 */ }
    }
    el.wrap.addEventListener('pointerup', endPan);
    el.wrap.addEventListener('pointercancel', endPan);
    // 1b 兜底一：按下后未越过 4px 阈值就移出画布 —— 手势作废，pan.active 不残留
    //（已捕获的拖拽不受影响：捕获期间浏览器不会对 wrap 触发 pointerleave）
    el.wrap.addEventListener('pointerleave', function () {
      if (pan.active && !pan.moved) { resetPan(false); clearDomSelection(); }
    });
    // 1b 兜底二：窗口失焦（Alt+Tab / 切窗口）时强制收尾，避免回窗后鼠标未按下却继续拖动
    window.addEventListener('blur', function () {
      if (!pan.active) return;
      resetPan(pan.moved);
      try { el.wrap.releasePointerCapture(pan.pointerId); } catch (e) { /* 忽略 */ }
      clearDomSelection();
    });
    el.wrap.addEventListener('click', function (ev) {
      if (Date.now() - state.lastDragEnd < 300) return;
      // 图例项 / 节点有自己的点击处理；放行冒泡会触发 clearSelection → renderOverview，
      // 把 toggleSection 刚插入的反馈提示（#mmNotice）立刻冲掉
      if (ev.target && ev.target.closest && ev.target.closest('.legend, g.node')) return;
      clearSelection();                                         // 点空白处取消选中
    });
  }
  /* ==================== 8. 悬停高亮 / 搜索淡出 ==================== */
  function highlightOnly(target) {
    var i, els = state.nodeEls;
    for (i = 0; i < els.length; i++) els[i].classList[els[i] === target ? 'remove' : 'add']('dim');
    // 连线始终保留对比度，避免一次悬停后整图连线持续变淡。
  }
  function clearHighlight() {
    for (var i = 0; i < state.edgeEls.length; i++) state.edgeEls[i].classList.remove('dim');
    applySearchDim(state.hits);
  }
  /** 按搜索命中表恢复淡出（hits 为 null 表示无搜索） */
  function applySearchDim(hits) {
    state.hits = hits || null;
    var els = state.nodeEls, e, id;
    for (var i = 0; i < els.length; i++) {
      e = els[i];
      if (!state.hits) { e.classList.remove('dim'); e.classList.remove('hit'); continue; }
      id = e.getAttribute('data-id');
      if (id && state.hits[id]) { e.classList.add('hit'); e.classList.remove('dim'); }
      else { e.classList.add('dim'); e.classList.remove('hit'); }
    }
  }
  /* ==================== 9. 搜索（防抖 150ms） ==================== */
  function runSearch(q) {
    var query = String(q || '').trim().toLowerCase(), n, hidden, hits, count, first;
    if (!query) { applySearchDim(null); setStatus(); return; }   // 清空只恢复淡出，不强制重新适应
    if (state.overviewMode) {
      state.overviewMode = false;
      renderDiagram(true).then(function () { runSearch(q); });
      return;
    }
    hidden = computeHidden(); hits = {}; count = 0; first = null;
    for (var i = 0; i < NODES.length; i++) {
      n = NODES[i];
      if (n.section && hidden[n.section]) continue;              // 已隐藏分区不参与搜索
      if ((n.label + ' ' + n.detail + ' ' + n.evidence).toLowerCase().indexOf(query) < 0) continue;
      hits[n.id] = true; count++;
      if (!first && n.__el) first = n.__el;
    }
    applySearchDim(hits);
    setStatus('搜索：' + count + ' 项匹配');
    if (first) centerOn(first);                                  // 居中第一个命中节点
  }
  var suggestionItems = [], suggestionIndex = -1;
  function closeSuggestions() {
    if (!el.suggestions) return;
    el.suggestions.hidden = true; suggestionIndex = -1;
    if (el.search) el.search.setAttribute('aria-expanded', 'false');
  }
  function renderSuggestions() {
    if (!el.suggestions || !el.search) return;
    var q = el.search.value.trim().toLowerCase(), used = {}, seenSection = {}, list = [], n, g, i, label;
    function add(kind, id, name) {
      var key = kind + ':' + id;
      if (used[key] || list.length >= 8) return;
      used[key] = true; list.push({ kind: kind, id: id, label: name });
    }
    for (i = 0; i < NODES.length; i++) {
      n = NODES[i];
      if (n.section && sectionHidden(n.section)) continue;
      if (q && (n.label + ' ' + n.detail).toLowerCase().indexOf(q) < 0) continue;
      if (!q && seenSection[n.section]) continue;
      add('节点', n.id, n.label);
      seenSection[n.section] = true;
      if (list.length >= (q ? 6 : 4)) break;
    }
    for (i = 0; i < GLOSSARY.length; i++) {
      g = GLOSSARY[i]; label = g.term || g.plain;
      if (!label || (q && (label + ' ' + g.plain).toLowerCase().indexOf(q) < 0)) continue;
      add('术语', String(i), label);
    }
    if (!list.length && !q) {
      for (i = 0; i < NODES.length && list.length < 4; i++) add('节点', NODES[i].id, NODES[i].label);
    }
    suggestionItems = list; suggestionIndex = -1;
    el.suggestions.innerHTML = list.map(function (item, index) {
      return '<button type="button" class="search-suggestion" role="option" data-index="' + index + '">' +
        '<span class="suggestion-kind">' + esc(item.kind) + '</span><span class="suggestion-label">' + esc(item.label) + '</span></button>';
    }).join('') || '<div class="suggestion-empty">没有找到匹配的节点或术语</div>';
    el.suggestions.hidden = false;
    el.search.setAttribute('aria-expanded', 'true');
  }
  async function chooseSuggestion(index) {
    var item = suggestionItems[index], node, card;
    if (!item) return;
    el.search.value = item.label; closeSuggestions();
    if (item.kind === '术语') {
      switchView('glossary');
      card = el.glossaryView && el.glossaryView.querySelector('.glossary-item[data-idx="' + item.id + '"]');
      if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    node = NODE_BY_ID[item.id];
    if (!node) return;
    switchView('map');
    if (state.overviewMode || state.trunkMode || Object.keys(state.collapsedNodes).length || sectionHidden(node.section)) {
      state.overviewMode = false; state.trunkMode = false; state.collapsedNodes = {};
      if (node.section) delete state.legendOff[node.section];
      buildLegend(); await renderDiagram(true);
    }
    applySearchDim({ [node.id]: true });
    if (node.__el) { centerOn(node.__el); selectNode(node.__el, node); }
  }
  var onSearchInput = debounce(function () { runSearch(el.search.value); }, 150);
  function clearSearch() {
    if (el.search) el.search.value = '';
    applySearchDim(null);
    closeSuggestions();
    setStatus();
  }
  /* ==================== 10. 节点详情面板 ==================== */
  var EXTRA_LABELS = { novelty: '新意', kind: '类型', severity: '严重度', analogy: '类比', why: '为什么重要',
    dimension: '维度', method: '方法', metric: '指标', dataset: '数据集', seq: '顺序' };
  var METHOD_TEXT = { exact: '精确匹配', squashed: '去空白匹配', fragment: '片段匹配',
    not_found: '未找到', too_short: '文本过短', none: '未校验' };
  function pageText(p) { return (typeof p === 'number' && isFinite(p)) ? ('第 ' + p + ' 页') : '未标注'; }
  /** 引用校验徽标：未找到 / 页码不符 / 已溯源 */
  function verifyBadge(v, page) {
    var p;
    v = obj(v);
    if (v.found === false) return '<span class="badge err">未在原文定位到</span>';
    if (v.mismatch === true) return '<span class="badge warn">页码不符</span>';
    if (v.found === true) {
      p = (typeof v.page === 'number') ? v.page : page;
      return '<span class="badge ok">已溯源 p.' + esc(typeof p === 'number' ? p : '?') + '</span>';
    }
    return '<span class="badge">未校验</span>';
  }
  /** 「定位到原文」说明行 */
  function locateLine(v, page) {
    var parts = [], p;
    v = obj(v);
    if (v.found === false) return '未在原文定位到该节点的证据摘录，请人工核对下方引用内容。';
    p = (typeof v.page === 'number') ? v.page : page;
    if (typeof p === 'number') parts.push('第 ' + p + ' 页');
    if (v.method) parts.push('匹配方式：' + (METHOD_TEXT[v.method] || v.method));
    if (typeof v.score === 'number' && isFinite(v.score)) parts.push('匹配度 ' + Math.round(v.score * 100) + '%');
    if (v.mismatch === true) parts.push('（模型标注为 ' + pageText(page) + '，与命中页不一致）');
    return parts.length ? parts.join(' · ') : '未提供溯源信息。';
  }
  function field(label, value) {
    if (!value) return '';
    return '<div class="field"><div class="field-label">' + esc(label) + '</div><div class="field-value">' + esc(value) + '</div></div>';
  }
  function quote(label, value) {
    if (!value) return '';
    return '<div class="field">' + (label ? '<div class="field-label">' + esc(label) + '</div>' : '') +
      '<div class="field-value quote' + (isLatin(value) ? ' en' : '') + '">' + esc(value) + '</div></div>';
  }
  /** 在上下文里标出证据摘录；优先整段匹配，换行差异时退回最长连续片段。 */
  function highlightedContext(context, evidence) {
    var source = str(context, ''), needle = str(evidence, '').trim(), hit = null, parts, i, match;
    if (!needle) return esc(source);
    var index = source.toLowerCase().indexOf(needle.toLowerCase());
    if (index >= 0) hit = { index: index, length: needle.length };
    if (!hit) {
      parts = needle.split(/\s+/).filter(function (part) { return part.length >= 6; });
      parts.sort(function (a, b) { return b.length - a.length; });
      for (i = 0; i < parts.length; i++) {
        index = source.toLowerCase().indexOf(parts[i].toLowerCase());
        if (index >= 0) { hit = { index: index, length: parts[i].length }; break; }
      }
    }
    if (!hit && needle.length >= 12) {
      for (i = Math.min(needle.length, 80); i >= 12; i -= 4) {
        match = needle.slice(0, i);
        index = source.toLowerCase().indexOf(match.toLowerCase());
        if (index >= 0) { hit = { index: index, length: match.length }; break; }
      }
    }
    if (!hit) return esc(source);
    return esc(source.slice(0, hit.index)) + '<mark class="ctx-highlight">' +
      esc(source.slice(hit.index, hit.index + hit.length)) + '</mark>' +
      esc(source.slice(hit.index + hit.length));
  }
  function showCloseBtn(show) { if (el.btnCloseDetail) el.btnCloseDetail.style.display = show ? '' : 'none'; }
  function renderNodeDetail(node) {
    var chip = node.sectionTitle || sectionTitle(node.section) || node.section || '未分区';
    var h = ['<div class="chips"><span class="chip">' + esc(chip) + '</span>'];
    if (node.extra.kind) h.push('<span class="chip">' + esc(text(node.extra.kind)) + '</span>');
    if (node.extra.severity) h.push('<span class="chip">严重度 ' + esc(text(node.extra.severity)) + '</span>');
    if (node.extra.dimension) h.push('<span class="chip">' + esc(text(node.extra.dimension)) + '</span>');
    h.push('</div>', '<div class="card"><h3>' + esc(node.label) + '</h3>',
      '<div style="margin-bottom:10px">' + verifyBadge(node.verify, node.page) +
      ' <span class="badge">' + esc(pageText(node.page)) + '</span></div>',
      field('详细说明', node.detail) || '<div class="muted">（该节点暂无详细说明）</div>', '</div>');
    if (node.evidence) h.push('<div class="card">' + quote('原文摘录', node.evidence) + '</div>');
    h.push('<div class="card">', field('页码', pageText(node.page)), field('定位到原文', locateLine(node.verify, node.page)));
    var ctx = (node.verify && typeof node.verify.context === 'string') ? node.verify.context.trim() : '';
    if (ctx) h.push('<details class="ctx"><summary>简要上下文 · 原文命中位置前后</summary><div class="ctx-body">' + highlightedContext(ctx, node.evidence) + '</div></details>');
    var keys = Object.keys(node.extra), extra = '', val;
    for (var i = 0; i < keys.length; i++) {                      // extra 全部展开为中文标签
      val = text(node.extra[keys[i]]);
      if (val) extra += field(EXTRA_LABELS[keys[i]] || keys[i], val);
    }
    if (extra) h.push('<div class="field-label">补充信息</div>' + extra);
    h.push('</div>');
    el.sideTitle.textContent = node.label;
    el.sideBody.innerHTML = h.join(''); el.sideBody.scrollTop = 0;
  }
  function selectNode(nodeEl, node) {
    var els = state.nodeEls, i;
    for (i = 0; i < els.length; i++) els[i].classList.remove('selected');
    if (nodeEl) nodeEl.classList.add('selected');
    state.selectedId = node.id;
    showCloseBtn(true);
    setSideCollapsed(false);                                     // 面板收起时点击节点自动展开
    try { renderNodeDetail(node); } catch (e) { console.log('[看板] 详情面板渲染异常：', e); }
  }
  function clearSelection() {
    var els = state.nodeEls, i;
    for (i = 0; i < els.length; i++) els[i].classList.remove('selected');
    state.selectedId = null;
    showCloseBtn(false);
    renderOverview();
  }
  /* ==================== 11. 概览面板（默认右侧内容） ==================== */
  function kv(k, v) { return '<div class="kv"><span class="k">' + esc(k) + '</span><span>' + esc(v) + '</span></div>'; }
  function renderOverview() {
    var h, li, i;
    if (!el.sideBody) return;
    el.sideTitle.textContent = '论文概览';
    h = ['<div class="card"><h3>' + esc(PAPER_TITLE) + '</h3>',
      '<div class="muted" style="font-size:12px;margin-bottom:8px">' + esc(str(META.source, '未提供来源文件')) + '</div>',
      '<div class="field-value">' + esc(str(FACTS.oneSentenceSummary, '未提供一句话总结。')) + '</div></div>',
      '<div class="card"><h3>统计</h3>',
      kv('节点总数', STATS.nodeCount), kv('分区数', STATS.sectionCount), kv('含证据节点', STATS.evidenceCount),
      kv('已溯源', STATS.verifiedCount), kv('页码不符', STATS.mismatchCount), kv('未溯源', STATS.unverifiedCount),
      kv('通俗解读条目', GLOSSARY.length), '</div>',
      '<div class="card"><h3>生成信息</h3>',
      kv('模型', str(META.model, '未知')), kv('模式', str(META.mode, '未知')),
      kv('总页数', num(META.pageCount, null) === null ? '未知' : META.pageCount + ' 页'),
      kv('正文字符数', num(META.charCount, null) === null ? '未知' : META.charCount + ' 字'),
      kv('生成耗时', num(META.pipelineSeconds, null) === null ? '未知' : META.pipelineSeconds + ' 秒'),
      kv('生成时间', str(META.generatedAt, '未知')), '</div>'];
    if (WARNINGS.length) {                                       // 生成警告
      li = '';
      for (i = 0; i < WARNINGS.length; i++) li += '<li>' + esc(WARNINGS[i]) + '</li>';
      h.push('<div class="notice">⚠ 生成提示<ul>' + li + '</ul></div>');
    }
    h.push('<div class="hint">点击图中任意节点查看详情与原文出处。</div>');
    el.sideBody.innerHTML = h.join(''); el.sideBody.scrollTop = 0;
  }
  /* ==================== 12. 通俗解读视图 ==================== */
  function renderGlossary() {
    var g, meta, items, j;
    if (!el.glossaryView) return;
    if (!GLOSSARY.length) { el.glossaryView.innerHTML = '<div class="hint">本论文未生成「通俗解读」条目。</div>'; return; }
    var h = ['<div class="hint" style="margin-bottom:14px">点击卡片可跳转到导图中对应节点（若存在）。</div>'];
    for (var i = 0; i < GLOSSARY.length; i++) {
      g = GLOSSARY[i]; meta = [];
      if (typeof g.page === 'number') meta.push(pageText(g.page));
      if (g.verify.found === false) meta.push('未在原文定位到');
      else if (g.verify.mismatch === true) meta.push('页码不符');
      h.push('<div class="glossary-item" data-idx="' + i + '">',
        '<div class="glossary-term">' + esc(g.term || '未命名术语') + ' ' + verifyBadge(g.verify, g.page) + '</div>');
      if (g.plain) h.push('<div style="margin-bottom:6px">' + esc(g.plain) + '</div>');
      if (g.analogy) h.push('<div class="muted" style="font-size:13px;margin-bottom:6px">💡 类比：' + esc(g.analogy) + '</div>');
      if (g.why) h.push('<div class="muted" style="font-size:13px;margin-bottom:6px">❓ 为什么重要：' + esc(g.why) + '</div>');
      if (meta.length) h.push('<div class="muted" style="font-size:12px">' + esc(meta.join(' · ')) + '</div>');
      if (g.evidence) h.push('<div style="margin-top:8px">' + quote('', g.evidence) + '</div>');
      h.push('</div>');
    }
    el.glossaryView.innerHTML = h.join('');
    items = el.glossaryView.querySelectorAll('.glossary-item');
    for (j = 0; j < items.length; j++) items[j].addEventListener('click', function () {
      gotoGlossaryNode(GLOSSARY[parseInt(this.getAttribute('data-idx'), 10)]);
    });
  }
  /** 术语卡片 → 导图节点：切到地图页、选中并居中 */
  function gotoGlossaryNode(g) {
    var key, hit = null, i, n, nk;
    if (!g) return;
    key = normKey(g.term);
    for (i = 0; i < NODES.length; i++) {                         // 优先 glossary 分区
      n = NODES[i];
      if (n.section !== 'glossary' && !/glossary|术语/i.test(n.section)) continue;
      nk = normKey(n.label);
      if (nk && key && (nk.indexOf(key) >= 0 || key.indexOf(nk) >= 0)) { hit = n; break; }
    }
    if (!hit) {                                                  // 再全局按标签找
      for (i = 0; i < NODES.length; i++) if (normKey(NODES[i].label) === key) { hit = NODES[i]; break; }
    }
    switchView('map');
    if (!hit) { showNotice('未在导图中找到与该术语对应的节点，已切换到导图视图。'); return; }
    if (!hit.__el) showNotice('节点「' + hit.label + '」当前被隐藏，请在图例中重新开启其分区。');
    selectNode(hit.__el, hit);
    if (hit.__el) centerOn(hit.__el);
  }
  /* ==================== 13. 批判性分析视图 ==================== */
  var CRIT_LABELS = [['data', '数据层面'], ['experiment', '实验层面'], ['method', '方法层面'],
    ['assumption', '关键假设'], ['applicationBoundary', '应用边界'], ['overall', '总体评价']];
  function chips(title, list, ordered) {
    var a = arr(list).map(text).filter(Boolean), h;
    if (!a.length) return '';
    h = '<div class="card"><h3>' + esc(title) + '</h3><div class="chips">';
    for (var i = 0; i < a.length; i++) h += '<span class="chip">' + esc((ordered ? (i + 1) + '. ' : '') + a[i]) + '</span>';
    return h + '</div></div>';
  }
  function renderCritique() {
    var h, cards = '', val, i;
    if (!el.critiqueView) return;
    h = [];
    if (PROBLEM.statement) {                                     // 核心问题卡片
      h.push('<div class="card"><h3>核心问题</h3><div style="margin-bottom:8px">' + esc(PROBLEM.statement) + '</div>');
      if (PROBLEM.whyItMatters) h.push(field('为什么重要', PROBLEM.whyItMatters));
      if (PROBLEM.evidence) h.push(quote('原文摘录' + (PROBLEM.page != null ? '（第 ' + PROBLEM.page + ' 页）' : ''), PROBLEM.evidence));
      h.push('</div>');
    }
    h.push(chips('待解问题', FACTS.openQuestions, false), chips('建议阅读路径', FACTS.readingPath, true));
    for (i = 0; i < CRIT_LABELS.length; i++) {
      val = text(CRITIQUE[CRIT_LABELS[i][0]]);
      if (!val) continue;                                        // 跳过空值
      cards += '<div class="crit-card"><div class="field-label">' + esc(CRIT_LABELS[i][1]) + '</div>' +
        '<div class="field-value">' + esc(val) + '</div></div>';
    }
    if (cards) h.push('<div class="card"><h3>批判性分析</h3>' + cards + '</div>');
    if (!h.length) h.push('<div class="hint">本论文未生成批判性分析内容。</div>');
    el.critiqueView.innerHTML = h.join('');
  }
  /* ==================== 13b. 原文视图（按页折叠展示提取到的正文） ==================== */
  /** 每页原文 → HTML：保留换行与段落（pre-wrap），## 标题行渲染成小标题样式 */
  function paperPageHtml(text) {
    var lines = String(text).split('\n'), out = [], line, i;
    for (i = 0; i < lines.length; i++) {
      line = lines[i];
      if (/^##\s+/.test(line)) out.push('<div class="paper-heading">' + esc(line.replace(/^##\s+/, '')) + '</div>');
      else out.push('<div class="paper-line">' + esc(line) + '</div>');
    }
    return out.join('');
  }
  function setPaperDetails(open) {
    if (!el.paperView) return;
    var ds = el.paperView.querySelectorAll('details.paper-page');
    for (var i = 0; i < ds.length; i++) ds[i].open = !!open;
  }
  var printPaperOpen = null;
  /** 打印原文前展开每一页，打印结束后恢复用户的阅读状态。 */
  function preparePaperPrint() {
    if (document.body.getAttribute('data-view') !== 'paper' || !el.paperView || printPaperOpen) return;
    var pages = el.paperView.querySelectorAll('details.paper-page');
    printPaperOpen = [];
    for (var i = 0; i < pages.length; i++) {
      printPaperOpen.push(pages[i].open);
      pages[i].open = true;
    }
  }
  function restorePaperAfterPrint() {
    if (!printPaperOpen || !el.paperView) return;
    var pages = el.paperView.querySelectorAll('details.paper-page');
    for (var i = 0; i < pages.length && i < printPaperOpen.length; i++) pages[i].open = printPaperOpen[i];
    printPaperOpen = null;
  }
  function renderPaper() {
    if (!el.paperView) return;
    if (!PAPER_PAGES.length) {
      el.paperView.innerHTML = '<div class="hint">本看板未附带论文原文（旧版产物或离线重渲染时缺少 PDF）。重新运行流水线即可生成。</div>';
      return;
    }
    var h = ['<div class="paper-toolbar"><span class="muted">正文 ' + PAPER_PAGES.length + ' 页 · 每页可折叠</span><span class="spacer"></span>',
      '<button class="btn" id="btnPaperExpandAll" type="button">全部展开</button>',
      '<button class="btn" id="btnPaperCollapseAll" type="button">全部折叠</button></div>'];
    for (var i = 0; i < PAPER_PAGES.length; i++) {
      var p = PAPER_PAGES[i];
      var pno = (typeof p.page === 'number') ? p.page : (i + 1);
      h.push('<details class="paper-page"' + (i === 0 ? ' open' : '') + '><summary>第 ' + pno + ' 页',
        '<span class="paper-page-len">' + p.text.length + ' 字</span></summary>',
        '<div class="paper-page-body">' + paperPageHtml(p.text) + '</div></details>');
    }
    el.paperView.innerHTML = h.join('');
    var be = document.getElementById('btnPaperExpandAll'), bc = document.getElementById('btnPaperCollapseAll');
    if (be) be.addEventListener('click', function () { setPaperDetails(true); });
    if (bc) bc.addEventListener('click', function () { setPaperDetails(false); });
  }
  /* ==================== 14. Mermaid 源码视图 ==================== */
  function noticeList(title, items, isErr) {
    var a = arr(items).map(text).filter(Boolean), s, i;
    if (!a.length) return '';
    s = '<div class="notice' + (isErr ? ' err' : '') + '"><b>' + esc(title) + '</b><ul>';
    for (i = 0; i < a.length; i++) s += '<li>' + esc(a[i]) + '</li>';
    return s + '</ul></div>';
  }
  function renderSource() {
    if (!el.sourceView) return;
    el.sourceView.style.whiteSpace = 'normal';                   // 头部按常规流排版，源码用 <pre>
    el.sourceView.innerHTML = ['<div class="card" style="margin-bottom:12px"><h3>源码与校验</h3>',
      kv('校验结果', VALIDATION.ok === false ? '存在错误' : '通过'),
      kv('错误数', arr(VALIDATION.errors).length), kv('警告数', arr(VALIDATION.warnings).length),
      kv('自动修复数', arr(VALIDATION.repairs).length), '</div>',
      noticeList('校验错误', VALIDATION.errors, true), noticeList('校验警告', VALIDATION.warnings, false),
      noticeList('自动修复', VALIDATION.repairs, false), noticeList('生成提示', WARNINGS, false),
      '<pre style="white-space:pre;margin:0;font-family:inherit;font-size:inherit;line-height:1.55">' +
      esc(PRISTINE || '（payload.diagram.mermaid 为空）') + '</pre>'].join('');
  }
  /* ==================== 15. 渲染失败兜底（错误 + 源码 + 节点列表） ==================== */
  function showFallback(message, source) {
    var h = ['<div class="fallback"><h3>⚠ 导图渲染失败</h3>',
      '<div class="notice err">' + esc(message) + '</div>',
      '<h3>原始 Mermaid 源码</h3><div class="mermaid-src">' + esc(source || PRISTINE || '（无源码）') + '</div>',
      '<h3>节点列表（' + NODES.length + '）</h3>'], n, i;
    if (!NODES.length) h.push('<div class="hint">payload.nodes 为空。</div>');
    for (i = 0; i < NODES.length; i++) {
      n = NODES[i];
      h.push('<div class="card"><h3>' + esc(n.label) + ' ' + verifyBadge(n.verify, n.page) + '</h3>',
        '<div class="muted" style="font-size:12px;margin-bottom:6px">' +
        esc((n.sectionTitle || n.section || '未分区') + ' · ' + pageText(n.page)) + '</div>');
      if (n.detail) h.push('<div style="margin-bottom:6px">' + esc(n.detail) + '</div>');
      if (n.evidence) h.push(quote('', n.evidence));
      h.push('</div>');
    }
    h.push('</div>');
    el.host.innerHTML = h.join('');                              // 绝不白屏
    state.nodeEls = []; state.edgeEls = [];
    for (i = 0; i < NODES.length; i++) NODES[i].__el = null;
    setStatus('render-error');
  }
  /* ==================== 16. 导出（全部离线） ==================== */
  function downloadBlob(blob, filename) {
    try {
      var url = URL.createObjectURL(blob), a = document.createElement('a');
      a.href = url; a.download = filename; a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { if (a.parentNode) a.parentNode.removeChild(a); URL.revokeObjectURL(url); }, 1500);
    } catch (e) { showNotice('下载失败：' + (e && e.message ? e.message : e)); }
  }
  /** SVG 尺寸：显式 width/height → 实际渲染尺寸 → getBBox() */
  function svgSize(svg) {
    var w = parseFloat(svg.getAttribute('width')), h = parseFloat(svg.getAttribute('height')), r, s, bb;
    if (!w || !h) { r = svg.getBoundingClientRect(); s = state.s || 1; if (r.width && r.height) { w = r.width / s; h = r.height / s; } }
    if (!w || !h) { try { bb = svg.getBBox(); w = bb.width; h = bb.height; } catch (e) { /* 忽略 */ } }
    if (!w || !h) { w = 1200; h = 800; }
    return { w: w, h: h };
  }
  function serializeSvg() {
    var svg = el.host.querySelector('svg'), out = '', size, clone, junk, bg, i;
    if (!svg) return '';
    size = svgSize(svg); clone = svg.cloneNode(true);
    // 清掉交互态（含 .sec-hidden）→ 导出完整图。理由：画布布局本就按全图计算，分区隐藏只是
    // 视觉遮蔽（会留白洞）；导出文件脱离页面样式后 .sec-hidden 类本就不生效，这里显式摘类，
    // 保证导出的 SVG / PNG 始终是布局完整、无破洞的完整图，与 .mmd 导出全量源码的语义一致。
    junk = clone.querySelectorAll('.dim, .hit, .selected, .sec-hidden');
    for (i = 0; i < junk.length; i++) {
      junk[i].classList.remove('dim'); junk[i].classList.remove('hit');
      junk[i].classList.remove('selected'); junk[i].classList.remove('sec-hidden');
    }
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');   // 命名空间（不是网络请求）
    clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
    clone.setAttribute('width', String(Math.ceil(size.w)));
    clone.setAttribute('height', String(Math.ceil(size.h)));
    if (!clone.getAttribute('viewBox')) clone.setAttribute('viewBox', '0 0 ' + Math.ceil(size.w) + ' ' + Math.ceil(size.h));
    bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');   // 背景，保证导出可读
    bg.setAttribute('width', '100%'); bg.setAttribute('height', '100%');
    bg.setAttribute('fill', isDark() ? '#1c1f27' : '#ffffff');
    clone.insertBefore(bg, clone.firstChild);
    try { out = new XMLSerializer().serializeToString(clone); }
    catch (e) { out = '<svg xmlns="http://www.w3.org/2000/svg">' + clone.innerHTML + '</svg>'; }
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + out;
  }
  function exportSvg() {
    var s = serializeSvg();
    if (!s) { showNotice('当前没有可导出的图形（渲染失败或尚未渲染）。'); return; }
    downloadBlob(new Blob([s], { type: 'image/svg+xml;charset=utf-8' }), safeName(PAPER_TITLE) + '.svg');
  }
  /** canvas.toBlob 缺失时退化为 dataURL 下载 */
  function dataUrlDownload(cv, filename) {
    try {
      var a = document.createElement('a');
      a.href = cv.toDataURL('image/png'); a.download = filename; a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { if (a.parentNode) a.parentNode.removeChild(a); }, 500);
    } catch (e) { showNotice('PNG 导出失败，请改用「下载 SVG」。'); }
  }
  function exportPng() {
    var svg = el.host.querySelector('svg'), s, size, scale = 2, w, h, filename, url = '', img;
    if (!svg) { showNotice('当前没有可导出的图形（渲染失败或尚未渲染）。'); return; }
    s = serializeSvg();
    if (!s) { showNotice('当前没有可导出的图形。'); return; }
    size = svgSize(svg);
    w = Math.min(8000, Math.max(1, Math.round(size.w * scale)));
    h = Math.min(8000, Math.max(1, Math.round(size.h * scale)));
    filename = safeName(PAPER_TITLE) + '.png';
    img = new Image();
    img.onload = function () {                                   // 2 倍分辨率绘制到 canvas
      try {
        var cv = document.createElement('canvas'), ctx;
        cv.width = w; cv.height = h;
        ctx = cv.getContext('2d');
        ctx.fillStyle = isDark() ? '#1c1f27' : '#ffffff';
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        if (url) URL.revokeObjectURL(url);
        if (typeof cv.toBlob === 'function') cv.toBlob(function (b) { b ? downloadBlob(b, filename) : dataUrlDownload(cv, filename); }, 'image/png');
        else dataUrlDownload(cv, filename);                      // 老浏览器兜底
      } catch (e) { showNotice('PNG 导出失败：' + (e && e.message ? e.message : e) + '，请改用「下载 SVG」。'); }
    };
    img.onerror = function () {
      if (url) URL.revokeObjectURL(url);
      showNotice('PNG 导出失败（图形转换异常，含 foreignObject 的图建议导出 SVG）。');
    };
    try {
      url = URL.createObjectURL(new Blob([s], { type: 'image/svg+xml;charset=utf-8' }));
      img.src = url;
    } catch (e) { img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(s); }  // 本地 data URI
  }
  function exportMmd() {
    if (!PRISTINE) { showNotice('没有可下载的 Mermaid 源码。'); return; }
    downloadBlob(new Blob([PRISTINE], { type: 'text/plain;charset=utf-8' }), safeName(PAPER_TITLE) + '.mmd');
  }
  /* ==================== 17. 主题 / 状态徽标 / 提示条 ==================== */
  function toggleTheme() {
    var next = isDark() ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { window.localStorage.setItem('paper-theme', next); } catch (e) { /* 隐私模式忽略 */ }
    // 导图全部颜色（节点框内/描边/文字、分区框、连线、边标签）都由 CSS 变量按 data-theme 驱动，
    // 切换主题无需重建 SVG —— 保留当前缩放和平移，也避免异步重绘造成的频闪与字体短暂模糊。
  }
  /** 本地服务打开时回到同源工作台；离线文件尝试最近的本机工作台地址。 */
  function goWorkbench() {
    var home = '/';
    if (window.location.protocol === 'file:') {
      home = 'http://127.0.0.1:8000/';
      try {
        var ref = new URL(document.referrer);
        if (/^(localhost|127\.0\.0\.1)$/.test(ref.hostname) && /^https?:$/.test(ref.protocol)) home = ref.origin + '/';
      } catch (e) { /* 离线直接打开时没有来源地址，沿用默认本机地址 */ }
    }
    window.location.assign(home);
  }
  function baseStatusText() {
    var t = STATS.nodeCount + ' 节点 · 已溯源 ' + STATS.verifiedCount + '/' + STATS.evidenceCount;
    if (STATS.mismatchCount > 0) t += ' · ' + STATS.mismatchCount + ' 处页码不符';
    if (STATS.unverifiedCount > 0) t += ' · ' + STATS.unverifiedCount + ' 未溯源';
    return t;
  }
  /** 无参 = 常规摘要；'render-error' = 渲染失败；其他字符串 = 临时提示 */
  function setStatus(mode) {
    if (!el.statusBadge) return;
    if (mode === 'render-error') { el.statusBadge.className = 'badge err'; el.statusBadge.textContent = '渲染失败 · 已切换文本视图'; return; }
    if (mode) { el.statusBadge.className = 'badge warn'; el.statusBadge.textContent = mode; return; }
    el.statusBadge.textContent = baseStatusText();
    el.statusBadge.className = (STATS.unverifiedCount > 0 || STATS.mismatchCount > 0) ? 'badge warn' : 'badge ok';
  }
  var noticeTimer = 0;
  function showNotice(msg, isErr) {
    var host, old, d;
    if (!msg) return;
    host = el.sideBody || document.body;
    old = document.getElementById('mmNotice');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    d = document.createElement('div');
    d.id = 'mmNotice';
    d.className = 'notice' + (isErr ? ' err' : '');
    d.textContent = msg;
    if (host === document.body) d.style.margin = '14px';
    host.insertBefore(d, host.firstChild);
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = setTimeout(function () { if (d.parentNode) d.parentNode.removeChild(d); }, 4600);
  }
  /* ==================== 18. 标签页 ==================== */
  function switchView(name) {
    var i, views = document.querySelectorAll('.view'), tabs = document.querySelectorAll('.tab');
    for (i = 0; i < views.length; i++) views[i].classList.toggle('active', views[i].getAttribute('data-view') === name);
    for (i = 0; i < tabs.length; i++) tabs[i].classList.toggle('active', tabs[i].getAttribute('data-view') === name);
    document.body.setAttribute('data-view', name);               // 顶栏里的导图工具组随视图显隐
    hideTip(true);
  }
  /* ==================== 19. 节点悬停提示（tooltip，纯本地数据） ==================== */
  /** 提示正文：说明截断到 ~120 字；页码 / 溯源状态来自 payload，不发任何请求 */
  function tipHtml(node) {
    var detail = str(node.detail, ''), more = detail.length > 120 ? detail.slice(0, 120) + '…' : detail;
    var meta = ['<span>' + esc(pageText(node.page)) + '</span>'], st = '', v = obj(node.verify);
    if (v.found === false) st = '<span class="tip-state err">未在原文定位到</span>';
    else if (v.mismatch === true) st = '<span class="tip-state warn">页码不符</span>';
    else if (v.found === true) st = '<span class="tip-state ok">已溯源' +
      (typeof v.page === 'number' ? ' p.' + v.page : '') + '</span>';
    if (st) meta.push(st);
    return '<div class="tip-label">' + esc(node.label) + '</div>' +
      '<div class="tip-detail' + (more ? '' : ' empty') + '">' + (more ? esc(more) : '（无说明）') + '</div>' +
      '<div class="tip-meta">' + meta.join('') + '</div>';
  }
  function hideTip(immediate) {
    if (state.tipTimer) { clearTimeout(state.tipTimer); state.tipTimer = 0; }
    state.tipNode = null;
    if (el.tip) el.tip.classList.remove('show');
  }
  function scheduleTip(node) {
    hideTip(true);
    state.tipNode = node;
    state.tipTimer = setTimeout(function () {
      state.tipTimer = 0;
      if (state.tipNode === node) showTip(node);
    }, 300);
  }
  /** 提示定位：锚在节点上方居中；放不下则移到下方，且始终留在画布内 */
  function showTip(node) {
    if (!el.tip || !node || !node.__el) return;
    el.tip.innerHTML = tipHtml(node);                            // 先填内容再定位（视口尺寸拿不到时内容也已就绪）
    el.tip.classList.add('show');
    el.tip.setAttribute('aria-hidden', 'false');
    var wr = el.wrap.getBoundingClientRect(), gr = node.__el.getBoundingClientRect();
    if (!wr.width || !gr.width || !gr.height) return;            // 画布不可见 / 测量失败时仅显示内容
    var tw = el.tip.offsetWidth, th = el.tip.offsetHeight;
    var cx = gr.left + gr.width / 2 - wr.left, top = gr.top - wr.top;
    var left = clamp(cx - tw / 2, 8, Math.max(8, wr.width - tw - 8));
    var y = top - th - 10;                                       // 默认在节点上方，不遮光标
    if (y < 6 || y + th > wr.height - 6) y = top + gr.height + 10;
    y = clamp(y, 6, Math.max(6, wr.height - th - 6));
    el.tip.style.left = Math.round(left) + 'px';
    el.tip.style.top = Math.round(y) + 'px';
  }
  /* ==================== 20. 详情面板收起 / 展开（状态记忆） ==================== */
  var SIDE_KEY = 'paper-side-collapsed';
  function applySideCollapsed() {
    var c = state.sideCollapsed;
    if (el.layout) el.layout.classList.toggle('side-collapsed', c);
    if (el.sidePanel) el.sidePanel.setAttribute('aria-hidden', c ? 'true' : 'false');
    if (el.btnToggleSide) {
      el.btnToggleSide.textContent = c ? '◀' : '▶';
      el.btnToggleSide.title = c ? '展开详情面板' : '收起详情面板';
      el.btnToggleSide.setAttribute('aria-expanded', c ? 'false' : 'true');
    }
  }
  function setSideCollapsed(collapsed, save) {
    state.sideCollapsed = !!collapsed;
    applySideCollapsed();
    if (save !== false) {                                        // 记住状态（隐私模式下静默失败）
      try { window.localStorage.setItem(SIDE_KEY, collapsed ? '1' : '0'); } catch (e) { /* 忽略 */ }
    }
  }
  function toggleSide() { setSideCollapsed(!state.sideCollapsed); }

  /* ==================== 20b. 左下角信息卡折叠（图例 + 标题徽章） ==================== */
  var DOCK_KEY = 'paper-dock-collapsed';
  function applyDockCollapsed() {
    if (el.cornerDock) el.cornerDock.setAttribute('data-collapsed', state.dockCollapsed ? 'true' : 'false');
    if (el.btnDockToggle) {
      el.btnDockToggle.textContent = state.dockCollapsed ? '▴ 信息' : '▾ 信息';
      el.btnDockToggle.title = state.dockCollapsed ? '展开左下角信息卡（图例与标题徽章）' : '折叠左下角信息卡（图例与标题徽章）';
      el.btnDockToggle.setAttribute('aria-expanded', state.dockCollapsed ? 'false' : 'true');
    }
  }
  function setDockCollapsed(collapsed, save) {
    state.dockCollapsed = !!collapsed;
    applyDockCollapsed();
    if (save !== false) {                                        // 记住状态（隐私模式下静默失败）
      try { window.localStorage.setItem(DOCK_KEY, collapsed ? '1' : '0'); } catch (e) { /* 忽略 */ }
    }
  }
  function toggleDock() { setDockCollapsed(!state.dockCollapsed); }

  /* ==================== 20c. 操作指南弹层 ==================== */
  /* 内容为静态说明文字（不含用户数据），集中展示看板全部操作方式 */
  var GUIDE_SECTIONS = [
    { icon: '🗺', title: '导图视图', items: [
      '点击节点 → 右侧详情面板查看标签、说明、原文摘录、页码与溯源结果',
      '双击有后续节点的节点可折叠或展开；键盘聚焦后按 Enter 查看、按 Shift+Enter 折叠或展开',
      '悬停节点浮出提示；点击空白处取消选中',
      '按住空白区域拖动平移；滚轮以光标为锚点缩放（0.2×–3×）',
      '顶栏 ＋ / － 缩放；选中节点后缩放以该节点为中心，不改变观察位置；⛶ 适应窗口、↺ 重置视图' ] },
    { icon: '🎛', title: '分区显隐', items: [
      '分区显隐仅在左下角图例操作：点击色块隐藏 / 显示对应分区',
      '顶栏 ⊞ 显示全部节点；⊟ 清晰概览按分区呈现关键节点',
      '画布内点击分区标题 / 背景不会隐藏分区（防止误触）' ] },
    { icon: '🔍', title: '搜索', items: [
      '顶部搜索框输入关键字（或 Ctrl/Cmd+F），命中节点高亮、其余变暗并定位到首个命中',
      '点击空搜索框可选推荐节点或术语，输入后可用方向键与 Enter 选择建议',
      'Esc 清空搜索并恢复显示' ] },
    { icon: '📋', title: '证据溯源', items: [
      '绿色徽标「已溯源」显示实测页码；黄色徽标「页码不符」提示与模型标注不一致；红色徽标「未在原文定位到」需人工核对',
      '详情面板中的「简要上下文」可折叠展开，摘要摘录会在上下文中高亮' ] },
    { icon: '📄', title: '原文浏览', items: [
      '「原文」标签页按页展示提取到的论文正文，保留段落与换行排版',
      '每页可折叠（默认展开第一页），并提供全部展开 / 全部折叠',
      '在原文视图打印时输出连续正文，不包含分页控件与看板界面',
      '章节标题行以加粗小标题突出显示' ] },
    { icon: '📖', title: '通俗解读与批判性分析', items: [
      '「通俗解读」：术语的大白话解释、类比与「为什么重要」，点击卡片跳回导图对应节点',
      '「批判性分析」：核心问题、待解问题、建议阅读路径与五维批判' ] },
    { icon: '💾', title: '导出 / 主题 / 打印', items: [
      '⬇ 导出：下载 SVG / PNG（2 倍分辨率）/ .mmd 源码，全部离线完成',
      '主题按钮切换深浅色并保持导图视角；打印按钮可打印当前视图',
      '顶栏 Home 按钮返回论文工作台，工作台可同时处理多份 PDF 并查看最近看板',
      '看板完全离线：断网双击即可打开，零外部网络请求' ] },
    { icon: '⌨️', title: '键盘操作', items: [
      'Ctrl/Cmd+F 聚焦搜索框',
      'Alt+Home 返回论文工作台',
      'Esc 逐层收起：节点提示 → 操作指南 → 导出菜单 → 详情选中 → 搜索',
      'Tab 遍历控件；图例项可用 Enter / Space 开关分区' ] }
  ];
  function renderGuide() {
    if (!el.guideBody) return;
    var h = [], s, i, j;
    for (i = 0; i < GUIDE_SECTIONS.length; i++) {
      s = GUIDE_SECTIONS[i];
      h.push('<div class="guide-section"><h3>' + s.icon + ' ' + esc(s.title) + '</h3><ul>');
      for (j = 0; j < s.items.length; j++) h.push('<li>' + esc(s.items[j]) + '</li>');
      h.push('</ul></div>');
    }
    el.guideBody.innerHTML = h.join('');
  }
  function guideOpen() { return !!(el.guideBackdrop && !el.guideBackdrop.hidden); }
  function setGuide(open) {
    if (!el.guideBackdrop) return;
    el.guideBackdrop.hidden = !open;
    if (el.btnGuide) el.btnGuide.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open && el.btnGuideClose) { try { el.btnGuideClose.focus(); } catch (e) { /* 忽略 */ } }
  }
  function toggleGuide() { setGuide(!guideOpen()); }

  /* ==================== 21. 事件绑定与启动 ==================== */
  function hideOverlay() { if (el.overlay) el.overlay.classList.add('hidden'); }
  /** 全局兜底：任何未捕获异常都以 .notice.err 呈现，绝不留下冻屏 */
  var fatalSeen = {};
  function fatal(kind, detail) {
    var msg = kind + '：' + detail;
    hideOverlay();
    if (fatalSeen[msg]) return;
    fatalSeen[msg] = true;
    try {
      showNotice(msg, true);
      if (el.statusBadge && !state.renderOk) { el.statusBadge.className = 'badge err'; el.statusBadge.textContent = '页面异常'; }
    } catch (e) { /* 兜底里的兜底 */ }
    console.log('[看板] ' + msg);
  }
  function setExportMenu(open) {
    if (!el.exportMenu || !el.btnExport) return;
    el.exportMenu.hidden = !open;
    el.btnExport.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  function exportMenuOpen() { return !!(el.exportMenu && !el.exportMenu.hidden); }
  function bindEvents() {
    bindHostInteractions();                                      // 2b：节点/分区交互事件委托（tooltip / 选中 / 分区折叠）
    if (el.btnZoomIn) el.btnZoomIn.addEventListener('click', function () { zoomCenter(1.15); });
    if (el.btnZoomOut) el.btnZoomOut.addEventListener('click', function () { zoomCenter(1 / 1.15); });
    if (el.btnFit) el.btnFit.addEventListener('click', function () { fitView(); });
    if (el.btnReset) el.btnReset.addEventListener('click', resetView);
    // 主干模式重建可见节点的布局，显示全部时恢复完整布局。
    if (el.btnExpandAll) el.btnExpandAll.addEventListener('click', function () {
      state.overviewMode = false; state.trunkMode = false; state.collapsedNodes = {}; setStatus(); showNotice('已展开全部分区。');
      renderDiagram(true);
      if (el.search && el.search.value) runSearch(el.search.value);
    });
    if (el.btnCollapseAll) el.btnCollapseAll.addEventListener('click', function () {
      state.overviewMode = true; state.trunkMode = false; state.collapsedNodes = {};
      setStatus(); showNotice('已切换为清晰概览；点击「显示全部节点」查看完整导图。');
      renderDiagram(true);
      if (el.search && el.search.value) runSearch(el.search.value);
    });
    if (el.btnSvg) el.btnSvg.addEventListener('click', function () { setExportMenu(false); exportSvg(); });
    if (el.btnPng) el.btnPng.addEventListener('click', function () { setExportMenu(false); exportPng(); });
    if (el.btnMm) el.btnMm.addEventListener('click', function () { setExportMenu(false); exportMmd(); });
    if (el.btnExport) el.btnExport.addEventListener('click', function (ev) {
      ev.stopPropagation();                                      // 不被外点关闭监听立刻收回去
      setExportMenu(!exportMenuOpen());
    });
    document.addEventListener('click', function (ev) {           // 点下拉外任意处收起菜单
      if (exportMenuOpen() && !(ev.target && ev.target.closest && ev.target.closest('#exportWrap'))) setExportMenu(false);
    });
    if (el.btnToggleSide) el.btnToggleSide.addEventListener('click', toggleSide);
    if (el.btnDockToggle) el.btnDockToggle.addEventListener('click', function (ev) {
      ev.stopPropagation();                                        // 不落入画布的空白点击处理
      toggleDock();
    });
    if (el.legend) {
      // 图例项点击在 #legend 上委托（3）：图例项随 toggleSection 反复重建，委托监听不受影响
      el.legend.addEventListener('click', function (ev) {
        var item = ev.target && ev.target.closest ? ev.target.closest('.item') : null;
        if (!item) return;
        ev.stopPropagation();                                      // 不落入画布空白点击处理
        toggleSection(item.getAttribute('data-section'));
      });
      el.legend.addEventListener('keydown', function (ev) {        // 图例项键盘触发（Enter / Space）
        if (ev.key !== 'Enter' && ev.key !== ' ' && ev.key !== 'Spacebar') return;
        var item = ev.target && ev.target.closest ? ev.target.closest('.item') : null;
        if (!item) return;
        ev.preventDefault();
        toggleSection(item.getAttribute('data-section'));
      });
    }
    if (el.btnHome) el.btnHome.addEventListener('click', goWorkbench);
    if (el.btnTheme) el.btnTheme.addEventListener('click', toggleTheme);
    window.addEventListener('beforeprint', preparePaperPrint);
    window.addEventListener('afterprint', restorePaperAfterPrint);
    if (el.btnPrint) el.btnPrint.addEventListener('click', function () { preparePaperPrint(); window.print(); });
    // 操作指南弹层：按钮开关、遮罩点击关闭（内容在 renderGuide 里静态生成）
    if (el.btnGuide) el.btnGuide.addEventListener('click', toggleGuide);
    if (el.btnGuideClose) el.btnGuideClose.addEventListener('click', function () { setGuide(false); });
    if (el.guideBackdrop) el.guideBackdrop.addEventListener('click', function (ev) {
      if (ev.target === el.guideBackdrop) setGuide(false);
    });
    if (el.btnCloseDetail) el.btnCloseDetail.addEventListener('click', clearSelection);
    if (el.search) {
      el.search.addEventListener('input', onSearchInput);
      el.search.addEventListener('input', renderSuggestions);
      el.search.addEventListener('focus', renderSuggestions);
      el.search.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape') { clearSearch(); ev.stopPropagation(); return; }
        if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
          if (el.suggestions && el.suggestions.hidden) renderSuggestions();
          if (!suggestionItems.length) return;
          ev.preventDefault();
          suggestionIndex = (suggestionIndex + (ev.key === 'ArrowDown' ? 1 : -1) + suggestionItems.length) % suggestionItems.length;
          var choices = el.suggestions.querySelectorAll('.search-suggestion');
          for (var i = 0; i < choices.length; i++) choices[i].classList.toggle('active', i === suggestionIndex);
        } else if (ev.key === 'Enter' && suggestionIndex >= 0) {
          ev.preventDefault(); chooseSuggestion(suggestionIndex);
        }
      });
      if (el.suggestions) {
        el.suggestions.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
        el.suggestions.addEventListener('click', function (ev) {
          var button = ev.target && ev.target.closest ? ev.target.closest('.search-suggestion') : null;
          if (button) chooseSuggestion(Number(button.getAttribute('data-index')));
        });
      }
      document.addEventListener('click', function (ev) {
        if (ev.target !== el.search && !(el.suggestions && el.suggestions.contains(ev.target))) closeSuggestions();
      });
    }
    if (el.tabs) el.tabs.addEventListener('click', function (ev) {
      var t = ev.target && ev.target.closest ? ev.target.closest('.tab') : null;
      if (t) switchView(t.getAttribute('data-view'));
    });
    document.addEventListener('keydown', function (ev) {
      var k = ev.key || '';
      if (ev.altKey && k === 'Home') { ev.preventDefault(); goWorkbench(); return; }
      if ((ev.ctrlKey || ev.metaKey) && (k === 'f' || k === 'F')) {   // Ctrl/Cmd+F 聚焦搜索
        ev.preventDefault();
        if (el.search) { el.search.focus(); el.search.select(); }
        return;
      }
      if (k === 'Escape' || k === 'Esc') {                            // Esc 逐层收起（3）：tooltip > 操作指南 > 导出菜单 > 详情面板 > 搜索
        if (el.tip && el.tip.classList.contains('show')) { hideTip(true); return; }
        if (guideOpen()) { setGuide(false); return; }
        if (exportMenuOpen()) { setExportMenu(false); return; }
        if (state.selectedId) clearSelection();
        else if (el.search && el.search.value) clearSearch();
      }
    });
    window.addEventListener('error', function (ev) {
      fatal('脚本错误', (ev && ev.message ? ev.message : '未知错误') + (ev && ev.filename ? '（' + ev.filename + ':' + ev.lineno + '）' : ''));
    });
    window.addEventListener('unhandledrejection', function (ev) {
      var r = ev && ev.reason;
      fatal('未处理的 Promise 异常', (r && r.message) ? r.message : String(r));
    });
  }
  /** 启动：初始化包在 try/finally 中，遮罩一定会隐藏 */
  async function boot() {
    try {
      try {
        var savedTheme = window.localStorage.getItem('paper-theme');
        if (savedTheme === 'dark' || savedTheme === 'light') document.documentElement.setAttribute('data-theme', savedTheme);
        setSideCollapsed(window.localStorage.getItem('paper-side-collapsed') === '1', false);  // 恢复面板收起状态
        setDockCollapsed(window.localStorage.getItem(DOCK_KEY) === '1', false);                // 恢复左下角信息卡折叠状态
      } catch (e) { /* 本地文件或隐私模式可能禁用存储 */ }
      if (payloadError) fatal('数据异常', payloadError);
      if (!PRISTINE) showNotice('payload.diagram.mermaid 为空，导图将显示兜底内容。', true);
      if (el.brandTitle) el.brandTitle.title = PAPER_TITLE;      // 1a：悬停看完整标题（不依赖模板转义是否完美）
      if (el.brandSub && (!el.brandSub.textContent.trim() || el.brandSub.textContent.indexOf('__BRAND_SUB__') >= 0)) {
        var bits = [];                                           // 副标题为空时补来源摘要
        if (META.source) bits.push(str(META.source));
        if (typeof META.pageCount === 'number') bits.push(META.pageCount + ' 页');
        bits.push(STATS.nodeCount + ' 个节点');
        if (META.model) bits.push('模型 ' + str(META.model));
        el.brandSub.textContent = bits.join(' · ');
      }
      if (el.brandSub) el.brandSub.title = el.brandSub.textContent.trim();   // 副标题截断时悬停同样可见全文
      buildLegend();
      renderOverview();
      renderGlossary();
      renderCritique();
      renderPaper();
      renderGuide();
      renderSource();
      bindPanZoom();
      bindEvents();
      applyTransform();
      setStatus();
      await renderDiagram();
    } catch (e) { fatal('初始化失败', (e && e.message) ? e.message : String(e)); }
    finally {
      hideOverlay();                                             // 渲染失败也必须摘掉遮罩
      setTimeout(hideOverlay, 80);                               // 双保险：首帧后再摘一次
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { boot(); });
  else boot();
})();
