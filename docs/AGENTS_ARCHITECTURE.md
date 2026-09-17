# 多智能体协同架构

记忆银行的后端核心不是 CRUD，而是**五类智能体围绕证据链协同**。本文说明它们各自做什么、怎么协作、以及为什么这样设计。

## 1. 五类智能体

| 智能体 | 职责 | 硬约束 |
|---|---|---|
| **口述校对** `TranscriptCleaner` | ASR 后生成可编辑校对建议 | 原始、建议、确认三份文字分开保存；不得新增事实 |
| **采访导演** `InterviewDirector` | 依据已讲内容与缺失要素，决定下一个问题 | 老人明确表示停止 → 立即停止追问；同一缺失项不重复追问 |
| **证据抽取** `EvidenceExtractor` | 从讲述中抽取七要素 | **每条事实必须带原文片段（quote）与来源轮次（turn_id）**；找不到证据就留空 |
| **写作** `WritingAgent` | 把已确认的事实组织成故事 | 只能用传入的证据，**不得新增事实**；每条事实句挂 claim id |
| **写作审计** `WritingAuditor` | 逐句校验草稿 | 无引用的事实句、引用不存在的证据、证据不可追溯 → 一律报出 |
冲突识别作为证据 Agent 的子能力执行：同一要素出现矛盾讲述时并列保留、不裁定，交给家人确认。

七要素：`time / place / people / event / result / impact / feeling`。缺失即留在 `missing_fields`，**禁止推测**。

## 2. 协同图（扁平父图）

```text
load_context → consent_gate ─(撤回)──────────────────→ revoke_and_delete
                    │(允许)
                    ↓
      interview_select_question ──(有下一个问题)──┐
             ↑                                   │
             │                          interview_commit_turn（interrupt 等回答）
             │                                   ↓
             │                          evidence_prepare
             │                                   ↓
             │                    ┌── Send 并行 fan-out（每轮一个分支）──┐
             │                    ↓                                    ↓
             │            evidence_extract_claims × N  ────────→ evidence_merge
             │                                                        │
             │                        ┌──(要素仍缺失且未讲完)──────────┘
             │                        ↓
             │                   writing_draft → writing_audit
             │                        │              │
             │        (审计不通过)─────┘              │(通过)
             └───────────────────────────────────────↓
                                            review_decide（interrupt 等人工确认）
                                    ┌────────┬──────────┬─────────┐
                                 approve    edit    request_more  reject
                                    ↓        ↓          ↓          ↓
                             creative_delivery  writing_audit  采访    END
```

关键机制：

- **双入口、同一协作图**：继续采访由 `interview_select_question` 进入；勾选已确认碎片从持久化事实进入 `selection_prepare`，两路汇入相同的 `writing_draft → writing_audit → review_decide` 节点。勾选碎片也有 SQLite checkpoint；旧版无 checkpoint 草稿保留兼容的证据复审入口。
- **发布硬闸门**：审计不仅验证 claim id，还按分句核对其内容与已确认原句；明确新增的年份会被拦下。发现未澄清的冲突时，原话并列显示，`auditPassed=false`，须修正碎片并重新生成，不能靠改故事正文绕过。

- **Send 并行**：每一轮讲述各自抽取，互不阻塞；结果用**幂等 reducer** 按 id 合并。
- **顶层 interrupt**：采访与人工确认两处暂停。中断挂在顶层图上，因此可以被外部 `Command(resume=...)` 恢复，并靠 SQLite checkpoint **跨进程存活**（服务重启后可继续）。
- **条件路由**：审计不通过 → 退回采访补录，而不是硬发；要素未齐或故事链仍单薄 → 继续追问。七要素齐全本身不触发采访完成。
- **历史证据上下文**：新采访先从业务库装入同一讲述者、同一主题下已确认的碎片与可验证事实。事实原文进入采访 Prompt 和图状态，跨碎片冲突可与本轮事实一起检查；`missing_fields` 从这些事实计算。模型问题即使把目标字段标错，也会由问题语义校验拦下重复提问。
- **采访充分性闸门**：模型可以建议结束，但少于 7 段确认碎片时不能主动宣称“故事足够”；7–9 段还需核心时间、地点、人物、事件、结果、感受有证据且确认原文达到基本细节长度，最多引导至 10 段。明确停止意愿、连续两轮无新事实和用户主动结束仍优先尊重。模型过早结束时改问安全的新细节，不重问已知年份。
- **自定义主题**：四个内置主题保持可用，用户新增主题按家庭隔离；主题 ID 贯穿录音、碎片、采访和故事，历史数据库通过新增可空 `topics.family_id` 字段兼容。
- **授权闸门**：`consent_version` 不符或已撤回 → 改道删除，不进入任何写路径。

