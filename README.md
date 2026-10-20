# 文档解析智能体 · 论文阅读助手

> 把一篇 PDF 论文**一键**转成**可离线打开的交互式思维导图看板**。
> 输入一个 PDF，输出一个自包含的 HTML —— 断网、换电脑、双击即可打开。

本作品为 **南京邮电大学 2026 级新生赛**参赛作品。

---

## 一、项目功能简介

给定一篇 `.pdf` 论文，程序自动完成五步，全程无需人工输入 Prompt、无需选择模板、无需指定论文结构：

```
PDF 文本提取  →  大模型结构化分析  →  证据溯源校验  →  Mermaid 导图生成  →  单文件离线 HTML 看板
```

产物是一个**单文件 HTML**，包含：

| 能力 | 说明 |
|---|---|
| 🗺 交互式思维导图 | 十大分区（概览 / 研究问题 / 论证路径 / 方法 / 贡献 / 实验证据 / 结论 / 局限 / 批判分析 / 通俗解读），用形状与配色区分类型 |
| 📄 原文浏览 | 「原文」标签页按页展示提取到的论文正文，保留段落与换行排版、标题行突出显示；每页可折叠，支持全部展开 / 折叠 |
| 📖 操作指南 | 顶栏一键打开操作说明弹层：导图 / 分区显隐 / 搜索 / 溯源 / 原文 / 导出 / 键盘操作一页讲清，Esc 或点击遮罩关闭 |
| 🖱 缩放与平移 | 滚轮以光标为锚点缩放（0.2×–3×）、拖动平移、一键适应窗口、一键重置 |
| 📋 节点详情面板 | 点击节点查看：标签、说明、**原文摘录**、**页码**、**溯源校验结果**（命中后另附**可折叠的原文上下文**），以及新意 / 类型 / 严重度 / 类比等附加字段 |
| 🔍 搜索高亮 | 按标签、说明、原文检索，命中高亮、其余变暗并自动定位到首个命中 |
| 🎛 展开折叠 | 「仅看主干」只保留核心链路；分区显隐**仅**通过左下角图例切换（画布内不再单点隐藏分区，杜绝误触） |
| 📖 通俗解读 | 面向大一新生的术语解释，带类比与「为什么重要」 |
| 🔬 批判性分析 | 数据 / 实验 / 方法 / 假设 / 应用边界 / 总体评价 六个维度 |
| 💾 导出 | 下载 SVG / PNG / `.mmd` 源文件，全部在前端离线完成 |
| 🌓 主题 | 深色 / 浅色一键切换 |
| 🔒 完全离线 | Mermaid 运行库、样式、脚本、数据全部内联，**零外部网络请求** |

---

## 二、作品亮点：对应评审维度

| 评审维度 | 分值 | 本作品的实现 |
|---|---|---|
| **一键跑通** | 30 | 单条命令 `python src/main.py 论文.pdf` 跑完全程，5 个阶段自动衔接，无任何交互式提问；默认输出到 `outputs/<论文名>.dashboard.html`。另有 `--web` 提供浏览器拖拽上传界面。 |
| **解析准确** | 25 | ① 只用 PyMuPDF 单一后端，避免多后端差异；② **刻意关闭 `sort=True`**（它跨栏按 y 排序会把双栏论文拆成「左一行、右一行」），改为**列感知重排**：按 x 重叠度分列（重叠超过较窄块宽度 50% 视为同列），列内再按 y 稳定排序；③ 页眉页脚**两层过滤**：块级跨页重复阈值 + 行级统计（解决页脚与正文挤在同一 block 的情况）；④ 标题靠「字号 + 编号模式 + 居中 + 加粗」联合判定，并排除作者 / 单位行与「2.6 个百分点」这类以数字开头的句子；⑤ 多块拼成的标题会合并，碎片不再污染大纲；⑥ 正文在参考文献 / 附录处截断。 |
| **看板可用** | 20 | ① Mermaid **不由模型产出**，由 `build_diagram()` 按十大分区确定性拼装；② 生成前 `validate_mermaid()` 静态校验（首行类型、subgraph 闭合、括号引号配平、节点 id 合法性）；③ `mermaid.min.js` **整体内联**进 HTML，断网可用；④ 节点详情、缩放、折叠、搜索、导出、主题切换。 |
| **内容深度** | 20 | ① 每个节点携带 `evidence`（原文摘录）+ `page`（页码）+ `section`（所属章节）；② `src/verify.py` 用「精确匹配 → 压缩空白连字符匹配 → 片段覆盖匹配 → 中文 k-mer 覆盖率兜底」四级策略把每条引用**回查原文**，命中就用实测页码覆盖模型自报页码并附带**命中位置的简要上下文**（详情面板可折叠展示），未命中在前端显式标红；③ Prompt 强制六要素 + 五维批判；④ 独立第二轮调用生成通俗解读。 |
| **稳定鲁棒** | 15 | ① 全链路异常处理：溯源模块异常退回内置实现、通俗解读失败不阻断主流程；② Mermaid 三级兜底：校验通过 → 规则修复 → 大模型修复 → 保留代码并启用前端兜底视图；③ 大模型 JSON 解析三级降级，结构校验失败自动带错误信息重问一次；④ 不支持 `response_format` 的服务自动降级重试；⑤ 对近扫描件与乱序内容流有专项样例，只降级不崩溃。 |
| **工程规范** | 10 | ① 密钥只从环境变量 / `.env` 读取，源码无硬编码，日志只打印 `abcd...wxyz`；② `.env.example` 覆盖全部 16 个变量并含五家服务商预设；③ 异常统一转成带修复建议的中文提示；④ `--check` 一键自检；⑤ README 覆盖比赛要求的全部 8 项，另附独立故障排查文档。 |

