# 记忆银行

一个面向家庭口述记忆的微信小程序：长辈录音，腾讯云 ASR 转写，五个核心 Agent 协助校对、提取证据、温和追问、写作和审计；家人可协助整理，但故事必须由长辈确认。当前目标是**本机后端 + 微信开发者工具 / 同局域网真机**的演示，不是公网服务。

> 项目规则以 [agent.md](agent.md) 为准。当前代码仍有待修复项，见文末“已知限制”；不要把待确认草稿当作已发布故事。

## 当前能做什么

- 四个本机演示账号、注册、密码登录、固定验证码演示登录，同一家庭内跨账号共享。
- 微信录音、腾讯云真实 ASR、人工填写与校对。转写失败时保留录音，可重试或人工填写，**不会自动填入演示文字**。
- 多段记忆碎片的保存、勾选、编辑、删除、排序和移动；显示原讲述者与确认人。
- 三种故事整理风格：`raw / 原味口述`、`natural / 自然整理`、`book / 适合成书`。
- 故事草稿、逐句证据、事实审计、家庭建议、长辈处理、故事列表与操作记录。
- LangGraph 采访会话通过 SQLite Checkpoint 暂停和恢复；后端重启后可继续尚未结束的采访。

## 五个核心 Agent

五个 Agent 是五种独立职责，**不要求五个模型或五把 API Key**。真实模式下，它们通过同一个 OpenAI 兼容模型接口、不同 Prompt 和结构化输出 Schema 工作。Prompt 目前直接定义在 [`backend/agents/llm.py`](backend/agents/llm.py)，输出模型在 [`backend/agents/schemas.py`](backend/agents/schemas.py)；目前没有独立且被代码加载的 Prompt Markdown 文件。

| Agent | 进入流程的时机 | 输出与硬约束 |
| --- | --- | --- |
| 口述校对 | 腾讯云 ASR 成功后 | 给出可编辑的清理建议及不确定项；`asrRawText` 原始转写、`agentCleanText` 建议、`confirmedText` 人工确认文本分开保存。Agent 不得覆盖原文或新增事实。 |
| 记忆证据 | 碎片文字经人确认后 | 提取时间、地点、人物、事件、结果、影响、感受七类事实；保存 `fragmentId`、`recordingId`、`narratorUserId` 和原文 `quote`。`quote` 必须是确认文字的真实子串；冲突并列保留。 |
| 采访提示 | 开始采访及每轮确认后 | 根据已有回答、缺失要素和已问问题生成下一问；识别停止意愿，达到完整度或轮次等结束条件后停止。 |
| 故事写作 | 用户勾选已确认碎片并选择风格后 | 只使用同一位讲述者的所选碎片，产出标题、正文、风格和句子级证据关系。混选林奶奶与王爷爷的碎片会被服务端拒绝。 |
| 事实审计 | 写作后及故事确认前 | 模型逐句检查新增人物、时间、地点、动作、结果、感受和关系变化；再叠加本地确定性证据规则。发现无依据内容时禁止发布，写作结果会尝试自动修订一次。 |

工作流由两部分组成：[`backend/agents/graph.py`](backend/agents/graph.py) 编排可中断、可恢复的采访与人工审核；ASR 校对、已确认碎片的持久化证据抽取以及勾选碎片生成故事由 FastAPI 业务服务接入同一个 Agent Provider。业务库和 Checkpoint 均为 SQLite，模型调用失败时允许**有记录的规则降级**，但不能悄悄把前端虚构数据混进真实家庭数据。前端 `fallbackToMock=false`。

可信边界：录音创建时确定 `narratorUserId`。小刘校对林奶奶的录音后，仍是“林奶奶的记忆”，可以另显示“小刘已确认”。只有 `confirmedText` 能成为事实与写作证据；AI 建议不等于用户原话，草稿不等于已发布故事。

## 技术结构

```text
微信原生小程序（pages / components / utils）
    ↓ 同机或局域网 HTTP；仅本地调试时关闭合法域名校验
FastAPI（backend/api.py、backend/routes/agent.py）
    ├── SQLite 业务库：账号、录音、三份转写、碎片事实、故事、审计日志
    ├── LangGraph + SQLite Checkpoint：采访问题、回答、暂停与恢复
    ├── Agent Provider：真实 LLM / 明示的规则降级
    └── 腾讯云 ASR：录音转写
```

录音、业务数据库、Checkpoint 和已生成内容存放在**运行后端的电脑**。启用腾讯云 ASR 与真实大模型时，必要的录音或文字会发送到相应厂商；不要对用户宣称“所有内容完全不上传公网”。Git 不同步 `.env`、数据库、录音或历史故事。

## 在队友电脑上运行（Windows / PowerShell）

