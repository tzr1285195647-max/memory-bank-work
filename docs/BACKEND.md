# 记忆银行后端（本机开发版）

FastAPI + SQLite 单文件库，供小程序在开发者工具中联调。**部署到云托管时只需替换配置，接口契约不变。**

## 启动

```powershell
cd D:\记忆银行
python backend/run.py
```

- 服务地址：<http://127.0.0.1:8787>
- 接口文档（自动生成，可直接试接口）：<http://127.0.0.1:8787/docs>
- 数据目录：`backend/.data/`（数据库 `app.db` + 录音 `objects/`，已加入 .gitignore）

改用其他端口：`$env:PORT=9000; python backend/run.py`

> **端口说明**：本机 `8000` 已被另一个服务占用（`cvmodel\local_model_server.py`），所以后端用 `8787`。改端口时记得同步修改小程序根目录的 `config.js`。

## 演示账号

| 手机号 | 密码 | 身份 |
|---|---|---|
| `13800008899` | `123456` | 长辈（首次启动自动创建，含 4 条示例故事） |

**任意 11 位手机号 + 6 位以上密码都能登录**：首次登录会自动注册并创建家庭与授权记录，方便现场用不同账号演示。

## 接口一览

### 基础接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| POST | `/api/auth/login` | 登录（首次自动注册） |
| GET | `/api/me` | 个人资料与统计 |
| GET | `/api/topics` | 四个主题（乡/校/业/家） |
| GET | `/api/home` | 今日叙事 + 最近的故事 |
| GET | `/api/family` | 家庭看板（成员、进度、待确认） |
| POST | `/api/recordings` | 上传录音（multipart） |
| GET | `/api/stories` | 故事列表 |
| GET | `/api/stories/{id}` | 故事详情（含原声地址） |
| POST | `/api/consent/revoke` | 撤回授权并删除内容与原声 |
| GET | `/fonts/*.ttf` | 思源宋体子集（供 wx.loadFontFace） |

### 多智能体接口（`/api/agent`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agent/interviews` | 开启采访，返回采访导演的第一个问题 |
| POST | `/api/agent/interviews/answers` | 提交一轮讲述（`finish=true` 触发写作与审计） |
| POST | `/api/agent/interviews/stop` | 尊重停止意愿，不再追问 |
| POST | `/api/agent/interviews/review` | 人工确认：`approve` / `edit` / `request_more` / `reject` |
| POST | `/api/agent/stories/{id}/review` | 故事书里的确认（先按证据核对正文） |

**典型流程**：

```powershell
# 1) 开启采访（拿到 session_id 与第一个问题）
curl -X POST http://127.0.0.1:8787/api/agent/interviews -H "Authorization: Bearer <token>" `
  -H "Content-Type: application/json" `
  -d '{"topicId":"hometown","subjectName":"林阿姨","maxRounds":3,"consentVersion":1}'

# 2) 提交讲述（finish=true 时产出草稿 + 证据链 + 审计结论）
curl -X POST http://127.0.0.1:8787/api/agent/interviews/answers -H "Authorization: Bearer <token>" `
  -H "Content-Type: application/json" `
  -d '{"sessionId":"interview-xxx","answer":"那年秋天，院子里的桂花开得很早。","finish":true,"topicId":"hometown","consentVersion":1}'

# 3) 人工确认（改写引入无证据内容会返回 403）
curl -X POST http://127.0.0.1:8787/api/agent/interviews/review -H "Authorization: Bearer <token>" `
  -H "Content-Type: application/json" `
  -d '{"sessionId":"interview-xxx","action":"approve","consentVersion":1}'
```

返回体里与证据相关的字段：`claims`（每条带 `quote` 与 `turn_id`）、`missing_fields`、
`audit_findings`、`audit_passed`、`conflicts`、`draft_text`、`storyId`。

**错误语义**：

- `403` —— 授权版本不符 / 改写引入了无法追溯到原声的内容
- `409` —— 会话不在人工确认点（例如改写被拒后已退回采访），此时发确认动作无效
- `404` —— 会话或故事不存在

## 产品规则在服务端的落地位置

| 规则 | 实现 |
|---|---|
| 所有查询按家庭范围 | 每个查询都带 `family_id`，且 `family_id` 只从 JWT 解析，**不信任请求体** |
| 写操作校验授权版本 | `require_consent()`，版本不符返回 403 |
| 撤回后停止使用 | `revoke_consent()` 把授权置为 revoked 并递增版本号，之后写操作全部 403 |
| 撤回后删除原声 | 删除**该家庭全部**录音文件与对象目录（不只是被故事引用的，避免孤儿文件） |
| AI 内容待确认 | 草稿一律 `pending_review`；修改后也会重置回 `pending_review` |
| 不信任原始文件名 | 对象键用内容 SHA-256 摘要生成 |
| 不泄露口令 | 口令 Argon2 哈希；响应只返回脱敏手机号 |

## 测试

```powershell
python -m pytest backend/tests -q      # 10 个契约与规则测试
python backend/api_selftest.py         # 29 项接口自测（需服务已启动）
python backend/smoke.py                # 打印演示数据，确认种子正常
```

测试使用临时数据目录与独立数据库，不会污染开发库。

## 小程序如何连本机后端

1. 后端已在 `127.0.0.1:8787` 运行；
2. 开发者工具「详情 → 本地设置」勾选 **不校验合法域名**（`project.config.json` 里已设 `urlCheck: false`，通常无需手动操作）；
3. 小程序根目录 `config.js` 的 `baseUrl` 指向 `http://127.0.0.1:8787`；
4. 后端不可用时会自动降级到本地 mock 数据，演示不中断（`fallbackToMock: true`）。

> **真机限制**：微信真机不允许请求 `http://127.0.0.1`，也不允许 IP 地址，必须 HTTPS 域名并加入小程序后台白名单。因此**现场路演请用开发者工具模拟器**；若将来要给评委扫码体验，需要部署到微信云托管或已备案域名的服务器（见 `docs/TECH_PLAN.md`）。

## 已知边界（演示版）

- 单家庭模型：每个用户首次登录自动建一个家庭，多家庭邀请属于 P1。
- 转写（ASR）与 Agent 正文生成为占位：草稿正文是明确标注的演示文案，不会伪装成讲述原文。
- 录音文件走静态目录 `/media`：生产环境必须改为带签名的短期 URL。
- 审计事件表尚未落地（`AuditEvent`），撤回目前只保留授权记录。
