# 架构

## 总览

单进程 FastAPI 同时提供 API 与前端静态资源；一次报告请求在同一个进程内串起
视觉、检索、多智能体与校验四个阶段；所有用户态数据落在 SQLite。

```
浏览器
  │  POST /api/generate-report-content/   (multipart: file, note, conversation_id)
  ▼
FastAPI  ── 鉴权(Bearer) ──►  report/service.py
                                 │
                                 ├─► vision.analyze_fundus      分割视盘/视杯，算 CDR
                                 ├─► knowledge.retrieve_evidence BM25 + 向量 → RRF → 证据包
                                 ├─► agents.ParallelOrchestrator 六专科并发
                                 ├─► agents.DirectorAgent        综合成文
                                 └─► agents.CitationVerifier     逐句核对引用
                                 │
                                 ▼
                         SQLite(reports/messages) + output/ 临时目录
```

---

## 分层

| 层 | 位置 | 职责 |
| --- | --- | --- |
| HTTP | `backend/*/api.py`、`main.py` | 绑定请求、鉴权、构造响应；不含业务逻辑 |
| 服务 | `backend/*/service.py` | 编排、事务、把异常翻译成 HTTP 错误 |
| 领域 | `backend/agents/`、`backend/knowledge/`、`backend/vision/` | 具体能力，不感知 HTTP |
| 基础设施 | `backend/llm/`、`backend/utils/` | LLM 客户端、配置、SQLite、鉴权、路径 |

`main.py` 只做三件事：初始化数据库与会话、挂载三个路由（`/api` 前缀）、
存在 `frontend/dist` 时把 SPA 挂到根路径。

---

## 报告流水线

入口 `MedicalReportGenerator.generate()`。

### 1. 视觉分析

`vision.analyze_fundus()` 用本地分割模型处理眼底图，输出视盘/视杯分割与杯盘比，
同时把叠加图写入请求专属的 `output/` 临时目录。这一步不调用 LLM。

### 2. 证据检索

`knowledge.service.retrieve_evidence()` 把病历描述和 CDR 拼成查询，召回知识库片段。
返回的每个片段带 `index`、`source`、`text`、`score`，`index` 就是报告里 `[n]` 的编号。

进入提示词的证据会被裁到 `max(RAG_TOP_K * 2, 6)` 条，避免上下文爆炸。

### 3. 六专科并发

`ParallelOrchestrator` 用线程池同时跑六个 `SpecialistAgent`：

- Ophthalmologist（青光眼/视神经/视野）
- Optometrist（眼压/视力/筛查）
- Glaucoma Specialist（杯盘比/RNFL/目标眼压/随访）
- Primary Care Physician（全身危险因素）
- Ophthalmic Technician（检查方法与数据）
- Pharmacist（药物与禁忌）

每个专科可进行最多 3 步工具调用（`search_evidence` / `read_evidence`），然后给出结论。
**单个专科失败（超时、输出损坏）只跳过该专科**，其余结论照常汇总；只有全部失败才报错。

### 4. Director 综合

`DirectorAgent` 拿到六份专科报告与证据包，写成一份面向临床的完整报告。提示词里
附带证据块，要求每条结论句末标 `[n]`。

### 5. 引用校验

`CitationVerifier` 是唯一会「否定」内容的环节，但**它不阻断交付**：

```
核查(LLM) ──► 无问题 ──► pass
   │
   └─► 有问题 ──► 改写(LLM) ──► 复核(LLM)
                                  ├─ 无问题 ─► rewritten
                                  └─ 仍有余留 ─► partial（交付 + 列出遗留）
```

每轮最多处理 12 条问题（`MAX_ISSUES_PER_ROUND`）；轮数由 `VERIFY_MAX_ROUNDS` 控制。
若改写把正文删得过短（低于 300 字或原稿 40%），可回退到 Director 原稿。

**状态语义：**

| status | 含义 | 是否交付 |
| --- | --- | --- |
| `pass` | 全部断言都有资料支持 | 是 |
| `rewritten` | 有问题，但已全部改写收敛 | 是 |
| `partial` | 仍有余留，或校验调用本身失败 | **是**，并列出遗留条目 |
| `disabled` | `VERIFY_ENABLED=0`，只摘掉非法编号 | 是 |
| `rejected` | 正文为空，没有任何可交付内容 | 否 |

交付前会调用 `strip_unknown_citations()` 摘掉对不上任何证据的 `[n]`——**交付不等于
放行伪造引用**。

### 6. 落库与返回

