# 接入真实大模型

智能体层支持真实模型和明确标注的规则降级。仅填写 API Key 不会自动启用；必须同时设置 `AGENT_MODE=llm`。

## 一、怎么把 Key 给我（推荐方式）

**不要把 Key 贴进对话**。钥匙放在仓库根目录的 `.env` 文件里，后端启动时读取：

```powershell
cd D:\记忆银行
Copy-Item .env.example .env
notepad .env          # 填 LLM_API_KEY 一行即可
```

`.env` 至少配置以下四项（不要把真实值提交到 Git）：

```ini
LLM_API_KEY=sk-你的密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
AGENT_MODE=llm
```

改完**重启后端**即可：

```powershell
python backend/run.py
```

### 为什么用 .env

| 方式 | 风险 |
|---|---|
| 贴进对话 | 密钥进入对话记录，可能被转发、留存 |
| 写进代码/配置文件 | 容易误提交到 Git |
| `$env:LLM_API_KEY=...` | 只对当前 shell 有效，换窗口就没了 |
| **`.env` 文件** | **已在 `.gitignore` 中，不进版本库；后端启动自动读取** |

`.env` 已被 `.gitignore` 忽略（第 4 行 `.env`），可以用 `git check-ignore -v .env` 复核。

## 二、确认当前用的是模型还是 Mock

```powershell
curl http://127.0.0.1:8787/api/agent/status -H "Authorization: Bearer <token>"
```

返回示例：

```json
{
  "provider": "llm-with-fallback",
  "llmEnabled": true,
  "model": "deepseek-chat",
  "baseUrl": "https://api.deepseek.com",
  "fallbackCount": 0,
  "lastError": null
}
```

- `provider` 为 `mock-agents` → 没读到 Key（检查 `.env` 位置与重启）
- `provider` 为 `llm-with-fallback` → 已接入模型
- `fallbackCount > 0` → 模型调用失败过，已自动回落到确定性实现（`lastError` 有原因）

## 三、换其他厂商

接口走 **OpenAI 兼容协议**，改三行即可：

| 厂商 | LLM_BASE_URL | LLM_MODEL 示例 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 阿里通义 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-plus` |
| 月之暗面 | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |

## 四、模型输出不可信：四道本地校验

这是本项目对"AI 会不会编"的技术回答——**不靠提示词，靠校验**：

| 环节 | 校验 | 不合规时 |
|---|---|---|
| 停止意愿 | **本地关键词规则**先判定，不让模型决定是否继续追问 | 直接停止，不再问 |
| 证据抽取 | 每条事实的 `quote` 必须**真的出现在讲述原文里**（子串匹配），要素必须在七要素内 | 丢弃该条，并记录丢弃数 |
| 写作 | 引用 id 必须真实存在；标题/来源说明句才允许无引用 | 无引用的事实句由审计报出 |
| 审计 | 事实审计 Agent 逐句检查，同时叠加确定性硬规则 | 任一层发现无依据内容即禁止发布 |

审计同时调用模型和确定性本地规则：模型负责识别语义层面的新增人物、动作、关系等；本地规则按分句校验引用和原文重合，并对明确新增的年份、未澄清冲突设置发布硬闸门。模型失效时会记录规则降级，不能把“有引用”误称为“全部事实已证明”。

## 五、失败与降级策略

```text
选择实现：
  AGENT_MODE!=llm 或未配置 Key → MockAgentProvider（纯确定性）
  AGENT_MODE=llm 且配置 Key    → FallbackAgentProvider(LLMAgentProvider)
                           ├── 调用成功 → 用模型结果
                           └── 失败/输出不合规 → 回落 Mock，并累计 fallbackCount
```

调用失败会额外重试 `LLM_MAX_RETRIES` 次（默认 1 次，无等待间隔），仍失败则回落。不支持严格 JSON Schema 时的格式能力探测最多切换一次，不消耗失败重试次数；`LLM_MAX_RETRIES=0` 仍允许这次格式切换。

`LLM_TIMEOUT_SECONDS` 是底层 HTTPX 网络超时配置，不是整条 Agent 流程的总时长上限。客户端耗时操作现在通过 `/api/agent/tasks` 提交并查询结果，每次 HTTP 请求最多等待 15 秒；任务处理中会继续轮询，不用固定 120/300 秒截断整条流程。增加模型重试次数无需同步修改客户端等待常量。旧同步接口保留兼容，但新客户端必须连接更新并重启后的后端。

**日志安全**：只记录长度、条数、错误类别；**不打印讲述正文、不打印密钥**。
模型返回的原始响应体也不写日志（可能含敏感内容）。

## 六、自测

```powershell
# 单元测试（不需要 Key，走 Mock）
python -m pytest backend/tests -q

# 端到端（需要后端在跑）
python backend/agent_http_check.py

# 新后台任务协议：临时数据库，不写入自己的故事/录音库
python backend/task_http_check.py
python backend/task_http_check.py --llm  # 使用自己的配置调用一次真实采访提示

# 显式验证"模型输出不合规会被丢弃"：见 backend/tests/test_llm_provider.py
python -m pytest backend/tests/test_llm_provider.py -q
```
