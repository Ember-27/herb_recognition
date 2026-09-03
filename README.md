# 中草药多模态识别系统 (Herb Recognition)

基于 **视觉 + 文本 + 知识图谱** 多模态融合的中草药识别与药性分析系统，专为 **8G 显存** 环境优化。

## 特性

- 多模态融合：Swin-Tiny 视觉编码器 + BERT 中文文本编码器 + HCA 跨模态注意力融合
- 小样本学习：内置 Prototypical Network，应对中草药长尾/稀缺类别
- 知识图谱：Networkx 内存版（可选 Neo4j），支持药性推理与方剂推荐；内置经典十八反/十九畏数据（甘草×甘遂、人参×五灵脂等）
- 图谱可视化：Gradio 内嵌交互式力导向关系网（节点=药材、边=配伍/禁忌），聚焦单药即可查看其配伍与禁忌网络
- RAG 增强问答：本地 BERT 对知识图谱切片语义检索，LLM 回答自动附「知识库依据」来源标注，减少幻觉
- 可解释性：融合注意力可视化 + 推理路径展示
- 交互演示：Gradio Web 界面，一键识别并给出药性/相似药/方剂说明
- 相似药推荐：按功效分类自动推荐功效相近的替代药材，辅助辨证选药（图片识别与 REST API 均支持）
- 结果导出（自定义命名）：图片识别结果与特性检索结果均支持一键导出为 **Markdown / PDF / Word**，导出时可自定义文件名（自动过滤 `\ / : * ? " < > |` 等非法字符，留空则回退默认名）
- 本草补遗库（用户增补药材）：关系图谱搜索未命中时，可一键进入「本草补遗库」手动增补药材（药名、图片、性味、归经、功效、别名、适用病症、个体禁忌、常用配伍）；新增药材自动并入知识图谱与检索语料，可在检索 / 图谱 / 对话中直接命中，并支持编辑与删除

## 目录结构

```
herb_recognition/
├── data/                     # 数据 (raw/processed/external；favorites.json 等运行时状态)
├── models/                   # 视觉/文本编码器、融合、分类头
│   ├── vision_encoder.py
│   ├── text_encoder.py
│   ├── fusion_module.py
│   └── classifier.py
├── knowledge_graph/          # 知识图谱构建与查询
├── training/                 # 训练循环
├── evaluation/               # 评估与指标
├── app/                      # Gradio 演示 + FastAPI REST 接口 (api.py)
├── web/                      # 新中式 Web 前端 (index.html + style.css + app.js，FastAPI 静态托管)
├── utils/                    # 数据加载、日志
├── tools/export_model.py     # 导出纯视觉分支为 TorchScript
├── experiments/
│   └── configs/default_config.yaml
├── main.py                   # 主入口（含 serve 模式）
├── check_environment.py      # 环境检查
├── requirements.txt
├── setup_env.bat / .sh       # 环境一键安装
└── README.md
```

## 快速开始

### 1. 安装环境

```bat
setup_env.bat        # Windows
# 或
bash setup_env.sh    # Linux / macOS
```

### 2. 检查环境

```bash
python check_environment.py
```

### 3. 准备数据

将标注数据放入 `data/processed/`，格式为 CSV：

```csv
image_path,label,text
data/raw/gouqi/001.jpg,枸杞,味甘性平，归肝、肾经，滋补肝肾
```

### 4. 训练

```bash
python main.py --mode train --config experiments/configs/default_config.yaml
```

> **关键经验：必须使用 `pretrained: true`**（`experiments/configs/default_config.yaml` 默认已开启）。
> 从零训练 Swin-Tiny 会严重欠拟合（纯视觉 acc 仅 15-20%）；启用 ImageNet 预训练权重微调后纯视觉 acc 可达 90%+。
> 历史教训：旧文档曾建议 `pretrained: false`，按旧配置重训会复现 86% 的低分模型，请勿回退该参数。
> 若首次运行需联网下载预训练权重；`experiments/configs/train_v2.yaml`（`pretrained: false`）仅用于本地快速加载已有权重验证流程，不可用于正式训练。

### 5. 演示

```bash
# A. 新中式 Web 前端（推荐，纯 HTML/CSS/JS 单页应用，托管于 FastAPI）
python main.py --mode serve --port 8000
# 打开 http://127.0.0.1:8000

# B. Gradio 演示（调试/备用入口，已与新前端统一新中式主题）
python main.py --mode demo --ckpt experiments/checkpoints/best_model.pth
# 打开 http://127.0.0.1:7862
```

> 未指定 `--ckpt` 时默认加载 `experiments/checkpoints/best_model.pth`；若该文件不存在会打印警告并使用随机模型（识别结果无意义），请先训练。

