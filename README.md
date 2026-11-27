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
powershell -NoProfile -ExecutionPolicy Bypass -File bridge\run.ps1   # 手动
# 常驻（推荐）：交给计划任务，DSH 崩溃不会带走它
schtasks /run /tn "DSH-GPTWebBridge"

# 3) 让它干活
python agent\middleware.py "在 demo 里写个脚本，打印 1..10 的平方，跑一遍验证"
```

桥的 API key 在 `bridge/data/key.txt`（不入库）；每个请求要带
`Authorization: Bearer <key>`。

## 速度：真正拖慢的是 Chrome 的节流，不是有头/无头

桥靠轮询页面 DOM 收回复，所以**页面被节流 = 变慢**。有头窗口被遮挡/最小化时，Chrome 会
降低定时器和渲染频率，实测一轮从 ~20s 掉到 45~75s。启动时加这三个开关即可（`run.ps1` 已带）：

```
--disable-background-timer-throttling
--disable-backgrounding-occluded-windows
--disable-renderer-backgrounding
```

同一请求的实测（同一账号、同一提示词，各 3 次）：

| 模式 | 单轮耗时 |
|---|---|
| 有头，被遮挡（默认启动） | 45 / 63 / 76 s |
| **有头 + 上面三个开关** | **19 / 24 / 26 s** |
| 无头（`-Headless`，需伪装 UA） | 14 / 19 / 22 s |

结论：有头加开关就够快了，跟无头差 2~4s；无头还多了两个坑——UA 里的 `HeadlessChrome`
会被 Cloudflare 挑战页拦住（必须伪装 UA），且没有界面不好排错。日常用有头，无人值守再上无头。

## 常驻与自愈

DSH 崩溃或重启时，会把它自己拉起的子进程一起带走——桥和 Chrome 如果是在 DSH 的终端里
`python server.py` 起的，就会跟着死。所以这两样要放在 DSH 的进程树外面。

[bridge/service.ps1](bridge/service.ps1) 干三件事：9225 没活着就拉 Chrome（顺手清掉被杀时留下的
单例锁）、8355 没活着就拉桥、都活着就直接退出。它被注册成计划任务：

```powershell
schtasks /query  /tn "DSH-GPTWebBridge"     # 看状态
schtasks /run    /tn "DSH-GPTWebBridge"     # 立刻拉起/自愈
schtasks /end    /tn "DSH-GPTWebBridge"
schtasks /delete /tn "DSH-GPTWebBridge" /f  # 不要了
```

触发器是"登录时 + 每 10 分钟"，`IgnoreNew` 所以不会叠进程，`ExecutionTimeLimit=PT0S` 所以不会
到点被杀。任务里的进程父级是 `taskhostw`，与 DSH 无关，DSH 再崩也不影响。脚本的日志在
`bridge/data/service.out.log` / `service.err.log`。

> 本机只有 Windows PowerShell 5.1（没有 `pwsh` 7），所以脚本一律用
> `powershell -NoProfile -ExecutionPolicy Bypass -File ...` 调用。
> 另外 `.ps1` 必须存成 **UTF-8 带 BOM**，否则 PS 5.1 按 GBK 解析中文字符串会直接语法报错。

## 新会话不再浪费一轮

**症状**：DSH 每开一个新会话，第一轮模型总是回一句“请给出具体任务或需要检查的项目目标”，
于是一个回合被白扔。

**根因**（`bridge/data/last_request.json` 里的真实消息顺序）：

```
[0] system  11924 字
[1] user    写一个程序，实现0到10的阶乘      ← 真正的任务
[2] user    <system-reminder> 工作目录/文件策略
[3] user    <system-reminder> 技能表
[4] user    Time sampled while preparing turn 1 …
[5] user    [MNEMON] 检索说明
[6] user    MNEMON RUNTIME MEMORY SNAPSHOT …
```

任务在第一条，后面又压了 4 陀背景提醒；网页模型最看“最后说了什么”，于是把提醒当成了本轮
内容。**不是模型笨，是任务被埋了。**

**解法**：`flatten()` 现在把这些背景块（`<system-reminder>` / `Time sampled` / `[MNEMON]` /
`MNEMON RUNTIME MEMORY SNAPSHOT` / `[dsh-mnemon]`）挪到前面，标成
`[会话背景 · 不是本轮任务]`，真正的任务放到最后并标 `[以下是本轮要处理的内容]`，紧贴尾部提醒。
复现脚本 [bridge/repro_turn1.py](bridge/repro_turn1.py) 用真实消息形状跑，改前回“请给出具体任务”，
改后第一轮直接给 `glob` 命令。

**顺带砍掉两处纯浪费**（本地直接答，不驱动网页）：

| 请求 | 以前 | 现在 |
|---|---|---|
| 会话标题生成（每开新会话一次） | 走网页，~40s | 本地从消息里取名，0.02s |
| Mnemon 空闲审查子代理 | 走网页，30~60s，还常触发拒答重试 | 直接回“无需变更”，0.01s |

关掉：`GPTWEB_FASTPATH=0`。

## 本地规则：仓库是"可选"的

模型不该操心"要不要建库、叫什么、推不推"。桥每轮自己判断，然后把结论写进提示词开头：

```
[本地已定死的规则 · 不要询问、不要自行更改]
- 工作目录：D:\Path\others\test（就是现在这个）
- 代码仓库：https://github.com/Zhengshuji/chatgpt-web-bridge（公开，可以直接打开看；分支 main）
- 本地每轮结束会自动 commit 并 force push；不要询问是否建库、是否推送，也不要自己改远端。
```

判断方式：从 DSH 塞进来的 `Current working directory: "..."` 取到目录 → `git remote get-url origin`
和当前分支（15s 缓存）→ 有 remote 就把**公开地址**直接给模型（它能自己打开看），没有就换成：

```
- 这个目录没有 git 仓库，也没有远端：不要建仓库、不要 push，直接改文件就行。
```

所以有的项目需要 GitHub、有的不需要，不用配任何东西；目录不存在时也会明说。要加项目级额外
规则（比如"测试命令是 pytest"）就设 `GPTWEB_POLICY`，会拼在同一块里。

> 注意别在 `GPTWEB_POLICY` 里写死某个仓库地址——那会在别的项目里变成错误信息。仓库信息交给上面的
> 自动检测。

## 配置项

桥（`bridge/server.py`，环境变量）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `GPTWEB_CDP` | `http://127.0.0.1:9225` | 已登录 Chrome 的调试地址 |
| `GPTWEB_PORT` | `8355` | 本地 OpenAI 兼容端口 |
| `GPTWEB_HOME` | `bridge/data` | key / 日志 / 会话记录目录 |
| `GPTWEB_REPO` | 空 | 写进提示词的仓库地址（公开库，模型自己去读） |
| `GPTWEB_THINKING` | `0` | 默认是否打开网页上的"思考"；请求头 `X-Thinking: 1/0` 可单次覆盖 |
| `GPTWEB_SESSION_ROUNDS` | `20` | 一个网页对话最多接几轮，超了下次自动换新对话 |
| `GPTWEB_DUMP` | `0` | `1` 时把最近一次请求体落到 `bridge/data/last_request.json`（排查"模型看到了什么"） |

