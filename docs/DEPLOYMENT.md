# 部署与运维

单机部署就够用：一个 Python 进程（FastAPI）同时提供 API 和前端页面，数据在 SQLite。
下面的步骤以 Ubuntu 22.04 为例。

---

## 1. 前置

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip nodejs npm fonts-noto-cjk
```

- Python 3.11+（实测 3.13 也可）
- Node 18+（只在构建前端时需要）
- `fonts-noto-cjk`：PDF 中文渲染依赖它，缺了会出现空框

---

## 2. 安装

```bash
git clone https://github.com/Hotpotfishsummer/OcuPanel.git /opt/ocupanel
cd /opt/ocupanel

python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

cd frontend && npm ci && npm run build && cd ..

cp backend/.env.example backend/.env
chmod 600 backend/.env
```

编辑 `backend/.env`，生产环境至少确认：

```ini
HOST=127.0.0.1          # 交给 nginx 反代，不要把 8000 直接暴露
PORT=8000
RELOAD=0
DEMO_AUTO_LOGIN=0       # 公网必须关闭

OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
OPENAI_API_KEY=sk-…

REPORT_FONT_PATH=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
LLM_CALL_TIMEOUT=90
REPORT_DEADLINE=210
```

首次启动会下载向量模型（约 470 MB）。网络受限时先设 `HF_ENDPOINT=https://hf-mirror.com`，
或提前把模型缓存准备好，避免第一个真实请求超时。

---

## 3. systemd

`/etc/systemd/system/ocupanel.service`：

```ini
[Unit]
Description=OcuPanel
After=network.target

[Service]
Type=simple
User=ocupanel
WorkingDirectory=/opt/ocupanel
ExecStart=/opt/ocupanel/.venv/bin/python /opt/ocupanel/backend/main.py
Restart=always
RestartSec=3
# torch 会预分配内存，单 worker 足够；多 worker 会成倍吃内存
Environment=OMP_NUM_THREADS=4

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin ocupanel
sudo chown -R ocupanel:ocupanel /opt/ocupanel
sudo -u ocupanel /opt/ocupanel/.venv/bin/python /opt/ocupanel/backend/main.py   # 先手动跑一次，确认能起
sudo systemctl daemon-reload
sudo systemctl enable --now ocupanel
sudo systemctl status ocupanel
```

> 不要开多 worker（`--workers 2`）。SQLite 写入与向量模型都会抢占资源，收益为负。

---

## 4. nginx 反代

```nginx
server {
    listen 443 ssl http2;
    server_name ocupanel.example.com;

    ssl_certificate     /etc/letsencrypt/live/ocupanel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ocupanel.example.com/privkey.pem;

    client_max_body_size 25m;          # 眼底图上传
    proxy_read_timeout   300s;         # 报告最坏要 3 分钟，别用默认 60s

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name ocupanel.example.com;
    return 301 https://$host$request_uri;
}
```

`proxy_read_timeout` 必须大于 `REPORT_DEADLINE`，否则请求还在跑 nginx 先返回 504。

---

## 5. 安全清单

- [ ] `DEMO_AUTO_LOGIN=0`（否则任何人都能拿到演示账号）
- [ ] `HOST=127.0.0.1`，只让 nginx 访问 8000
- [ ] HTTPS 已启用；登录密码不在 URL 里
- [ ] `backend/.env` 权限 `600`，属主是运行用户
- [ ] `backend/ocupanel.db`、`backend/img_store/`、`backend/output/` 不在 Web 可访问路径下
- [ ] `OCUPANEL_SECRET_KEY` 已备份（丢了等于所有用户要重填 Key）
- [ ] 定期备份数据库

---

## 6. 备份

需要备份三样：

