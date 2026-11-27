# chatgpt-web-bridge

把**已登录的 ChatGPT 网页**接成本机可用的东西，两条线：

1. **桥**（`bridge/`）：网页 ↔ OpenAI 兼容接口。任何 OpenAI 客户端（含 DeepSeek Harness）
   都能把网页版 ChatGPT 当模型用，支持流式、工具调用仿真、多轮续聊。
2. **本地 agent**（`agent/`）：网页 ChatGPT 当大脑，PowerShell 当手脚。模型输出一条 JSON
   指令 → 本地执行 → 结果贴回去 → 直到它给出结论。每轮结束自动 `git commit` + `git push`，
   让网页那头"靠 GitHub 看代码"始终看到最新状态。

底座是 [laoniubia/gpt-web-bridge](https://github.com/laoniubia/gpt-web-bridge)（MIT，见
`bridge/LICENSE.gpt-web-bridge`），这里做了本机化（Windows/CDP 9225）、新版 DOM 适配、
纯 JSON 协议、以及工具库与中间件。

## 目录

```
bridge/          OpenAI 兼容桥（aiohttp + CDP）
  server.py        主体：/v1/chat/completions、/v1/models、提示词拼接、JSON 校验与重发
  probe*.py        CDP 探针：页面状态 / 输入框 / 发送键 / 助手消息定位
  test_client.py   打本地接口的测试客户端
agent/
  tools.py         工具库：PowerShell / 读写文件 / 列目录 / 正则搜索
  middleware.py    中间件：agent loop（OpenAI messages 结构）+ git 同步
demo/             agent 跑出来的示例产物
```

## 快速开始

```powershell
# 1) 带调试端口的 Chrome（用已登录 chatgpt.com 的 profile）
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --user-data-dir="D:\Path\others\test\chrome_profile" `
  --remote-debugging-port=9225 https://chatgpt.com/

# 2) 桥
pwsh -File bridge\run.ps1        # 或手动 python bridge\server.py

# 3) 让它干活
python agent\middleware.py "在 demo 里写个脚本，打印 1..10 的平方，跑一遍验证"
```

桥的 API key 在 `bridge/data/key.txt`（不入库）；每个请求要带
`Authorization: Bearer <key>`。

## 工具库（`agent/tools.py`）

全部落在 **Windows PowerShell 5.1** 上，schema 是 OpenAI function 格式：

| 工具 | 说明 |
|---|---|
| `run_powershell(command, timeout_s)` | 跑一条命令，返回 `exit=<code>` + 输出（UTF-8，超时强杀，输出截断到 20K） |
| `read_file(path, offset, limit)` | 读文本，返回带行号内容（utf-8/gbk 自动回退） |
| `write_file(path, content)` | 新建或整体覆盖，自动建目录 |
| `list_dir(path)` | 列目录（跳过 `.git` / `node_modules` / `chrome_profile`） |
| `search_text(pattern, path, include)` | 正则搜内容，返回 `文件:行号: 内容` |

所有路径都被限制在工作区内（`AGENT_WORKSPACE`，默认仓库根），越界直接报错。

## 中间件（`agent/middleware.py`）

消息结构与 DeepSeek Harness 的 agent loop 一致，就是 OpenAI 那套：

```
system → user → assistant(tool_calls) → tool(tool_call_id=...) → assistant → ...
```

- 每轮把 `messages` 全量发给桥；桥用指纹链判断是"同一会话的续聊"还是"新任务"，
  续聊只把增量消息追加进同一个网页对话，所以多轮不失忆。
- 收到 `tool_calls` 就本地执行，结果作为 `role="tool"` 塞回去，直到某轮不带 tool_calls。
- **每轮结束自动同步 git**：`git add -A` → 有改动就 commit → `git push --force origin HEAD:main`。
  本地永远是权威，远端只负责让网页版 ChatGPT 读到代码；`--no-push` 可关，`--push-end-only`
  改成整轮结束才推。

## 协议：为什么模型会给 JSON

桥在每轮提示词前面加一段"我这边只有命令行通道和一个 JSON 解析器"的说明，后面接正常对话，
要求整条回复是一个 JSON 对象：

```json
{"content": null, "tool_calls": [{"name": "run_powershell", "arguments": {"command": "Get-Location"}}]}
{"content": "说给人听的话", "tool_calls": []}
```

拿到回复后本地校验：

- **不是合法 JSON** → 三套说辞轮换着让它重发（只强调"只输出 JSON"，不解释原因）；
- **变成"我没有执行器/无法访问"式拒答** → 三套说辞轮换着把它推回格式（"别管接口，给我命令"）；
- 最多重发 3 次，仍不合格就把原文当 `content` 返回。

## 已知限制

- **单并发**：一个网页账号同时只能聊一个窗口，请求排队。
- **拒答是概率性的**：同一个提示词有时直接给命令，有时先回一句"我没有接口"，靠重发兜；
  这不是 bug，是模型侧的抖动。
- **速度**：一轮 20s～3min 不等（免费档 + 网页渲染），别拿它当实时接口。
- **DOM 依赖**：ChatGPT 改版会打破选择器，`bridge/probe.py` 是第一排查工具。
- 单条消息约 24K 字符上限，超了会裁剪。
- 工具调用是提示词仿真：一次一个调用，无并行，`tool_choice` 强制不支持。
