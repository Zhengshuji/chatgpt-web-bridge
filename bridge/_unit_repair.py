"""用假页面验证 collect_json 的重发逻辑(不需要浏览器)。"""
import asyncio

import server as s

REPLIES = [
    "好的，我来看看项目结构。",              # 非 JSON -> 应触发重发 1
    "这不是 JSON，只是解释。",               # 非 JSON -> 应触发重发 2
    '{"content":"读完了","tool_calls":[]}',   # 合法 -> 停止
]


async def main() -> None:
    state = {"i": 0}
    sent: list[str] = []

    async def fake_snapshot(base_count: int = -1):
        return {"text": REPLIES[state["i"]], "count": base_count + 1, "gen": False, "full": False}

    async def fake_send(prompt: str):
        sent.append(prompt)
        state["i"] += 1

    s.snapshot = fake_snapshot
    s.type_and_send = fake_send
    s.log = lambda *a, **k: None

    text, base = await s.collect_json(0, __import__("time").monotonic() + 60)
    print("重发次数:", len(sent))
    for i, p in enumerate(sent, 1):
        print(f"  说辞{i}: {p[:40]}…")
    print("最终回复:", text)
    print("解析结果:", s.parse_reply(text))
    assert len(sent) == 2, "应重发 2 次"
    assert s._extract_json_object(text) is not None, "最终应是合法 JSON"
    print("OK: 非 JSON 会自动重发, 直到拿到 JSON")


asyncio.run(main())
