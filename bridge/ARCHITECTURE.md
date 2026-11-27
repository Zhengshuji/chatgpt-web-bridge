# 桥的分层架构（以 OSI 七层为基准）

这里的"桥"干的事本质上是**协议搬运**：把 DSH 的结构化请求搬到网页聊天里，再把网页的文本
搬回结构化。用 OSI 的层次来切，正好能回答"哪段代码该管哪件事"，也解释了现在的乱在哪。

一行话：**每层只跟相邻层说话，只负责一种变换，越靠下越像硬件、越靠上越像策略。**

## 一、七层与职责

| OSI | 本桥的对应物 | 职责（只做这一件事） | 现有实现（`server.py` 行号） |
|---|---|---|---|
| **L1 物理** | Chrome 进程、profile、调试端口 | 让 9225 上有一个已登录的浏览器 | `service.ps1`、`run.ps1`、`login.py`（不在 server.py） |
| **L2 链路** | CDP websocket | 连上某个标签页，帧的收发与 id 配对、断线重连 | `class Page`(108)、`_current_url`(267) |
| **L3 网络** | 会话选路 | 这次请求该落在**哪个网页对话**：指纹链前缀匹配 → delta / full；到轮数上限换新；撞上限重建 | `_msg_fp`(191)、`_session_key`(200)、`_find_session`(207)、`_remember`(232)、`ConvoFull`(181) |
| **L4 传输** | 一次投递 | 把一段文本送进输入框、按发送、等它写完、超时保活；拿回"最后一条回复的全文" | `type_and_send`(543)、`snapshot`(75)、`collect_text`(399)、`_keepalive`(1174)、`wait_ready`(486) |
| **L5 会话** | 说什么 | 把 DSH 的多段消息重排成"网页能读懂的一段话"：head/背景/任务/tail、命令表、项目事实、历史提示 | `flatten`(818)、`_render`(729)、`_is_boiler`(696)、`build_tool_protocol`(863)、`_project_rules`(796)、`_history_hint`(710)、`truncate_messages`(1138) |
| **L6 表示** | 怎么表达 | 文本 ↔ 结构的编解码：抽 JSON、归一 `tool_calls`、按 Schema 校验参数、编码成 OpenAI JSON/SSE | `_extract_json_object`(1059)、`parse_reply`(1099)、`validate_args`(1029)、`chunk`(1186)、`err`(1193) |
| **L7 应用** | 对外契约与策略 | HTTP 面、鉴权、快速通道、六类重发、幂等、日志/进度、错误码 | `completions`(1489)、`_completion`(1226)、`_fast_answer`(285)、`collect_json`(423)、`progress`(349)、`_log_request`(240) |

一次请求的路径：`L7 收包 → L3 选路 → L5 组话 → L4 投递 → L6 解码/校验 →（不合格回 L7 重发）→ L4 再投 → L6 编码 → L7 回包`。

## 二、现在乱在哪（都是"层被穿透"）

> **2026-11-27 更新**：下面第 3、4、5 条已经处理掉了（`config.py` / `codec.py` / 合并双管线），
> 见文末"已完成"一节。留着原文是为了记住当初为什么疼。

1. **`collect_json`(423) 是 L4 的函数，里面却编了六类重发策略（L7）**，还直接调 `type_and_send`
   和 `snapshot`。想加一类新毛病，就得改传输层。
2. **`flatten`(818) 是纯文本加工（L5），却经 `_project_rules`→`_git_info` 跑 `subprocess`（L1）**，
   每轮都执行一次外部命令。
3. **`progress()`（L7 观测）被 L4/L6 到处直接调用**，约 6 处；换个存储方式要改五个地方。
4. **全局可变单例**：`PAGE`、`SESSIONS`、`_CALL_COUNTS`、`_PROG`、`_LOG_FH`、`_models_cache`。
   后果很直接——单测只能靠 monkeypatch 全局，`_unit_*.py` 全是这个味道。
5. **快速通道 `_fast_answer`(285) 在 L7，但 `_fast_response`(316) 是 OpenAI 编码（L6）**，
   策略与编码粘在一起。
6. **`new_chat`(500) 放在 L4**，但"要不要开新对话"是 L3 的路由决定。
7. **`valid_names`/`schemas` 在 L7 组装、传给 L4 的 `collect_json`**，导致传输层签名越来越长
   （现在已经是 6 个参数）。

一句话：**分层不是没分，是被"少写一层"省掉了，于是状态和策略都往下渗。**

## 已完成（2026-11-27）

| 改动 | 结果 |
|---|---|
| `config.py` | 12 个 `os.environ.get` 收敛成 `Config` 一个对象；`server.py` 里 0 处直接读环境；启动日志回显 `CFG.describe()` |
| `codec.py` | JSON 抽取 / `parse_reply` / `validate_args` / 两个检测器变成纯函数（142 行），单测不再依赖浏览器；`server.py` 以旧名字 re-export，老测试零改动 |
| 合并双管线 | `receive()`（统一收包，工具模式自动带 JSON 校验与保活）、`interpret()`（文本→content/tool_calls）、`page_problem()`（限流/太长判定）。原来在非流式与流式分支各写一遍的代码现在只有一份；`_names` 重复 2→0，`progress("下发命令")` 2→1 |
| 规模 | `server.py` 1504 → 1384 行；十个单测全过；非流式与流式实测都正常 |