agent（`agent/middleware.py`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENT_BRIDGE` | `http://127.0.0.1:8355` | 桥地址 |
| `AGENT_KEY_FILE` | `bridge/data/key.txt` | API key 文件 |
| `AGENT_MODEL` | `gpt-web` | 模型 slug（`gpt-web` = auto） |
| `AGENT_WORKSPACE` | 仓库根 | 工具允许活动的目录，路径越界直接报错 |
| `AGENT_REMOTE` | 空 | 远端 URL；不设就只本地提交 |
| `AGENT_AUTO_REPO` | 空 | 没有远端时用 `gh repo create <名字>` 自动建一个 |
| `AGENT_BRANCH` | `main` | 推送目标分支 |
| `AGENT_THINKING` | 空 | `1`/`0` 覆盖思考开关 |

命令行：`--model` `--max-rounds` `--no-push` `--push-end-only` `--new-chat` `--thinking 1|0`。

## 常见问题

**换一个 ChatGPT 账号？**
登录态就是 profile 目录里的 cookie。新账号 = 新目录：

```powershell
python bridge\login.py --profile profiles\account-b --port 9226   # 会弹 Chrome，人工登录
$env:GPTWEB_CDP="http://127.0.0.1:9226"; python bridge\server.py  # 桥指向新账号
```