### 一个关键设计决定：为什么不让模型直接写 Mermaid

| 让模型直接写 Mermaid | 本作品的做法 |
|---|---|
| 语法错误率极高（括号、引号、中文标点、`subgraph` 闭合都可能出错） | 模型**只输出 JSON**，Mermaid 由本地代码确定性拼装，语法结构 100% 可控 |
| 出错后只能整段重生成，浪费 token 且结果随机 | 出错先走**确定性规则修复**，零成本、结果可预测 |
| 无法保证节点层级简洁 | 十大分区、形状、配色、每区节点上限全部由代码约束 |

同理，**模型自报的页码不被采信** —— 每条引用都必须能在提取出的正文里回查得到，否则前端标红。

---

## 三、目录结构

```
Project1/                           # 项目根目录（所有命令都在此目录下执行）
├── src/                            # 源代码
│   ├── main.py                     # 命令行入口（一键运行 / --check / --web）
│   ├── config.py                   # 配置：环境变量读取、路径常量、密钥脱敏
│   ├── parse.py                    # PDF 解析：列感知重排、样板过滤、标题识别
│   ├── textutils.py                # 文本规范化、分句、CitationVerifier 引用匹配
│   ├── verify.py                   # 溯源校验：回查原文、纠正页码、生成审计报告
│   ├── llm.py                      # 大模型客户端：OpenAI 兼容 API、JSON 鲁棒解析、离线 mock
│   ├── prompts.py                  # Prompt 模板、输出 Schema、结构校验
│   ├── diagram.py                  # JSON → Mermaid：建图、标签清洗、语法校验、规则修复
│   ├── render.py                   # payload 组装 + 单文件 HTML 渲染
│   ├── pipeline.py                 # 五阶段编排、日志、调试产物导出
│   └── webapp.py                   # 可选网页上传界面（仅标准库 http.server）
├── assets/
│   ├── templates/
│   │   ├── dashboard.html          # 看板 HTML 模板（含全部 CSS 与占位符）
│   │   ├── upload.html             # 本地网页工作台（上传、进度、最近看板）
│   │   └── app.js                  # 前端交互逻辑（原生 DOM，无第三方库）
│   └── vendor/
│       └── mermaid.min.js          # 离线 Mermaid 渲染库（构建时内联进产物）
├── examples/                       # 示例输入与离线分析结果
│   ├── make_samples.py             # 示例 PDF 生成器（可重复生成下面 4 个样例）
│   ├── sample_en_two_column.pdf    # 英文 6 页双栏论文
│   ├── sample_zh_single_column.pdf # 中文 4 页单栏论文
│   ├── sample_long_outline.pdf     # 24 页三级标题长文档
│   ├── sample_scan_like.pdf        # 对抗性输入（近扫描件 / 乱序内容流）
│   ├── sample_en_two_column.analysis.json
│   ├── sample_zh_single_column.json
│   └── sample_analysis.json
├── outputs/                        # 生成的 HTML 看板（含两份示例产物，已随仓库提交）
├── tests/
│   ├── test_units.py               # 单元测试
│   ├── webapp_smoke.py             # 网页上传、进度与看板联动测试
│   ├── smoke_test.py               # 端到端冒烟测试（现场生成 PDF 跑全链路）
│   ├── frontend_check.js           # 前端无头验证（jsdom 真实执行产物 HTML，79 项）
│   └── run_all_tests.py            # 一键跑完全部自测
├── _verify/
│   └── package.json                # 前端无头验证的依赖声明（jsdom；node_modules 不入库）
├── .github/workflows/tests.yml     # CI：push / PR 自动跑全量自测
├── tools/
│   └── fetch_mermaid.py            # 一次性下载 mermaid.min.js
├── docs/
│   ├── frontend-contract.md        # 前后端数据契约（payload 字段定义）
│   └── 常见错误处理.md              # 故障排查手册（18 个问题）
├── 启动.bat                        # Windows 双击启动菜单（不依赖绝对路径）
├── 打开论文工作台.bat              # Windows 双击直达浏览器工作台
├── run_tests.ps1                   # Windows 一键自测脚本
├── requirements.txt
├── .env.example
└── README.md
```