仍然没做的：L2/L3/L4 拆成类（`CdpPage` / `SessionRouter` / `Delivery`）、彻底去掉 `PAGE`/`SESSIONS`
全局。等真要加多账号或写适配器插件时再动。


## 三、目标结构

```
bridge/
  transport/
    chrome.ps1           # L1: 探活/启动/清单例锁（从 service.ps1 抽）
    cdp.py               # L2: CdpPage —— eval/call/ensure/close/current_url
  session/
    router.py            # L3: SessionRouter —— pick/remember/bump/drop，含轮数与重建
    store.py             # L3: 会话表（先内存，日后可换磁盘）
  delivery/
    compose.py           # L4: 清空输入框 + 插入 + 发送 + Enter 兜底
    collect.py           # L4: snapshot + 稳定性判定 + 超时 + 保活
  dialogue/
    prompt.py            # L5: flatten / head / tail / 背景重排
    tools_card.py        # L5: 命令表（带参数类型）+ 示例
    context.py           # L5: 项目事实、历史提示、策略文本（纯函数，输入是快照）
  codec/
    json_extract.py      # L6: 围栏/杂字容忍的 JSON 抽取
    reply.py             # L6: parse_reply / _norm_call
    validate.py          # L6: validate_args
    openai.py            # L6: 请求解析、JSON 响应、SSE 帧
  policy/
    nudges.py            # L7: 六类说辞
    judge.py             # L7: judge(reply, ctx) -> ok | retry(理由, 说辞)
    fastpath.py          # L7: 标题/Mnemon 审查
    guards.py            # L7: 幂等计数、重试上限
  obs/
    log.py               # L7: bridge.log / requests.jsonl
    progress.py          # L7: .agent/PROGRESS.md
  app.py                 # 装配：路由 + 依赖注入，只做接线
  config.py              # 环境变量与常量
```

### 层间契约（关键几条）

```python
# L2
class CdpPage(Protocol):
    async def eval(self, js: str, timeout: float) -> dict: ...
    async def call(self, method: str, params: dict, timeout: float) -> dict: ...
    async def ensure(self) -> None: ...
    async def current_url(self) -> str: ...

# L3
class SessionRouter:
    def pick(self, key: str, chain: list[str]) -> tuple[Session | None, int]: ...
    def remember(self, sess: Session | None, key: str, chain: list[str], url: str) -> None: ...
    def drop(self, sid: str) -> None: ...

# L4 —— 只搬字节, 不认识"工具调用"这回事
class Delivery(Protocol):
    async def send(self, text: str) -> None: ...
    async def wait_reply(self, base: Snapshot, deadline: float) -> str: ...

# L5 —— 纯函数, 输入是快照, 输出是文本
def build_prompt(req: ChatRequest, ctx: ProjectSnapshot) -> Prompt: ...

# L6 —— 纯函数
def decode(text: str) -> Reply: ...
def validate(reply: Reply, schemas: dict[str, dict]) -> list[str]: ...

# L7 —— 策略: 拿到解码结果, 决定"放行还是重发、发什么话"
def judge(reply: Reply, ctx: RequestContext) -> Verdict: ...
```

### 三条铁律

1. **单向依赖**：`L7 → L5/L6 → L4 → L3 → L2`。下层不许 import 上层，也不许回调。
   重发是 L7 的循环：L7 调 L4 发话 → L6 解码 → L7 判定 → 再调 L4。
2. **没有全局可变状态**：`RequestContext`（会话键、工作目录、项目快照、计数器）一路传下去。
   单例只允许出现在 `app.py` 的装配处。
3. **纯函数优先**：L5、L6 不碰 IO、不碰时间、不碰网络。它们的单测不需要浏览器——现在
   `_unit_*.py` 要 monkeypatch 全局，就是因为这条没做到。

## 四、迁移计划（每步都能独立验收）

| 步骤 | 动作 | 风险 | 验收 |
|---|---|---|---|
| 1 | 抽 L6 `codec/` + L5 `dialogue/` 成纯函数模块，`server.py` 只 import | 低（纯函数搬家） | 现有 `_unit_*.py` 不用改就能跑过 + `repro_turn1.py` 冒烟 |
| 2 | 抽 L2/L3/L4：`CdpPage`、`SessionRouter`、`Delivery` 成类，去掉 `PAGE`/`SESSIONS` 全局 | 中（涉及生命周期） | `probe.py`、`test_client.py`、一次真实 DSH 请求 |
| 3 | 装配 `app.py`：路由 + 依赖注入，`server.py` 只剩 `main()`；L7 的 `judge` 收敛六类重发 | 中 | 同步骤 2 + 日志/进度/幂等计数行为不变 |

每一步都保持**对外行为完全不变**（同一个端口、同一套协议），随时可停。

## 五、和"换适配器插件"的关系

分层之后，L5/L6 是纯函数，将来换 `dsh-llm-webchat` 适配器时可以直接复用：适配器只需把
DSH 的结构化请求喂给 `build_prompt`，把 `decode`/`validate` 的结果翻回 `StreamChunk`。
换句话说，**这次分层是换插件的前置条件**——不拆干净，插件化会变成第二次重写。