```bash
# 1) 数据库（一致性快照，别直接 cp 运行中的 db）
sqlite3 /opt/ocupanel/backend/ocupanel.db ".backup '/backup/ocupanel-$(date +%F).db'"

# 2) 加密密钥
grep OCUPANEL_SECRET_KEY /opt/ocupanel/backend/.env >> /backup/secrets.txt

# 3) 上传的图片
tar czf /backup/img_store-$(date +%F).tgz -C /opt/ocupanel/backend img_store
```

恢复时三样必须配套：换掉 `OCUPANEL_SECRET_KEY` 会让库里所有 API Key 无法解密。

---

## 7. 升级

```bash
cd /opt/ocupanel
sudo systemctl stop ocupanel
git pull
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..
sudo systemctl start ocupanel
```

只改后端时不需要 `npm run build`；只改前端时必须重新构建，否则托管的还是旧 `dist`。

---

## 8. 观测

日志走 stdout，`journalctl -u ocupanel -f` 或用 `local/start.sh` 时的 `logs/ocupanel.log`。

关键日志：

| 关键字 | 含义 |
| --- | --- |
| `[LLM] 12.3s in=2171 out=1680` | 一次模型调用的耗时与输入输出规模 |
| `[LLM] … FAILED TimeoutError` | 该次调用触发了看门狗 |
| `[VERIFY] 第 N 轮：X 处问题` | 引用校验进度 |
| `[VERIFY] 仍有 X 处断言无法被资料支持，交付正文并标注未核实` | 正常收尾（非错误） |
| `[AGENT] 某专科失败，已跳过` | 单个专科失败，报告仍会生成 |
| `[REPORT] 已用 Xs，超出总预算` | 触发 `REPORT_DEADLINE`，跳过校验直接交付 |
| `[RAG] 命中 N 条证据` | 检索结果 |
| `RAG gate selfcheck needs attention` | 门控阈值漂移，需要重新标定 |

报告耗时看 `POST /api/generate-report-content/` 那条 access log 的 `time`。

---

## 9. 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 页面 404 / 空白 | 没构建前端 | `cd frontend && npm run build` |
| 上传 413 | nginx 默认 1MB | `client_max_body_size 25m` |
| 504 但日志显示报告已生成 | `proxy_read_timeout` 太短 | 调到 300s 以上 |
| 报告生成超过 3 分钟 | 网关长尾拖住某次调用 | 调小 `LLM_CALL_TIMEOUT`；确认 `LLM_REASONING_EFFORT=low` |
| 报告标注「部分结论未核实」 | 校验仍有断言无依据 | 正常行为，补充资料可减少条目 |
| PDF 中文空框 | 缺 CJK 字体 | `apt install fonts-noto-cjk` 或设 `REPORT_FONT_PATH` |
| 首次生成卡很久 | 正在下载向量模型 | 预下载或设 `HF_ENDPOINT` |
| 接口 409 | 用户没配 LLM 且无服务器默认 | 填 `.env` 或让用户在设置页配置 |
| 进程反复重启 | OOM | 加内存；确认只跑一个 worker |
| 用户 Key 全部失效 | `OCUPANEL_SECRET_KEY` 变了 | 恢复原密钥，或让用户重填 |
| 报告接口 500 且日志有 `Invalid port: ':1]'` | `NO_PROXY` 里有带括号的 IPv6 | 启动时会自动清洗；若仍出现，检查是否有其他进程覆盖了该变量 |

---

## 10. 容量参考

- 单次报告：6 次专科调用 + 1 次综合 + 1–5 次校验调用（首轮核查 + 每轮改写与复核；
  开启 `VERIFY_REGENERATE` 再加 2 次），约 3–6 万 token（取决于模型与报告长度）。
- 内存：torch + 分割模型 + 向量模型常驻约 2–4 GB。
- 磁盘：模型缓存约 1–2 GB，SQLite 与图片随使用量增长。
- 并发：单进程可同时处理少量报告（专科阶段内部已有 6 路并发）；大量并发请在前面排队，
  不要让同一个 LLM 配额被瞬间打满。