---

## 四、环境依赖

| 项目 | 要求 | 说明 |
|---|---|---|
| Python | **≥ 3.10**（开发与验证于 3.14.6） | 用到 `X \| Y` 类型标注与 `dataclass` |
| 操作系统 | Windows / macOS / Linux | 仅在 Windows + Python 3.14 上完整验证过 |
| 网络 | **仅首次准备环境与调用 API 时需要** | 产物看板与全部测试完全离线 |
| Node.js | 可选（≥ 18） | 仅用于 `tests/frontend_check.js`。依赖声明在 `_verify/package.json`，`tests/run_all_tests.py` 首次运行会自动 `npm install`（约 10 MB，仅一次）；完全离线时可加 `--no-install` 跳过 |
| 大模型 API | 需要一个 OpenAI 兼容接口 | DeepSeek / Kimi / 通义 / 智谱 / Ollama 均可 |

三个 Python 依赖：

| 包 | 用途 |
|---|---|
| `pymupdf` | PDF 文本与坐标提取 |
| `openai` | 调用 OpenAI 兼容接口的官方 SDK |
| `python-dotenv` | 自动加载 `.env`（缺失时退化为内置极简解析器） |

---

## 五、安装方式

```bash
# 1) 获取代码后进入项目根目录（本仓库中即 Project1 文件夹）
cd Project1

# 2) 建议使用虚拟环境
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 3) 安装依赖
pip install -r requirements.txt

# 4) 下载离线 Mermaid 运行库（仅需一次，约 3.3 MB）
python tools/fetch_mermaid.py
#    若网络无法访问 CDN，可手动下载
#    https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js
#    另存为 assets/vendor/mermaid.min.js，再用 --check 验证

# 5) 配置 API Key
Copy-Item .env.example .env      # Windows PowerShell
cp .env.example .env             # macOS / Linux

# 6) 自检：确认依赖、离线素材、API 配置都就绪
python src/main.py --check
```

`--check` 会逐项打印 Python 版本、三个依赖、三个离线素材、API 配置、示例 PDF 数量与磁盘空间，并以如下两行之一结尾：

```text
✓ 基础环境就绪，可以运行：python src/main.py examples/sample_en_two_column.pdf
✗ 存在问题，请按上面的提示修复后再运行。
```

> 缺少 `python-dotenv` 只会提示「可选」，不影响通过自检。
> 仓库自带 4 个示例 PDF，可以直接跑离线演示（不联网、不花钱）：
> `python src/main.py examples/sample_en_two_column.pdf --mock`

---

## 六、API Key 配置方式

**密钥只从环境变量或项目根目录的 `.env` 读取，源码中没有任何硬编码密钥。**

```bash
cp .env.example .env
```

编辑 `.env`，最少只需要三行：

```ini
LLM_API_KEY=sk-你的真实密钥
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-pro
```

常见服务商配置（**均为示例值，请以各服务商官方文档为准**）：

```ini
# DeepSeek
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-pro

# 月之暗面 Kimi
LLM_BASE_URL=https://api.moonshot.cn/v1
LLM_MODEL=moonshot-v1-32k

# 阿里云通义千问（DashScope 兼容模式）
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus

# 智谱 GLM
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4-plus

# 本地 Ollama（无需真实 Key，填任意非空字符串即可）
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:14b
LLM_API_KEY=ollama
```

