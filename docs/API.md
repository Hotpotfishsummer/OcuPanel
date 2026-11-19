# API 参考

- 基地址：`http://127.0.0.1:8000`
- 所有业务接口前缀：`/api`
- 请求/响应：JSON（报告生成接口为 `multipart/form-data`）
- 交互式文档：服务启动后访问 <http://127.0.0.1:8000/docs>

---

## 认证

除注册、登录、演示登录外，所有接口都需要：

```
Authorization: Bearer <token>
```

token 由 `POST /api/account/login`、`/api/account/register` 或 `/api/account/demo-login`
返回，有效期 **7 天**，过期返回 `401`。

### 错误格式

业务错误统一为：

```json
{ "detail": "人类可读的错误原因" }
```

参数校验错误（FastAPI 默认）为 `422`，`detail` 是字段错误数组。

| 状态码 | 含义 |
| --- | --- |
| `400` | 参数/图片不合法 |
| `401` | 未登录或 token 过期 |
| `404` | 资源不存在（他人的会话也返回 404，不泄露存在性） |
| `409` | 当前用户没有可用的 LLM 配置（服务器也没给默认值） |
| `422` | 请求体校验失败；或报告正文为空 |
| `502` | 上游模型调用失败（`detail` 里带原因） |

---

## 账户

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/account/register` | 否 | 注册并直接登录 |
| POST | `/api/account/login` | 否 | 登录 |
| POST | `/api/account/demo-login` | 否 | 演示直登；`DEMO_AUTO_LOGIN=0` 时返回 404 |
| POST | `/api/account/logout` | 是 | 注销当前 token |
| GET | `/api/account/me` | 是 | 当前用户 + 已保存的 LLM 配置（Key 打码） |
| GET | `/api/account/llm-presets` | 是 | 端点预设 + 服务器默认值 |
| PUT | `/api/account/llm` | 是 | 保存个人 LLM 配置 |
| DELETE | `/api/account/llm` | 是 | 清除个人配置，回落默认值 |
| POST | `/api/account/llm/test` | 是 | 用给定配置发一次最小请求，不落库 |
| GET | `/api/account/usage` | 是 | 报告数、会话数、拒答数、最近生成时间 |

### 注册 / 登录

```bash
curl -X POST http://127.0.0.1:8000/api/account/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"secret123","display_name":"Alice"}'
```

```json
{
  "user": { "id": "…", "username": "alice", "display_name": "Alice" },
  "token": "…",
  "demo": true
}
```

`demo` 表示服务端是否开启了演示模式，并不代表这次登录是演示登录。

`username` 3–32 字符，`password` 至少 6 位；重名返回 `400`。

### 保存 LLM 配置

```bash
curl -X PUT http://127.0.0.1:8000/api/account/llm \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"api_key":"sk-…","base_url":"https://api.deepseek.com","model":"deepseek-chat"}'
```

`api_key` 加密后存库；留空表示「不需要 Key」（本地网关）。三项都留空等于回到服务器默认。

### 测试连通性

```bash
curl -X POST http://127.0.0.1:8000/api/account/llm/test \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"api_key":"","base_url":"http://127.0.0.1:3425/v1","model":"remote-magpie/minimax-cn/MiniMax-M3.1-Flash"}'
```

```json
{ "ok": true, "reply": "你好", "prompt_tokens": 12, "completion_tokens": 3 }
```

---

## 会话与聊天

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/chat/conversations` | 会话列表（按更新时间倒序，最多 50 条） |
| POST | `/api/chat/conversations` | 新建会话 |
| GET | `/api/chat/conversations/{id}` | 会话详情 + 全部消息 |
| PATCH | `/api/chat/conversations/{id}` | 重命名 |
| DELETE | `/api/chat/conversations/{id}` | 删除 |
| POST | `/api/chat/conversations/{id}/messages` | 发消息，等模型回复 |
| PATCH | `/api/chat/conversations/{id}/mode` | 切换模式 |

### 新建会话

```json
{ "title": "新对话", "mode": "general" }
```

`mode`：`general`（普通问答）/ `ophthalmic`（眼科追问，带知识库检索上下文）。
两个字段都可省略。

### 发消息

```bash
curl -X POST http://127.0.0.1:8000/api/chat/conversations/$CID/messages \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"青光眼的一线治疗原则是什么？"}'
```

```json
{ "reply": "青光眼治疗以控制眼压……", "message_id": "…" }
```

字段名是 **`message`**，不是 `content`。首条消息会自动把会话标题改成前 20 字。
模型调用失败返回 `502`，`detail` 里是翻译过的原因。

---

## 报告

| 方法 | 路径 | 返回 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/generate-report-content/` | JSON | 生成报告，前端用这个 |
| POST | `/api/generate-report/` | PDF 文件 | 生成后直接下载 |
| GET | `/api/get-report-image/{img_name}` | JPEG | 取分割叠加图 |

### 请求

`multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | 文件 | 是 | 眼底图，`.jpg/.jpeg/.png` |
| `note` | 文本 | 否 | 临床描述（症状、眼压、家族史等） |
| `conversation_id` | 文本 | 否 | 给定时把报告写入该会话 |

```bash
curl -X POST http://127.0.0.1:8000/api/generate-report-content/ \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@samples/fundus_right_eye.png" \
  -F "note=右眼胀痛3天，眼压28mmHg，杯盘比0.7，有青光眼家族史"
```

### 响应（`generate-report-content`）

```json
{
  "summary": "# 青光眼临床综合诊断报告\n\n## 一、临床结论……",
  "cup_to_disc_ratio": 0.62,
  "img_name": "abc123.png",
  "evidence": [
    {
      "index": 1,
      "id": "glaucoma-diagnosis#1-1",
      "source": "glaucoma-diagnosis.md",
      "text": "……",
      "score": 0.91
    }
  ],
  "verification": {
    "status": "partial",
    "rounds": 2,
    "initial_issues": 9,
    "fixed": [{ "quote": "……", "new": "……", "citations": [2] }],
    "remaining_issues": [{ "quote": "……", "verdict": "uncited", "reason": "……" }],
    "reason": "unverified_claims_remain"
  },
  "rejected": false,
  "report_id": "…"
}
```

`verification.status` 取值与含义：

| status | 含义 | 是否交付 |
| --- | --- | --- |
| `pass` | 全部有据 | 是 |
| `rewritten` | 改写后收敛 | 是 |
| `partial` | 仍有余留 / 校验调用失败 | 是（列出遗留） |
| `disabled` | 校验关闭 | 是 |
| `rejected` | 正文为空 | 否 |

`rejected` 与 `summary` 为 `null` 同步出现；正常调用不会走到这里。

### 响应（`generate-report`）

直接返回 `application/pdf`，`Content-Disposition` 带文件名 `diagnostic_report.pdf`。
正文生成失败时与 `generate-report-content` 一样返回 JSON 错误。

### 常见错误

| 场景 | 状态码 | detail |
| --- | --- | --- |
| 没配 LLM | `409` | 提示去设置页或 `.env` 配置 |
| 图片格式不对 | `400` | `仅支持 .jpg / .jpeg / .png 格式的眼底图像` |
| 图片无法解析 | `400` | `图片无法解析，请换一张` |
| 会话不属于当前用户 | `404` | `会话不存在` |

---

## 前端调用顺序

`frontend/src/api.js` 里的典型流程：

1. `POST /api/account/demo-login`（或 login）→ 存 token；
2. `GET /api/chat/conversations` → 选一个或新建；
3. `POST /api/generate-report-content/` → 渲染报告、校验条、证据；
4. 后续追问走 `POST /api/chat/conversations/{id}/messages`。