1. 克隆仓库，在仓库根目录创建环境文件和 Python 虚拟环境：

   ```powershell
   git clone https://github.com/tzr1285195647-max/memory-bank-work.git
   cd memory-bank-work
   Copy-Item .env.example .env
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
   npm ci
   ```

2. 在**自己的** `.env` 中配置模型和 ASR；不要把密钥发到群里、写入源码或提交到 Git。首次只验证页面时可保留 `AGENT_MODE=mock`，但这不代表真实模型已启用。真实五 Agent 演示至少需要：

   ```ini
   AGENT_MODE=llm
   LLM_API_KEY=填写你自己的密钥
   LLM_BASE_URL=https://api.deepseek.com
   LLM_MODEL=deepseek-chat
   TENCENTCLOUD_SECRET_ID=填写你自己的腾讯云 ID
   TENCENTCLOUD_SECRET_KEY=填写你自己的腾讯云 Key
   ```

   完整选项见 [`.env.example`](.env.example) 和 [模型配置说明](docs/LLM_SETUP.md)。单独填写 Key **不会**切换到真实模型；必须设置 `AGENT_MODE=llm` 并重启后端。模型网络、额度或输出异常会增加 `fallbackCount`，降级结果不能当成真实模型成功结果。

3. 启动后端：

   ```powershell
   .\.venv\Scripts\python.exe backend\run.py
   ```

   默认监听 `127.0.0.1:8787`，适合微信开发者工具。同局域网手机真机调试时，把 `.env` 改为 `HOST=0.0.0.0`，并把 [`config.js`](config.js) 的 `deviceBaseUrl` 改为**自己电脑的 IPv4**（现有地址仅适用于原开发电脑）。手机与电脑需在同一网络，并确认防火墙允许本机后端端口。

4. 用微信开发者工具导入仓库根目录。本机调试阶段在“详情 → 本地设置”关闭合法域名校验；这不等于已具备公网发布条件。首次启动会初始化本机数据库和四个演示账号，**不会**带来原开发电脑上的录音或故事。

5. 登录后查询 `/api/agent/status`（接口需要登录令牌），或使用“我的账户 → 一键演示自检”。真实模式应看到 `provider=llm-with-fallback`、`configuredMode=llm`、`llmEnabled=true`；还要观察正常操作时 `fallbackCount` 不持续增加。`llmEnabled=true` 只证明配置已启用，**实际调用成功仍需结合降级计数和业务结果判断**。

### 四个本机演示账号

密码统一为 `123456`；固定验证码演示登录使用 `246810`，**没有接入真实短信**。

| 账号 | 手机号 | 身份 |
| --- | --- | --- |
| 林奶奶 | `13800008899` | 长辈、主要讲述者 |
| 王爷爷 | `13900007788` | 长辈、另一位讲述者 |
| 小刘 | `13700006677` | 晚辈家属、家庭管理员 |
| 小李 | `13600005566` | 晚辈家属 |

四账号共享同一个本机家庭。账号是演示数据，不应在真实环境沿用统一密码。当前家庭邀请接受/拒绝流程尚未完成；请勿将现有邀请入口描述为已具备被邀请人确认机制。

## 建议演示路径

1. 林奶奶登录，选择“上学的日子”，分段录音。
2. 每段录音经腾讯云转写后，对照只读原始转写和 Agent 建议，由林奶奶人工修改、确认并保存碎片。
3. 观察采访提示随内容变化；小刘登录后可以校对碎片，但不改变讲述者身份。
4. 勾选同一讲述者的已确认碎片，分别试用三种写作风格；查看故事的逐句证据。
5. 故意加入一条原声未提到的事实，观察审计阻止确认；删去无依据句或继续补充讲述。
6. 由长辈处理家庭建议并确认故事。**当前由勾选碎片生成的故事在预览页确认时有已知接线问题，正式展示发布成功前须先修复并真机复测。**

## 验证

```powershell
npm run test:miniprogram
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe backend\demo_check.py --readonly
```

最近一次代码回归：小程序测试全部通过，后端 **71 项通过**。原开发电脑的只读演示检查为 **11/12**：当前本机业务库没有“至少两篇已确认故事”的演示数据。队友新克隆后数据库同样为空；不要为凑计数偷偷写入虚构已发布故事。完整规则和验收标准见 [agent.md](agent.md)，架构细节见 [docs/AGENTS_ARCHITECTURE.md](docs/AGENTS_ARCHITECTURE.md)。

## 已知限制与阶段边界

- 勾选碎片生成故事后，预览页“确认故事”可能错误请求不存在的采访会话；草稿保留，但这一发布入口仍需修复。
- 家庭邀请接受/拒绝、真实短信、家庭回忆录 Agent、美术编排 Agent、插画、PDF 导出、RAG、账号找回、头像上传、可见范围和公网部署不属于当前已完成功能。
- 模型输出经过 Pydantic / JSON Schema 与本地证据规则校验，但自动审计不能替代长辈核对原声和事实。
