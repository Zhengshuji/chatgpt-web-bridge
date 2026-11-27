# chatgpt-web-bridge（本地版）

把**已登录的 chatgpt.com 网页**包装成本机的 **OpenAI 兼容接口**，供 DeepSeek Harness 当模型用。
底座是 [laoniubia/gpt-web-bridge](https://github.com/laoniubia/gpt-web-bridge)（MIT，见 `LICENSE.gpt-web-bridge`），
这里做了本机化 + 纯 JSON 协议改造。

```
DSH / 任意 OpenAI 客户端
   │  POST http://127.0.0.1:8355/v1/chat/completions
   ▼
server.py (aiohttp)
   │  CDP websocket (127.0.0.1:9225)
   ▼
Chrome（独立 profile，已登录 chatgpt.com）+ 真实页面 DOM
```

## 1. 启动

两步，顺序不能反。

```powershell
# 1) 带调试端口的 Chrome（用工作区里已登录的 profile）
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --user-data-dir="D:\Path\others\test\chrome_profile" `
  --remote-debugging-port=9225 https://chatgpt.com/

# 2) 桥
cd D:\Path\others\test\bridge
$env:GPTWEB_CDP="http://127.0.0.1:9225"
$env:GPTWEB_PORT="8355"
$env:GPTWEB_REPO="https://github.com/Zhengshuji/q1-test-temp"   # 写进提示词的仓库地址，可省
python server.py
```

也可以直接 `pwsh -File bridge\run.ps1`，它会检查 9225 再决定是否拉起 Chrome。

> Chrome 必须在**普通终端**里启动。在受限沙箱里启动时，Chrome 的崩溃上报服务
> 打不开自己的进程句柄（`OpenProcess: 拒绝访问`），进程会立刻自杀。

首次运行会在 `bridge/data/key.txt` 生成 API key，请求必须带 `Authorization: Bearer <key>`。

## 2. 协议：前半场景 + 后半对话

每次请求（带 `tools` 时）的提示词是拼接出来的：

- **前半（head）— 场景设定**：告诉模型"这台机器没有图形界面，我手上只有一条命令行通道
  和一个 JSON 解析器；我读不懂自然语言，项目代码全在 `<GPTWEB_REPO>`，要了解项目就去读仓库"。
  结论是：**每条回复必须是一个 JSON 对象**，从 `{` 到 `}`，前后不许有别的字符。
- **中间**：正常的 system / 历史 / 本轮用户消息（`flatten()` 拍平成 `[role]` 文本块）。
- **后半（tail）— 正常对话**：工具清单（每工具一行，压缩过）+ "结果会作为下一条消息贴回来"
  + 再提醒一次"只认 JSON"。

约定的 JSON 只有两种：

```json
{"content": null, "tool_calls": [{"name": "read_file", "arguments": {"path": "D:\\a.txt"}}]}
{"content": "给用户的回答", "tool_calls": []}
```

`arguments` 必须是对象（上游把它写成字符串，和"必须对象"自相矛盾，模型会照抄，已修）。
解析端还兼容：OpenAI 原形（`function.arguments` 为字符串）、`{"message": ...}`、
`{"commentary": [...], "instructions": [{"action": "write_file", ...}]}`。

### 不是 JSON 怎么办

`collect_json()` 会检查 `json.loads` 能否成功；失败就往同一会话追加一条催prompt，
三套说辞轮换（只强调"请只输出 JSON"，不解释具体哪里错），最多重发 3 次：

1. 你上一条不是合法 JSON，我这边解析不了。请重新回答：只输出一个 JSON 对象，别的什么都不要。
2. 解析失败。整条回复必须是一个 JSON 对象，从 `{` 开始、到 `}` 结束，前后不留任何字符。
3. 上一条格式不对。再重申一次：我这边只认 JSON，请只输出 JSON。

## 3. 相对上游改了什么

| 改动 | 原因 |
|---|---|
| `GPTWEB_CDP` / `GPTWEB_PORT` / `GPTWEB_HOME` / `GPTWEB_REPO` 环境变量 | 上游硬编码 9225 端口与 `~/gpt-bridge` |
| composer 选择器加 `div.ProseMirror[contenteditable]` | 会话页里输入框没有 `#prompt-textarea` |
| 发送键 `button[aria-label="发送"]`、停止键 `button[aria-label="停止"]` | 2026-11 版中文界面已无 `data-testid="send-button"` |
| 助手消息 `[data-dil-response]` | 新版 DOM 不再有 `data-message-author-role` / `article` / `.markdown` |
| 发送前清空输入框 | 上一轮发送失败时旧文本会被追加进去 |
| 发送失败兜底用真实 Enter（`Input.dispatchKeyEvent`） | 发送键未渲染时也能发出去 |
| 提示词改成"场景 + 正常对话"两段 | 之前的"覆盖一切规则/禁止反驳"被模型识别为越狱，直接拒答 |
| 纯 JSON 协议 + 三段催prompt重发 | 模型稳定输出纯 JSON，本端 `json.loads` 后转成 `content`/`tool_calls` |
| SSE 加 `charset=utf-8` | 不加的话客户端按 latin-1 解码，中文变乱码 |

## 4. 接进 DeepSeek Harness

`C:\Users\26074\.dsh\profiles\desktop\cordis.patch.yml` 的 `llm-pi-ai.providers` 已加一条路由：

```yaml
      chatgpt-web:
        displayName: ChatGPT Web (本地 CDP)
        apiKeyEnv: GPTWEB_API_KEY
        api: openai-completions
        baseURL: http://127.0.0.1:8355/v1
        models:
          - { id: gpt-web,           name: gpt-web (auto), contextWindow: 32768, maxTokens: 8192 }
          - { id: gpt-6,             name: gpt-6,          contextWindow: 32768, maxTokens: 8192 }
          - { id: gpt-5-6-t-mini,    name: gpt-5-6-t-mini, contextWindow: 32768, maxTokens: 8192 }
```

`GPTWEB_API_KEY` 写在 `C:\Users\26074\.dsh\.credentials.yaml` 的 `refs:` 里，值与
`bridge/data/key.txt` 一致。之后在界面里把模型切成 `ChatGPT Web (本地 CDP) / gpt-web` 即可。

## 5. 自测

```powershell
cd bridge
python probe.py                 # 页面状态：URL / 是否登录 / 输入框在不在
python test_client.py "读一下xxx" --tools          # 非流式 + 工具调用
python test_client.py "素数是什么"  --stream       # 流式
python test_client.py "读一下xxx" --tools --followup  # 工具结果回填（delta 续聊）
python _last_reply.py           # 直接把页面上最后一条回复打出来，排查解析问题
python _unit.py                 # 解析器单测
python _unit_repair.py          # 重发逻辑单测（不需要浏览器）
```

## 6. 已知限制

- **单并发**：一个网页账号同时只能聊一个窗口，请求会排队（`BUSY` 锁）。
- **DOM 依赖**：ChatGPT 改版会打破选择器；`probe.py` 是第一排查工具。
- **单条消息 ~24K 字符上限**（网页产品限制，与模型上下文无关），超了会裁剪。
- **每次新会话在账号里留一条记录**。
- **免费额度**：限流文案会转成 429，不会当成回答。
- 工具调用是**提示词仿真**，不是原生 function calling：一次一个调用，无并行。

## 7. 下一步（还没做）

1. **工具库**：`read_file` / `list_dir` 只是测试用的两个；真正干活要么接 DSH 的工具集，
   要么在本地实现一套最小工具（读/写/跑命令/git）。
2. **中间件**：把 `{"tool_calls": [...]}` 落到本地执行的调度器 + 结果回填（DSH 的 agent loop
   已经承担这层；如果脱离 DSH 单独用，需要自己写一个）。
3. **GitHub 取文件**：场景里说"了解项目靠 GitHub 仓库"，但本地没有对 GitHub 的工具，
   模型目前只能靠已有上下文；要真正读仓库，得补 `web_fetch` 类工具或用 `git clone` + `read_file`。