与运行最相关的变量（完整 15 个见 `.env.example`）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_API_KEY` | 空 | **必填**（除非用 `--mock`） |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容接口地址 |
| `LLM_MODEL` | `deepseek-v4-pro` | 验收模型名，可按主办方配置覆盖 |
| `LLM_THINKING` | `disabled` | DeepSeek V4 的思考模式开关；结构化 JSON 默认关闭以减少耗时与输出截断 |
| `LLM_TEMPERATURE` | `0.2` | 越低越稳定，建议不超过 0.5 |
| `LLM_MAX_TOKENS` | `8192` | 单次回复上限 |
| `LLM_TIMEOUT` | `180` | 单次请求超时（秒） |
| `MAX_INPUT_CHARS` | `60000` | 送入模型的正文上限，长论文按页节选并标记省略部分 |
| `STRICT_CITATION` | `false` | 开启后，未能在原文定位到的引用会被清空 |
| `MOCK` | `false` | 等价于命令行 `--mock` |

> ⚠️ `.env` 已在 `.gitignore` 中排除，**切勿把真实密钥提交到仓库或打进提交包**。
> `--check` 与运行日志只会打印脱敏后的 `abcd...wxyz`。

---

## 七、运行命令

### Windows 用户：双击 `启动.bat`

项目根目录下的 `启动.bat` 是一个菜单式启动器，**双击即可**，不需要打开命令行：

```
  [1]  打开示例看板          （直接看效果，不需要任何配置）
  [2]  离线演示一篇论文      （不调用 API，零费用）
  [3]  分析我自己的论文      （需要先在 .env 里配置 API Key）
  [4]  启动网页上传界面      （浏览器里拖拽 PDF）
  [5]  环境自检              （检查依赖与素材是否齐全）
  [6]  运行全部自测          （单元 + 前端 + 冒烟）
  [7]  创建 .env 配置文件    （填写 API Key 用）
  [0]  退出
```

关于路径：脚本内**没有任何硬编码的绝对路径**。它会先记住调用时的目录，再从该目录逐级向上查找
含 `src\main.py` 的目录作为项目根，随后所有路径都相对项目根解析。因此：

- 把整个 `Project1` 文件夹拷到任何盘符、任何层级都能直接用；
- 从 `Project1` 的任意子目录里调用它也能正确定位项目；
- 输入 PDF 时既可拖拽（绝对路径），也可写相对项目根的路径。

### 命令行方式

```bash
# 一键生成（最常用）
python src/main.py 论文.pdf

# 指定输出路径
python src/main.py 论文.pdf -o outputs/我的报告.html

# 离线演示：不调用任何 API，零费用
python src/main.py examples/sample_en_two_column.pdf --mock

# 额外导出中间产物（解析结果 / 模型输出 / 导图源码 / 正文）
python src/main.py 论文.pdf --keep-debug

# 跳过「通俗解读」第二轮调用，省一半 token
python src/main.py 论文.pdf --no-audience

# 用已有的分析 JSON 重新渲染看板（调前端时不必重复调用模型）
python src/main.py 论文.pdf --render-from outputs/报告.dashboard.debug/02_analysis.json

# 环境自检
python src/main.py --check

# 启动网页上传界面（浏览器拖拽 PDF）
python src/main.py --web              # 默认 http://127.0.0.1:8000
python src/main.py --web --port 8899