## 3. 证据链是怎么成立的

1. 抽取时，每条 claim 记录 `quote`（**原文子串**）与 `turn_id`；
2. 写作时，每条事实句挂上 `claim_ids`；
3. 审计时**真的去核对**：引用是否存在、证据是否有原文与轮次；
4. 测试会验证 `quote` 确实是对应轮次回答的子串（见 `test_claims_carry_original_quote_and_turn_id`）。

因此系统能回答"这句话凭什么这么说"，并且在证据不足时**主动打回**，而不是让模型自由发挥。

## 4. 端口与实现分离

```text
provider.py   AgentProvider 协议（五类核心能力 + 证据冲突子能力）
mock.py       MockAgentProvider —— 明示的规则降级，确定性、离线可跑
llm.py        OpenAI 兼容真实模型适配器；Pydantic/JSON Schema 校验后才进入业务流程
```

架构约束：**图与业务层只依赖 `AgentProvider`，不依赖任何模型 SDK**。换真模型时图、状态、审计、测试全部不动。

真实模式使用 `FallbackAgentProvider`。模型失败会明示记录降级次数和原因；本地规则仍作为证据与发布的硬闸门，不把降级结果伪装为真实模型输出。

故事详情的 `workflow` 保存脱敏的节点轨迹、模型名称、Prompt 版本、耗时和降级标记。页面同时呈现逐句依据与审计发现；不会在协作记录里保存密钥或 Prompt 正文。旧库通过原地新增 `stories.workflow_json` 字段迁移，不清空历史故事。

## 5. 已踩过的坑（别再踩）

| 坑 | 现象 | 结论 |
|---|---|---|
| 子图里的 `interrupt` | `Command(resume=...)` 后状态不变、图仍停在原中断 | **中断必须挂在顶层图**；嵌套子图无法被外部恢复 |
| `Send` 传完整状态 | 同一批 turns/claims 成倍增长（4 倍） | Send 的输入必须是**增量** |
| 非幂等 reducer | 重复 resume 后数据翻倍（[langgraph#4007](https://github.com/langchain-ai/langgraph/issues/4007)） | 列表类 reducer 一律按 id 去重 |
| 返回 `Send` 的普通节点 | `InvalidUpdateError: Expected dict, got [Send(...)]` | `Send` 只能由**路由函数**返回 |
| 子图写回 `stage` | 父图阶段被子图覆盖（`revoked` 变成 `interview`） | 阶段由**父图独占**；对外状态从信号推导 |
| 状态键不在 schema | 写入被静默丢弃（`consent_ok` 失效） | 用到的键必须出现在 `MemoryBankState` |

## 6. 运行与验证

```powershell
python -m pytest backend/tests -q      # 当前后端回归测试
python backend/agents_smoke.py         # 端到端跑一遍：采访 → 证据 → 写作 → 审计 → 确认 → 交付
```

测试锁住的不变量（节选）：

- 每条 claim 的 `quote` 必须是该轮回答的原样子串
- 未讲到的要素必须留在 `missing_fields`，不得被补造
- 无引用的事实句必须被审计报出，过渡句不误报
- 停止意愿出现后不得再提问
- 多次 resume 后轮次与证据不得重复、答案不得丢失
- 撤回授权后不得产生草稿

## 7. 下一步

| 项 | 说明 |
|---|---|
| Agent 评测扩充 | 持续补充方言、人名地名、年代模糊与跨碎片冲突样本 |
| 时间码回听 | 转写分段与 claim 建立字符偏移→时间码映射，点句跳回原声 |
| 七要素完整度视图 | 把 `missing_fields` 呈现到 07 屏，直观展示"查漏补缺" |
| 冲突并列展示 | `conflicts` 已在状态里产出，界面按并列呈现，不做裁定 |
| 审计事件 | 授权、写入、审批、撤回落成不可静默覆盖的 `AuditEvent` |