`login.py --check` 只查状态，会打印账号名和套餐（免费版 / Plus）。想搬登录态就把整个
profile 目录复制走（`robocopy` 即可，注意先关掉 Chrome）。

**项目一开始没有 git？**
`agent` 启动时会 `git init`、把分支改成 `main`、本地配一个 `local-agent` 身份；有
`AGENT_REMOTE` 就挂远端，有 `AGENT_AUTO_REPO` 就用 `gh repo create --push` 现建一个。
git 完全没装的话，同步会失败但 agent 照跑（只返回"仅本地"）。

**日志在哪？**

| 文件 | 内容 |
|---|---|
| `bridge/data/bridge.log` | 桥的运行日志：每次 REQ / OK / RETRY / CONV-FULL |
| `bridge/data/requests.jsonl` | 每次请求一行：模型、流式、工具数、消息数、耗时、状态、最后一条用户消息（截断 300 字） |
| `logs/run-*.log` | agent 每次运行的完整终端输出 |
| `logs/transcript-*.json` | 这次会话的完整 `messages`，可用来复现 |

`bridge/data/` 和 `logs/` 都在 `.gitignore` 里——里面有 key 和会话内容，不进仓库。

**网页对话到上限了怎么办？**
两道保险：① 会话超过 `GPTWEB_SESSION_ROUNDS`（默认 20）轮，下次请求自动换新对话；
② 真撞上"对话过长"时，桥丢掉会话映射、新建对话、把全量上下文压缩后重发一次，只有第二次
还失败才返回 429。远端（DSH 之类）看到 429 重试一次通常就恢复了。

**能让它自动开"思考"吗？**
能。输入框左侧那个"思考"是 `aria-pressed` 的开关，桥会去找 `form` 里文本为"思考"的按钮并
点开。`GPTWEB_THINKING=1` 全局默认，请求头 `X-Thinking: 1` 单次强制，agent 用
`--thinking 1`。开着更慢，但推理题明显好使。

**怎么开新对话？**
三种：① 换任务（消息指纹链对不上）会自动开新的；② `POST /new_chat`；③
`python agent\middleware.py --new-chat "任务"`，启动前先让网页那边新建一个对话。

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
- **只说话不动手**（回复是合法 JSON，但 `content` 里贴着代码/运行结果/一句"已了解"，`tool_calls` 为空）→
  提示它"代码写在 content 里等于没写，必须用 write_file / run_powershell 落地"；
- 最多重发 3 次，仍不合格就把原文当 `content` 返回。

对应的提示词里也写死了三条规矩：要落地就给命令、别编造没跑过的结果、第一句就给命令不要"已了解"。
另外长提示词被截断时只截中段，**尾部那段"必须给命令"的提醒永远保留**（recency 最管用）。

## 已知限制

- **单并发**：一个网页账号同时只能聊一个窗口，请求排队。
- **拒答是概率性的**：同一个提示词有时直接给命令，有时先回一句"我没有接口"，靠重发兜；
  这不是 bug，是模型侧的抖动。
- **速度**：一轮 20s～3min 不等（免费档 + 网页渲染），别拿它当实时接口。
- **DOM 依赖**：ChatGPT 改版会打破选择器，`bridge/probe.py` 是第一排查工具。
- 单条消息约 24K 字符上限，超了会裁剪。
- 工具调用是提示词仿真：一次一个调用，无并行，`tool_choice` 强制不支持。
