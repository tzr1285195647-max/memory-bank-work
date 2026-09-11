# 记忆银行 · 比赛版 MVP

一个证据驱动、可恢复、有人类确认的家庭记忆 Agent。当前版本默认运行在 **Mock 模式**，无需任何模型密钥即可完整演示。

## 已实现

- LangGraph 父图编排五个子图：采访、证据、写作审计、人工确认、创意交付。
- `interrupt / Command(resume)` 驱动的采访与审批暂停。
- SQLite checkpoint，服务重启后可按 `thread_id` 恢复。
- `Send` 并行提取每轮采访的 Claim，并用稳定去重 reducer 合并。
- Claim → 采访轮次 → 原回答的证据链。
- 草稿证据审计：新增无证据段落会自动退回采访。
- `consent_version` 持续鉴权；撤回后删除采访、Claim、草稿和交付内容。
- 幂等的采访提交、草稿生成与最终交付。
- 自适应中文单页界面和可见审计轨迹。

## 启动

最简单的方式：双击 `启动记忆银行.bat`。

也可以在 PowerShell 中运行：

```powershell
.\.venv\Scripts\python.exe run.py
```

然后打开：<http://127.0.0.1:8765>

接口文档：<http://127.0.0.1:8765/docs>

## 比赛演示建议

1. 创建主题为“童年的夏天”的记忆项目。
2. 回答第一问，展示会话事件和审计轨迹。
3. 第二问选择“提交并生成章节”。
4. 展示 Claim 与采访轮次的绑定关系。
5. 在草稿中增加一段没有 `〔证据:...〕` 的新事实，点击“保存修改并重审”，展示自动退回采访。
6. 补充回答后再次生成，批准并输出记忆卡。
7. 重新启动服务，刷新页面，展示 checkpoint 恢复。
8. 新建另一个项目并演示撤回授权，确认业务内容被删除、审计记录仍保留。

## 目录

```text
memory_bank/
  agents.py       Mock 智能节点；后续可替换为真实模型
  api.py          FastAPI 接口与静态页面
  config.py       数据目录与运行模式
  schemas.py      State、请求模型与并行 reducer
  storage.py      业务数据、权限、幂等与审计
  workflow.py     父 StateGraph 与五个子图
  static/         比赛演示界面
tests/
  test_workflow.py
app.py            ASGI 入口
run.py            本地启动入口
```

## 自动测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

覆盖完整交付与重启恢复、授权撤回删除、无证据编辑退回采访三条核心路径。

## 当前边界

- 输入暂为文字；音频录制、ASR 和按时间码回听是下一阶段。
- 智能节点目前为确定性 Mock；接口已与图和存储隔离，可以替换为真实模型实现。
- 使用本地 SQLite 和文件系统，适合比赛单机演示，不代表生产部署方案。
- 图片、TTS、视频和声音克隆暂未接入；主流程不会依赖这些外部供应商。

