"""纯函数层：文本 ↔ 结构。

这里只做编解码和校验，不碰网络、不碰时间、不碰全局状态——所以单测直接调，不用起浏览器。
从 server.py 抽出来的，行为与原来一模一样（server.py 里仍以旧名字 re-export）。
"""
import json
import re
import uuid

# ---------------- JSON 抽取 ----------------

def balanced_json(text: str, start: int):
    """取 text[start]='{' 起的平衡大括号 JSON 片段(容忍内部引号错误)"""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def extract_json_object(text: str):
    """从模型回复中取出第一个平衡的 JSON 对象; 容忍 ``` 围栏与前后杂字。"""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[A-Za-z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s).strip()
    i = s.find("{")
    if i < 0:
        return None
    frag = balanced_json(s, i)
    if not frag:
        return None
    for candidate in (frag, frag.replace("\r\n", "\\n").replace("\n", "\\n")):
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


# ---------------- 回复归一 ----------------

def norm_call(c: dict):
    """把一条工具调用规整成 OpenAI tool_call; 兼容 {name,arguments} 与 {function:{...}}。"""
    fn = c.get("function") if isinstance(c.get("function"), dict) else c
    name = fn.get("name") or fn.get("tool") or fn.get("command")
    args = fn.get("arguments", fn.get("args", fn.get("parameters", {})))
    if not name:
        return None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            pass
    return {"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
            "function": {"name": str(name),
                         "arguments": args if isinstance(args, str)
                         else json.dumps(args, ensure_ascii=False)}}


def parse_reply(text: str):
    """把模型的一条回复解析成 (content, tool_calls)。

    约定: 整条回复是一个 JSON 对象
        {"content": "..." | null, "tool_calls": [{"name": "...", "arguments": {...}}]}
    另兼容: OpenAI 原形(带 function.arguments 字符串)、{"message": ...}、
    {"commentary": [...]}(无 content 时当正文)。完全不是 JSON 就原样当 content。
    """
    obj = extract_json_object(text)
    if obj is None:
        return text, None
    content = obj.get("content")
    if content is None:
        content = obj.get("message") or obj.get("reply") or obj.get("text")
    if content is None and obj.get("commentary"):
        cm = obj["commentary"]
        content = "\n".join(str(x) for x in cm) if isinstance(cm, list) else str(cm)
    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False)
    calls = obj.get("tool_calls")
    if calls is None:
        calls = obj.get("tools") or []
    if isinstance(calls, dict):
        calls = [calls]
    # 兼容另一种常见形态: {"instructions": [{"action": "write_file", "path": ..., "content": ...}]}
    if not calls and isinstance(obj.get("instructions"), list):
        calls = [
            {"name": it.get("action") or it.get("name") or it.get("command"),
             "arguments": {k: v for k, v in it.items() if k not in ("action", "id", "name")}}
            for it in obj["instructions"] if isinstance(it, dict)
        ]
    tcs = [tc for tc in (norm_call(c) for c in calls if isinstance(c, dict)) if tc] \
        if isinstance(calls, list) else []
    return content, (tcs or None)


# ---------------- 参数校验 ----------------

_TYPE_MAP = {"string": str, "number": (int, float), "integer": int,
             "boolean": bool, "array": list, "object": dict}


def validate_args(schema: dict | None, args) -> str:
    """按请求里带的 JSON Schema 校一遍参数, 把能本地看出来的错就地挡住。

    只管两类硬错: 缺必填、类型不符。多说出来的参数只记录不拦截——有些工具容忍额外字段,
    误拦反而会逼模型重来。
    """
    if isinstance(args, str):  # parse_reply 规整后 arguments 是 JSON 字符串
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return "arguments 不是合法 JSON"
    if not isinstance(args, dict):
        return "arguments 必须是 JSON 对象"
    if not isinstance(schema, dict):
        return ""
    props = schema.get("properties") or {}
    missing = [k for k in (schema.get("required") or []) if k not in args]
    if missing:
        return "缺少必填参数 " + ", ".join(missing)
    for k, v in args.items():
        want = (props.get(k) or {}).get("type") if isinstance(props.get(k), dict) else None
        if not isinstance(want, str) or want not in _TYPE_MAP:
            continue
        if want in ("integer", "number") and isinstance(v, bool):
            return f"参数 {k} 需要 {want}，不能是布尔值"
        if not isinstance(v, _TYPE_MAP[want]):
            return f"参数 {k} 需要 {want}，收到的是 {type(v).__name__}"
    return ""


# ---------------- 回复质量检测 ----------------

# "只说话不动手"的痕迹: 贴了代码块、给了运行结果、或者只是确认收到
PASTE_RE = re.compile(
    r"```|^\s*def\s+\w+\s*\(|^\s*(import|from)\s+\w+|运行结果|输出结果|已了解|好的[，,。]|收到[，,。]|明白[，,。]",
    re.M)

REFUSAL_RE = re.compile(
    r"(?:没有|未)(?:连接|接入|提供|可用)[^。\n]{0,30}(?:执行器|接口|工具|环境)"
    r"|(?:执行器|接口|工具)[^。\n]{0,12}(?:没有|未)(?:连接|接入|提供)"
    r"|无法[^。\n]{0,10}(?:访问|执行|运行|读取|连接)"
    r"|不能[^。\n]{0,10}(?:访问|执行|运行)"
    r"|不会(?:伪造|编造|谎称|声称|假装)"
    r"|请提供[^。\n]{0,12}(?:执行器|工具|连接|接口)"
    r"|I (?:can'?t|cannot|do not have)[^.\n]{0,24}(?:access|execute|run|connect)",
    re.I)


def looks_like_paste(text: str) -> bool:
    """有内容但没有 tool_calls, 且内容像是"把活写在了回答里"。"""
    return bool(text) and bool(PASTE_RE.search(text[:3000]))


def looks_like_refusal(text: str) -> bool:
    return bool(text) and bool(REFUSAL_RE.search(text[:800]))
