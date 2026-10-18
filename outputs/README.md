# outputs —— 生成产物目录

本目录存放 `src/main.py` 运行后生成的**交互式 HTML 看板**。每个产物都是**单文件、可离线双击打开**的
（Mermaid 运行库、样式、脚本与数据全部内联，不请求任何 CDN）。

## 已附带的示例产物

| 文件 | 对应样例 | 说明 |
|---|---|---|
| `sample_en_two_column.dashboard.html` | `examples/sample_en_two_column.pdf` | 英文 6 页双栏论文，33 个节点 |
| `sample_zh_single_column.dashboard.html` | `examples/sample_zh_single_column.pdf` | 中文 4 页单栏论文，35 个节点 |

这两份是用 `--mock` 离线模式生成的，导图内容来自 `examples/` 下同名的 `.analysis.json`。
其中的证据摘录**逐字取自对应论文原文**，所以溯源校验可以全部命中（英文样例 33/33、中文样例 30/30），
打开看板后每个节点都能看到页码与原文出处，用于演示"证据溯源 + 反幻觉"链路。

> 注意：这两份是**离线演示产物**，看板顶部会明确标注"离线演示模式"，不会被伪装成真实大模型分析结果。

## 自己生成

```bash
# 离线演示（零 API 费用）
python src/main.py examples/sample_en_two_column.pdf --mock

# 真实调用大模型（需先在 .env 中配置 LLM_API_KEY）
python src/main.py 你的论文.pdf

# 指定输出文件名
python src/main.py 你的论文.pdf -o outputs/我的报告.html
```

## 本目录下可能出现的其他内容

| 路径 | 何时产生 | 说明 |
|---|---|---|
| `<报告名>.dashboard.debug/` | 加 `--keep-debug` 时 | 中间产物：`01_parsed.json` 解析结果、`02_analysis.json` 模型输出、`03_diagram.mmd` 导图源码、`04_payload.json` 前端数据、`05_body.txt` 送进模型的正文 |
| `_uploads/` | 使用 `--web` 网页上传时 | 浏览器上传的原始 PDF 落盘位置 |

这两类目录都已在 `.gitignore` 中排除，不会进入版本库。