#### 新中式 Web 前端（推荐入口）

`web/` 目录下的单页应用（`index.html` + `style.css` + `app.js`），由 FastAPI 直接托管，无外部依赖、无需构建。包含 5 个功能页签：

- **图片识别**：拖拽/点击/Ctrl+V 粘贴上传 → Top-5 识别 + 「药材档案大卡」（置信度徽章、药性详情、相似药、易混鉴别、配伍风险、经典方剂）
- **特性检索**：输入性味/归经/功效 → 卡片网格，印章式「完全匹配/部分匹配」角标
- **Grad-CAM**：上传图片生成热力图，滑块实时调节热力叠加透明度
- **AI 对话**：聊天室式多轮对话，可附图提问，回答附「知识库来源」折叠卡片
- **药材关系图谱**：力导向网络图（拖拽/缩放/点击），聚焦单药查看配伍与禁忌网络；工具栏含 **「本草补遗库」** 入口，搜索未命中时可一键进入手动增补药材

> 前端「图片识别」与「特性检索」结果区均含 **导出** 按钮（Markdown / PDF / Word，可自定义文件名）；所有结果区均附医疗风险提示。图谱与特性检索接口返回结构化 JSON（`/graph`、`/herbs` 为新增图谱接口）。

#### Gradio 演示（调试/备用）

网页包含 5 个功能页签：

- **图片识别**：上传图片 + 可选文本描述 → Top-3 识别 + 药性/相似药/方剂说明
- **特性检索**：输入性味/归经/功效 → 列出所有匹配药材 + 方剂推荐
- **模型关注区域 (Grad-CAM)**：可视化模型识别依据的图像部位
- **AI 对话**：与外部大模型（默认 DeepSeek，OpenAI 兼容）多轮对话，自动附带本地知识图谱 + RAG 知识库检索依据（回答末尾标注来源）；未配置 API Key 或调用失败时自动降级为本地检索结果
- **药材关系图谱**：交互式知识图谱可视化（拖拽/缩放/点击），选择药材聚焦其配伍与禁忌网络，红色虚线=十八反、橙色虚线=十九畏、绿色实线=相须相使

> 首次发起 AI 对话时会加载本地 BERT 模型（`D:/models/bert-base-chinese`，约 20 秒，仅一次），用于知识库语义检索；检索失败会自动降级为关键词匹配，不影响对话。

#### 局域网访问（其他设备打开网页）

服务默认绑定 `0.0.0.0`，同一局域网内的手机/电脑可直接访问：

1. 确认 API Key（可选，用于「AI 对话」页签）：
   ```powershell
   echo $env:DEEPSEEK_API_KEY
   ```
2. 防火墙放行端口（首次需管理员权限的终端）：
   ```powershell
   netsh advfirewall firewall add rule name="HerbGradio" dir=in action=allow protocol=TCP localport=7862
   ```
3. 启动服务：
   ```powershell
   python main.py --mode demo --ckpt experiments/checkpoints/best_model.pth
   ```
4. 查看本机局域网 IP（`IPv4 地址`，如 `192.168.1.100`）：
   ```powershell
   ipconfig
   ```
5. 其他设备浏览器打开 `http://192.168.1.100:7862`。

> - 端口可用环境变量 `GRADIO_SERVER_PORT` 覆盖（换端口后需同步修改防火墙规则）。
> - API Key 只需配置在运行服务的那台电脑上，访问网页的设备**不需要**任何配置。
> - 运行服务的电脑需保持开机、不睡眠，其他设备才能持续访问。
> - 如需从公网（异地）访问，可另用内网穿透（cpolar/ngrok）或部署到云服务器。

### 6. REST API 服务化

```bash
python main.py --mode serve --port 8000
```

| 接口 | 说明 |
|------|------|
| `GET /` | 新中式 Web 前端单页应用（静态托管） |
| `GET /health` | 健康检查，返回类别数与 LLM 可用状态 |
| `POST /predict` | 图片+文本识别（multipart；`image` 留空则做纯文本特性检索），返回 Top-5 + 药性/相似药/方剂 |
| `POST /search` | 纯文本特性检索（JSON: `{"text":"味甘平，归肝肾经"}`），返回结构化 `parsed/full/partial` |
| `POST /explain` | Grad-CAM 热图（multipart，返回 PNG，说明在 `X-Explain-Info` 头，URL 编码） |
| `POST /chat` | 外部 LLM 对话解释（multipart；`question` + 可选 `image` + 可选 `history` 多轮），返回 `answer` + 本地识别结果 |
| `GET /graph` | 药材关系图谱力导向图 JSON（可选 `?focus=枸杞`；含 `nodes/links/categoryColors`；用户增补药材 `user_added=true` 一并纳入） |
| `GET /herbs` | 全部药材名列表（前端 datalist 自动补全用） |
| `POST /api/export_recog_pdf` | 导出识别/检索结果为 PDF（`{title, items:[{heading,images,text,markdown}]}`），返回文件流下载 |
| `POST /api/export_recog_docx` | 导出识别/检索结果为 Word（同上结构） |
| `POST /api/export_chat_pdf` | 导出 AI 对话为 PDF（`[{role, content}]`） |
| `GET /api/user_herbs` | 获取「本草补遗库」全部用户增补药材 |
| `POST /api/user_herbs` | 新增用户增补药材（JSON，含图片 base64） |
| `PUT /api/user_herbs/{name}` | 按药名编辑指定用户增补药材 |
| `DELETE /api/user_herbs/{name}` | 按药名删除指定用户增补药材 |

