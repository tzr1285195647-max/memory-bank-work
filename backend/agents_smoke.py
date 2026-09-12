"""多智能体流程冒烟：跑完整的采访 → 证据 → 写作 → 审计 → 确认链路。

用法：python backend/agents_smoke.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents import AgentRuntime  # noqa: E402
from backend.config import settings  # noqa: E402

ANSWERS = [
    "那年秋天，院子里的桂花开得很早。我和妹妹每天放学，都要绕路去看一眼。",
    "外婆总是在门口等我们，她会把落下来的桂花扫成一堆。",
    "后来外婆走了，我每次闻到桂花香，心里都特别想念她。",
]


def show(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main() -> int:
    runtime = AgentRuntime(settings.data_dir / "checkpoints.sqlite3")
    # 每次运行用独立 session，避免复用旧 checkpoint 导致轨迹混入历史记录
    session_id = f"smoke-{uuid.uuid4().hex[:8]}"

    show("1. 启动会话（图开始运行，采访导演提第一个问题）")
    view = runtime.start_session(
        session_id=session_id,
        family_id="family-demo",
        subject_name="林阿姨",
        topic="我的家乡",
        max_rounds=4,
    )
    for item in view["interrupts"]:
        print(f"  采访问题：{item['value'].get('question')}")
    print(f"  stage={view['stage']}  round={view['round_index']}")

    for index, answer in enumerate(ANSWERS, start=1):
        show(f"2.{index} 回答第 {index} 轮")
        view = runtime.resume(session_id, {"answer": answer, "finish": index == len(ANSWERS)})
        print(f"  已提交轮次：{len(view['turns'])}  已抽证据：{len(view['claims'])}")
        print(f"  stage={view['stage']}  缺失要素={view['missing_fields']}")
        if view["interrupts"]:
            print(f"  下一个问题：{view['interrupts'][0]['value'].get('question')}")

    show("3. 证据链（每条事实必须能回指原句）")
    for claim in view["claims"][:6]:
        print(f"  [{claim['element']:<7}] {claim['text'][:28]}…")
        print(f"            来源轮次={claim['turn_id']}  原文={claim['quote'][:26]}…")

    show("4. 写作与审计")
    print("  草稿：")
    for sentence in view["draft_sentences"]:
        cited = ",".join(sentence["claim_ids"]) or "（过渡句，无需引用）"
        print(f"    - {sentence['text'][:34]}…   引用={cited}")
    print(f"\n  审计结论：通过={view['audit_passed']}  问题数={len(view['audit_findings'])}")
    for finding in view["audit_findings"]:
        print(f"    ✗ [{finding['kind']}] {finding['message']}")

    show("5. 人工确认点")
    for item in view["interrupts"]:
        payload = item["value"]
        print(f"  类型={payload.get('kind')}  标题={payload.get('title')}")

    show("6. 批准后交付")
    view = runtime.resume(session_id, {"action": "approve"})
    print(f"  stage={view['stage']}")
    print("  交付内容：")
    for line in (view["delivery"] or "").splitlines():
        print(f"    {line}")

    show("7. 智能体调用轨迹（谁做了什么）")
    for step in view["agent_trace"]:
        node = step.pop("node", "?")
        print(f"  {node:<32} {step}")

    runtime.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