`report/service.py` 保存报告与两条聊天消息（用户提问、AI 报告），把
`verification` 与 `rejected` 写进消息 `meta`，前端据此渲染校验条。

---

## 超时与并发

三层保护，缺一层都会出现「转圈几分钟」：

| 层 | 变量 | 默认 | 作用 |
| --- | --- | --- | --- |
| 传输 | `LLM_TIMEOUT` | 120s | httpx 单次请求超时 |
| 调用 | `LLM_CALL_TIMEOUT` | 90s | 看门狗，到点放弃等待，**不受网关慢速吐字节影响** |
| 流水线 | `REPORT_DEADLINE` | 210s | 超预算跳过校验，直接交付草稿 |

为什么需要看门狗：传输层 timeout 是「两次数据之间」的间隔，网关只要偶尔吐一点字节
就能一直重置。实测出现过单次调用 277.8 秒的情况，SDK 自动重试还会把 120s 翻倍。
看门狗把每次等待硬截断在上限内，被放弃的线程自行结束，不阻塞后续阶段。

六个专科是并发执行的（线程池大小 = 专科数），所以四类 LLM 调用里只有专科阶段能并行，
Director 与校验都是串行的。

---

## 检索细节

`RAG_MODE` 决定召回方式：

- `bm25`：纯关键词，零依赖，中文查询查不到英文块；
- `vector`：多语言向量余弦；
- `hybrid`（默认）：两路召回后用 RRF 融合，`score = Σ 1/(RAG_RRF_K + rank)`。

两个防退化机制：

1. **语言配额**（`RAG_LANG_QUOTA`）：每种语言至少保留 `k / 语言数` 个位置。否则中文
   查询的 top-k 会被中文块占满，英文指南永远进不来。
2. **配额门控**（`RAG_LANG_GATE=0.89`）：只有最佳命中足够强时才启用配额。跨语言相关块
   的余弦（0.82–0.84）低于同语言无关块（0.85–0.87），用绝对阈值必然误杀，只能用
   「查询整体是否强相关」来门控。

`RAG_MAX_PER_SOURCE` 限制同一文档最多入选几条，防止相邻段落占满名额。
启动时 `retrieval.selfcheck_gate()` 会用探针复核这个门控值，失配会打 warning。

---

## 数据模型

SQLite（`backend/ocupanel.db`），`storage.init_db()` 建表：

| 表 | 内容 |
| --- | --- |
| `users` | 账号、密码哈希（PBKDF2）、显示名 |
| `sessions` | 登录态 token 哈希、过期时间（7 天） |
| `llm_configs` | 每用户 LLM 配置，API Key 加密存储 |
| `conversations` | 会话 |
| `messages` | 消息，`meta` 里存报告附件、校验结果、证据 |
| `reports` | 报告正文与元数据 |
| `agent_traces` | 可选的协作轨迹（默认关闭） |
| `graph_metadata` | GraphRAG 预留 |

加密密钥来自 `OCUPANEL_SECRET_KEY`；换掉它等于让所有已存 Key 失效。

---

## 前端

React 18 + Vite，无路由库，靠 `App.jsx` 的视图状态切换：

- `ChatView`：消息流、报告卡片、生成进度（时间驱动，非真实进度）、追问输入；
- `ReportView` / `VerificationResult` / `EvidenceList`：报告、校验条、证据列表；
- 报告气泡顶部渲染 `ReportFigure`：杯盘分割叠加图 + CDR 图注，点击开大图（Esc/点击关闭）；
- `SettingsView`：个人 LLM 配置与用量；
- 开发模式 5500 端口，`/api` 代理到 8000；生产由 FastAPI 托管 `dist`。

`DEMO_AUTO_LOGIN=1` 时前端启动即调 `/api/account/demo-login`，跳过登录页。

---

## 设计取舍

**校验为什么不阻断？** 早期版本在「仍有断言无依据」时直接拒答。实测这对用户是双输：
等了 2 分钟，什么都没拿到。现在改为「标注交付」——报告可用，风险显式列出。真正
不可交付的只剩「正文为空」。

**为什么不做整篇重生成？** `VERIFY_REGENERATE` 保留但默认关闭。重生成要走两次长调用，
实测常常只是换一套说法把同样的幻觉写回来，还更慢。

**为什么缓存 generator 被删了？** generator 现在只存配置，对话历史在 SQLite；缓存它
没有收益，反而会和数据库状态不一致。

**为什么单服务托管前端？** 少一个 nginx、少一个端口、少一类 CORS 问题。需要横向扩展
时再拆开即可。
