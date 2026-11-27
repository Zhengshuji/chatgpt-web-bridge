"""用假页面验证 collect_json 的重发逻辑(不需要浏览器)。

模拟真实页面的一点: 发出催prompt后, 页面上"最后一条回复"会先停在旧文本,
过两拍才换成新回复——base_text 就是在那个时刻取的。
"""
import asyncio
import time

import server as s

REPLIES = [
    "好的，我来看看项目结构。",               # 非 JSON -> 重发 1
    "这不是 JSON，只是解释。",                # 非 JSON -> 重发 2
    '{"content":"读完了","tool_calls":[]}',    # 合法 -> 停止
]


async def main() -> None:
    state = {"idx": 0, "visible": REPLIES[0], "pending": None, "lag": 0}
    sent: list[str] = []

    async def fake_snapshot(base_count: int = -1):
        if state["pending"] is not None:
            if state["lag"] <= 0:
                state["visible"] = state["pending"]
                state["pending"] = None
            else:
                state["lag"] -= 1
        return {"text": state["visible"], "count": base_count + 1, "gen": False, "full": False}

    async def fake_send(prompt: str):
        sent.append(prompt)
        state["idx"] += 1
        state["pending"] = REPLIES[state["idx"]]
        state["lag"] = 2

    s.snapshot = fake_snapshot
    s.type_and_send = fake_send
    s.log = lambda *a, **k: None

    text, _, _ = await s.collect_json(0, "", time.monotonic() + 30)
    print("重发次数:", len(sent))
    for i, p in enumerate(sent, 1):
        print(f"  说辞{i}: {p[:40]}…")
    print("最终回复:", text)
    assert len(sent) == 2, f"应重发 2 次，实际 {len(sent)}"
    assert s._extract_json_object(text) is not None, "最终应是合法 JSON"
    print("OK: 非 JSON 会自动重发, 直到拿到 JSON")


asyncio.run(main())