示例（Windows PowerShell）：

```bash
curl.exe -F "image=@photo.jpg;type=image/jpeg" -F "text=味甘" http://127.0.0.1:8000/predict
```

#### 外部 LLM 对话（`/chat` 接口，可选）

本地识别 → 组装「中医药专家」上下文 → 调用外部大模型（默认 DeepSeek，OpenAI 兼容）生成自然语言解释。
**API Key 为敏感信息，请手动配置，勿写入代码或配置文件**：

```powershell
# PowerShell（当前会话）
$env:DEEPSEEK_API_KEY="你的key"
# 可选覆盖（默认 https://api.deepseek.com/v1，模型 deepseek-v4-flash）
$env:LLM_BASE_URL="https://api.deepseek.com/v1"
$env:LLM_MODEL="deepseek-v4-flash"

python main.py --mode serve --port 8000
```

兼容智谱 GLM：改用 `$env:ZHIPU_API_KEY="你的key"` + `$env:LLM_BASE_URL="https://open.bigmodel.cn/api/paas/v4"` + `$env:LLM_MODEL="glm-4-flash"`。

- 未配置 Key 或调用失败时**不会报 500**：`/chat` 自动降级返回本地知识图谱结果，`llm` 字段标记为 `disabled` / `error`；返回体含 `rag_sources`（本次引用的知识库条目）。
- 多轮对话：`history` 传 JSON 字符串 `[{"role":"user","content":"..."},{"role":"assistant","content":"..."}]`。
- 默认模型为 `deepseek-v4-flash`，遇 429 限流/5xx 自动退避重试 3 次；可改 `LLM_MODEL` 或 `experiments/configs/llm_config.yaml`（其中 `api_key` 请留空，走环境变量）。

```bash
# 图片 + 问题（单轮）
curl.exe -F "image=@gouqi.jpg;type=image/jpeg" -F "question=这是什么药材？有什么功效？" http://127.0.0.1:8000/chat
# 文本特性检索 + 多轮追问
curl.exe -F "question=枸杞和菊花能一起用吗？" -F "history=[{\"role\":\"user\",\"content\":\"介绍一下枸杞\"},{\"role\":\"assistant\",\"content\":\"枸杞：滋补肝肾、益精明目……\"}]" http://127.0.0.1:8000/chat
```

### 7. 模型导出（端侧部署）

```bash
python tools/export_model.py --out exports/vision.pt --verify
# 产出 exports/vision.pt (TorchScript) + exports/label2idx.json
# 输入 [B,3,224,224] RGB 归一化张量，输出 [B,163] logits（纯视觉分支，无需 Python/文本模型）
```

完整多模态推理（含 BERT 与知识图谱）请走 REST API；导出文件适合端侧快速识图。

## 评估结果（官方全量）

- 数据集：val 集 10000 张，163 类
- 权重：`experiments/checkpoints/best_model.pth`（Epoch 5，510.9 MB）
- 精炼报告见 `evaluation/reports/eval_official_report.md`（含逐类摘要与易混淆分析）；原始逐类分类报告留档于 `evaluation/reports/eval_official.log`（163 类中绝大多数 precision/recall/f1 = 1.00）

| 模式 | Accuracy | Top-5 |
|------|----------|-------|
| 有文本（多模态） | **0.9995** | **1.0000** |
| 无文本（纯视觉） | **0.9548** | **0.9965** |
| 文本辅助增益 | Δacc = **+0.0447** | — |

## 硬件说明

| 项目 | 配置 |
|------|------|
| GPU 显存 | 8G（batch_size=16, image_size=224） |
| 共享内存 | 11.9G |
| 推荐编码器 | Swin-Tiny + CBAM（约 5-6GB） |

若训练出现 OOM，优先降低 `batch_size` 至 8，或 `image_size` 至 192。
