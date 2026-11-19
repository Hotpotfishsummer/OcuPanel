# 配置参考

所有配置集中在 `backend/.env`，由 `backend/utils/config.py` 读取并做类型转换；
模板见 `backend/.env.example`。代码里没有硬编码的端点、模型或密钥。

配置优先级：**用户在「个人设置」里保存的配置** > `backend/.env` 的服务器默认值。
用户没配过时才会用默认值；两者都没有，报告/聊天接口返回 `409`。

---

## 服务

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | 监听地址。Docker/反代场景改 `0.0.0.0` |
| `PORT` | `8000` | 监听端口 |
| `RELOAD` | `0` | `1` 开启热重载，仅供开发；生产务必关闭 |
| `CORS_ORIGINS` | `http://127.0.0.1:5500,http://localhost:5500` | 仅 Vite 开发服务器需要；同源部署不用改 |
| `FRONTEND_DIST` | `frontend/dist` | 前端产物目录；不存在则降级为纯 API 模式 |

---

## LLM

服务器默认值这一组决定「没配过的用户」用什么模型。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OPENAI_BASE_URL` | 空 | 任意 OpenAI 兼容端点，如 `http://127.0.0.1:3425/v1`、`https://api.deepseek.com` |
| `OPENAI_MODEL` | `gpt-4.1` | 模型名 |
| `OPENAI_API_KEY` | 空 | 本地网关留空即可。空值会自动补占位符，避免发出发非法的 `Bearer ` 头 |
| `LLM_TIMEOUT` | `120` | 传输层单次请求超时（秒） |
| `LLM_MAX_RETRIES` | `1` | SDK 自动重试次数 |
| `LLM_CALL_TIMEOUT` | `90` | **单次调用硬上限**（秒）。看门狗，到点放弃等待；`0` 关闭 |
| `REPORT_DEADLINE` | `210` | **整条报告流水线预算**（秒）。超时跳过校验直接交付草稿；`0` 不限制 |
| `LLM_REASONING_EFFORT` | 空 | 推理强度 `low/medium/high`，留空不传。推理型模型建议 `low` |
| `LLM_MAX_OUTPUT_TOKENS` | `4096` | 单次生成输出上限（token） |
| `LLM_DISABLE_THINKING` | `1` | 仅对 DeepSeek 端点生效：关闭 thinking 提速 |
| `REPORT_LANGUAGE` | `zh` | `zh` / `en`，同时影响模型输出与 PDF 文案 |
| `REPORT_FONT_PATH` | 空 | PDF 中文字体路径；留空自动在系统字体里找 |

**关于 `LLM_REASONING_EFFORT`**：MiniMax、DeepSeek-R1 这类推理模型默认会花大量 token
在隐藏思考上。实测同一段专科提示词，默认强度单次 116 秒，`low` 只要 2–8 秒；报告流水线
一次要调用十几次，不降强度就会几分钟不出结果。非推理端点通常会忽略该参数。

**关于 `LLM_CALL_TIMEOUT`**：传输层超时是「两次数据之间」的间隔，网关只要偶尔吐几个字节
就能一直重置，实测出现过单次调用 277.8 秒。看门狗把等待硬截断，这才是防止「转圈几分钟」
的关键开关。

---

## 聊天上下文