# 详细日志 / 只输出结果路径
python src/main.py 论文.pdf -v
python src/main.py 论文.pdf -q
```

| 参数 | 说明 |
|---|---|
| `pdf` | 输入论文路径（位置参数） |
| `-o`, `--output` | 输出 HTML 路径，默认 `outputs/<论文文件名>.dashboard.html` |
| `--mock` | 离线演示模式 |
| `--mock-file` | 离线模式使用的分析 JSON 路径 |
| `--check` | 环境自检 |
| `--web` / `--port` / `--host` | 网页上传界面 |
| `--keep-debug` | 导出中间产物 |
| `--render-from` | 跳过模型调用，用已有分析 JSON 重新渲染 |
| `--no-audience` | 跳过通俗解读第二轮调用 |
| `-v` / `-q` | 详细日志 / 静默（只打印产物路径） |

### 退出码

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 运行失败（缺少 API Key、PDF 打不开、模型调用失败、素材缺失等），stderr 会给出中文原因与修复建议 |
| `2` | 命令行参数错误（例如没给 PDF 路径） |
| `130` | 用户按 Ctrl+C 中断 |

> 注意：在 PowerShell 里用 `2>&1 | Select-String ...` 之类的管道查看输出时，`$LASTEXITCODE` 有时会显示为 `1`（原生命令 stderr 重定向所致）。**程序自身的退出码是正确的**，需要判断时建议先重定向到文件再检查。

---

## 八、输入 PDF 方式

三种方式任选：

1. **命令行给路径**（最常用）：
   ```bash
   python src/main.py 论文.pdf
   python src/main.py "D:\论文\Attention Is All You Need.pdf"
   python src/main.py ./examples/sample_zh_single_column.pdf
   ```
   支持相对与绝对路径；路径含空格或中文时请加引号。

2. **网页上传**（`--web`）：浏览器工作台支持拖拽上传、阶段进度和最近看板；完成后点击结果链接打开。上传文件落在 `outputs/_uploads/`。

3. **程序内调用**：
   ```python
   from src import run_pipeline, Settings

   result = run_pipeline("论文.pdf", settings=Settings.from_env())
   print(result.html_path)               # 产物路径
   print(result.verification_counts())   # 溯源统计
   ```

**输入要求**：PDF 必须含文本层（文字版）。纯扫描件会给出明确报错，提示先用 OCR（如 `ocrmypdf`）处理；加密 PDF 会提示无法打开。

---

## 九、输出 HTML 位置

| 情况 | 产物位置 |
|---|---|
| 不指定 `-o` | `outputs/<论文文件名>.dashboard.html` |
| 指定 `-o outputs/报告.html` | `outputs/报告.html`（父目录自动创建） |
| `--web` 上传 | `outputs/<上传文件名>.dashboard.html`，浏览器自动打开 |
| 加 `--keep-debug` | 额外生成 `outputs/<报告名>.dashboard.debug/` |

| 产物 | 路径 | 何时产生 |
|---|---|---|
| **交互式 HTML 看板**（主产物） | `outputs/xxx.dashboard.html` | 每次运行 |
| `01_parsed.json` 解析结果 | `outputs/xxx.dashboard.debug/` | 仅 `--keep-debug` |
| `02_analysis.json` 模型输出 | 同上 | 仅 `--keep-debug` |
| `03_diagram.mmd` 导图源码 | 同上 | 仅 `--keep-debug` |
| `04_payload.json` 前端数据 | 同上 | 仅 `--keep-debug` |
| `05_body.txt` 送进模型的正文 | 同上 | 仅 `--keep-debug` |

产物约 3.3 MB（其中约 3.2 MB 是内联的 Mermaid 运行库），**单文件自包含**：拷到任何电脑、断网双击即可打开，不会请求任何 CDN。

`outputs/` 下已附带两份示例产物（英文样例 38 个节点、中文样例 35 个节点），可直接打开查看效果。

---

## 十、工作原理

```
①  PDF 文本提取              src/parse.py
   · PyMuPDF 逐页取块，保留 bbox / 字号 / 加粗等版面属性
   · 刻意不用 sort=True，改为列感知重排（见"解析准确"一栏）
   · 页眉页脚两层过滤：块级跨页重复 + 行级统计
   · 标题识别：字号 + 编号模式 + 居中 + 加粗，排除作者/单位行与数字开头的句子
   · 在"参考文献/致谢/附录"处截断正文，不污染后续分析
   · 记录 page_spans，使正文每个字符都能回溯到页码
   │
   ▼
②  大模型结构化分析          src/prompts.py + src/llm.py
   · 模型只输出结构化 JSON，Schema 见 prompts.ANALYSIS_SCHEMA
   · 正文按 <<<PAGE n>>> 锚点注入 Prompt，模型据此填 page 字段
   · validate_analysis() 结构校验失败 → 带错误信息自动重问一次
   · 第二轮（可 --no-audience 关闭）生成通俗解读
   │
   ▼
③  证据溯源校验              src/verify.py（+ textutils.CitationVerifier）
   · 不信模型自报页码：每条 evidence 都回原文回查
   · 四级策略：规范化精确匹配 → 压缩空白/连字符匹配 → 片段覆盖匹配 → 中文 k-mer 覆盖率兜底
     （最后一级专治"多分句引用里被轻微改写"的情况；实测中文政策论文的 3 条误杀由此救回）
   · 命中 → 用实测页码覆盖模型填的页码；未命中 → 前端标注"未在原文定位到"
   · STRICT_CITATION=true 时，未定位到的引用直接清空
   · 返回 VerificationReport：总条数/已溯源/未定位/页码纠正/占位清空/可疑明细
   · 为不改写入参，返回的是 analysis 的副本——调用方必须用副本继续后续步骤
   │
   ▼
④  Mermaid 生成与校验        src/diagram.py
   · build_diagram()：十大分区 + 形状区分 + 配色 classDef，确定性拼装
   · sanitize_label()：清洗引号、括号、竖线等危险字符并智能截断
   · validate_mermaid()：纯 Python 静态校验，不依赖 mermaid-cli / Node
   · 失败 → repair_mermaid() 规则修复（补首行 / 补 end / 统一边语法 / 去围栏）
   · 仍失败 → 让大模型修复一次，产物必须再次通过本地校验才采纳
   · 全部失败 → 保留问题代码，但前端启用兜底视图，绝不白屏
   · attach_verification() 把溯源结果回填到图节点，前端才能显示页码与红标
   │
   ▼
⑤  渲染单文件 HTML 看板      src/render.py + assets/
   · 组装前端契约 payload（见 docs/frontend-contract.md）
   · 把 mermaid.min.js / CSS / app.js / payload JSON 全部内联进模板
   · 数据里的 < > & 转义为 \u003c 等，防止论文中的 </script> 提前闭合脚本
   · 产出可直接双击打开的单文件 HTML，零外部请求