数据库保留全量消息，只有**发给模型**的部分受这些预算限制。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CHAT_HISTORY_MAX_MESSAGES` | `20` | 最多带多少条历史消息 |
| `CHAT_CONTEXT_MAX_CHARS` | `20000` | 上下文总字数上限 |
| `CHAT_REPORT_MAX_CHARS` | `6000` | 报告正文进入上下文时最多截多少字 |
| `CHAT_MAX_OUTPUT_TOKENS` | `2048` | 追问回复的输出上限 |

四项都必须为正数，否则启动时直接报错。

---

## 知识检索（RAG）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `RAG_ENABLED` | `1` | `0` 退回纯模型生成，便于 A/B 对比 |
| `RAG_MODE` | `hybrid` | `bm25` / `vector` / `hybrid`（两路召回 + RRF） |
| `RAG_TOP_K` | `4` | 进入证据包的片段数 |
| `RAG_MIN_SCORE` | `0.35` | 相对阈值：不低于最佳命中的该比例 |
| `RAG_VECTOR_MODEL` | `intfloat/multilingual-e5-small` | 多语言向量模型（约 470 MB） |
| `RAG_VECTOR_MIN_SCORE` | `0.78` | 向量余弦绝对阈值（e5 分数整体偏高） |
| `RAG_RRF_K` | `60` | RRF 融合常数，越大排名越平缓 |
| `RAG_LANG_QUOTA` | `1` | 为每种语言保底分配名额，`0` 关闭 |
| `RAG_LANG_GATE` | `0.89` | 只有最佳命中 ≥ 该值才启用语言配额 |
| `RAG_MAX_PER_SOURCE` | `2` | 同一文档最多入选几条，`0` 不限 |

`RAG_LANG_GATE` 是标定值，换 embedding 模型或大改语料后必须重新标定。启动时的
`selfcheck_gate()` 会用探针复核，失配会在日志里告警。

---

## 引用校验

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `VERIFY_ENABLED` | `1` | `0` 整体关闭校验（只摘掉非法编号） |
| `VERIFY_MAX_ROUNDS` | `2` | 改写轮数上限，每轮最多 2 次 LLM 调用 |
| `VERIFY_REGENERATE` | `1` | 不收敛时是否整篇重生成；`0` 关闭（更快，推荐） |
| `VERIFY_MIN_CHARS` | `300` | 正文低于此字数判定「不成文」 |

注意：无论怎么配，**校验都不会让报告消失**——最多把状态标成 `partial` 并在界面列出
待复核条目。详见 [ARCHITECTURE.md](ARCHITECTURE.md#5-引用校验)。

---

## 演示模式

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DEMO_AUTO_LOGIN` | `1` | 开启后跳过登录页，任何访问者都能拿到演示账号 |
| `DEMO_USERNAME` | `demo` | 演示账号用户名 |
| `DEMO_PASSWORD` | `Demo12345` | 演示账号密码 |
| `DEMO_DISPLAY_NAME` | `演示账号` | 界面显示名 |

> ⚠️ `DEMO_AUTO_LOGIN=1` 时 `/api/account/demo-login` 不校验密码。只在本机或内网演示
> 环境开启，公网部署请设为 `0`。

---

## 安全与存储

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OCUPANEL_SECRET_KEY` | 首次启动自动生成 | 加密用户 API Key 的主密钥。写入 `backend/.env`，**不要更换**，否则已存 Key 全部失效 |

`backend/.env`、`backend/ocupanel.db`、`frontend/dist`、`logs/` 都已在 `.gitignore` 中。

---

## 高级 / 实验开关

默认值即当前行为，一般不用动。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HF_ENDPOINT` | 空 | HuggingFace 镜像，如 `https://hf-mirror.com` |
| `MULTI_AGENT_MODE` | `parallel` | `parallel`（当前唯一实现）/ `collaborative` / `adaptive` |
| `GRAPH_RAG_ENABLED` | `0` | GraphRAG 图检索路，实验性 |
| `GRAPH_STORE` | `networkx` | 图存储后端：`networkx` / `neo4j` / `light_rag` |
| `AGENT_TRACE_ENABLED` | `0` | `1` 时把协作轨迹写入 `agent_traces` 表 |
| `EXPIRY_SECONDS` | `300` | 临时文件清理检查周期 |
| `CLEANUP_INTERVAL` | `300` | 同上 |

`ANALYSIS_PIPELINE` 已被移除：只实现了青光眼分析，设为非 `legacy` 值会在启动时报错。

---

## 前端构建变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | 同源 | 构建时注入，指向不同源的 API 时使用 |
| `API_PROXY_TARGET` | `http://127.0.0.1:8000` | 仅开发服务器：`/api` 代理目标 |