```

### 离线保证

产物 HTML 中的 `mermaid.min.js` 是**整体内联**的（模板占位符 `__MERMAID_JS__`，写入前把内部的 `</script` 切断为 `<\/script`）。因此断网双击可正常渲染，不请求任何 CDN、外链字体或图标；导出 SVG / PNG / `.mmd` 也全部在前端离线完成。

---

## 十一、快速验证

以下步骤**全部零 API 费用**，可在 2 分钟内验证整条链路。

```bash
# 步骤 1：环境自检（期望最后一行出现 ✓ 基础环境就绪）
python src/main.py --check

# 步骤 2：一键跑完全部自测（单元 + 网页联动 + 前端无头 + 环境自检）
#         首次运行会自动 npm install jsdom（约 10 MB）；完全离线时加 --no-install
python tests/run_all_tests.py
#        Windows 也可以用：powershell -ExecutionPolicy Bypass -File run_tests.ps1

# 步骤 3：离线演示仓库自带的样例论文
python src/main.py examples/sample_en_two_column.pdf --mock
python src/main.py examples/sample_zh_single_column.pdf --mock

# 步骤 4：换成你手边的任意文字版 PDF，先离线跑一遍看解析与看板
#         注意：若该 PDF 没有同名 .analysis.json，会回落到通用样例，
#         看板内容与本文无关、证据溯源全部标红 —— 这是预期的"离线演示"行为，
#         日志与看板顶部都会明确提示。要跑真实分析请去掉 --mock。
python src/main.py 你的论文.pdf --mock --keep-debug

# 步骤 5（可选）：配好 .env 后真实调用大模型
python src/main.py 你的论文.pdf -v
```

也可以单独运行某一项：

```bash
python tests/test_units.py                                                  # 单元测试
python tests/smoke_test.py                                                  # 冒烟测试（现场生成 PDF 跑全链路）
node tests/frontend_check.js outputs/sample_en_two_column.dashboard.html     # 前端无头验证（79 项）
```

步骤 2 的期望结尾：

```text
======================================================================
  汇总
======================================================================
  ✓ 单元自测                                          （284 项断言）
  ✓ 双栏版式解析测试
  ✓ 端到端冒烟测试（离线 mock）
  ✓ 本地网页上传与任务进度
  ✓ 前端无头验证（jsdom）                              （79 项）
  ✓ 环境自检
  ✓ 开发环境交付检查（清单 / 密钥 / 离线自证 / 编码）
----------------------------------------------------------------------
  总耗时：13.8 秒

  全部自测通过。产物：outputs/*.dashboard.html（单文件，可离线打开）
======================================================================
```

> 没有安装 jsdom 时，前端一项会显示 `○ 前端无头验证（jsdom）（skipped）` 并在结尾提示启用方式——**跳过不会被伪装成通过**。

步骤 3 的期望结尾（样例论文的引用摘录逐字取自该论文，因此溯源全部命中）：

```text
  导图节点    : 38 个（10 个分区）
  证据溯源    : 33/33 条已定位，7 条页码已纠正
  Mermaid     : Mermaid 语法预校验通过
  输出看板    : outputs/sample_en_two_column.dashboard.html
  文件大小    : 3472 KB（单文件，含离线 Mermaid，可直接双击打开）
```

打开看板后请确认三件事：① 显示思维导图而非白屏；② 点击节点后右侧出现「原文摘录 / 页码 / 溯源状态」；③ **断网刷新后导图依然正常渲染**。

---

## 十二、离线演示模式（`--mock`）

```bash
python src/main.py 论文.pdf --mock
python src/main.py 论文.pdf --mock --mock-file examples/sample_analysis.json
```

对应环境变量：`MOCK=true`、`MOCK_FILE=...`。

- **完全不调用大模型 API**，因此**零费用、零网络依赖、无需 API Key**；
- 仍然**真实解析**你传入的那篇 PDF（页数、字符数、标题、双栏检测、证据溯源全部照跑）；
- 导图内容来自本地样例 JSON，查找顺序：
  1. `MOCK_FILE` / `--mock-file` 指定的路径；
  2. 输入 PDF **同名**的 `<论文名>.analysis.json` 或 `<论文名>.json`；
  3. `examples/sample_analysis.json`（仓库自带的通用兜底）；
  4. `src/llm.py::_builtin_analysis` 内置占位样例；
- 仓库自带两份与样例论文配套的分析 JSON（英文、中文各一份），其 `evidence` 摘录**逐字取自对应论文**，所以溯源校验能全部命中 —— 这是演示「证据溯源 + 反幻觉」链路的关键；
- 一旦回落到第 3/4 级通用兜底，**看板内容必然与当前 PDF 无关**：日志与看板的 `warnings` 会明确写出「离线演示使用的是通用样例分析（与本 PDF 无关）」，此时证据会全部标红。这是预期行为，不是程序出错；要分析真实内容请去掉 `--mock`，或用 `--mock-file` 指定配套 JSON。
- 看板顶部会明确显示**离线演示模式**，**不会**把样例内容伪装成真实分析结果。

适用场景：装完依赖后先验证解析 / 渲染链路；评委现场断网演示时完整展示整条链路与看板交互；改完前端后做零成本回归。

---

## 十三、看板功能一览

| 功能 | 操作方式 |
|---|---|
| 查看节点详情 | 单击任意节点，右侧面板显示标签、说明、原文摘录、页码、溯源状态与附加字段 |
| 关闭详情 / 回到概览 | 点击面板右上角「关闭」，或按 `Esc` |
| 缩放 | 鼠标滚轮（以光标为锚点）、工具栏 `＋` / `－` |
| 平移 | 在画布上按住鼠标左键拖动 |
| 适应窗口 / 重置 | 工具栏「适应」/「重置」 |
| 仅看主干 | 工具栏「仅看主干」，只保留核心链路；「展开全部」恢复 |
| 隐藏某个分区 | 点击左下角图例中的分区名，可单独隐藏 / 显示 |
| 搜索 | 顶部搜索框（或 `Ctrl+F`），命中高亮、其余变暗并自动定位 |
| 清空搜索 | `Esc` 或清空输入框 |
| 切换主题 | 顶部 🌓 按钮 |
| 导出 SVG / PNG / 源码 | 工具栏三个下载按钮，全部离线完成 |
| 打印 / 导出 PDF | 顶部 🖨 按钮（打印样式已隐藏工具栏与侧栏） |

若 Mermaid 渲染意外失败，看板**不会白屏**：会显示错误信息 + 原始 Mermaid 源码 + 可点击的节点列表，仍可查看全部内容。

---

## 十四、常见错误处理方式

完整手册见 [`docs/常见错误处理.md`](docs/常见错误处理.md)（18 个问题的「现象 → 原因 → 解决」）。以下是最高频的几类：

| 现象 | 原因 | 解决 |
|---|---|---|
| `未配置 API Key` | 没有 `.env` 或未填 `LLM_API_KEY` | 复制 `.env.example` 为 `.env` 并填写；或先用 `--mock` |
| `401 / invalid api key` | 密钥无效或过期 | 检查 `.env` 中的 `LLM_API_KEY`（注意别带引号或空格） |
| `404` | Base URL 或模型名错误 | 核对 `LLM_BASE_URL`（通常要以 `/v1` 结尾）与 `LLM_MODEL` |
| `429 / rate limit` | 触发限流 | 稍后重试，或降低调用频率 |
| `请求超时` | 论文太长或模型太慢 | 调大 `LLM_TIMEOUT`，或调小 `MAX_INPUT_CHARS` |
| `PDF 中没有可提取的文本层` | 扫描件 | 先用 OCR 处理（如 `ocrmypdf`），或更换文字版 PDF |
| `缺少离线渲染资源` | 没下载 `mermaid.min.js` | `python tools/fetch_mermaid.py` |
| 中文乱码 | 控制台编码非 UTF-8 | 程序已强制 UTF-8 输出；若仍异常，用 `-o` 输出到文件查看，或执行 `chcp 65001` |
| 双栏论文阅读顺序错乱 | 版式特殊 | 程序已做列感知重排；若仍异常，用 `--keep-debug` 查看 `05_body.txt` 定位 |
| 长论文内容被节选 | 超出模型输入上限 | 调大 `MAX_INPUT_CHARS`，或换用长上下文模型 |
| 导图空白 / 生成页面打不开 | Mermaid 渲染失败 | 看板会展示兜底视图；用 `--keep-debug` 查看 `03_diagram.mmd` |

**排查顺序**：先 `python src/main.py --check` 确认环境；再 `python tests/run_all_tests.py` 确认代码本身没问题；然后 `--keep-debug` 看中间产物 —— `01_parsed.json` 看解析对不对，`02_analysis.json` 看模型答得对不对，`03_diagram.mmd` 看导图源码。**哪一环不对，问题就在哪一环。**

---

## 十五、常见问题 FAQ

**Q1：一定要联网吗？**
只有两处需要网络：首次下载 `mermaid.min.js`（`tools/fetch_mermaid.py`），以及调用大模型 API。产物看板与全部测试都完全离线。

**Q2：没有 API Key 能用吗？**
能。`--mock` 离线演示模式会用本地样例分析 JSON 跑通整条链路（含真实的 PDF 解析与溯源校验），零费用。仓库自带的两份配套分析 JSON 的证据摘录逐字来自论文原文，因此溯源能全部命中。

**Q3：调用一次大概花多少钱？**
取决于模型与论文长度。一篇 10 页论文约消耗 1.5 万–3 万 token；用 `--no-audience` 可省掉第二轮调用。

**Q4：支持中文论文吗？**
支持。仓库自带的中文样例（4 页单栏）已验证：标题、章节、摘要识别正常，30/30 条引用全部溯源命中。

**Q5：支持扫描版 PDF 吗？**
不支持 —— 扫描件没有文本层。程序会明确报错并提示先用 OCR 处理，而不是静默产出垃圾结果。

**Q6：论文太长超出模型上下文怎么办？**
`MAX_INPUT_CHARS`（默认 60000 字符）控制送入模型的正文上限。超出时从各页均衡节选并保留页码锚点，日志与看板会明确提示内容不完整。可调大该值，或换用长上下文模型；当前尚未实现分块汇总。

**Q7：`page` 页码是模型自己填的，可信吗？**
不完全可信，所以程序不采信。每条 `evidence` 都会用 `CitationVerifier` 回到提取出的正文里回查：命中就用**实测页码**覆盖模型填的页码，未命中就在前端标注「未在原文定位到」。想更严格可设 `STRICT_CITATION=true`，未定位到的引用会被直接清空。

**Q8：为什么导图和我预想的结构不一样？**
模型的六要素分析本来就是一种「提炼」，不是目录抄写。比赛明确要求「不机械套用固定模板、不照搬原文小标题」。若想调整分区与层级，改 `src/diagram.py::build_diagram()` 即可，无需改 Prompt。

**Q9：能换成别的模型吗？**
可以，任何 OpenAI 兼容接口都行（DeepSeek / Kimi / 通义 / 智谱 / 本地 Ollama / vLLM）。只需改 `.env` 里的三行。

**Q10：看板能分享给别人吗？**
可以，把单个 `.html` 文件发出去即可。对方断网双击也能正常打开、缩放、搜索、导出。

**Q11：可以批量处理多篇论文吗？**
可以，写个循环即可：
```bash
for f in papers/*.pdf; do python src/main.py "$f" -o "outputs/$(basename "$f" .pdf).html" -q; done
```

**Q12：改了代码怎么确认没改坏？**
跑 `python tests/run_all_tests.py`（约 14 秒，零 API 成本），它会依次跑单元测试、双栏版式专项、端到端冒烟、网页联动、前端无头验证、环境自检与交付检查。首次运行会自动 `npm install jsdom`（约 10 MB，仅一次）；完全离线时加 `--no-install`，前端层会标记为「跳过」而不是失败。

**Q13：`.env` 会不会被打进提交包？**
`.env` 已在 `.gitignore` 中排除。打包提交前请再确认一次压缩包里**没有** `.env`，只有 `.env.example`。

**Q14：为什么产物有 3.3 MB？**
因为把 Mermaid 运行库整体内联了（约 3.2 MB）。这是为了满足「HTML 可本地离线打开、不依赖外部 CDN」的硬性要求。

**Q15：程序会不会把我论文内容发到别处？**
不会。除了发送给你自己配置的大模型接口外，没有任何其他网络请求；生成的 HTML 也不含任何外链。

**Q16：为什么 28 页的论文只分析了 10 页？**
正文会在「参考文献 / 附录」标题处截断，避免参考文献列表污染分析。若论文的附录排在参考文献**之后**（arXiv 论文常见），这部分暂不进入看板：日志与看板的 `warnings` 会写明「第 N 页起共 M 页未参与分析」，`--keep-debug` 的 `05_body.txt` 可确认截断位置。这是已知限制，不是解析失败。

**Q17：日志里的「标题 167 个」正常吗？**
不正常时会被自动拦掉。图表坐标轴刻度（`1.0` / `0.8` / `100 101`）与公式碎片字号常大于正文，早期版本会把它们当成章节标题（实测一篇 28 页论文产出 167 个"标题"）。现在标题识别有语言学守卫（字母占比、括号配平、纯数字 token 数），同类论文只保留 25 个真实标题。

---

## 附：开发者命令速查

```bash
python src/main.py --check                                  # 环境自检
python tests/run_all_tests.py                               # 全部自测
python tests/test_units.py                                  # 单元测试
python tests/smoke_test.py                                  # 端到端冒烟
node tests/frontend_check.js outputs/xxx.dashboard.html      # 前端无头验证
python examples/make_samples.py                             # 重新生成 4 个示例 PDF
python tools/fetch_mermaid.py --check                       # 检查离线渲染库
python src/verify.py 论文.pdf 分析.json --strict             # 单独跑溯源校验
```

---

本作品为 **南京邮电大学 2026 级新生赛**参赛作品。
