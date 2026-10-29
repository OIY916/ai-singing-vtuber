# 大肥鱼 QQ 机器人 —— 项目状态文档

> 最后更新：**2026-10-27 00:30**
> 工作目录：`E:\qq-bot`
>
> **🔴 新窗口必读（按顺序）**
> 1. **「零之九」→ RVC 音色**（零之九章节内）：当前音色、`/换音色` 指令、
>    女声版优先选歌、自动升调为何关闭。**这是最近一轮的全部工作。**
> 2. **「零之九」→ 电话**：QQ 语音通话已打通（LuckyLillia 有头 + UIA 点击接听 +
>    ASR/LLM/TTS），实测 14 轮对话。含 9 个坑。
> 3. **「零之八」**：`/token 详细` + 缓存优化。
> 4. **「零之五」**：2026-10-25 全部工作。
>
> **一句话现状**：机器人日常运行正常（文字/点歌/语音/识图/看直播/打电话都通）。
> 当前 RVC 音色 = `nikou48K1999`（可用 `/换音色` 随时换）。
>
> ⚠️ **唯一未解决的问题**：「电话的耳朵」用整端点环回录音，
> 会被浏览器/壁纸引擎的声音污染（详见「零之九」同名小节）。用户已决定暂不修。

---

## 一、一句话现状

**机器人日常运行正常**：群聊读空气、点歌翻唱、表情包动图、RVC 语音、分段回复、识图全部可用。
**已上线**：跨群用户记忆插件（含好感度）+ 火山 Vivi 语音频道 + 戳一戳防骚扰 + token 统计。
**2026-10-25 新增**：
- **QQ ↔ DSH 双向桥**（群里 `/dsh 派活`，DSH 干完活 @ 你回原群）
- **表情包畸形标签修复**（`<img src="sticker...">` 不再泄漏）
- **RVC 翻唱音质修复**（选歌排序 + 无人声拦截 + 削波归一化 + 清理坏曲库）
- **LLM 兜底重试**（MiMo 内容安全过滤时自动换 DeepSeek，不再回「AstrBot 请求失败」）
- **看直播 / 看视频插件**（抖音走 CDP 真实浏览器：截图理解画面 + 读弹幕 + 关注；B站走 HTTP+ffmpeg）
**2026-10-26 新增**：
- **`/token 详细` 花费报表**（人民币金额、按模型/会话分组、单次均价、日均；内置 MiMo 与 DeepSeek 两套价格表）
- **缓存命中优化**（关历史时间戳 + 上下文 550→200 条，单次成本降 21%）
- **修掉 gcp 一个真 bug**（`req.audio_urls` 不存在，开调试日志会导致 LLM 请求钩子崩掉）

---

## 零之二、2026-10-24 本轮进度（新窗口优先看这里）

### ✅ 刚完成（可直接用，无需再改）

**1. 戳一戳防骚扰插件 `astrbot_plugin_poke_guard`（全新，本地插件）**

- 规则：**60 秒内被同一人戳 6 次 → 静默该人 5 分钟**
- 静默期内这个人的**一切消息和戳都被 `stop_event()` 拦掉**（gcp / LLM / 其他插件全收不到），实现「不回复、不理它」
- 不是写配置黑名单（那个要重启），而是**内存表 `_MUTED = {qq: 到期时间戳}`**，到期自动解除
- 静默期每 60 秒最多提醒一次（`notice_gap_seconds`），避免对方以为死机
- 指令：`/戳防`（查状态）、`/戳防 解除 <QQ>`、`/戳防 清空`
- 技术要点：`@filter.event_message_type(ALL, priority=10**9)`，高于 gcp 的 `sys.maxsize-1`
- 实测通过：独立计数、其他用户不受影响、机器人自己戳被忽略

**2. 好感度手动锁定 + 结算**

```
2481389971（主人）   → 100 分 🔒 锁定
2051613272（无敌原神小丸子）→ 0 分，已解锁（明日起自动评估可调整）
```

指令：`/记忆 好感 <QQ> <分数>`（锁定）、`/记忆 解锁好感 <QQ>`

**3. 画像时间改为每天 00:00（午夜）**

- 新配置 `profile_time`（"HH:MM"，精确到分钟），取代旧的 `profile_hour` 整点限制
- 指令 `/记忆 画像时间 [HH:MM]` 在线改，**改完立即重排定时任务，无需重启**
- 上次全量画像：**50 人 / 66 秒 / 0 失败**

**4. 戳一戳回复概率改为 15%（不回复时反戳）**

- 新增配置 `poke_bot_reply_probability = 0.15`（gcp 插件内，我加的）
- 被戳时**固定 15% 回复**，不受群读空气概率影响（这点很关键，否则会被群概率干扰）
- 不回复分支直接调 `_reverse_poke_direct()` 反戳回去
- `poke_reverse_on_poke_probability = 1.0`（不回复时 100% 反戳）

**5. 修复的两个 bug**

- **表情包标签漏进语音**：LLM 输出 `<sticker name="SHY"/>`（大写），而库目录是 `shy`，大小写不匹配导致标签残留被 TTS 念成「Sticker name=SHY」
  - 修法：`_resolve_sticker_dir()` 三级匹配（精确 → 大小写不敏感 → 去分隔符），失败则丢弃标签
  - 另在 volc_voice_call 的 `_strip_voice_tags()` 加了 XML 标签兜底清洗
- **`/记忆 改好感` 是假指令**：模型会自己编「已把好感度改成 80」这类回复。**数据实际没变**，权限本身没问题（非管理员确实会被拦）

### 📌 白名单现状（`data/cmd_config.json` → `platform_settings.id_whitelist`）

```json
["1105761562", "default:FriendMessage:2252572250", "default:FriendMessage:3774572947",
 "1121732680", "821537482", "739980532", "414858556", "963459350"]
```

- `386321903` **已移除**（该群现在完全静默）
- **2026-10-26 新增 `963459350`**（加之前该群被拦了 51 次，完全不响应）
- 机制：`WhitelistCheckStage` 在 pipeline 最前面，不在名单的会话 `stop_event()`，**连记忆插件都收不到**
- ⚠️ 加群要同步改 `enabled_groups` + `group_probability_overrides`，详见「七、白名单」

### 🎙️ 语音链路现状（三套并存，互不干扰）

| 场景 | 走哪个 | 配置位置 |
|---|---|---|
| 日常随机语音 + `speak_text` | MiMo TTS + 本地 RVC（8899） | `rvc_tts` 插件 |
| **通话模式 `/通话`** | **火山 Vivi**（`zh_female_vv_uranus_bigtts`） | `volc_voice_call` 插件 |
| 唱歌 | RVC 曲库 | `cover_sing` 插件 |

**通话模式可切换引擎**：`/通话引擎 volc`（火山）↔ `/通话引擎 rvc`（原来的 MiMo+RVC）

> `/通话` 现在是**开关**：已经在通话中再发一次即挂断，也可用 `/挂断`（别名 `/结束`）

**火山凭据**（`data/config/astrbot_plugin_volc_voice_call_config.json`）：
```
APP ID: 1946972187
Access Token: <已移除>
服务: 豆包语音合成模型2.0 字符版（试用 20000 字，有效期至 2027-04-24）
```

### 📦 已发布到 AstrBot 插件市场

- 仓库：https://github.com/OIY916/astrbot_plugin_qq_user_memory
- **v1.0.0 已过审上架**（VirusTotal 66 引擎零检出 + Claude Code Agent 安全审查通过）
- GitHub 上已是 v1.0.1（含画像时间精确化、好感度锁定），**但市场还没重新提交**（当时报「该版本号使用过」）

### ⏳ 待办 / 下一步（用户已确认要做的）

**1. ✅ 已完成 —— token 用量统计插件（用户已选 A 方案，下一步就做这个）**

> **已于 2026-10-24 落地**，插件 `astrbot_plugin_token_stats` 已可用。详情见上方 **「零之三、token 统计插件（2026-10-24）」**。以下为当初的立项记录，保留备查。

需求：能看到**输入 / 缓存命中 / 输出** 三项，以及缓存命中率。

**已确认的技术事实（重要，别重复调研）**：
- 市场有个很对口的 [astrbot_plugin_token_controller](https://github.com/QingchenWait/astrbot_plugin_token_controller)，但**要求 AstrBot >= 4.24.2，我们是 4.14.6，用不了**
- 它依赖 `ProviderStat` + `register_web_api`，**4.14.6 都没有**（已实测确认）
- 4.14.6 只把 `token_usage.total` 存进 `ConversationV2.token_usage:int`，**缓存明细被丢弃**
- **但插件层能拿到完整数据**：`llm_response.usage` 是 `TokenUsage(input_other, input_cached, output)`
  - 见 `astrbot-venv/lib/site-packages/astrbot/core/provider/entities.py:228`
  - ~~采集点：`on_llm_response` 钩子里截获，自己存 SQLite~~ ⚠️ **此设想已被实测推翻**：4.14.6 只在「最后一轮无 tool_calls」才触发该钩子，带工具调用的中间轮次全漏。实际改为**运行时包装 provider 实例方法**，见「零之三」→「采集原理」与「已知坑与修复记录」
- 工期估算：**3-4 小时**（数据采集 1-1.5h + 统计查询 1h + 指令 0.5h + 测试 1h）
- 需要写 WebUI 的话再加 3-4 小时（4.14.6 没有 `register_web_api`，只能自己起 HTTP 服务）

**2. 其他待办**
- 市场重新提交 v1.0.1（需要用户登录操作）
- 云服务器部署（用户暂缓，已调研：阿里云 2核4G ¥9.9/月最优，但**NapCat 要有图形界面登录**，且 RVC 在 Linux 要重装）

### ⚠️ 用户明确否决的方案（不要再提）

- **AstrBot 升级**：4.14.6 → 4.29.1 跨 15 版本，风险是 gcp（71 万字节，改不动）可能挂掉、MiMo ASR patch 会被覆盖、配置可能错乱。用户已决定**不升**。
- **QQ 语音通话（真电话）**：需要 Linux + `libAVSDKPlugin.so` + PulseAudio，Windows 做不到。B站有人做成了（作者「?!哦鲸鲸!?」，视频 BV1jan46ZEDh，承诺后续开源），但**目前未发布**。用户接受现状（语音消息对讲机模式）。
- **关键词触发语音**：用户否决过（"不要关键词，还是交由模型判断"），代码已回退。

### 🔧 常用操作备忘

**重启 AstrBot**（只杀 astrbot.exe，保留 NapCat/RVC）：
```powershell
Get-CimInstance Win32_Process -Filter "Name='astrbot.exe'" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 3
Set-Location 'E:\qq-bot'
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
Start-Process -FilePath 'E:\qq-bot\astrbot-venv\Scripts\astrbot.exe' -ArgumentList 'run' -WorkingDirectory 'E:\qq-bot' -RedirectStandardOutput 'E:\qq-bot\astrbot_run.log' -RedirectStandardError 'E:\qq-bot\astrbot_run.err.log' -WindowStyle Hidden
Start-Sleep -Seconds 35
```

**跑画像**（不依赖 AstrBot 是否运行）：
```bash
cd E:\qq-bot\data\plugins\astrbot_plugin_qq_user_memory
ASTRBOT_ROOT='E:\qq-bot' E:\qq-bot\astrbot-venv\Scripts\python.exe profiler.py \
  --db 'E:\qq-bot\data\plugin_data\astrbot_plugin_qq_user_memory\user_memory.db'
```

**服务端口**：6185 (AstrBot WebUI) / 62000 (OneBot WS) / 8899 (RVC) / 6099 (NapCat)

---

## 零之三、token 统计插件（2026-10-24）

### 一句话

**每次 LLM 调用的输入 / 缓存命中 / 输出 token 现在都落本地 SQLite 了**，`/token` 一条指令看汇总、命中率和分模型明细，不用 WebUI、不起额外 HTTP 服务（用户已选 A 方案）。

| 项目 | 内容 |
|---|---|
| 插件名 | `astrbot_plugin_token_stats`（本地插件，`repo: local`，未上市场） |
| 目录 | `E:\qq-bot\data\plugins\astrbot_plugin_token_stats\` |
| 库文件 | `E:\qq-bot\data\plugin_data\astrbot_plugin_token_stats\token_stats.db`（WAL 模式） |
| 指令 | `/token`（默认今日）、`/token 本周`、`/token 本月`、`/token 全部`、`/token 模型`、`/token 最近`、`/token 清理 [天数]`（管理员） |
| 版本 | 1.0.0 |

文件构成：

```
main.py            插件主体（Star）：钩子注册、/token 指令、配置读取、生命周期
stat_store.py      存储层（不依赖 AstrBot）：SQLite 建表/写入/聚合/命中率/报表
stat_capture.py    采集层（不依赖 AstrBot）：provider 方法运行时包装
tests_selfcheck.py 自测（不依赖 AstrBot，可直接用 venv python 跑）
metadata.yaml / _conf_schema.json
```

### TokenUsage 三个字段（这是核心，别记错）

AstrBot 4.14.6 的 `TokenUsage`（`astrbot/core/provider/entities.py:228`）：

```python
TokenUsage(input_other, input_cached, output)
```

| 字段 | 含义 |
|---|---|
| `input_other` | 输入 token 中**未命中缓存**的部分 |
| `input_cached` | 输入 token 中**命中缓存**的部分 |
| `output` | 输出 token |

**DeepSeek 的缓存映射已实测确认**：API 返回的 `prompt_tokens_details.cached_tokens`（即 `prompt_cache_hit_tokens`）已由框架 `openai_source._extract_usage` 映射进 `input_cached`。实测：`prompt_cache_hit_tokens=1024` → `input_cached=1024`。所以插件层直接读 `usage` 就是准的，**不需要自己解析原始 API 响应**。

> 注意 4.14.6 本身只把 `token_usage.total` 存进 `ConversationV2.token_usage:int`，**缓存明细被丢弃**——这正是必须自己采集的原因。

### 采集原理（关键：为什么不是只用 on_llm_response）

**最初设想是挂 `@filter.on_llm_response` 就够了，实测不够，会严重漏计。**

原因：4.14.6 的 `ToolLoopAgentRunner` 只在**最后一轮没有 `tool_calls`** 时才走 `on_agent_done` → 触发 `OnLLMResponseEvent`。中间那些「带工具调用的 LLM 轮次」根本不会触发该事件钩子。本机 gcp + `speak_text` / `sing_song` 工具调用很频繁，只靠事件钩子会大面积失真。

**实际采用的主采集点：运行时包装 provider 实例方法。**

- `initialize()` 里遍历 `context.get_all_providers()`，对每个实例把 `text_chat` / `text_chat_stream` 换成 wrapper
- 原方法保存在实例属性上，wrapper 打 `_token_stats_patched=True` 标记保证幂等（重复执行不叠层）
- wrapper 逻辑：`await` 原方法拿结果 → 读 `response.usage` → 写库
- `text_chat_stream` 是 async generator，需 `async for` 透传；**只在「非 chunk（`is_chunk=False`）且 usage 不为空」那一条写库**（流式 chunk 上也带 usage，必须跳过，否则一次调用会被记好几笔），最终照常 yield 出去
- `@filter.on_llm_response` 仍然保留，但**只作兜底**（覆盖插件自建 provider 实例这类漏网情况）；靠响应对象上的 `_token_stats_recorded` 标记去重，wrapper 记过的不会再写第二条

**去重三重保险**：① 流式只认 final；② 响应对象 `_token_stats_recorded` 标记；③ `resp_id` 非空时唯一索引 `(provider_id, resp_id, is_stream)` 兜底。

**session / 模型归属**：
- `session_id` 从 kwargs 里取（框架在 `tool_loop_agent_runner` 传 `session_id=self.req.session_id`）
- 完整 umo 按 `平台:消息类型:会话ID` 解析出平台与会话 ID
- ⚠️ 但真实调用传进来的常常是**裸会话号**（如 `1121732680`），不是完整 umo，解析不出平台。所以插件在 `on_llm_request` 钩子里把 `event.get_platform_name()` 按 session_id 记进映射表，落库时回填 platform；`parse_umo` 本身的语义不动
- `provider_id` / `model` 每次调用时实时取 `provider.meta()`，不缓存旧值

**健壮性**：所有写库都 `try/except` 包住，失败只 `logger.warning`，**统计挂了绝不能让机器人不回复**。

### 缓存命中率算法

```
缓存命中率 = input_cached / (input_other + input_cached) × 100%
```

分母为 0（该时段无输入 token）时显示 `0.0%`，并注明「无数据」。

### 指令输出示例

```
📊 Token 统计 · 今日
调用次数: 27
输入: 804,511
缓存命中: 139,648
缓存未命中: 664,863
输出: 316
总 token: 804,827
缓存命中率: 17.4%
```

`/token 模型` 会在后面追加分模型明细（按总 token 倒序，默认最多 5 条）：

```
分模型:
· deepseek-chat  27 次  804,827 token  命中 17.4%
```

输出是**多行纯文本、不用表格线**，为的是 QQ 里好读。

### 实测数据（首轮线上真实统计）

| 指标 | 数值 |
|---|---|
| 记录条数 | 27 |
| 输入未命中 | 664,863 |
| 缓存命中 | 139,648 |
| 输出 | 316 |
| 命中率 | **17.4%** |
| 流式 | 0 条（本机 `streaming_response=false`，非流式） |

缓存命中率 17.4% 说明 DeepSeek 的上下文缓存确实在工作——重复的系统提示词和上下文被大量复用。

### 配置项（`_conf_schema.json`）

| 配置 | 默认 | 说明 |
|---|---|---|
| `enable` | true | 总开关，关掉后不采集，`/token` 提示已关闭 |
| `record_stream` | true | 是否记录流式调用 |
| `retention_days` | 0 | 数据保留天数，0 = 永久；插件每 6 小时清理一次 |
| `max_models_shown` | 5 | `/token 模型` 最多显示几个模型 |

### 自测

```bash
E:\qq-bot\astrbot-venv\Scripts\python.exe E:\qq-bot\data\plugins\astrbot_plugin_token_stats\tests_selfcheck.py
```

- **完全不依赖 AstrBot**（用鸭子类型的假 provider），临时库放插件目录下 `_selftest_tmp/`（C 盘紧张，不用系统 temp），跑完自动清理
- 覆盖：写入、去重、按时间汇总（今日/本周/本月/全部）、命中率计算（含分母 0）、空库不崩、6 线程并发写、分模型聚合、报表字段、以及 wrapper 的非流式/流式/幂等/热重载不叠加等行为

### ⚠️ 已知坑与修复记录（实测确认，别再踩一遍）

首版上线后实际踩到 3 个坑，全部已定位根因并修复。**这里保留根因，不是「已解决」就算完**——下次改这块代码前先看这里。

**坑 1：启动时 `initialize()` 一个 provider 都包装不到**

- 现象：重启后日志打印「本次包装 provider **0** 个」，要等到第一次 `on_llm_request` 才出现「已包装 3 个 provider」。中间那段时间的 LLM 调用全部漏计。
- 根因：`astrbot/core/core_lifecycle.py` 里 **`plugin_manager.reload()`（触发插件 `initialize()`）在 `provider_manager.initialize()`（实例化 provider）之前执行**（前者约 193 行、后者约 196 行）。所以插件 `initialize()` 里调 `context.get_all_providers()` 拿到的是空列表。
- 后果：本机那次窗口期恰好没有 LLM 调用（0 损失），但 **gcp 的决策 AI（`utils/decision_ai.py:892`）和图片描述（`utils/image_handler.py:767`）是直接调 `provider.text_chat`、不走 `OnLLMRequestEvent`** ——窗口期内一旦有这类调用就必丢。
- 修法：补 `@filter.on_astrbot_loaded()` 钩子，在 provider 就绪后再包一遍（该事件由 `core_lifecycle.start()` 触发，此时 provider 已实例化）。日志里应看到「已包装 N 个 provider（on_astrbot_loaded，累计 N）」，本机 N = 3。

**坑 2：为什么不能只用 `on_llm_response` 采集（这是本插件架构的根本原因）**

- 4.14.6 的 `ToolLoopAgentRunner` **只在「最后一轮没有 `tool_calls`」时才走到 `on_agent_done` → 触发 `OnLLMResponseEvent`**。中间那些「带工具调用的 LLM 轮次」根本不会触发该事件钩子。
- 本机 gcp + `speak_text` / `sing_song` 工具调用非常频繁，只靠事件钩子漏计会相当严重。
- 所以采集点做成了**运行时包装 provider 实例的 `text_chat` / `text_chat_stream`**，`on_llm_response` 只保留作兜底（靠响应对象上的 `_token_stats_recorded` 标记去重）。这条值得反复读——它是整个插件不用「标准钩子」而走「包装」的**唯一理由**。

**坑 3：`platform` 字段曾大面积为空（26/27 条）**

- 现象：库里 27 条记录有 26 条 `platform=''`，只有 1 条是 `default`。`session` 列倒是对的（都有值、能归组），只丢了平台，所以分平台统计做不了。
- 根因：**真实调用传进 provider 的 `session_id` 是裸会话号**（如 `1121732680`），不是完整 umo（`aiocqhttp:GroupMessage:1121732680`）。`parse_umo` 对裸会话号只能拆出 `('', '1121732680')`——它处理完整 umo 的行为是对的，别去改它的语义。
- 修法：在 `on_llm_request` 里把 `event.get_platform_name()` 按 `session_id` 记进映射表（另加 `on_astrbot_loaded` 时从 `platform_manager.get_insts()` 播种一次，兜住那些没有 event 可用的直调），wrapper 落库时查表回填 `platform`。旧记录用 `backfill_platform()` 一次性补齐（只补 `platform=''` 的行，不覆盖已有值）。

**线上实测佐证（截至 2026-10-24 23:25）**：库里 27 条记录，输入未命中 **664,863** / 缓存命中 **139,648** / 输出 **316** / 命中率 **17.4%**。这是 DeepSeek 的**真实缓存命中，非模拟数据**——重复的系统提示词和上下文确实被复用了。

---

## 零之四、QQ → DSH 消息桥（2026-10-25 新增）

### 一句话

**在 QQ 群里发 `/dsh 帮我看看 xxx`，这段字会直接出现在电脑上 DSH「最近活跃会话」的输入框里**，不用切电脑就能远程派活。

### 指令

```
/dsh <内容>      把内容转发到 DSH 窗口（仅 2481389971 可用）
/dsh 状态        检查桥接是否连通（不往窗口里塞测试消息）
```

成功时机器人只回一句：**「知道了，我告诉妈妈了」**。失败才回错误详情。

### 链路（两段，缺一不可）

```
QQ 群 /dsh xxx
  → AstrBot 插件 astrbot_plugin_dsh_bridge
      · 校验 sender_id == 2481389971，不匹配静默 stop_event()
      → HTTP POST http://127.0.0.1:3080/api/dsh-inject
          → DSH 插件 dsh-qq-inject（挂在 web profile）
              · ctx.sessionController.list() 取 updatedAt 最大的普通会话
              · ctx.sessionController.prompt({ mode:'queue' }) 投递
  → 消息出现在 DSH 窗口（排队模式，不打断正在跑的任务）
```

### 文件位置（两边都要在）

| 侧 | 路径 |
|---|---|
| AstrBot 插件 | `E:\qq-bot\data\plugins\astrbot_plugin_dsh_bridge\`（main.py / metadata.yaml / tests_selfcheck.py） |
| DSH 插件 | `C:\Users\Administrator\.dsh\profiles\web\node_modules\dsh-qq-inject\lib\index.js` |
| DSH 挂载 | `C:\Users\Administrator\.dsh\profiles\web\cordis.patch.yml` 末尾的 `- insert: qq-inject` |

### 安全边界（重要）

- **只校验 QQ 号**（按用户要求，未加 secret token）
- 非 2481389971 发 `/dsh` 会被 `stop_event()` 静默拦掉，**连 LLM 都不会调**
- DSH 只监听 `127.0.0.1:3080`，不对外网暴露
- ⚠️ **这条链路等价于一个远程执行入口**：能操作用户 QQ 号的人 = 能远程指挥 DSH 改文件/跑命令（当前权限 `danger-full-access`）。账号安全直接等于机器安全。

### 踩过的坑（都实测确认过，别再踩）

1. **`ctx.sessionController.list()` 返回的不是数组，是 `{ items: [...] }`**
   Remote 包装层（`index.js:2954` 的 `async list(_request, signal) { return { items: ... } }`）把结果裹了一层。直接 `for...of` 会报 `sessions is not iterable`。插件里已做兼容：`Array.isArray(listed) ? listed : (listed?.items ?? [])`。

2. **`prompt(request, signal)` 必须传第二参数 signal**
   包装层第一行就是 `signal.throwIfAborted()`，不传会直接抛异常。插件里传 `new AbortController().signal`。

3. **`cordis.patch.yml` 里新建插件行必须用 `insert:`，不能写 `- id: xxx`**
   `- id: xxx` 是「修改已存在条目」的语法，新 id 会报 `patch: entry "xxx" not found`。正确写法：
   ```yaml
   - insert:
       - id: qq-inject
         name: dsh-qq-inject
   ```

4. **改 DSH 插件代码后 Node 不会热加载，必须重启 DSH**
   `patchReload: live` 只管客户端 bundle，服务端 Node 模块改动不生效。改 `lib/index.js` 后必须重启 `node bin.js web`。重启会短暂断开 Web GUI，但**会话历史不丢**，刷新页面即可继续（DSH 重启会自动带新 token 打开浏览器）。

5. **DSH 重启命令**（进程命令行实测为 `node <npm>\@deepseek-ai\dsh\lib\bin.js web`）：
   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
     Where-Object { $_.CommandLine -like '*dsh*bin.js*web*' } |
     ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
   Start-Sleep -Seconds 3
   Start-Process -FilePath 'node' -ArgumentList '"C:\Users\Administrator\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh\lib\bin.js"','web' -WindowStyle Hidden
   ```

6. **AstrBot 用 `detached:true` + Node spawn 启动会吞掉反斜杠**（`E:\qq-bot` 变成 `E:qq-bot`）。
   用 PowerShell 的 `Start-Process`，并带 `-RedirectStandardOutput`，否则日志不写入、无法确认插件是否加载。

### 自测

```bash
# AstrBot 侧（不依赖 AstrBot，16 项全过）
E:\qq-bot\astrbot-venv\Scripts\python.exe E:\qq-bot\data\plugins\astrbot_plugin_dsh_bridge\tests_selfcheck.py

# DSH 侧链路（返回 200 + sessionId 即成功）
curl -X POST http://127.0.0.1:3080/api/dsh-inject -H "Content-Type: application/json" -d "{\"text\":\"test\"}"

# 路由是否挂载（405 = 已挂载，404 = 未加载）
curl -i http://127.0.0.1:3080/api/dsh-inject
```

### 实测结果（2026-10-25 01:45）

- AstrBot 日志确认：`Plugin astrbot_plugin_dsh_bridge (1.0.0) by OlY` 已载入
- DSH 侧 `POST /api/dsh-inject` 返回 `{"ok":true,"sessionId":"session-4394e426-..."}`，投递的正是当时打开的窗口
- 自测 16/16 通过
- ✅ **真机验证通过**：用户在 QQ 群里实际发 `/dsh`，消息成功出现在 DSH 窗口，机器人在群里回了「知道了，我告诉妈妈了」

---

## 零之六、看直播 / 看视频插件（2026-10-25 新增）

### 一句话

**机器人现在能「看懂」抖音直播画面、读弹幕、点关注，也能看 B站视频截帧。**
核心突破：用 **CDP（Chrome DevTools Protocol）**控制副屏真实浏览器，**全程不抢用户焦点、不抢鼠标、窗口最小化也能截图**。

| 项目 | 内容 |
|---|---|
| 插件名 | `astrbot_plugin_live_watch`（本地插件，未上市场） |
| 目录 | `E:\qq-bot\data\plugins\astrbot_plugin_live_watch\` |
| 指令 | `/看播 <房间号\|链接\|关键词>`、`/关注`、`/看播登录`、`/看播状态`、`/看视频 <BV号\|链接>` |
| **模型工具** | `watch_live(target, want_other)` / `follow_live` / `stop_watch` / `watch_video`（模型自己判断何时调用，不用发指令） |
| **关闭直播** | `/关闭直播`（别名 `/别看了` `/关直播`）或直接说「别看了」「把直播关了」 |
| 版本 | 1.0.0 |
| 自测 | 144/144 通过（不依赖 AstrBot） |

### 文件构成（2866 行）

```
main.py             插件主体：5 个指令、权限、配置、CDP 生命周期
cdp_client.py       CDP 客户端：会话管理、截图、按键、DOM 操作（不依赖 AstrBot）
douyin.py           抖音业务：直播间信息、弹幕清洗、关注、被挤下线恢复
bilibili.py         B站业务：WBI 签名、直播抽帧、视频截帧（纯 HTTP，预留）
vision.py           识图：图片裁剪/压缩、多模态调用、缓存
tests_selfcheck.py  自测（48 项）
tools/              辅助脚本（开登录窗、调登录框）
```

### 为什么用 CDP 而不是「窗口截图 + 模拟按键」（这是本插件的核心技术决策）

最初设想是「用 Windows 的 `PrintWindow` 截图 + 发 `G` 快捷键关注」。**实测后否决了，全部改用 CDP。**

| 场景 | `PrintWindow` + 模拟键盘 | **CDP** |
|---|---|---|
| 窗口被遮挡 | ✅ 正常（亮度 246.5） | ✅ 正常 |
| **窗口最小化** | ❌ **只截到 160×28 空白** | ✅ **完整 1920×970** |
| **抢焦点** | ❌ **会抢，用户鼠标会被带走** | ✅ **全程不抢**（实测前台窗口始终未变） |
| 鼠标操作 | 抢鼠标 | 不抢（事件注入渲染层，绕过系统输入队列） |
| 读文字 | 要 OCR | ✅ 直接 `Runtime.evaluate` 读 DOM |
| DPI 缩放影响 | ❌ 进程 DPI UNAWARE，125% 缩放导致坐标虚拟化（1920→1536） | ✅ 无关 |

**实测数据**：CDP 截图 **0.06~0.09 秒**、139KB；`Input.dispatchKeyEvent` 注入 `g` 成功触发抖音关注按钮（弹出登录框，证明后台按键有效）。

**`PrintWindow` 的正确参数**（留作备查）：必须用 `PW_RENDERFULLCONTENT(2)`，flag 0/1 都是全黑。

### 抖音侧技术方案

**链路**：CDP 控制副屏最大化的持久化 Chrome → 截图 → 裁剪 → MiMo 多模态理解 → 拼回复

| 能力 | 实现 |
|---|---|
| 开播查询 | 走抖音搜索页（`type=live`）抓第一个直播间链接 |
| 截图 | `Page.captureScreenshot`（JPEG，可设质量） |
| 画面理解 | MiMo 多模态（**不用 OCR**，见下） |
| 弹幕 | 直接读 DOM（`webcast-chatroom` 容器） |
| 关注 | DOM click（比 G 键可靠，能校验状态） |

**Chrome 启动参数**（缺一不可，否则副屏不聚焦时视频会卡住/降帧）：
```
--remote-debugging-port=9444
--user-data-dir=<插件数据目录>/chrome_profile
--disable-background-timer-throttling
--disable-backgrounding-occluded-windows
--disable-renderer-backgrounding
--disable-features=CalculateNativeWinOcclusion
--autoplay-policy=no-user-gesture-required
--window-position=-1920,0   # 副屏在左
```

**⚠️ 能力必须注册成 `@llm_tool`，模型才用得上**（这是第一版最大的教训）：
只写 `@filter.command` 的话，模型根本不知道有这个能力，会直接否认
「我哪看得到直播间」。详见下面「核心修复记录」。

**登录态**：持久化 profile 扫码一次长期有效
- 目录：`E:\qq-bot\data\plugin_data\astrbot_plugin_live_watch\chrome_profile`
- 2026-10-25 已登录成功（用户扫码）
- 重开浏览器登录态不丢

### 不用 OCR 的原因（实测）

本机 `tesseract` / `paddleocr` / `easyocr` / `cnocr` **全部未安装**。
但现有配置已有支持视觉的多模态模型：`image_to_text_provider_id = mimo/mimo-v2.6-flash`。
实测直接传 base64 图片 → **HTTP 200，正确描述画面**。所以 OCR 完全不需要。

### 识图性能优化（实测数据，三条结论）

**1. `max_tokens` 必须 ≥ 1000，否则内容为空**

| max_tokens | 结果 |
|---|---|
| 200 | ❌ `finish_reason=length`，content 空 |
| 400 | ❌ 同样截断 |
| 1000 | ✅ 正常 |

原因：`prompt_tokens` 就有 815，MiMo 的推理 token 会先吃掉预算（同 STATUS.md 坑 24）。

**2. 耗时取决于「模型愿意写多长」，不是 prompt 长度**

| prompt 写法 | 耗时 | 输出字数 |
|---|---|---|
| 写明「150 字以内」 | **7.5s** | 161 字 |
| 不写字数上限 | 20.9s | 800 字 |

→ **提示词里必须写死字数上限**，这是最快的提速招。

**3. 裁剪截图能显著提准确性 + 速度**

裁掉顶部导航栏和右侧聊天栏，只留视频区：

| 方案 | 耗时 | 质量 |
|---|---|---|
| 原图 | 13.6s | 被 UI 弹窗带偏 |
| **裁掉顶栏+右侧（默认）** | **8.8s** | **最佳平衡** |
| 全裁（只留游戏） | 15.1s | 最准但慢 |

默认裁剪 `0,0.09,0.78,1`（配置项 `vision.crop`）。
**端到端总耗时：25.5s → 10.8s（提速 58%）**

### B站侧（已验证可用，预留接口）

**链路**：纯 HTTP + ffmpeg，**不依赖浏览器**

已实测打通：直播信息、推荐房间、直播流取流+截帧、视频信息、视频取流+截帧。

### ⚠️ 本次实测踩到的坑（都是真踩的，别再踩）

**坑 A：`-352` 不是签名问题，是缺 `buvid3/buvid4` 设备指纹 cookie**

- 现象：`getDanmuInfo` 一直返回 `code=-352`，以为 WBI 签名算错了。
- 真相：**只有签名不够**，B站风控要求带设备指纹。补上后立刻 `code=0`。
- 修法：从 `/x/frontend/finger/spi` 拿 `b_3`/`b_4`，种进 cookie。
- 对照实测：只有签名 → `-352`；签名+buvid → `0`。
- **教训：遇到 -352 别只怀疑算法，先补 cookie。**

**坑 B：`offsetParent === null` 会误杀 `position:fixed` 元素**

- 现象：找关注按钮怎么都找不到，但截图里明明有。
- 真相：抖音直播间顶栏是 `position:fixed`，**fixed 元素的 `offsetParent` 恒为 `null`**，
  用它判可见性会把整个顶栏（含按钮）全部过滤掉。
- 修法：改用 `getBoundingClientRect()` + `getComputedStyle` 的 `visibility/display/opacity`。

**坑 C：抖音「账号已在其他地方进入直播间」遮罩**

- 现象：手机扫码登录后，手机进直播间会把网页端挤掉，弹出遮罩「继续看播」，
  之后**整个顶栏消失、弹幕停更**，表现为「突然什么都读不到」。
- 修法：`page_health()` 检测 + `recover_duplicate_session()` 自动点「继续看播」。
- 实测：点完正文从 81 字恢复到 516 字，弹幕从 1 条恢复到 10 条。

**坑 D：B站直播 FLV 会 403，HLS(m3u8) 可用**

- 官方返回的 `.flv` 地址带 expires，ffmpeg 直连报 `Server returned 403 Forbidden`；
  **同一房间的 `.m3u8` 直接成功**。所以取流必须优先 HLS（配置 `bilibili.prefer_hls`）。

**坑 E：B站视频 `playurl` 必须带真实 cid**

- 传 `cid=0` → `code=-400 请求错误`。要先调 `/x/web-interface/view` 拿 `cid`。

**坑 F：弹幕原始数据很脏，必须清洗**

- 会混进系统公告（「欢迎来到直播间！抖音严禁未成年人...」）、
  孤立用户名（`锦鲤宝宝：`）、进房提示（`爱吃白饭的大肥鱼`、`来了`、`小表情`）。
- 修法：冒号结尾丢弃 + 纯符号碎片丢弃 + 关键词黑名单 + 长度上限。
- 实测：15 条原始 → 11 条干净弹幕。

**坑 G：`asyncio.CancelledError` 继承自 `BaseException`**

- 现象：CDP 会话关闭时抛异常打断调用方。
- 原因：`except Exception` **抓不到** `CancelledError`（3.8+ 起它是 `BaseException`）。
- 修法：`close()` 里显式 `except asyncio.CancelledError`。

### 配置项（26 个，`_conf_schema.json`）

主要几项：

| 配置 | 默认 | 说明 |
|---|---|---|
| `douyin.debug_port` | 9444 | CDP 端口（避开 3080/6185） |
| `douyin.window_position` | `-1920,0` | 副屏在左 |
| `douyin.maximize` | true | 最大化后不被遮挡 |
| `vision.provider_id` | `mimo/mimo-v2.6-flash` | 多模态模型 |
| `vision.max_tokens` | 1000 | **低于 600 会空白** |
| `vision.crop` | **空（不裁剪）** | 用户要求原图直出；想只留视频区可填 `0.03,0.09,0.72,0.88` |
| `vision.autocrop_black` | **false** | 自动去黑边，默认关闭（用户要求不裁） |
| `douyin.search_list_max` | 10 | 搜索后列几个候选让群友选 |
| `douyin.pending_choice_ttl` | 180 | 候选列表等待选号的超时秒数 |
| `send_screenshot` | true | 是否把截图发到群里 |
| `show_live_link_tracking` | true | 直链是否带分享追踪参数（=按 V 复制的结果） |
| `inject_capability` | true | 注入「你能看直播」给模型（**关掉会退化成「我哪看得到」**） |
| `bilibili.prefer_hls` | true | **保持开启**，FLV 会 403 |
| `permission.allowed_users` | `[]` | 留空=所有人可用 |
| `permission.cooldown_seconds` | 20 | 防刷 |

### 自测

```bash
# 48 项，不依赖 AstrBot
cd E:\qq-bot\data\plugins\astrbot_plugin_live_watch
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ^
E:\qq-bot\astrbot-venv\Scripts\python.exe tests_selfcheck.py
```

覆盖：解析函数、图片裁剪压缩、CDP 连接/截图/按键/DOM 读取、抖音健康检查与恢复、
登录检测、关注状态、B站 WBI 签名+buvid、直播/视频接口、ffmpeg 截帧、配置读取。

### 线上状态（2026-10-25 16:05）

- AstrBot 已重启加载插件（进程 28232），**0 个错误**
- 5 个指令全部注册
- 抖音已登录（持久化 profile）
- 端到端实测：**抖音直播截图 + 识图 + 11 条干净弹幕，总耗时 10.8 秒**

### 核心修复记录：模型不知道「自己能看直播」（2026-10-25 实际故障）

**现象**：群里 @ 机器人问「直播间现在什么情况」，它回
**「我哪看得到直播间啊？我又不在现场」**。

**根因（不是模型笨，是我漏了）**：

插件第一版**只注册了 `@filter.command`，一个 `@llm_tool` 都没有**。
模型只能看到**注册过的 llm_tool**；没注册 = 它不知道这个能力存在 = 合理否认。

> 对照就明白了：机器人为啥能唱歌、能念话？因为 `cover_sing` 注册了 `sing_song`、
> `rvc_tts` 注册了 `speak_text`。同一个道理 —— **能力必须注册成工具，模型才用得着。**

诊断脚本扫出来一目了然：
```
sing_song     <- astrbot_plugin_cover_sing
speak_text    <- astrbot_plugin_rvc_tts
共 2 个 llm_tool
live_watch 的 llm_tool: 0 个  ❌
```

**修法（两件事，缺一不可）**：

1. **注册 3 个工具**：`watch_live`（看直播）、`follow_live`（关注）、`watch_video`（看B站视频）
2. **`on_llm_request` 注入能力声明**：光有工具还不够，模型容易在对话里先本能否认
   （同 STATUS.md 坑 56：gcp 的「有人@你」系统提示会把模型带偏）。
   所以额外写一句：「你确实看得到，绝不要说我哪看得到」。

**修复后实测证据（AstrBot 日志，决定性）**：
```
[TOOL-PROBE] 模型收到 16 个工具: ['sing_song', 'watch_live', 'follow_live',
                                  'watch_video', 'speak_text', ...]
[16:28:06] Agent 使用工具: ['watch_live']
[16:28:18] Tool `watch_live` Result: 【你实际看到的内容，请据此回答群友...】
[16:29:14] Agent 使用工具: ['watch_live']    ← 第二次又主动调用了
```

**验证方法可复用**（下次怀疑「模型不知道某能力」时照做）：
写一个临时 `on_llm_request` 探针插件，打印 `req.func_tool` 里的工具名清单。
源码里有 ≠ 模型看得到，**必须看运行时**。用完即删。

**同时修的一个隐患**：`_check_enable()`/`_check_permission()` 原来内部调
`event.stop_event()`。指令里调没问题，但**在 llm_tool 内部调会把整个事件掐断** ——
模型刚拿到工具结果就被打断，群里收不到任何回复。已改成只返回 bool，
由调用方自己决定是否 stop。

### 附带改进：问直播会**直接发画面截图**

**需求**：用户希望问「直播间什么情况」时，除了文字回复，还能**收到一张实时画面截图**。

**为什么必须插件自己发**：模型**没有发图能力** —— 它只会把画面描述成文字。
所以截图必须由插件用 `event.send()` 发出去，模型只负责组织回复文字。

**实现**（`_send_screenshot()`）：
```python
Comp.Image(file=本地路径)          # 需要真实文件，不能喂 bytes
await event.send(event.chain_result([Comp.Image(file=path)]))
```
- 截图先落盘到 `plugin_data/.../_shots/`，发完即删（实测 0 残留）
- 发送失败只记 warning 并返回 False，**绝不让整个看播流程挂掉**
- 模型会收到一句「截图我已经发到群里了，你不用再发图」，避免它重复描述

**真机实测（2026-10-25 16:40，决定性日志）**：
```
[16:40:37] 用户: @bot 直播间现在什么情况
[16:40:38] Agent 使用工具: ['watch_live']
[16:40:52] [live_watch] 截图已发送 (140908 bytes)      ← 图片真发出去了
[16:40:55] 机器人: "还是那个爆宝，没换人，弹幕在闲聊，有人说被阴阳了，主播还在互动"
```
**文字 + 截图同时到达，0 异常，临时文件 0 残留。**

### 顺带修掉的问题

**B站视频 403（主 URL 时效签名失效）**

- 现象：`extract_frame` 偶尔报 `Server returned 403 Forbidden`。
- 根因：B站 dash 流地址带 `e=` 时效参数，且**每次请求都会给一组 backupUrl**，
  单条 URL 失效/被 CDN 拒就 403。
- 修法：`get_video_streams()` 返回主 URL + 全部 backupUrl，`extract_frame()`
  逐个轮试；同时带上 `Referer` 头（B站 CDN 会校验来源）。
- 实测：3 条候选地址轮试后成功。

**`os.getcwd()` 会导致误建目录**

- 现象：直接跑插件脚本时，在插件目录下多出一个 `data/`。
- 修法：新增 `_astrbot_root()`，从插件自身位置往上 3 层反推根目录
  （`<root>/data/plugins/<plugin>`），只在兜底时用 cwd。

### 直播间直链（对应直播间快捷键 V）

**需求**：回复里在房间号下面附一条直播间链接。用户提到抖音直播间除了关注（G），
还有快捷键 **V = 复制直播间直链**。

**实测确认**（2026-10-25）：在直播间里按 V，剪贴板确实变成：
```
https://live.douyin.com/70637574542?enter_from_merge=link_share
&enter_method=copy_link_share&action_type=click&from=web_code_link
```
（CDP 需要先 `Browser.grantPermissions` 授予 `clipboardReadWrite` +
`clipboardSanitizedWrite`，并 `Page.bringToFront` 让文档获得焦点，否则
`navigator.clipboard.readText()` 报 `Document is not focused`）

**关键结论：不需要真的按 V。** 按模板直接拼出来的字符串与 V 复制的结果
**逐字节完全一致**（已比对）。所以插件用 `build_share_link()` 拼字符串 ——
更快、更稳、不依赖页面焦点、不需要剪贴板权限。

**展示位置**：截图和链接合成**一条消息**由插件直接发：
```
📺 炸毛小雾🌬️²¹³  房间 70637574542
🔗 https://live.douyin.com/70637574542?enter_from_merge=link_share&...
```
模型只负责补一两句自然描述。配置项 `show_live_link_tracking`（默认 true）
控制用长链还是短链。

### 📌 截图不裁剪（用户明确要求，2026-10-25）

**用户原话：「不需要裁剪，现在这样挺好的」** —— 所以：

- `vision.crop` 默认**留空**（不裁剪，原图直出）
- `vision.autocrop_black` 默认**关闭**

代码里的 `vision.autocrop_black()` 函数**保留着**（实测可用，能把竖屏直播
左右暗边从 43% 降到 4%），想用时在配置里打开即可，不用改代码。

> 关于裁剪的调研记录（备查）：一度以为竖屏直播两侧的大片暗区会让模型误判，
> 于是做了自动去黑边。算法踩了三个坑才调对：
> ① 阈值 18 只认纯黑，右侧深灰（亮度约 50）没抓到；
> ② `min_run=0.05` 太低，左侧黑区残留 5~7% 亮点就被判成「有内容」；
> ③ 逐列阈值判断会被底部礼物栏的亮柱干扰，右侧灰区留下。
> 最终方案：阈值 60 + `min_run=0.15` + 从外往里扫「连续平坦段」（用列的
> 标准差判断纯色底，实测灰区 std≈3、真实画面 std>16）。
> **但用户最终觉得不裁更好，所以默认关闭。**

**顺带修掉的三个问题**

**1. 链接不能让模型代劳（重要教训）**

- 一开始把直链塞进工具返回文本，指望模型回答时贴出来。
- 实测（16:57:14）**模型没贴** —— 回复只有「还是那个炸毛小雾…」。
- 结论：**模型不一定遵守「请原样贴出链接」这种指令**。
- 修法：`_send_screenshot(caption=...)` 把「主播 + 房间号 + 直链」和图片
  合成一条消息**由插件自己发**；并告诉模型「不要重复贴链接」。

**2. B站视频 403（三个原因叠加）**

- 原因1：只拿「最低清晰度」的主 URL + 备份，整组 403 时全军覆没
  → `get_video_streams(all_quality=True)` 收所有清晰度的主/备（实测 12 条）
- 原因2：这批地址整体失效时不重试
  → `_video_pipeline` 整批失败会**重新取流再试一次**
- **原因3（根因）：ffmpeg 只带了 `Referer`，没带 `User-Agent`**
  → B站 CDN 两个都校验。补上 UA + Origin 后彻底稳定。
  **这是踩得最久的一个坑 —— 别把 `-headers` 里的 UA 删掉。**
- 实测：修后连续 3 轮自测 81/81 全过。

**3. `AstrBot` 的 `get_astrbot_root()` 会退回 `os.getcwd()`**

- 现象：在插件目录里跑测试，插件目录下会莫名多出一个 `data/`。
- 真相：`astrbot_path.py:25` 的 `get_astrbot_root()` 优先读 `ASTRBOT_ROOT`，
  **没有该变量时退回 `os.getcwd()`**。cd 到插件目录跑脚本就会误建。
- 修法：跑自测要在 `E:\qq-bot` 下，并带 `ASTRBOT_ROOT='E:\qq-bot'`：
  ```bash
  cd /e/qq-bot
  ASTRBOT_ROOT='E:\qq-bot' PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
    E:/qq-bot/astrbot-venv/Scripts/python.exe \
    data/plugins/astrbot_plugin_live_watch/tests_selfcheck.py
  ```
  插件自身也加了 `_astrbot_root()` 从插件位置反推根目录作为兜底。

### 搜索定向性（2026-10-25 用户新需求）

**用户原话**：
1. 「我给出准确的用户名『七日世界星川』就直接去这个直播间」
2. 「我『@bot看美女跳舞的直播，不要看之前那个』那它就换一个相关的直播间」

**实测前提（这决定了能做什么）**：抖音搜索页的直播间卡片文本是
```
['炸毛小雾²¹³', '·正在直播']       # lines[0] = 主播名
['梨涡！', '·正在直播']
['南荔', '·正在直播']
```
→ **卡片里能拿到主播名**，所以「按用户名精确匹配」是可行的。
（早先以为拿不到，因为 `a.innerText` 是空的 —— 要往上找到含「正在直播」的
卡片容器，取它的 `innerText` 按行拆。）

**两种模式**：

| 用户说法 | 匹配方式 | 行为 |
|---|---|---|
| 准确用户名 | exact / contains / reverse / loose | **直达该直播间** |
| 「看美女跳舞」 | random | 内容搜索，**排除当前房间**后随机挑 |
| 「看别的」「换一个」 | want_other=True | **强制排除当前房间**，即使关键词命中当前房间也要换 |

**匹配优先级**（`_pick_best_card`）：
`主播名完全等于关键词` > `包含` > `反向包含` > `归一化后互相包含` > `内容随机`

**`_normalize_name` 要处理的装饰**（实测主播名都很花）：
- `炸毛小雾🌬️²¹³` → `炸毛小雾`（emoji + 上下标）
- `梨涡！` → `梨涡`（全角标点）
- `星川游戏菌【七日世界】` → `星川游戏菌`（括号装饰）

⚠️ 注意 **`\ufe0f` 变体选择符**：emoji 去掉后它会单独残留
（实测「炸毛小雾🌬️²¹³」去掉 emoji 后剩「炸毛小雾️」）。必须一起清掉。

**工具签名变化**：`watch_live(target, want_other=False)`。
`want_other` 专给「看别的」用；docstring 里明确写了「只问当前情况就留空 target，
不要因为上一轮搜过啥就把那个词又传进来」，防止模型反复搜索同一个词。

### 两个实测 bug 的修复（2026-10-25 用户反馈）

**Bug 1：「已经在直播间里了还重新进一遍，截图显示直播加载中」**

- 根因：`douyin_open_or_navigate` **无条件 navigate**，哪怕目标房间已经打开。
  重载后 `video` 需要 5~10 秒就绪，这段时间截图必然是「加载中」。
- 修法两条：
  1. **目标房间已打开 → 直接复用，绝不 navigate**（日志会打「直接复用」）
  2. 新增 `douyin.wait_frame_ready()`，等到 `videoWidth >= 320` 且
     `readyState >= 2` 才截图；已在房间时会**立刻通过**（实测 0.0 秒），不浪费等待
- 实测验证：`performance.timeOrigin` 前后一致（无重载），画面 0 秒就绪 ✅

**Bug 2：「搜『美女跳舞』总去同一个直播间，说看别的还是原来那个」**

- 根因三个：① 永远取 `links[0]`（排序稳定，永远同一个）
  ② 没排除当前房间 ③ 搜索页抢了直播间标签页
- 修法：收集候选 → 排除当前房间 → 随机挑 / 按用户名定向
- 实测验证：连续搜两次命中**不同房间**（`86306542355` vs `305249978869`）✅

**顺带：标签页自动清理**

搜索时为不踩掉正在播的直播间会新开标签页，搜完调用
`cleanup_extra_tabs()` 关掉多余的（`max_search_tabs` 默认留 1 个），
否则搜几次浏览器里就堆一堆搜索页。

### 搜索→列表→选号 交互（2026-10-25 用户新需求）

**用户原话**：「我说 @bot 去看 xxxx 直播，bot 去抖音搜索后给我个 10 条的列表，
问我要看看哪个，我 @bot 说几号直播间后，bot 进直播间等待画面加载好后，
再群里发直播间截图 + 直播间链接」

**流程**：
```
群友: @bot 看美女跳舞的直播
  ↓ 插件搜索、滚动凑够 10 个
bot:  🔍 搜到「美女跳舞」正在直播的房间（共 10 个）:
      1. 炸毛小雾²¹³
      2. 梨涡！
      3. 淼淼-海洋斗罗（98级极限斗罗）
      …（10 条）
      要看哪个？回我序号就行（比如「3」）
  ↓ 群友回「3」
bot:  好，进第 3 个：淼淼-海洋斗罗，等画面加载一下…
      [截图 + 📺 主播  房间 xxx + 🔗 直链]
```

**⚠️ 关于「凑够 10 个」——这是本次最大的技术发现**：

抖音搜索**首屏只给 3 个**直播间。往下滚动能加载更多，
但它用的是**虚拟列表** —— 滚过去之后上方卡片会被回收，
所以某一时刻 DOM 里永远只有 6~7 个（实测在 6/7 之间反复横跳）。

**所以不能「滚到底再刮一次」**，必须**边滚边累积**：
每滚一次就把当前 DOM 抓进 pool（按 roomId 去重），直到凑够或滚不动。
实测：首屏 3 → 滚 2 次 9 → 滚 3 次 13 → 取前 10 个 ✅

**实现要点**：

| 功能 | 方法 |
|---|---|
| 搜索候选（边滚边收） | `_search_candidates()` |
| 列表文案 | `_format_candidate_list()` |
| 记住待选（内存表） | `_save_pending()` / `_get_pending()`，TTL 默认 180 秒 |
| 解析序号 | `_parse_choice_index()` |
| 按序号进直播间 | `_do_choice()` |
| 进房间+等画面+抓数据 | `_capture_live()`（三处复用，唯一实现） |
| 发截图+链接 | `_publish_live()`（直播统一出口） |

**序号解析的坑**（实测）：
- 支持「3」「第3个」「3号」「3号直播间」「看3」
- ⚠️ 不能直接用 `\d{1,2}` —— 会让「100」被截成 `10`。
  要先用 `\d+` 抓完整数字，再判断是否 1~99。

**列表由插件直接发，不让模型复述**：
实测模型复述列表会漏项、改序号。所以 `event.send()` 直接发列表，
工具只返回一句「我已经把列表发到群里了，等群友回序号，**不要自己复述**」。

### ⚠️ 又踩了一次的坑：`yield` + `return 值`

`_do_choice` 一开始写成异步生成器（有 `yield`），却又 `return None` →
`SyntaxError: 'return' with value in async generator` →
**整个插件加载失败**，日志里才看得到（运行时无提示）。

这就是 STATUS.md 坑 49 的同一个问题，**第二次踩**。已修：
- `_do_choice` 改为普通协程，用 `await event.send(...)` 发中间消息，
  返回结果对象，调用方 `yield r`
- 自测新增 **[15] 异步生成器陷阱扫描**：用 AST 扫全文件，
  断言没有「同时含 yield 和 return 值」的 async 函数

### 关闭直播 + 登录态持久化（2026-10-25 用户需求）

**用户原话**：「@bot 别看直播了（别看了、把直播关了）等，好让他不看直播，
关闭那个浏览器，对了下次让它来看播还需要重新登录抖音吗，能否本地保存cookie」

#### 一、关闭直播

三种说法都能触发（指令 + llm_tool 双通道）：

| 说法 | 处理 |
|---|---|
| `/关闭直播` `/别看了` `/关直播` | 指令，直接执行 |
| 「别看了」「把直播关了」「不看了」 | 模型调 `stop_watch` 工具 |

**关闭方式：CDP `Browser.close`，1.6 秒干净退出。**

⚠️ **不要改成强杀进程**（`Stop-Process -Force`）：有一定概率让 Chrome 的
Cookies 数据库没写完，反而坑到登录态。能优雅关就优雅关。

关闭时会顺手清掉待选列表，避免「关完了还能回序号进直播间」。

#### 二、登录态：本来就已经本地保存了

**答案：不需要重新登录，早就存本地了。**

机制：Chrome 用固定的 `--user-data-dir`，cookie 落在
`<插件数据目录>/chrome_profile/Default/Network/Cookies`。

```
profile 目录: E:\qq-bot\data\plugin_data\astrbot_plugin_live_watch\chrome_profile
目录大小: 548 MB
Cookies: Default/Network/Cookies ✅ 存在
```

**实测验证（2026-10-25，完整跑通）**：
```
1. 关闭前:  loginBtnCount=0, isLoggedIn=true   ✅ 已登录
2. 关浏览器: close_browser -> {ok:true, method:cdp}, 耗时 1.6s
3. 重启后:  loginBtnCount=0, isLoggedIn=true   ✅ 登录态保留
```

**踩过的测试陷阱（值得记）**：
判断登录态**必须先站在抖音页面上**。浏览器重启后停的是
`chrome://new-tab-page/`，此时 `localStorage` 是空的、`document.cookie` 长度为 0，
会误判成「登录态丢了」。我第一次就差点被这个假象骗过去。

判据用「顶部是否还有『登录』按钮」最可靠（`loginBtnCount === 0` 即已登录）。

### 使用前提（重要）

- **抖音截图依赖副屏那个 Chrome 窗口在运行**。机器人不能自己打开浏览器，需要窗口保持开着。
- 关掉浏览器不会丢登录态（profile 持久化），但下次需要重新启动。
- 想重新登录：跑 `tools/open_douyin_login.py`

---

## 零之五、2026-10-25 当天全部工作（新窗口优先看这里）

> 本节记录 2026-10-25 一整天的改动。**按时间顺序**，后面的是最新状态。

### ✅ 已完成 1：QQ ↔ DSH 双向桥（含任务完成回调）

| 方向 | 链路 |
|---|---|
| QQ → DSH | 群里发 `/dsh 内容` → AstrBot 插件 → `POST 127.0.0.1:3080/api/dsh-inject` → 投递到最近活跃会话 |
| DSH → QQ | Agent 回合结束 → 读 `turn/end` 判定结果 → `POST 127.0.0.1:6187/dsh/notify` → bot @ 主人在**原群**通知 |

**三类通知文案（每类多句随机，避免腻）**：

| 类型 | 触发原因 | 文案示例 |
|---|---|---|
| `done` | `reason.kind == "completed"` | 我妈妈说弄好了，喊你去看看 / 妈妈那边收工了，让你过去验货 |
| `failed` | `reason.kind == "error"` | 妈妈说她那里出现了点问题，要你去看看怎么办 |
| `aborted` | `reason.kind == "aborted"` | 妈妈说活儿被打断了，你要不要重新派一次 |

**派发成功回复**：随机 5 句（原句「知道了，我告诉妈妈了」仍在池里）

**文件位置**：
- AstrBot 侧：`data/plugins/astrbot_plugin_dsh_bridge/`（main.py + tests_selfcheck.py，自测 41/41）
- DSH 侧：`~/.dsh/profiles/web/node_modules/dsh-qq-inject/lib/index.js`
- 挂载：`~/.dsh/profiles/web/cordis.patch.yml` 末尾 `- insert: qq-inject`
- 诊断端点：`GET http://127.0.0.1:3080/api/dsh-status`（看监听器计数、在途任务）

**三个坑（都实测确认）**：

1. **`session/event` 插件收不到** —— 它只在 Session 自己的 emitCtx 派发（`dsh-session/lib/index.js:1468` 用 `collectSessionCallbacks(entry.emitCtx, ...)`），不是全局广播。改用 `agent/status`（running→idle）。
2. **事件业务字段在 `data` 里，不在顶层** —— 事件形状是 `{ type, seq, time, data }`，`turn/end` 的 reason 在 `ev.data.reason`。读 `ev.reason` 永远 undefined，这是「回调一直不触发」的真正根因。
3. **重启 DSH 会清空 `inflight` 内存表** —— 派发后若重启，那条通知就丢了（无持久化）。

### ✅ 已完成 2：表情包标签泄漏修复

**现象**：群里出现 `<img src="sticker name=&quot;sigh&quot;"/>都聊了三小时了` 这种畸形文本。

**根因**：模型没按插件要求输出 `<sticker name="sigh"/>`，而是输出 `<img src="sticker name=&quot;sigh&quot;"/>`（包了 img + 引号转成 HTML 实体，甚至有换行拆分的形态）。插件正则 `(<sticker.*?/>)` 认不出 → 整串当普通文本发出。

**修法**（`data/plugins/astrbot_plugin_meme_manager_lite/main.py`）：
1. 新增 4 个正则，把畸形形态**纠回标准形态**（贴纸能正常发出去，不是丢弃）
2. `_remove_sticker_tags` 增加多重兜底清洗
3. `_process_text_with_sticker` 处理前先纠正 + 最后一道防线拦残骸
4. 提示词加「格式错误示范」，明确禁止包 img / 禁止 `&quot;` / 禁止换行

**验证**：自测 23/23；真实库 4 条畸形记录全部正确纠正；正常图片 `<img src="data:image/..."/>` 不受影响。

### ✅ 已完成 3：RVC 翻唱音质修复（4 个问题）

**用户反馈**：男声歌翻唱「破音、发闷、不像人、跑调」，女声歌效果好。

#### 问题 3-1：本地曲库复用旧坏文件（**用户自己发现，最关键**）

`netease_download()` 第 655-656 行：
```python
if os.path.exists(path) and os.path.getsize(path) > 50000:
    return path        # ← 本地有就直接复用，不再下载
```

曲库 108 首歌都是之前用**削波音源**下载的，导致所有后续修复全部失效。
**处理**：已删除 `songs/`（3.0 GB / 108 首）+ `cache/`（598 MB / 73 首），后续重新下载。

#### 问题 3-2：选歌选到翻唱版

**根因**：网易云没有周杰伦版权，搜「晴天」结果全是深情版/女声版/钢琴版/DJ版，原 `_pick_song` 会选中「晴天 - 梦里啥都有」这种不知名翻唱。

**修法**：改为**两档扣分、绝不硬排除**（硬排除会导致《晴天》这类歌一首也选不出来）：
- **致命类 -120**：钢琴版/伴奏/纯音乐/演奏/instrumental/karaoke/降调版/0.8x/0.9x（没人声或严重变调）
- **降级类 -25**：Live/现场/Remix/DJ版/女声版/深情版/烟嗓版/翻自/cover/抖音/热搜版/童声等

**实测 9 首歌全部选中「原唱级、无修饰词」**：《十年》→陈奕迅、《后来》→刘若英、《光年之外》→邓紫棋、《红豆》→王菲、《长大别害怕》→奚缘。

#### 问题 3-3：钢琴版/伴奏版拿去转换

**修法**：新增 `_vocal_energy_ratio()` + `MIN_VOCAL_RATIO = 0.30`

**阈值依据（实测校准 17+ 样本）**：
- 正常有人声的歌曲：人声/伴奏比值 **0.44 ~ 0.90**
- 无人声（钢琴版/伴奏）：**0.16**

低于 0.30 直接抛错：「这首可能是纯音乐/钢琴版/伴奏版（人声能量仅占 X%），没有人声可唱」。
**回归验证**：21 个正常样本零误伤，只拦下那 1 个无人声的。

#### 问题 3-4：音源削波导致破音

**实测**：翻唱版音源 `peak=1.000`（0.027% 采样已削平）。实测高频噪声占比是真实人声的 **2.7 倍**（0.194 vs 0.070）。

**修法**：新增 `_normalize_source()` + `_audio_stats()`，峰值 `> 0.95` 时归一化到 0.89。
**关键设计**：峰值正常（≤0.95）**直接跳过**，不做无谓重编码损失。

**注意**：`_audio_stats` 只支持 WAV（scipy 读不了 FLAC/MP3），已加 try/except 返回 `unknown` 兜底。主流程因为是先转 wav 再测，不受影响。

### 📊 最终实测数据（从空库跑完整流程）

```
点《十年》→ 选中陈奕迅原唱 → 下载 21.8MB FLAC 无损
→ 分离 → 转换 → 混音 → 45 秒出成品
峰值 0.882 | 削波 0% | 高频噪声 0.163（修复前坏版本 0.194，真实人声 0.070）
```

### 🔬 已验证无效的思路（别重复调研）

**用户提的想法：把人声处理成"最适合 RVC 模型的频率"** —— 实验证明**对音质无效**：

| 方案 | 高频噪声比 |
|---|---|
| 原样转换 | 0.218 |
| 先移调 -3 再转换 | 0.219 |
| RVC 内部移调 -3 | 0.219 |
| 先移调 +5 | 0.242 |
| RVC 内部移调 +12 | 0.438 |

**结论**：移调不改变音质，噪声来自音源削波，不是音高不匹配。真正的问题是音源质量。

### ⚠️ 新发现的坑（RVC 参数）

**`protect` 参数只能 < 0.5 才生效**（`RVC_Core/infer/modules/vc/pipeline.py:221`）：
```python
if protect < 0.5 and pitch is not None and pitchf is not None:
    # 保护气声/清辅音的逻辑
```
设成 0.5 或更高会**整段跳过保护逻辑**，输出和 0.33 完全一样（实测频谱重心都是 1523Hz）。调这个参数时注意。

**音高检测要用 rmvpe，不要用 pyin/yin**：rmvpe 是 RVC 自带的，与实际转换一致；pyin 在混音音频上会被贝斯带偏（周杰伦原曲测出 104Hz，分离后 rmvpe 实测 278Hz，差 3 倍）。

### 📌 待办（未完成）

1. **`STATUS.md` 本次记录**：正在写（本节）
2. **Hindsight 记忆库**：不可用（缺 API key，返回 401），踩坑记录只能靠本文件
3. **QQ 市场重新提交 v1.0.1**：需要用户登录操作
4. **画像 50 人上限**：按「历史总发言数」排序导致当天活跃的人被挤掉。用户决定**暂不改**。
5. **RVC 破音若仍存在**：需要用户反馈具体歌曲继续排查（目前已修 4 类问题）

### 🔧 关键服务与重启方式

| 服务 | 端口 | 重启方式 |
|---|---|---|
| AstrBot | 6185 | 杀 `astrbot.exe` 后 `Start-Process ...astrbot.exe run`（必须带 `-RedirectStandardOutput`）|
| DSH Web | 3080 | 桌面 `重启DSH.bat`（调 `Restart-DSH.ps1`，走 explorer 继承环境变量，最可靠）|
| **DSH 回调** | **6187** | 由 `astrbot_plugin_dsh_bridge` 自建（⚠️ 不能占 6185）|
| RVC 翻唱 | 8899 | 杀监听进程后 `Runtime\python.exe cover_service.py`（需 `COVER_PORT=8899`）|
| NapCat | 6099 | — |

**DSH 重启特别注意**：改服务端插件代码后 Node **不会热加载**，必须重启进程。`patchReload: live` 只管客户端 bundle。

---

## 零之七、LLM 兜底重试插件（2026-10-26）

### 问题现象

群里偶尔出现机器人回一句：

```
AstrBot 请求失败。
错误类型: Exception
错误信息: API 返回的 completion 由于内容安全过滤被拒绝(非 AstrBot)。
```

### 根因（坑 33 的实例）

**默认模型是小米 MiMo（`mimo/mimo-v2.6-flash`），它的内容安全策略比 DeepSeek 严。**
服务端返回 `finish_reason == "content_filter"` 主动截断，AstrBot 在
`openai_source.py:306` 如实抛出异常。

**这不是配置错误，也不是 AstrBot 的 bug。** 触发场景：群里聊擦边话题、亲密角色扮演（好感度系统会让它说「叫老公」之类）。

**实测频率（2026-10-26 上午）**：4 次失败，其中 3 次在 `963459350` 群。

### 解决：`astrbot_plugin_llm_fallback`（自研）

**思路**：在**插件层**包装每个 provider 的 `text_chat`，捕获这个特定异常后
改用备用 provider（DeepSeek）重试一次，成功则用户无感。

**为什么不改 `openai_source.py`**：那是 AstrBot 核心文件，升级会被覆盖（同坑 2 的教训），且会影响所有 provider。

### 文件与配置

| 项目 | 内容 |
|---|---|
| 目录 | `data/plugins/astrbot_plugin_llm_fallback/` |
| 文件 | `main.py` / `metadata.yaml` / `_conf_schema.json` / `tests_selfcheck.py` |
| 自测 | **16/16 通过** |
| 备用模型 | `deepseek/deepseek-chat`（可配） |

### 设计要点（都实测验证过）

1. **只拦内容安全过滤**，其它错误（额度/网络/参数）原样抛出，不掩盖真实故障
2. **备用也失败 → 抛出原始异常**，不吞错
3. **备用 provider 不存在/与主相同 → 回退原始错误**
4. **幂等包装**：打 `_llm_fallback_patched` 标记，重复执行不叠加
5. **参数不兼容（TypeError）→ 回退原始错误**（不同 provider 支持的 kwargs 可能不同）

### ⚠️ 与 token_stats 的包装顺序（重要）

两个插件都包装 `text_chat`。**实测顺序**：

```
llm_fallback 先包（内层）→ token_stats 后包（外层）
```

**这个顺序是对的**：兜底成功后的结果能被 token_stats 正常记录。

日志确认：
```
[12:39:28] [llm_fallback] 已包装 3 个 provider（累计 3）
[12:39:28] [token_stats] 已包装 3 个 provider（on_astrbot_loaded，累计 3）
```

**两个包装器必须都靠 `on_astrbot_loaded` 钩子才生效** —— 因为 `initialize()` 时
provider 还没实例化（坑 1 的老问题）。本插件同时用了 `initialize()` 和
`@filter.on_astrbot_loaded()` 双保险。

### 验证方式

看日志里是否出现：
```
[llm_fallback] 内容安全过滤拦截，改用备用 provider 'deepseek/deepseek-chat' 重试
[llm_fallback] 兜底成功（累计 N 次）
```

若出现 `备用 provider 也失败` / `参数不兼容`，说明兜底没成功，会回退成原来的错误。

---

## 零之八、`/token 详细` + 缓存命中优化（2026-10-26）

### 一句话

给 token 统计插件加了 **`/token 详细`** 花费报表（含人民币金额），
并按实测数据压低了群聊上下文，**单次调用成本降 21%**。

> ⚠️ **最重要的教训放最前面**：计价必须**先识别模型再算钱**。
> 我第一版只内置了 DeepSeek 价格，结果 MiMo 的 400 次调用全被兜底按
> DeepSeek 最贵档估算，**算出 ¥170.12，实际只有 ¥33.84 —— 虚高 5 倍**。
> 用户当时用的正是 `mimo-v2.6-flash`。

### `/token 详细` 用法与输出

指令：`/token 详细`（别名：详情 / 花费 / 成本 / cost）

真实输出（2026-10-26 实测，1794 次调用）：

```
【token 统计】
累计调用：1,794 次
累计消耗：49,223,180 token
  输入(未缓存)：33,115,563
  输入(缓存)：15,993,216
  输出：114,401
累计花费：¥33.84
缓存命中：33%

今日：504 次 / ¥13.51

最近 10 次平均：
  输入 30,127 / 缓存 12,762 / 输出 177
  单次约 ¥0.0307

日均 ¥20.92（按 2 天算）

按模型分组：
  deepseek-chat — 1,394 次 / ¥24.97
  mimo-v2.6-flash — 400 次 / ¥8.87

按会话分组：
  1121732680 — 809 次 / ¥22.66
  946241512 — 315 次 / ¥3.37
  ...

当前：空闲时段（半价）
统计期内高峰时段调用占比：9%
```

### 计价规则（两家供应商完全不同，别混）

**内置价格表**（元 / 百万 token，已写进 `pricing.py`）：

| 模型 | 输入(命中) | 输入(未命中) | 输出 | 分峰谷？ |
|---|---|---|---|---|
| `mimo-v2.6-flash` | 0.02 | 1.00 | 2.00 | **否**（单一价） |
| `mimo-v2.6-pro` | 0.025 | 3.00 | 6.00 | 否 |
| `mimo-v2.6-pro-ultraspeed` | 0.25 | 30.00 | 60.00 | 否 |
| `deepseek-flash` | 0.02 / 0.04 | 1 / 2 | 4 / 8 | **是** |
| `deepseek-chat`（旧名，按 flash 计） | 0.02 / 0.04 | 1 / 2 | 4 / 8 | 是 |
| `deepseek-v4-pro` | 0.15 / 0.30 | 4.5 / 9 | 13.5 / 27 | 是 |

- **MiMo 不分时段**，全天一个价
- **DeepSeek 分峰谷**：高峰 = 北京时间**周一至周五 9:00-12:00、14:00-18:00**
  （法定节假日无法本地判断，按工作日算，方向是略高估，不会低估）
- 高峰价是空闲价的 **2 倍**

**所以计价必须逐条按「每条记录自己的模型 + 自己的时间戳」查价**，
用统一单价乘总量一定错。

**未匹配到价格的模型**：按 DeepSeek 最贵档兜底，并在报表底部明确警告
`⚠️ 未匹配到价格：xxx，这些按最贵档估算，费用偏高` —— **不再静默估算**。

### 新增/改动的文件

| 文件 | 说明 |
|---|---|
| `pricing.py` | **新增**。价格表 + 峰谷判断 + 计价 + 金额格式化（不依赖 AstrBot） |
| `report.py` | **新增**。`/token 详细` 报表渲染（不依赖 AstrBot） |
| `stat_store.py` | 新增 `rows_between` / `by_session` / `recent_rows_for_avg` / `span_days` |
| `main.py` | 新增 `_detail_text` / `_prices` / `_max_sessions`，版本 1.0.0 → 1.1.0 |
| `_conf_schema.json` | 新增 `max_sessions_shown`、`prices_json` |
| `tests_selfcheck.py` | 117 项 → **约 160 项**（新增 [12] 花费报表专项） |

### ⚠️ 本轮踩的坑（都实际踩了，别再踩）

**坑 A：`object` 类型的配置项在 4.14.6 上会炸**

我一开始把价格表做成 `"type": "object"`，结果插件加载直接失败：

```
File "astrbot_config.py", line 82, in _parse_schema
    _parse_schema(v["items"], conf[k])
KeyError: 'items'
```

根因（`astrbot_config.py:79-83`）：框架的 `object` 类型**必须带 `items` 子 schema**，
不能放自由度高的嵌套字典。

**修法**：改成 `"type": "text"`，存 JSON 字符串，代码里 `json.loads` 解析。

**坑 B：插件内多文件必须用相对导入**

`report.py` 里写 `from pricing import ...`（绝对导入），但插件是按包加载的：

```
ModuleNotFoundError: No module named 'pricing'
```

**修法**：用 try/except 双路导入 ——
```python
try:
    from .pricing import ...   # AstrBot 包加载
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pricing import ...    # 脚本直接跑（自测）
```

**坑 C：`\b` 词边界对中文不生效**

`/token详细`（不带空格）解析不出来，因为 `re.sub(r"^token\b", "", ...)` 里的
`\b` 在中文字符前不成立。**改用 `^token(?![a-z0-9])`**。

**坑 D：自测通过 ≠ 线上能加载**

上面 A/B/C 三个坑，**全部是自测 100% 通过、但重启 AstrBot 才暴露的**。
教训：改了插件结构（新增文件/改 schema）**必须真重启验证**，不能只看自测。

### 缓存命中率的真相（重要，别再做无效优化）

**用户问「怎么提高缓存命中」，实测结论是：聊天场景下 33% 基本就是天花板。**

按模型看：

| 模型 | 次数 | 命中率 |
|---|---|---|
| deepseek-chat | 1,394 | 35.3% |
| mimo-v2.6-flash | 407 | 23.0% |

按会话看（差别巨大）：

| 会话 | 单次输入 | 命中率 |
|---|---|---|
| 1121732680 | ~64,000 | **19.0%** |
| 946241512 | — | 54.8% |
| 739980532 | ~19,360 | **69.7%** |

**规律：输入越大，命中率越低。**

**逐条明细暴露的真相**（改完上下文后仍然）：

```
命中=6,144 未命中=18,075  → 25.4%   mimo
命中=6,144 未命中=18,214  → 25.2%   mimo
命中=7,040 未命中=10,918  → 39.2%   deepseek
命中=2,048 未命中=   725  → 73.9%   mimo  ← 小请求命中率最高
```

**命中值始终卡在 6,144 不动** —— 只有「系统提示 + 人格 + 工具定义」那约 6K
被缓存，**后面 200 条历史一条都没命中**。

**为什么**：缓存是**前缀匹配**。历史块在 prompt 中间，且每条消息都带
发送者昵称 + QQ 号；群里只要换个人说话、或内容不同，前缀就变，后面全失效。
这是聊天场景的本质，不是配置问题。

**测算过的理论上限**（基于真实数据）：

| 目标 | 花费 | 降幅 |
|---|---|---|
| 现状 33% | ¥34.37 | — |
| 命中率 50% | ¥25.77 | -25% |
| 命中率 60% | ¥20.87 | -39% |
| 命中率 75% | ¥13.52 | -61% |

但把命中率做到 75% 意味着历史几乎不能变，**违背聊天机器人的基本需求**，不划算。

### 实际做的优化（两项，已验证）

| 配置 | 改动 | 文件 |
|---|---|---|
| `include_timestamp` | `true → false` | `data/config/astrbot_plugin_group_chat_plus_config.json` |
| `private_include_timestamp` | `true → false` | 同上 |
| `max_context_messages` | `-1 → 200` | 同上 |

**为什么要关时间戳**：历史消息原来带 `%Y-%m-%d 周X %H:%M:%S` 秒级时间戳，
**每秒都不同**，让整个历史块永远无法命中缓存前缀。而秒级时间戳对「读空气」毫无价值。

**实测效果**（改后 7 分钟真实数据）：

| 指标 | 改前 | 改后 | 变化 |
|---|---|---|---|
| 输入中位数 | 55,974 | **18,033** | **-68%** |
| 输入 P90 | ~63,000 | 24,167 | -62% |
| 输入最大 | 73,868 | 27,911 | -62% |
| 单次成本 | ¥0.0191 | **¥0.0150** | **-21%** |

换算：按 1000 次调用算，¥19.11 → **¥15.01**。

> **注意**：`max_context_messages` 是「每轮喂给 AI 的历史条数」，
> 和 `custom_storage_max_messages`（消息缓存池大小）**是两个不同的东西**，
> 别搞混。我之前就搞混过一次。

### 顺带修掉 gcp 的一个真 bug

`data/plugins/astrbot_plugin_group_chat_plus/main.py:11262`：

```python
logger.info(f"    - audio_urls 数量: {len(req.audio_urls) if req.audio_urls else 0}")
```

4.14.6 的 `ProviderRequest` **没有 `audio_urls` 字段** → `AttributeError` →
**整个 `on_llm_request` 钩子崩掉**。

**这个 bug只在 `enable_debug_log = True` 时才触发**，所以一直没暴露。
已改成 `getattr(req, "audio_urls", None)` 兜底。

> 顺带说明：调试开关的正确配置键是 **`enable_debug_log`**，
> 不是 `debug_mode`（后者不存在，框架会把它从配置里删掉并打一行日志）。

### ⚠️ 尚未验证的部分（诚实记录）

**`/token 详细` 从未在真实 QQ 聊天里触发过。**

数据、渲染、计价全部验证过（直接连真实数据库跑），但**指令入口没有实测**。
同一类问题之前也遇到过（`/token` 首版）。

想确认的话：在 `821537482` 群发一次 `/token 详细`，看日志即可。

---

## 零之九、QQ 语音通话方案（2026-10-26，**已成功打通**）

### 一句话

**QQ 语音通话跑通了：LuckyLillia 有头模式 + UIA 鼠标点击自动接听。**
从 NapCat 切换到 LLBot、申请 Auth Token、解决多账号共存，全部实测完成。
下面记的是**每一步怎么成的、以及每一个坑**，别再重走。

> ⚠️ 中途我曾错误地判定「方案不可行、只能你拨它接」，**那是错的**，已推翻。
> 真正卡住的从来不是技术，而是 **NapCat 无头模式没有窗口**。

### ✅ 最终可行的完整方案（2026-10-26 实测）

```
① 停掉 NapCat（无头模式，永远不会有窗口）
② 用 LuckyLillia 有头模式（headless=false）拉起真实 QQ.exe
③ 在它的「系统配置」里填 auto_login_qq = 3862642627
④ 机器人号就有了真实 QQ 窗口 → 来电会弹出独立的「语音通话」窗口
⑤ tools/call_auto_answer.py 轮询该窗口，鼠标点击「接听」→ 接通
```

**实测已验证的窗口结构**（这是整件事的钥匙）：

```
窗口标题精确 == '语音通话'，class = Chrome_WidgetWin_1
  [50000] '接听'  enabled=1  IsOffscreen=0  坐标 (635,689)-(675,729)
  [50000] '挂断'  enabled=1
  [50020] 'OlY'                    ← 来电人
  [50026] '邀请你语音通话'
```

### 🔑 Auth Token（免费，但要人工审核）

LuckyLillia 新版启动需要 Auth Token。**免费、开源（GPL v2）**，但要人工审核。

| 项目 | 内容 |
|---|---|
| 授权页 | **https://auth.luckylillia.com**（国内备线 https://llbot.wumiao.wang） |
| 登录方式 | GitHub 或微信扫码，**scope 仅 `read:user user:email`** |
| 审核要求 | 填 **Bot 用途（≥30 字）** + **附件截图（1~3 张，每张≤2MB）** |
| ⚠️ 截图禁忌 | **必须是「你自己机器人的使用截图」，不能是 LLBot 界面截图** |
| 审核速度 | 官方答复：**一般当天处理完** |
| 加分项 | 附上你的 Bot GitHub 链接更容易过 |

**实测通过的填法**（照抄即可，参考 issue #875）：

```
个人 QQ 机器人，部署在自己家里的 Windows 11 电脑上。
协议端：LLBot（本机运行，仅监听 127.0.0.1，不对公网开放）
应用端：AstrBot 4.14.6 + NapCat，OneBot v11 反向 WS
用途：在本人和朋友的几个 QQ 群（每个群 50 人以内）里做闲聊助手，
支持跨群记忆、点歌翻唱、表情包、语音回复等功能。
完全自用，不做商业用途，不对外提供服务，不批量加群。
开源记录：https://github.com/OIY916/astrbot_plugin_qq_user_memory
```

**2026-10-26 实测**：用户账号 `OIY916`（注册 302 天）提交后**当天通过**，
拿到的 Token 是 JWT，`exp` 解出来是 **2126 年**（≈100 年不过期）。

> Token 存放位置：`<LLBot目录>/data/auth_token.txt`（纯文本，一行）

### 📦 LuckyLillia 安装（实测步骤）

```
下载 v8.3.2 Desktop（65,767,668 字节）
https://github.com/LLOneBot/LuckyLilliaBot/releases/download/v8.3.2/LuckyLillia-Desktop-win-x64.zip
SHA256 = 38fde3cc61d76fc9bfcfabcc7b0b362a5c938016d8b57a1665d4173492d3981f
```

- 解压出 `LuckyLillia.exe` + `bin/`，本机装在 `E:\qq-bot\runtime\LL-test`
- **`bin/llbot/default_config.json` 要改**：`webui.port` 从 3080 改 **6198**（3080 被 DSH 占）
- 反向 WS 配置：`ob11.connect` 里 `type=="ws-reverse"` → `enable=true`,
  `url="ws://127.0.0.1:62000/ws"`, `messageFormat="array"`
- 关键配置在 **`app_settings.json`**：

```json
"qq_path": "D:\\qq9\\QQ.exe",
"auto_login_qq": "3862642627",   ← 机器人号
"auto_start_bot": true,
"headless": false                ← 必须 false！true 就没窗口了
```

### 🔀 多账号共存（用户实测确认可行）

**同一台电脑能同时登录两个 QQ 号**，靠 `--user-data-dir` 隔离：

```
你的大号 OIY   → C:\Users\Administrator\AppData\Roaming\QQ（默认）
机器人号       → LuckyLillia 自己拉起（也用默认，但能共存）
```

实测：LuckyLillia 拉起的进程（parent 为空那种）有**独立可见窗口**，
和用户手开的 QQ 进程**互不干扰**，两个号都能正常收发。

### ⚠️ 关键坑（全是实打实踩的，别再踩）

**坑 A：`InvokePattern` 在 Electron 上会骗人**

```python
# 看起来能用，实际调用必失败：
pat = element.GetCurrentPattern(UIA.UIA_InvokePatternId)
pat.Invoke()   # ❌ 抛异常，但按钮照样报告 IsEnabled=1、Pattern「可用」
```

QQ 是 Electron 应用，按钮是**自绘**的，UIA 报告的所有 Pattern（Invoke /
LegacyIAccessible / Toggle / SelectionItem）**全部"可用"但全部无效**。

**正解：读 `CurrentBoundingRectangle` 取中心坐标，用鼠标真实点击。**

```python
br = element.CurrentBoundingRectangle
cx, cy = (br.left + br.right) // 2, (br.top + br.bottom) // 2
user32.SetCursorPos(cx, cy)
user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
```

实测坐标：接听按钮 `(635,689)-(675,729)` → 中心 `(655,710)`，**一击命中**。

**坑 B：`NapCat` 的 `bypass.window` 不是窗口开关**

我曾以为把它设成 `true` 就能出窗口。**读完源码后确认：错。**
那 6 个字段（`hook`/`window`/`module`/`process`/`container`/`js`）只是
**WebUI 配置的类型校验白名单**，只检查是不是 boolean，**不控制窗口**。

NapCat 无论怎么配置都是无头（无头 = 直接 `import()` 加载 `QQNT.dll`，
**根本不创建任何 HWND**）。实测：`window:true` 后依然 0 窗口。

**坑 C：`pywinauto` 的 `Desktop().windows()` 单次 8.7 秒**

被桌面 `Program Manager`（Progman）拖死，**单独这一个窗口就 9.3 秒**。
来电只响 20~30 秒，用它必然漏接。

**正解**：先 Win32 `EnumWindows`（**0.4ms**）按标题筛 hwnd，再走 UIA 读按钮。
→ 对方 `call_agent.py` 每 0.6 秒轮询整个桌面，**同样会踩这个坑**
（它注释里也写了「一轮扫描超过 20 秒」，说明作者踩过但没从根上解决）。

**坑 D：`UIA_ControlTypePropertyId` 是 30003，不是 729**

写错的话 `FindAll` **永远返回 0 个按钮且不报错**（静默失败，最难查）。

**坑 E：`NapCatWinBootMain.exe` 拉起的 QQ 也没有窗口**

试过 `launcher.bat -q 3862642627`，它能注入 `D:\qq9\QQ.exe`、能免扫码登录、
消息收发全正常，但 **17 个窗口全部不可见**（纯后台注入）。
启动参数 `--enable-logging -q <号>` 本身就不带 GUI 语义。

**坑 F：手机登着同一个号时，电脑端不弹来电**

QQ 会把来电推给手机，电脑端**不弹窗不响铃**。测电话前必须让手机退出该号。

### 🎨 RVC 音色更换 + 音质诊断（2026-10-26 晚，用户实测「唱歌还不错」）

#### 起因

用户反馈电话/语音**「发闷」**。这不是主观错觉，频谱分析定位到了确切原因。

#### 诊断方法与数据（可复现）

用同一句 `哎，我在呢，听得清吗`，对「RVC 转换前」和「转换后」做 FFT 分频段统计：

| 环节 | 低频 0-1k | **中频 1-4k** | 高频 4-8k | 空感 8-16k |
|---|---|---|---|---|
| **MiMo TTS 原始**（RVC 输入） | 40.0% | **35.0%** | 14.4% | 10.6% |
| **youzhanv2-xi 转换后**（旧默认） | **62.1%** | **15.2%** ⬇ | 16.6% | 5.5% |

**结论：RVC 把中频削掉 20 个百分点、低频抬了 22 个百分点** —— 这就是「闷」的量化解释。
人声清晰度主要靠 1~4kHz，被削必然闷。

> ⚠️ 重要：**调 vc_single 参数几乎无效**。实测 5 组参数
> （`rms_mix_rate` 1.0→0.0、`protect` 0.33→0.45、`filter_radius` 3→2），
> 中频最多从 18.8% 拉到 19.7%，**听感无差**。
> 「闷」是这个模型本身的音色特性，**换模型才是正解，不是调参数**。
> （另：`youzhanv2-xi.pth` **没有配套 .index**，所以 `index_rate` 调了也没用。）

#### 解决：换成 `nikou48K1999.pth`

用户从网盘下载了 56 个模型（`E:\game\rvc模型\106nvsheng`），
其中 **15 个带配套 index**，单独整理到 `E:\game\rvc模型\整理后`，
并压缩成 `<模型名的pth和index双全.zip`（`E:\game\rvc模型\压缩包`，2.4GB）。

实测 16 个模型的频谱排名（中频从高到低 = 从最不闷到最闷）：

| 排名 | 模型 | 低频 | **中频** | 空感 | 峰值 |
|---|---|---|---|---|---|
| 🥇 1 | **shaoluosusu** | 41.0% | **30.2%** | 10.6% | 0.49 |
| 🥈 2 | **nikou48K1999** ← 已选用 | 43.2% | **27.9%** | 12.6% | 0.52 |
| 🥉 3 | Soyo_Nagasaki_e100_s6000 | 45.5% | 26.3% | 8.8% | 0.37 |
| 4 | 雾雾の少萝音06(1) | 42.6% | 25.6% | 7.5% | 0.48 |
| 5 | 雾雾の少御音05(1) | 47.6% | 25.5% | 13.7% | 0.54 |
| … | … | | | | |
| 15 | 御姐多多 | 33.8% | 19.0% | 28.0% | 0.36 |
| 16 | 七妹1000模型 | 36.2% | 18.7% | 24.9% | 0.44 |

**参照基准**：原始 MiMo TTS 输入中频 **27.6%**；旧模型 youzhanv2 **18.8%**。

`nikou48K1999` 中频 27.9%，**已追平原始输入**，比旧模型高 9.1 个百分点。
（`shaoluosusu` 更高但用户先选了这个。）

#### 改了哪 4 个地方（共 7 处配置 + 9 处代码默认值）

| 位置 | 文件 | 字段 |
|---|---|---|
| 说话 | `data/config/astrbot_plugin_rvc_tts_config.json` | `model` |
| 唱歌 | `data/config/astrbot_plugin_cover_sing_config.json` | `default_model` |
| 通话 | `data/config/astrbot_plugin_volc_voice_call_config.json` | `rvc_model` |
| 电话服务 | `tools/call_tts_engine.py` | `RvcEngine.__init__` 默认值 |

代码默认值也同步改了：`astrbot_plugin_cover_sing/main.py`(4处)、
`astrbot_plugin_rvc_tts/main.py`(2处)、`astrbot_plugin_volc_voice_call/main.py`(3处)。

**备份**：`backups/20261026_212807_pre_model_switch/`（配置 + 3 个插件的 main.py）

#### 别名表调整（`cover_sing/main.py` 的 `MODEL_ALIASES`）

差点踩坑：批量替换把 `"youzhan": "youzhanv2-xi.pth"` 也改成了 nikou，
会导致「说 youzhan 却出 nikou 的音色」。已修正为：

```python
"nikou": "nikou48K1999.pth",        # 新默认
"nikou48k": "nikou48K1999.pth",
"youzhan": "youzhanv2-xi.pth",      # 旧别名保留，随时能切回去
"youzhanv2": "youzhanv2-xi.pth",
```

#### 用户验证结果

- 删干净曲库（`cover_service/songs`）和缓存（`cover_service/cache`）后重新点歌
- 完整链路跑通：`长大别害怕 - 奚缘`，`cache=MISS`（真的重新下载了）
- 日志确认用的是新音色：`[cover_sing] 识别到点歌请求: 长大别害怕 (音色 nikou48K1999.pth)`
- **用户反馈：实测下来唱歌的还不错**

#### 📁 新增工具

| 文件 | 作用 |
|---|---|
| `tools/rvc_ab_test.py` | 单模型多参数 A/B（证明调参数无效） |
| `tools/rvc_batch_test.py` | **多模型批量对比**，出频谱排名 + wav |
| `tools/organize_rvc_models.py` | 按模型名建文件夹，pth+index 配对 |
| `tools/zip_rvc_models.py` | 每个模型单独打 zip |
| `tools/_gen_tts_wav.py` | 用主 venv 生成测试音频（RVC Runtime 没 httpx） |

**对比结果**：`C:\Users\Administrator\Desktop\RVC音色对比\`（16 个 mp3 + 对比报告.txt）

#### ⚠️ 这个模块踩的 4 个新坑

**坑 J：`Config()` 内部跑 argparse，会吃掉脚本参数**

`rvc_batch_test.py --limit 3` 报 `unrecognized arguments`。
因为 RVC 的 `configs/config.py` 里 `Config.__init__` 调 `arg_parse()` →
`parser.parse_args()` 读的是全局 `sys.argv`。

修法：调用配置前临时清空 `sys.argv = [sys.argv[0]]`，构造完再恢复。

**坑 K：`weight_root` 是环境变量，不是 Config 属性**

`infer/modules/vc/modules.py:100` 里是
`person = f'{os.getenv("weight_root")}/{sid}'`。
不设环境变量会拼出 `None/xxx.pth`，报 FileNotFoundError。

必须设的 4 个：`weight_root` / `rmvpe_root` / `index_root` / `outside_index_root`，
全部指向 `<RVC_Core>\assets\{weights,rmvpe,indices,indices}`。

**坑 L：import `configs` 前必须先 chdir + 插 sys.path**

```python
os.chdir(RVC_ROOT)              # 先切目录
sys.path.insert(0, RVC_ROOT)    # 再插路径
from configs.config import Config   # 最后才 import
```

顺序错了报 `ModuleNotFoundError: No module named 'configs'`。

**坑 M：index 文件名匹配不能用「规范化 stem」**

我一开始把 `雾雾06.index` 规范化成 `雾雾` 去匹配，结果匹配不上
（报 `faiss ... could not open ...雾雾06.index`，其实**文件明明存在**）。
改成「映射表 key 直接做包含匹配」才对。

**顺带：`X-Cost-Seconds` 响应头很有用**

RVC 服务端返回的头 `X-Cost-Seconds` 直接给服务端真实耗时，
比客户端测总时间准。实测拆解出的结论：
**RVC 转换本身只要 0.36 秒**（模型已加载时），
之前测的 2~4 秒大头在 **ffmpeg 编码 + 服务排队**，不在推理。

---

### 🛠 留下的工具

| 工具 | 作用 |
|---|---|
| `tools/call_auto_answer.py` | **自动接听**（鼠标坐标点击，已实测接通） |
| `tools/call_uia_probe.py` | 只观察不点击的 UIA 探针（排查窗口结构用） |
| `tools/call_watch_multi.py` | 多路监视（日志/窗口/网络三合一） |

### 🎉 对话链路已打通（2026-10-26，实测 14 轮连续对话）

**实测通话记录（真实日志，未编辑）：**

```
[识别] '喂，你好。'        → [回复] '你好你好，听得清吗'
[识别] '听得清。'          → [回复] '那就好，啥事儿啊'
[识别] '嗯，我想你了。'     → [回复] '哈哈，我也想你，咋突然这么肉麻'
[识别] '你今天怎么样？'     → [回复] '还行，就是有点累，你呢'
[识别] '我觉得还行啊。'     → [回复] '那就好，晚上吃的啥'
[识别] '还不错吧，厨子。'   → [回复] '哈哈，自己下厨啊，可以'
[识别] '我靠！'            → [回复] '哎哟，咋了，吓我一跳'
[识别] '嗯。'              → [回复] '你这半天就嗯啊，困了'
```

**端到端延迟 2~4 秒**（录音 → ASR 0.8s → LLM 1s → TTS 1.2s → 播放）。

### 完整链路（每一环都实测过）

```
对方说话
  → QQ 播到「扬声器 (2- High Definition Audio Device)」（pycaw 探测到该端点有 7 个音频会话）
  → WASAPI 环回录音 16k（soundcard, include_loopback=True）
  → VAD（能量 + 自适应噪声底）
  → MiMo ASR    实测 0.81s   ← 坑：不是 /audio/transcriptions
  → DeepSeek    电话模式人设（1~2 句 / 40 字内 / 不用句号）
  → 火山 TTS    实测 1.22s 合成 1.98s 音频（pcm 24000Hz）
  → 写入 CABLE Input (VB-Audio Virtual Cable)
  → QQ 麦克风（CABLE Output）→ 对方听到
```

### 三个实测数据（关键）

| 环节 | 实测值 |
|---|---|
| VB-CABLE 虚拟线 | 往 CABLE Input 播 440Hz，从 CABLE Output 录回 **peak=0.2500**（无损） |
| TTS 合成 | "你好，我是测试语音" → **1.98s 音频 / 1.22s 耗时** |
| ASR 识别 | 同上音频 → **"你好，我是测试语音。" / 0.81s**（完全正确） |

### ⚠️ 音视频链路的新坑（都踩了）

**坑 G：MiMo ASR 不是 OpenAI 标准接口**

调 `POST /audio/transcriptions` → **404 Not Found**。

正解：走 `POST /chat/completions` + `input_audio` 消息格式：

```python
payload = {
    "model": "mimo-v2.5-asr",
    "messages": [{"role": "user", "content": [{
        "type": "input_audio",
        "input_audio": {"data": f"data:audio/wav;base64,{b64}"},
    }]}],
}
```

依据：`astrbot/core/provider/sources/whisper_api_source.py:104-131` 的
「MiMo ASR (TokenPlan)」分支 —— 这正是 STATUS.md 记过的、AstrBot **核心文件被 patch**
的那一处。

**坑 H：`soundcard` 会刷 `data discontinuity in recording` 警告**

无害，是 WASAPI 缓冲不连续的提示。不影响识别，但日志会变吵。
可考虑 `warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)`。

**坑 I：电话模式绝不能用群聊人设**

群里的人设要求「2~4 行、每行一句话、不限字数」，电话里会变成一长串独白。
必须单独写 `PHONE_SYSTEM`（本机 `tools/call_loop.py` 里已写好）：
1~2 句、40 字内、口语化、禁用括号星号旁白、不用句号结尾。

### 📁 新增的五个工具（都在 `tools/`）

| 文件 | 作用 |
|---|---|
| `call_audio.py` | 音频底层：`LoopbackRecorder`（环回）、`CablePlayer`（进虚拟线）、`selftest()` |
| `call_asr_tts.py` | ASR/TTS 客户端，凭证从 `cmd_config.json` 读；含 `selftest()` |
| `call_loop.py` | **对话主循环**：VAD → ASR → LLM → TTS → 播放 |
| `call_auto_answer.py` | 自动接听（鼠标坐标点击） |
| `which_endpoint.py` | pycaw 探测「QQ 往哪个端点播」（从 ohwhale 抄的，实测可用） |

**自检命令（都不依赖 AstrBot 运行）：**

```bash
cd /e/qq-bot
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./astrbot-venv/Scripts/python.exe tools/call_audio.py
PYTHONIOENCODING=utf-8 ASTRBOT_ROOT='E:\qq-bot' ./astrbot-venv/Scripts/python.exe tools/call_asr_tts.py
```

**实际打电话（两个进程同时跑）：**

```bash
# 窗口 1：自动接听
PYTHONIOENCODING=utf-8 ./astrbot-venv/Scripts/python.exe tools/call_auto_answer.py

# 窗口 2：对话循环
PYTHONIOENCODING=utf-8 ASTRBOT_ROOT='E:\qq-bot' ./astrbot-venv/Scripts/python.exe tools/call_loop.py
```

### ⚙️ VAD 参数（实测调优值，别乱改）

| 参数 | 值 | 依据 |
|---|---|---|
| 静音判定 | **0.90s** | 0.55s 太急，一句话中间换气就被截断 |
| 预录缓冲 | **0.45s** | 防止吞掉第一个字 |
| 打断阈值 | 3.0 倍能量 | 2.0 时环境噪音就误触发 |
| 最短语音 | 0.25s | 滤掉咳嗽、键盘声 |
| 单句上限 | 18s | 防止卡住 |

### 🔀 RVC 音色运行时切换 + 女声版优先选歌（2026-10-26 深夜）

#### 一句话

**现在可以在群里用 `/换音色` 随时换 RVC 音色（说话/唱歌/电话同时生效，不用重启），
点歌时会自动优先选女声翻唱版，并先说一句「只能唱女声版的」。**

#### 一、音色运行时切换（`/换音色`）

**为什么要做**：用户原话「能让我切换 rvc 的模型吗，因为有些地方我感觉没原来的那个模型好」。
之前模型写死在 3 个插件配置里，改一个音色要动 3 个文件 + 重启 AstrBot。

**做法：单一事实来源**

```
data/rvc_model.txt      ← 就一行，存模型文件名（如 nikou48K1999.pth）
        │
        ├→ astrbot_plugin_rvc_tts        群聊说话
        ├→ astrbot_plugin_cover_sing     唱歌
        └→ volc_voice_call + tools/call_tts_engine.py   电话
```

三个插件都改成：**优先读 `rvc_model.txt`，读不到才退化到各自配置里的旧值**。
所以改这个文件 = 全局生效，**每轮都重读，不用重启**。

**群聊指令**（`cover_sing` 插件内）：

| 指令 | 作用 |
|---|---|
| `/换音色` | 看当前用什么 |
| `/换音色 列表` | 看可选（**只列 3 个**，见下） |
| `/换音色 模型名` | 切换（支持模糊匹配，不用写 `.pth`） |
| `/当前音色` | 快速查看 |

**`/换音色 列表` 只显示这 3 个**（用户明确要求，2026-10-26）：

```
· youzhanv2-xi.pth    （用户原来用的）
· shaoluosusu.pth     （频谱测试中频最高 30.2%）
★ nikou48K1999.pth    （当前默认，中频 27.9%）
```

> ⚠️ 列表只显示 3 个，但**切换仍支持全部 60 个模型**（文件都在
> `RVC_Core\assets\weights\`）。用户没要求限制切换范围。

**命令行工具**：`tools/rvc_model_manager.py`

```bash
./astrbot-venv/Scripts/python.exe tools/rvc_model_manager.py show          # 查看
./astrbot-venv/Scripts/python.exe tools/rvc_model_manager.py list          # 全部 60 个
./astrbot-venv/Scripts/python.exe tools/rvc_model_manager.py set 微甜       # 切换
```

它切换时会**同步回写 3 个插件配置文件**（保持两边一致，避免看着混乱）。

#### 二、优先选女声版（解决「男声歌唱出来奇怪」）

**问题**：用户反馈「唱男的声唱出来的歌很奇怪」。

**根因（实测数据）**

| 版本 | 人声基频中位数 |
|---|---|
| 宋冬野《安和桥》原唱（男） | **93~97 Hz** |
| **白允y「女生版」** | **100 Hz** ← 几乎没升调！ |
| 不要玫瑰「治愈女版」 | 180 Hz |
| 我们之前升调后的成品 | 205 Hz |

**关键发现**：**网易云上真实的「女声版」往往并不升调**，而是女声用自己音色唱原本低音。
白允y 版 100Hz 听着自然，我们强行升到 205Hz 反而怪。

**所以方案从「升调」改成「选对版本」**：

| 阶段 | 做法 |
|---|---|
| **选歌** | 歌名带「女生版/女版/女声」→ **+45 分**；「男声版/烟嗓/撕裂」→ **-60 分** |
| **搜索量** | `limit` 从 8 提到 **20**（实测「安和桥」女声版排第 11 位，8 个拿不到） |
| **升调** | **已关闭**（`AUTO_PITCH_ENABLE = False`），因为选对版本就不需要了 |

**用户体验流程**（用户明确要求的话术）：

```
群友: @bot 唱 安和桥

bot（0.4 秒内）：
  找到啦！不过只能唱女声版的《安和桥》，不要玫瑰 唱的，委屈你一下哈～

（几十秒后）
bot：
  唱好了！34.7 秒搞定，觉得好听就给我个赞
  [语音消息]
```

**新增 `/pick` 接口**（`cover_service.py`）：

`/sing` 是同步返回音频的，服务端要几十秒才回包，**中途拿不到任何信息**，
没法及时说「我找到女声版了」。所以拆两步：

```
GET /pick?kw=安和桥     → 0.2~0.4 秒返回 {name, artist, version, candidates}
                         version = female / male / unknown
POST /sing              → 真正下载 + 翻唱（几十秒）
```

插件先调 `/pick` → 说一句 → 再调 `/sing`。

**实测 `/pick` 效果**：

| 搜索 | 选中 | 版本判断 |
|---|---|---|
| 安和桥 | 《安和桥(治愈女版）》- 不要玫瑰 | female |
| 晴天 | 《晴天 (女声版)》- GYBeat | female |
| 十年 | 《十年 (深情女版)》- 淳玥 | female |
| 我记得 赵雷 | 《我记得》- 赵雷 | unknown（原唱无标注） |

**⚠️ 已知限制**：网易云 API **没有歌手性别字段**（`/api/artist/{id}` 里确认过，
字段只有 img1v1Id/topicPerson/picUrl/musicSize/albumSize/briefDesc/picId/
followed/trans/alias/name/id 等，**无 gender**）。所以只能靠歌名关键词判断，
无标注时显示 `unknown`，走原来的「我去找找」话术，不会瞎说。

#### 三、自动升调为什么关掉（`AUTO_PITCH_ENABLE = False`）

代码还在（`_estimate_f0` / `_auto_pitch_shift`，约 50 行），只是默认不跑。

**关掉的理由（三条，都是实测）**：

1. **已被更好的方案取代**：选女声版就不需要升调。实测点「治愈女版」时
   检测到 320Hz ≥ 180Hz 阈值，auto-pitch 压根没触发。
2. **留着反而有害**：万一没找到女声版、落到男声原唱上，它会升 6 个半音，
   产出的正是那个已验证「很怪」的效果。**不升调反而更接近真实**（白允y 100Hz 就很好听）。
3. **检测本身不可靠**：自相关算法会把谐波误判成基频
   （实测 P75 全是 800Hz，那是算法上限）。

**保留代码的理由**：以后若换男声模型，这套逻辑可能重新有用。
需要时把 `AUTO_PITCH_ENABLE` 改成 `True` 即可。

#### 四、话术改动

`REPLY_DONE` 里「做好了！{sec}秒搞定」→ **「唱好了！{sec}秒搞定」**
（用户指出「做好了」听着像在说别的活）。

#### 五、这次踩的坑（都是自己给自己挖的）

**坑 N：`\n` 写进 Python 源码会被解释成真换行**

给插件写 `"a\nb"` 这种字符串时，我用工具生成代码，`\\n` 层层转义后变成真的换行符，
导致 **12 处 string literal 断裂**，语法反复报错修了 6 轮。

**教训**：给源码里写带换行的字符串，**优先用 `chr(10)` 拼接**，不要碰 `\n`：

```python
body = chr(10).join(items)                    # 安全
msg = "标题：" + chr(10) + body               # 安全
```

**坑 O：路径字符串里的 `\r` / `\n` 会被当转义**

写 `r"E:\qq-bot\data\rvc_model.txt"` → 其中 `\r` 是回车、`\n` 是换行，
即使用了 `r""` 前缀，**经过工具层转义后仍会被破坏**，变成
`E:\qq-bot\data<CR>vc_model.txt`。

**教训**：**路径优先用正斜杠** `"E:/qq-bot/data/rvc_model.txt"`，
Windows 下完全可用，且不会有任何转义问题。

**坑 P：`/search` 接口被替换时吃掉了 `except` 块**

用 `str.replace` 拼接新增 `/pick` 接口时，替换文本没有包含原 `/search` 的
`except Exception` 两行，导致 `try` 悬空 →
`SyntaxError: expected 'except' or 'finally' block`。

**教训**：用字符串替换改 Python 源码时，**替换块必须自带完整的 try/except**，
或者改成按行号区间替换（本次最后就是这么修好的）。

#### 六、验证状态

| 项目 | 状态 |
|---|---|
| `/换音色` 指令 | ✅ 已实现，AstrBot 已重载 |
| `/换音色 列表` 只显示 3 个 | ✅ 已实现 |
| 切换三处同步 | ✅ 实测 `set nikou48K1999`，4 个位置全部一致 |
| `/pick` 接口 | ✅ 实测 4 首歌都能正确选女声版（0.2~0.4 秒） |
| 女声版优先提示 | ✅ 用户实测「效果不错可以了」 |
| 自动升调已关闭 | ✅ `AUTO_PITCH_ENABLE = False` |
| 「唱好了」话术 | ✅ 已改，AstrBot 已重载 |

**用户最终反馈（2026-10-26 深夜）**：「效果不错可以了」。

#### 七、当前正在用的音色

```
data/rvc_model.txt = nikou48K1999.pth
```

三个插件配置也都同步成了 `nikou48K1999.pth`。

> 注：`shaoluosusu` **只做过频谱测试（中频 30.2%，最高），从未在群里实装验证过**。
> 文件（57MB pth + 31MB index）都在，随时可以用 `/换音色 shaoluosusu` 切过去试。

---

### ⚠️ 未解决的问题：**耳朵会被其他程序的声音污染**（2026-10-26 用户反馈）

**这是当前电话方案唯一的实质缺陷，用户已决定「算了就这样」。下次要续做，从这里开始。**

#### 现象

打电话时，如果电脑上同时有浏览器放视频、壁纸引擎发声、系统提示音，
这些声音会被**当成「对方在说话」录进去**，送去 ASR 后 AI 会答非所问。

#### 根因（已实测确认，不是猜测）

我们的「耳朵」用的是**整端点环回录音**：

```python
mic = sc.get_microphone(id='扬声器 (2- High Definition Audio Device)',
                        include_loopback=True)
```

录的是**这个扬声器上的全部声音**。实测该端点挂着 6~8 个音频会话：

```
[1] svchost.exe      pid=3964    ← 系统提示音
[0] QQ.exe           pid=3172    ← 我们要的（电话里对方的声音）
[0] msedge.exe       pid=31832   ← 浏览器
[0] wallpaper64.exe  pid=12424   ← 壁纸引擎（会自己发声）
[0] QQ.exe           pid=34088
[0] QQ.exe           pid=38432
```

**决定性实验**（2026-10-26，`/tmp/mix_test.py` 同款逻辑）：
往物理扬声器播 660Hz 正弦（模拟"别的程序在响"），同时从该端点环回录音 →

```
录到: peak=0.5124 rms=0.2093
⚠️ 确认：其他程序的声音会被环回录音录进去
```

#### 已确认的事实（省得重新试）

| 尝试 | 结果 |
|---|---|
| `soundcard` 支持进程级环回？ | ❌ **不支持**。源码里既没有 `PROCESS_LOOPBACK` 也没有 `ActivateAudioInterfaceAsync` |
| `IAudioPolicyConfigFactory`（Win11 per-app 音频路由） | ❌ `CreateObject` 报 **0x80040154 没有注册类**，此 CLSID 在本机不可用 |
| 系统版本够不够？ | ✅ **Windows 11 build 26100**，理论上支持进程级环回（19041+ 即可） |
| 用户把默认播放设成 CABLE Input | ⚠️ **属实但无效**。实测物理扬声器上**仍然挂着** QQ/浏览器/壁纸引擎的会话，说明默认设备改动没真正生效（或被应用覆盖） |
| 两条 VB-CABLE 能隔离吗？ | ⚠️ **测试方法有坑**：`sc.get_microphone(id='入梦麦克风...', include_loopback=True)` 会把 Cable A 也当环回对象，导致两条线录到同一个源（`peak` 都是 0.3000）。**要测真实隔离，必须用非环回方式打开虚拟录音端点** |

#### 正确的解决方向（按代价排序）

**方向 1：进程级 WASAPI 环回（最干净，但最难）**

Windows 10 2004+ 的 `ActivateAudioInterfaceAsync` +
`AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`，只录指定 pid 的进程树。

关键要写完整 COM 接口 `IActivateAudioInterfaceCompletionHandler`
（`QueryInterface`/`AddRef`/`Release`/`ActivateCompleted` 四个方法）。
用 comtypes 手工实现容易漏方法导致崩溃 —— 我在
`/tmp` 里曾有个原型（已删）把结构体和常量备过，
**但回调接口没实现完**（文件里显式 raise 了 `PROCESS_LOOPBACK_NOT_IMPLEMENTED`）。
续做时从那里接着写。

**方向 2：让 QQ 单独走虚拟线（改动小，需接受副作用）**

用 Win11 「设置 → 系统 → 声音 → 音量合成器 → 展开 QQ → 输出设备」
把 QQ 单独指到 `CABLE Input`，其他程序仍走物理扬声器。

- 优点：不用写 COM，系统原生支持
- 代价：**电话里对方的声音你听不到了**（除非再从 CABLE Output 同时录一份播到耳机）
- 注意：这是**系统设置**，能不能用程序改没验证过

**方向 3：人声过滤（治标）**

把录到的音频过一道人声检测（如 webrtc VAD 或按频谱判断），
非人声段丢弃。但音乐里的人声照样骗过，**治标不治本**。

**方向 4：接受现状（用户已选这个）**

约定打电话时电脑上不放音乐/视频。已知限制，不修。

#### 顺带说明：**「嘴」这条路是好的，没问题**

用户原话：「只用弄虚拟麦克风就可以了啊」——**完全正确**。

- 嘴（我方说话到对方）：`TTS → CABLE Input → QQ 麦克风（CABLE Output）→ 对方`
  这条**实测无损**（播 440Hz 录回 peak=0.2500），且**不会**被其他程序干扰。
- 只有「耳朵」（听对方）这一侧有污染问题。

**所以下次不要动嘴那条链，只修耳朵。**

### ❌ 仍没做的部分（诚实记录）

- **主动拨出**：没做。实测主窗口有「语音通话」按钮且是 Button 类型，理论上能点，
  但**没验证**（点错会拨给陌生人，先不做）。
- **打断（她说的时候你插话能停下）**：没做。当前是「说完一句才回」的半双工。
- **跨天记忆**：电话进程直连 DeepSeek，**不继承** `astrbot_plugin_qq_user_memory`
  的跨群记忆。要接的话得改成走 AstrBot 的会话。
- **长时稳定性**：只测了约 3 分钟连续对话，**没测长时间通话**。

---

## 一、一句话现状（旧）

---


---

## 零、当前任务（新窗口继续看这里）

### 目标
让机器人**记住每个 QQ 号是谁、说过什么，跨群也能认出来**。

### 需求确认
用户原话：「就是记住那个人的qq号，即使跨群聊也知道他是谁」

### 技术方案（已定）
- **不用 Embedding**（DeepSeek 无 Embedding API，且要省成本）
- 改用 **SQLite FTS5 全文检索**（已实测可用）
- 记录三条线：身份档案 / 事实记忆 / 原文检索

### 关键发现：与 group_chat_plus 的联动机制（已摸清，可直接开发）

`group_chat_plus` 有**官方第三方插件兼容层**，参考源码：
- `main.py:868` `_install_per_plugin_context_tracking()` —— 自动包装所有第三方插件的 `OnLLMRequest` handler
- `main.py:226` `event.set_extra("_gcp_per_plugin_injections", per_plugin)` —— 记录每个插件注入的增量
- `main.py:660` `_merge_per_plugin_prompt()` —— 合并时打标记 `[第三方插件补充 - 插件名]`

**所以我的插件只需标准写法**：

```python
@filter.on_llm_request()
async def inject(self, event, req):
    mem = "..."  # 从数据库查到的记忆
    req.prompt += "\n\n[关于这位群友的记忆]\n" + mem
    # 注意：加到 prompt，不是 system_prompt
```

`group_chat_plus` 会自动检测、标记、合并，**零冲突**。

⚠️ **注意**：必须注入 `req.prompt`，**不要动 `req.system_prompt`**（源码注释说明会重复）。

### 开发计划（预计 30 分钟出最小可用版）

| 步骤 | 内容 | 状态 |
|---|---|---|
| 1 | 插件骨架 + SQLite 三表（身份/事实/原文）+ FTS5 索引 | ✅ 完成 |
| 2 | `record_message` 记录：QQ号、昵称、群号、时间、内容 | ✅ 完成 |
| 3 | 按 QQ 号聚合，跨群查同一人 | ✅ 完成 |
| 4 | `on_llm_request` 注入「这个人在各群说过什么」 | ✅ 完成 |
| 5 | `/记忆 查/搜/记住/忘记/统计` 指令 | ✅ 完成 |

### 实施结果（2026-10-23 14:12 大改版：方向A + 方向B）

**插件已安装并跑在线上**：`data/plugins/astrbot_plugin_qq_user_memory/`
数据库：`data/plugin_data/astrbot_plugin_qq_user_memory/user_memory.db`
线上实测：21 人 / 342 条发言 / **13 条画像**。

#### 核心转变：从「记流水账」到「认识每个人」

改版前的问题：group_chat_plus 每轮已喂 **500 条同群历史**，而记忆插件注入的「最近说过」全部来自
同一个群 —— 模型早就看得见。等于花 700 字 token 复读模型已知的内容，**信息量为 0 甚至为负**。

改版后只保留两类真正不可替代的信息：
1. **跨群信息**（方向A）：这个人**在别的群**说过什么 —— 模型在单个群的上下文里看不到
2. **画像**（方向B）：这个人是什么性格、怎么跟他相处 —— 真人记住的就是这个，不是原文

文件构成：
| 文件 | 作用 |
|---|---|
| `main.py` | 插件主体（记录 / 注入 / 指令 / 每日画像定时任务） |
| `memory_store.py` | SQLite + FTS5 存储层、中文逐字切分、**只注入跨群**的上下文构造 |
| `profiler.py` | **画像生成**：固定 deepseek-chat，每人最近 40 条发话 → 2-3 句性格描述 |
| `_conf_schema.json` | 18 项配置（含 enable_daily_profile / profile_hour） |
| `tests_selfcheck.py` | 存储层自测 65 项，全通过 |
| `tests_integration.py` | 插件级集成自测 43 项，全通过 |
| `tests_profiler.py` | 画像模块自测（含真实 deepseek-chat 调用），全通过 |

#### 方向A：只注入跨群（砍掉同群冗余）

`build()` 新规则：
- **只查「群号 ≠ 当前群」的发言**（`recent_messages_other_groups` / `search_other_groups`）
- **在其他群发言 < 2 条 → 整块不注入**，所以单群用户完全没有记忆注入
- 活跃群列表也过滤掉当前群（避免复读当前群信息）
- 实测：18 个单群用户全部 `build()` 返回 `""`，3 个跨群用户只出现其他群内容，**当前群零泄漏**

#### 方向B：每日画像（固定 deepseek-chat）

- 每天 03:00 自动跑一次（`profile_hour` 可调），也可 `/记忆 更新画像` 立刻触发
- 固定用 `deepseek-chat`（`PROFILE_MODEL` 硬编码，**不允许 mimo 兜底**），key 从 cmd_config.json 读
- 每人取最近 40 条发言 → 归纳成 2-3 句性格画像，写入 `facts` 表 `source='profile'`
- 重复生成会先删旧 profile 再插入，**手动 `/记忆 记住` 的事实保留**
- 实测：13 人成功 / 0 失败，耗时 16.5 秒，约 444 tokens/人（50 人约 2 万 token，几分钱）

画像真实样例（deepseek-chat 生成）：
> 爱折腾技术、精打细算的实用派，说话随性直白，带点自嘲和玩梗的松弛感。
> 跟他相处不用客套，直接聊方案、抛问题就行，他乐意分享经验。

#### 注入样例（跨群用户 OlY，在群 1121732680 说话）

```
[关于这位群友的记忆]
QQ号：2481389971，当前昵称「OlY」
用过的昵称：灿灿的欧尼酱
活跃群：村口情报局promax(821537482)（19条）；肥鱼bot(1105761562)（10条）
【画像】
- 爱折腾技术、精打细算的实用派，说话随性直白，带点自嘲和玩梗的松弛感……
在其他群里说过：
- 肥鱼bot(1105761562) 10-23 13:23：现在登场的是deepseek-chat
- 肥鱼bot(1105761562) 10-23 13:30：ok好了
```

#### 注入预算调优（踩过的坑）

`max_inject_chars` 最初定 200，实测**画像就占 75 字**，剩下的空间全被「…（已省略）」吃光，
具体发言一条都显示不出来。200 → **300** 是平衡点：画像完整 + 2-3 条跨群发言。

### 技术实现要点（踩过的坑）

1. **中文检索**：FTS5 的 unicode61 不切中文，把「今天天气」当一个 token，整串查不到。
   解法：写入时把中文**逐字切分**（`今 天 天 气`），检索词同样切分后按短语匹配，
   就能命中任意子串。「麻辣烫」「关东煮」实测精确命中。
2. **绝不写 `req.system_prompt`**：只写 `req.prompt`，否则和 group_chat_plus 的
   SystemPromptRewriter 重复。**已验证** gcp 的差分兜底逻辑会保留我们的注入块
   （`_extract_third_party_prompt_additions` → `_merge_safe_injected_text_into_prompt`），
   即普通群消息和 @bot 场景下记忆都能到达模型。
3. **`@昵称(QQ号)` 污染检索**：AstrBot 把 @ 提取成 `@昵称(数字)` 塞进 message_str，
   数字会被当成正文关键词。已还原成 `@昵称`，并加了一次性数据迁移（schema v2）
   清洗旧数据（实测清掉 4 条）。
4. **指令别名不能写 `/记忆`**：唤醒前缀 `/` 在 WakingCheckStage 就被剥离，
   `filter.command` 注册 `"记忆"` 即可，加 `/记忆` 别名永远匹配不到。
5. **不要把 `@staticmethod` 放错位置**：给类加方法时注意装饰器继承（沿用旧坑 20）。
6. **all handler 必须消息被唤醒才执行**：未 @ 的普通群消息若 `is_wake=False`，
   WakingCheckStage 会 `stop_event()`。实测 gcp 在读空气场景会置 `is_wake`，
   因此普通群聊消息能正常入库。

### 待观察
- [ ] 真实群聊里 @bot 时「记忆块」是否让回复更连贯（看 `[user_memory] 已注入记忆` 日志）
- [ ] 每人保留 2000 条 / 180 天，长期库大小是否可控
- [ ] 是否需要升级到 LLM 事实抽取（当前只存原文 + 手动 `/记忆 记住`）

### 第一版**不做**的（后续可加）
- ❌ LLM 事实抽取（如自动提炼「张三养了猫」）—— 先只存原文
- ❌ Embedding 语义检索 —— 用 FTS5 关键词检索代替

### 插件名（拟）
`astrbot_plugin_qq_user_memory`，路径 `data/plugins/astrbot_plugin_qq_user_memory/`

### 参考过的第三方插件（用户曾问有没有更好的）
| 插件 | 星数 | 为什么没直接用 |
|---|---|---|
| `LivingMemory` | 364 | 按会话组织，不按人 |
| `星火 Sparking` | 1 | 描述最贴合（「记忆跨群共享」），但星数低 |
| `灵笔司书 Scriptor` | 15 | 有跨平台身份管理，但需验证主键 |
| `qq_personal_memory` | 0 | 按 QQ 号存，最贴需求，但无人验证 |
| `iris_chat_memory`（用户提到） | 21 | 作者已有升级版 `iris_memory`(34★, v3.2.1) |

**结论**：现成插件都无法确认「用 QQ 号做主键」，所以自研。

---

## 一之二、本轮（10-22 ~ 10-23）已完成改动

### 配置最终状态

| 项目 | 值 | 说明 |
|---|---|---|
| 读空气概率 | 全局 10% | `initial_probability: 0.1` |
| **按群概率** | **1105761562 = 80%**，**1121732680 = 30%**，其他 10% | `group_probability_overrides`（自研功能） |
| **决策AI** | **已关闭** | `disable_decision_ai: true`（自研开关） |
| 拟人静默阈值 | 30 | 原 3，太低导致 30% 概率失效 |
| 兴趣关键词 | 13 个（鲸鱼/大白米饭/肥鱼等） | 命中 +30% 概率 |
| **分段回复** | **开启** | log 间隔法，短句快发长句慢发 |
| **表情包概率** | **80%** | `sticker_probability: 80`（自研） |
| 表情包库 | 1220 张（**432 动图**） | 优先发 gif ≤3MB |
| 唱歌 | 开启 | `enable_sing: true` |
| 语音回复 | 20% | `rvc_tts.probability: 20` |
| 识图 | 仅 @ 时 | `mention_only`，MiMo 视觉 |
| 管理员 | 2481389971 | 不绕白名单，与普通群友同待遇 |
| 白名单群 | 693514204 / 821537482 / **1121732680** / **1105761562** | 四群 |
| 白名单私聊 | 2252572250 / 3774572947 | 完整 UMO 格式，见第七节 |
| 模型 | 3 个可选 | 1=MiMo 2=deepseek-flash 3=deepseek-chat |

### 本轮修的 bug（都有日志坐实）

1. **翻唱像原曲** → `HP5_only_main_vocal` 只提主唱，和声残留伴奏轨。改 `HP2_all_vocals` + 自适应响度混音（人声 RMS 目标 = 伴奏×2）。**相关性 0.7585 → 0.2549**
2. **重复回复两次** → `message_id` 在 `send()` 后会变。改用「群号+发送者+歌名+10秒时间桶」冷却键
3. **跨群点同一首歌被拦** → 冷却键缺群号维度。已加
4. **英文歌名被截断**（`Let me go（温柔版）`→`Let`）→ AstrBot 的 `CommandFilter` 按空格切参数。改用 `event.message_str` 全文提取
5. **表情包不发送** → 提示词写死「10% 以内」，改成可配置 80%；**并且我自己写坏过一次 f-string 导致 NameError，每条回复都报错**
6. **30% 概率不生效** → 被「拟人静默模式」（连续3次不回就闭嘴）压制。阈值提到 30
7. **决策AI 拒绝所有回复** → 5 次通过概率 5 次被拒。加 `disable_decision_ai` 开关关闭
8. **deepseek-flash 不回复** → 它是**带推理的模型**，`max_tokens=20` 全被 reasoning 吃掉，content 为空。不是模型名错

### 新增文件

| 文件 | 用途 |
|---|---|
| `E:\qq-bot\start_all.ps1` | 一键启动（四服务） |
| `E:\qq-bot\stop_all.ps1` | 一键关闭 |
| `E:\qq-bot\check_models.py` | LLM 模型自检（检测无效模型/空回复） |
| `桌面\启动QQBot.bat` | 桌面一键启动（GBK 编码） |
| `桌面\关闭QQBot.bat` | 桌面一键关闭 |
| `data/plugins/astrbot_plugin_img_lookback/` | 图片回看（@bot 说「看看刚才的图」） |
| `data/plugins/astrbot_plugin_llm_guard/` | 模型守卫（切模型后自动验证） |
| `cover_service/cache/` | 翻唱结果缓存（199s→0.4s） |

### 翻唱缓存
`/cache/stats` 查占用，`/cache/clear` 清空。同一首歌+音色第二次 0.4 秒返回。

---

## 二、当前运行的服务

| 服务 | 端口 | 状态 |
|---|---|---|
| AstrBot (WebUI) | 6185 | ✅ 运行 |
| OneBot WS | 62000 | ✅ 运行（6199 被 Edge 占用，故改 62000） |
| NapCat | 6099 | ✅ 运行（QQ 3862642627 阿巴阿布） |
| RVC 翻唱/说话服务 | 8899 | ✅ 运行，`logged_in: true` |
| DSH Web GUI | 3080 | ✅ 运行（`node ...@deepseek-ai/dsh/lib/bin.js web`） |
| **DSH 回调接收** | **6187** | ✅ 由 `astrbot_plugin_dsh_bridge` 自建（⚠️ 不能占 6185，那是 AstrBot 自己的） |

**RVC 服务启动命令**：
```
E:\RVC-bsq\RVCMAX_Nvidia_xiaoyuan\RVCMAX_Nvidia_xiaoyuan\Runtime\python.exe E:\qq-bot\cover_service\cover_service.py
```
（环境变量 `COVER_PORT=8899`）

**AstrBot 启动**：
```
E:\qq-bot\astrbot-venv\Scripts\astrbot.exe run
```

---

## 三、核心突破：网易云自动下载（已解决）

### 问题演进
1. 服务端扫码登录 → ❌ 被风控 `8821 安全环境风险`（requests 和 curl_cffi 都试过）
2. 公开音乐 API → ❌ 6 个全部失效
3. 浏览器 cookie 导出 → ❌ MUSIC_U 是 HttpOnly，读不到；profile 被独占锁

### 最终解法 ✅
**Playwright 持久化 profile**（`netease_login_tool.py`）：
- 启动**可见**的 Chrome 窗口，用户手动登录
- 脚本用 `ctx.cookies()` 直接读取 HttpOnly cookie（这是关键，页面 JS 读不到，Playwright API 可以）
- 保存到 `netease_cookie.json`，服务端复用
- profile 永久保存在 `E:\qq-bot\netease_profile`

**已验证**：账号 铲屎官916（VIP 会员），可获取 **无损 FLAC 直链**（1497kbps）

### 如果以后 cookie 失效
```
E:\qq-bot\astrbot-venv\Scripts\python.exe E:\qq-bot\cover_service\netease_login_tool.py
```
会弹出浏览器，重新登录即可。

---

## 四、RVC 翻唱服务（8899）

脚本：`E:\qq-bot\cover_service\cover_service.py`（跑在 RVC 自带 Python 3.9 上）

| 接口 | 说明 |
|---|---|
| `GET /health` | 健康检查 + 音色列表 |
| `GET /login/status` | 网易云登录状态 |
| `GET /search?kw=` | 搜索歌曲 |
| `POST /sing` | **一条龙**：搜索→下载→翻唱→返回 mp3 |
| `POST /cover_path` | 本地文件翻唱 |
| `POST /speak` | MiMo TTS → RVC 变声（说话） |
| `POST /login/qrcode` `/login/check` | 扫码登录（已弃用，风控） |

**音色**（`youzhanv2-xi.pth` 默认，48kHz v2 质量最好）：
`youzhanv2-xi` / `kikiV1` / `keruanV1` / `guanguanV1`

**处理链**：UVR5(HP5) 人声分离 → RVC 转换 → 浮点混音 → 峰值校准 -1dBFS → 48k/320k
**性能**：112 秒歌 ≈ 17 秒；330 秒歌 ≈ 61 秒

---

## 五、AstrBot 插件

| 插件 | 功能 | 状态 |
|---|---|---|
| `cover_sing` | **@bot 点歌翻唱** + `/唱` `/音色` | ⚠️ 刚修复 `_cfg` bug，待群内验证 |
| `rvc_tts` | 语音：`speak_text` 工具（**由模型自己判断该不该念、念什么**）+ 随机 hook（10%）；`/rvc语音 on\|off` | ✅ |
| `group_chat_plus` | 读空气群聊（20% 概率 + 决策AI） | ✅ |
| `meme_manager_lite` | 表情包（19套） | ✅ |
| `universal_tts` | MiMo TTS 引擎 | ✅ |
| `qq_user_memory` | **跨群用户记忆**（QQ号主键 + FTS5 检索） | ✅ 新上线 |
| `token_stats` | token 用量统计（`/token` 指令 + SQLite） | ✅ 新上线 |
| `poke_guard` | 戳一戳防骚扰（6 次/60 秒 → 静默 5 分钟） | ✅ 新上线 |
| `dsh_bridge` | **QQ → DSH 消息桥**（`/dsh 内容` 转发到电脑窗口，仅管理员） | ✅ 2026-10-25 新上线 |
| `live_watch` | **看直播/看视频**（抖音 CDP 截图识图+弹幕+关注，B站截帧） | ✅ 2026-10-25 新上线 |
| `img_lookback` | 让机器人回看上文图片（@bot 说「看看刚才那个图」） | ✅ 已装（文档此前漏记） |
| `llm_guard` | LLM 模型守卫（切换模型后检测可用性，无效回滚） | ✅ 已装（文档此前漏记） |
| `llm_fallback` | **LLM 兜底重试**（MiMo 内容安全过滤时自动换 DeepSeek 重试） | ✅ 2026-10-26 新上线 |

### 点歌触发规则（cover_sing）
- **必须 @机器人**（`event.is_at_or_wake_command`）
- 关键词：`唱` `唱首` `唱一首` `唱个歌` `来首` `来一首`
- 闲聊过滤：`唱歌好难` `别唱` `跑调` 等不触发
- 示例：**@大肥鱼 唱 我记得** → 自动翻唱

---

## 六、MiMo 配置（2026-10-23 已从 TokenPlan 切到普通 API）

### 当前（普通 API Key）
- 入口：`https://api.xiaomimimo.com/v1`
- Key：`<已移除：改用环境变量 MIMO_KEY>`
- 文本模型：`mimo-v2.6-flash`（另有 `mimo-v2.6-pro`、`mimo-v2.6-pro-ultraspeed`）
- TTS：`mimo-v2.5-tts`（音色「冰糖」）
- ASR：`mimo-v2.5-asr`（走 `/chat/completions` + `input_audio`，**不是** `/audio/transcriptions`）

### 旧的 TokenPlan（2026-10-23 额度耗尽，429 quota exhausted）
- 入口曾是 `https://token-plan-cn.xiaomimimo.com/v1`
- Key 曾是 `tp-c8ibvlo54mj22z7ja6e1ob027yqvbjv9z720mysgkl72ohm8`

⚠️ **两种 key 不通用**：`tp-` 开头的只认 token-plan 域名，`sk-` 开头的只认 api.xiaomimimo.com。
用错域名会返回 `401 invalid_key`，**不是** 额度问题。

⚠️ **mimo 所有接口共用一个额度池**：对话、TTS、ASR 任一接口额度耗尽，全部一起 429。
所以对话模型能跑不代表 TTS 能用，要分别实测。

### 换了新 key 后要改的 4 处（都在文件里硬编码）
1. `data/cmd_config.json` → `provider_sources[id=mimo].key` 和 `.api_base`
2. `data/cmd_config.json` → `provider_sources[id=mimo_asr].api_key` 和 `.api_base`
3. `data/config/astrbot_plugin_universal_tts_config.json` → `engines[].api_key` 和 `.api_base`
4. `cover_service/cover_service.py` 第 562-563 行 `MIMO_KEY` / `MIMO_URL`（**CRLF 文件**）

⚠️ 第 4 处最容易被忘——它不在 AstrBot 配置里，是 RVC 语音服务自带的硬编码。

---

## 七、白名单（`platform_settings.id_whitelist`）

### 群白名单（直接写群号即可）
**当前 8 条**（2026-10-26 更新）：

`1105761562`、`1121732680`、`821537482`、`739980532`、`414858556`、`963459350`

### 私聊白名单（必须写完整会话 ID）
`default:FriendMessage:2252572250`
`default:FriendMessage:3774572947`

### ⚠️ 加新群必须同时改两处（否则不生效）

| 位置 | 字段 | 作用 |
|---|---|---|
| `data/cmd_config.json` | `platform_settings.id_whitelist` | **准入**：不在名单的会话被 `stop_event()`，插件全收不到 |
| `data/config/astrbot_plugin_group_chat_plus_config.json` | `enabled_groups` | **读空气准入**：不在列表的群 `_is_enabled()` 返回 False |
| 同上 | `group_probability_overrides` | **该群的读空气概率**（JSON 字符串，不是 dict） |

只改白名单不加 `enabled_groups` → 机器人能响应 @ 和指令，但**不会读空气**。
只改 `enabled_groups` 不改白名单 → **完全不响应**（被准入层拦掉）。

**实测案例（2026-10-26）**：`963459350` 群在加白名单前，日志里被拦了 **51 次**
（`不在会话白名单中，已终止事件传播`）。加完并重启后归零。

### 📌 已知的历史不一致（2026-10-26 发现，用户决定暂不处理）

| 群号 | 在 enabled_groups | 在 id_whitelist | 实际效果 |
|---|---|---|---|
| `693514204` | ✅ | ❌ | **机器人不响应** |
| `946241512` | ✅ | ❌ | **机器人不响应** |

这两个群在 gcp 里配了，但没进 AstrBot 白名单，所以实际完全静默。
用户已知悉并决定**暂不处理**（可能本来就不想让它响应）。

⚠️ **私聊不能只写 QQ 号**。`WhitelistCheckStage` 的判定是：
```python
event.unified_msg_origin not in whitelist and str(event.get_group_id()).strip() not in whitelist
```
私聊的 `get_group_id()` 返回**空字符串**，只能靠 `unified_msg_origin` 命中。

⚠️ **前缀是平台实例 id，不是平台类型名**！`unified_msg_origin` 格式为
`{platform_id}:{message_type}:{session_id}`，其中 `platform_id` 取自
`cmd_config.json` 的 `platform[0].id`（本机是 `default`），**不是** `aiocqhttp`
（那是 `type` 字段/类型名）。写成 `aiocqhttp:FriendMessage:xxx` 会静默失效——
白名单不报错，只是永远匹配不上，表现为私聊完全没回复。

群消息则因为 group_id 有值，写裸群号就能命中，不受前缀影响。

配置位置：`data/cmd_config.json` → `platform_settings.id_whitelist`
⚠️ 改完**必须重启 AstrBot**（`WhitelistCheckStage.initialize` 只在启动时读一次）。

⚠️ **两个配置文件都带 UTF-8 BOM**，用 `json.load` 必须 `encoding='utf-8-sig'`，
写入时保持 `utf-8-sig`，否则 AstrBot 读配置可能出错（见坑 32）。

---

## 八、已知坑（重要）

1. **端口 6199 被 Edge 占用** → 已改 62000
2. **curl 在 git-bash 传中文参数乱码** → 用 Python requests
3. **RVC 的 Config 会解析 sys.argv** → 脚本必须先 `sys.argv = [sys.argv[1]]`
4. **ffmpeg 4.3.2 不支持 `amix=normalize`** → 用 volume + alimiter
5. **pydantic 锁定 ~=2.10.3**（装 qqmusic-api-python 会破坏）
6. **AstrBot 的 `@filter.regex` 不传 match 给 handler** → 自己 re.search
7. **插件 `__init__` 要接收 config** → 否则 `self.config` 报错
8. `whisper_api_source.py` 已 patch 为 MiMo ASR 格式（升级会丢）
9. **网易云 cookie 存两份**：服务的 cookie 在 `cover_service/netease_cookie.json`；`netease_login_tool.py` 却把 cookie 写到**当前工作目录**（从 `E:\qq-bot` 跑就写 `E:\qq-bot\netease_cookie.json`，服务读不到）→ 重新登录后要手动把 json 复制到 `cover_service\`**
10. **改白名单后必须重启 AstrBot** 才生效；WebUI 的「保存」是右下角的悬浮磁盘图标
11. **`@bot 唱 xxx` 会同时触发 `/唱` 指令和自然语言 handler**（AstrBot 把 @bot 开头的消息当斜杠指令）→ 必须靠 token 去重，`stop_event()` 拦不住
12. **`/sing` 出错绝不能返回 200 + JSON** —— QQ 端 ffmpeg 会报 `retcode=1200 Failed to open input`
13. **改插件 `main.py` 后要重启 AstrBot**；日志是 **GBK 编码**，PowerShell 按 UTF8 读会腏码
14. **AstrBot 独占 `astrbot_run.log`** → 直接读会报占用，需共享读：`[IO.File]::Open($p,'Open','Read','ReadWrite')`
15. `cover_service.py` 是 **CRLF** 换行，用字符串替换时要按 CRLF 匹配
16. **去重不能用 `message_id`！** 实测：`event.send()` 发完语音后 `message_id` 会变，导致同一条消息被重新处理一次（重复翻唱两次）。改用「发送者ID + 消息文本 + 10秒时间桶」作去重键。
17. **AstrBot 日志编码是 GBK**，`group_chat_plus` 的 `logger.info("...\u2705...")` 会抛 `UnicodeEncodeError` **直接崩进程**。启动必须带 `PYTHONIOENCODING=utf-8` 和 `PYTHONUTF8=1`
18. **RVC 服务会静默退出**（无崩溃堆栈）。用 `start_all.ps1 watch` 守护模式可自动拉起
19. **`group_chat_plus` 修改后必须重启**，且 **schema 不支持 `dict` 类型**（只支持 int/float/bool/string），写 dict 会导致**整个插件初始化中断**
20. **给 `group_chat_plus` 加方法时小心 `@staticmethod`** —— 插入位置的装饰器会被新方法继承，报 `missing 1 required positional argument: 'self'`
21. **Python f-string 里的 `{变量}` 会立即求值** —— 写提示词模板时如果变量名不存在会抛 `NameError`，且 `.replace()` 补救无效（构造时就崩了）
22. **`.bat` 文件必须用 GBK 编码**；UTF-8 会让 cmd 按 ANSI 解析成一堆碎片命令。字符串里的 `
` 也会被当换行符拆断路径
23. **PowerShell 5.1 的 `-Environment` 参数不存在**（只有 PS7 支持）→ 改用 `$env:XXX='val'; Start-Process ...`
24. **`deepseek-flash` 是带推理的模型**，max_tokens<50 时 token 全被 `reasoning_tokens` 吃光，`content` 为空 → 表现为「机器人不回复」
25. **AstrBot 的 `on_llm_request` 注入应写 `req.prompt`**，写入 `req.system_prompt` 会与 group_chat_plus 的 SystemPromptRewriter **重复**
26. **FTS5 的 unicode61 不切中文** —— 「今天天气」是一个 token，整串查不到子串。解法：入库时中文逐字切分（`今 天 天 气`），检索词同步切分后按短语匹配
27. **AstrBot 把 @ 写成 `@昵称(QQ号)` 塞进 message_str**（`aiocqhttp_platform_adapter.py` 的 at_parts），QQ 号会被当正文关键词污染检索 → 入库前要还原成 `@昵称`
28. **指令别名不能写 `/记忆`** —— 唤醒前缀 `/` 在 WakingCheckStage 就被剥离，`filter.command("记忆")` 即可，加 `/记忆` 别名永远匹配不到
29. **未 @ 的普通群消息 `is_wake=False` 时 WakingCheckStage 会 `stop_event()`** —— 插件 handler 根本不执行。实测 gcp 读空气时会置 `is_wake`，所以普通群消息能入库
30. **私聊白名单必须写完整会话 ID**，且**前缀是平台实例 id（`default`）不是类型名（`aiocqhttp`）** —— 后者写错不会报错，只是永远匹配不上，表现为私聊完全不回复。正确值从 `cmd_config.json` 的 `platform[0].id` 取；群白名单写裸群号即可，不受前缀影响
31. **白名单改完必须重启 AstrBot** —— `WhitelistCheckStage.initialize` 只在启动时读一次 `id_whitelist`
32. **`data/config/*_config.json` 带 UTF-8 BOM** —— 用 `json.load` 要 `encoding='utf-8-sig'`，否则报 `Unexpected UTF-8 BOM`
33. **`Exception: API 返回的 completion 由于内容安全过滤被拒绝(非 AstrBot)`** —— 是**模型供应商端**返回 `finish_reason == "content_filter"`，不是 AstrBot 的 `content_safety` 配置能关的。表现为机器人回一句「AstrBot 请求失败」而不回复。换模型（如 deepseek）或改写触发词可绕开
34. **验证代码假设时要用真实运行值** —— 我曾用 `platform_name='aiocqhttp'` 验证白名单，而运行时 `platform_id` 是 `default`，验证通过但线上失效。凡涉及运行时字符串（会话 ID、群号前缀），必须从日志或配置里取真实值，不能按类型名推
35. **`enabled_groups` 有值时是"白名单"语义** —— 新群只加 `group_probability_overrides` 而忘了加 `enabled_groups`，该群**完全不读空气**（`_is_enabled` 返回 False，`.1` 概率形同虚设）。加群必须两处同步改
36. **`group_probability_overrides` 是 JSON 字符串不是 dict** —— schema 不支持 dict 类型（踩过坑19）。改的时候要 `json.loads` → 改 key → `json.dumps` 写回，别直接当 dict 赋值
37. **`_parse_group_overrides` 是 `@staticmethod`** —— 用它做离线验证时要 `ChatPlus._parse_group_overrides(raw)` 这样调，不能绑实例
38. **gcp 每轮喂 500 条同群历史** —— 所以「注入同群最近发言」是纯冗余，模型早就看得见。跨群记忆插件必须只注入**其他群**的内容，否则 700 字 token 换来零信息
39. **改了插件代码的 `build()` 默认值不算生效** —— `main.py` 会用 `self._cfg("max_inject_chars", 700)` 显式传参覆盖。必须同时改 `_conf_schema.json` 的 default **和** `data/config/<插件>_config.json` 的运行时值（后者优先级更高，改 schema 不会自动同步已存在的运行时值）
40. **注入预算要按「画像长度 + 发言条数」算** —— 画像约 75 字，预算 200 时发言全被「已省略」吃掉。300 是平衡点
41. **AstrBot 插件 handler 里不能假设画像已生成** —— 定时任务凌晨才跑，首次部署时 facts 表是空的，`build()` 要能优雅处理只有 fact 无 profile 的情况
42. **`wait_agent` 返回 `no-active-peer` 不代表成员停了** —— 可能是刚开工还没有可观测状态变更。要查 `.agent-teams/<team>/team.json` 里 member 的 status 和 task 的 updatedAt 判断，别误判后重派任务
43. **gcp 情绪检测分析错了对象**（2026-10-23 已修）—— 原先 `update_mood_from_context(chat_id, formatted_context)` 传的是**整段 500 条历史上下文**，导致关键词永远被大量问句带偏成「疑惑」，跟当前消息无关。改为分析 `event.message_str`（当前这条），长度 <6 才补 300 字历史
44. **情绪关键词在中文里必然误判** —— `"？"` `"怎么"` `"什么"`（疑惑）、`"气"`（生气，命中「天气」「运气」）、`"..."`（无语）太泛化。要么删掉，要么换成 `没看懂/啥意思/真的假的` 这类精确表达。**改完要从「疑惑主导」变成「几乎全平静」**，说明关键词法本身就不适合中文情绪判断
45. **关键词情绪检测是死路** —— 删掉泛化词后 40 条真实消息里 39 条判为「平静」，情绪系统等于没有。正确解法不是加关键词，而是**把情绪行为准则写进人格 prompt，让主模型自己判断**（零额外调用、零延迟）。已实测：同一套准则对「怼你/玩梗/道谢」三种输入给出明显不同的回复风格
46. **情绪提示要给「行为指令」而不是「状态标签」** —— `[情绪参考: 生气]` 是让模型在心里默念，真人不会这样。要写「现在有点不爽，回短一点、可以直接回呛，别顺着对方说」
47. **考据型验证优于假设** —— 我一度以为情绪系统是「空转」，查代码发现它**确实注入 system_prompt**，只是分析对象错+提示写法差。结论要靠日志和源码，不要靠印象
48. **机器人自己输出「唱 xxx」不会触发唱歌** —— 指令需要**新事件**，模型输出只是回复文本；且 `_is_self_message` 会把机器人自己的消息过滤掉。想让 AI 主动唱歌必须用 `@llm_tool`，不是靠 prompt 让它「说出台词」
49. **`@llm_tool` 修饰的函数必须是协程，不能 yield** —— 一旦函数里有 `yield` 就变成异步生成器，`return "值"` 会报 `SyntaxError: 'return' with value in async generator`。工具返回值要回传给 LLM，所以只能 `await` 内部链路、用列表收集产出，再在协程里 `await event.send(r)` 真正发出
50. **工具调用会被自然语言 handler 抢 token** —— `cover_sing` 有 `_claim_token(event)` 事件级去重，`sing_natural` 先跑就抢占了，工具再调 `_do_sing` 会被判「已被其他处理器接手」。加 `from_tool=True` 参数跳过 token 去重
51. **gcp 通过 `context.get_llm_tool_manager().get_full_tool_set()` 取全部工具** —— 这个返回 `func_list.copy()`，所以任何插件用 `@llm_tool` 注册的工具都会被传给模型，**与 `enable_tools_reminder` 无关**（那个只控制是否往 prompt 里塞工具清单文案）
52. **`extract_song_name` 对自然语言很脆弱** —— 实测：`唱首《晴天》` → `晴天》`、`那我唱一首晴天吧` → `晴天吧`。所以让模型「自由发挥说唱歌」必然唱错歌名，必须走工具传干净的歌名参数
53. **工具明明在，模型却不调用 → 先查「工具是否真的传给了模型」** —— 我一开始怀疑模型不听话，实际是没证据。加一个 `@filter.on_llm_request` 临时诊断钩子打印 `req.func_tool` 里的工具名列表，一次就看清：13 个工具里 `speak_text` 确实在。**验证顺序要反过来：先证伪基础设施，再怀疑提示词**
54. **旧的随机语音 hook 会「抢戏」** —— `rvc_tts_hook` 按概率把整条回复替换成语音，它和 `speak_text` 工具是两条独立路径。hook 概率 20% 时会经常先发语音，看起来像「工具没生效」。调低（20→10）给 AI 主动调用留空间
55. **两种语音路径要能从日志区分** —— hook 打的是 `[RVC-TTS] 已发送语音`（main:133 附近），工具打的是 `[RVC-TTS][工具调用] AI 主动念出`。行号不同、关键字不同，排查时不会误判
56. **gcp 的「有人@你」系统提示会把模型带偏** —— 实测同一句话 `说 今天天气很好啊`：
    - 纯文本 → ✅ 调用 speak_text
    - 加上 `[系统提示]注意，现在有人在直接@你…` → ❌ 当成普通聊天回内容
    逐项排查确认：时间戳不干扰、`[At:xxx]` 不干扰，**只有那段系统提示会破坏指令意图**。
    这段提示在 `utils/message_processor.py:353`（gcp 给 @消息生成的标准提示），属核心逻辑，不要为单个插件去改它
57. **"让 AI 念某句话"不该交给 LLM 判断，要用关键词确定性触发** —— ~~模型判断受上下文干扰、还费 token、有延迟~~ → **此结论已被用户否决（2026-10-23 15:26 回退）**。关键词方案实测可跑通（1.5 秒出语音、27 个正负样本全过），但用户明确要求「不要关键词，交由模型思考决定」，已全部移除。**记录教训：技术可行性不等于产品意图，做之前先确认要不要做**
58. **关键词提取要配负面词表，否则误触发率很高** —— 中文里「说/念/读」太常见：`念旧的人`、`说得好`、`读不懂你`、`读后感想` 都会被误判。若将来重新启用关键词方案，`SPEAK_NEGATIVE` 这套词表可复用（已随代码一并删除，可从 git/备份找回）
59. **关键词 handler 要设 `event.call_llm = True`** —— 否则关键词触发语音后，框架还会再调一次 LLM 生成文字回复，群友会看到「语音 + 一条无关文字」。合成失败时要记得改回 False 让 LLM 兜底
60. **回退要连配置一起清** —— 移除 `speak_keyword` handler 后，`_conf_schema.json` 和 `data/config/*_config.json` 里的 `enable_keyword` 都要删，否则 WebUI 会显示一个已经无效的开关。删完用 handler 注册表反查确认：`get_handlers_by_module_name()` 只剩预期的 3 个（hook / 工具 / 指令）
61. **好感度必须对单群用户也注入** —— 方向A 原本让「没有跨群内容的用户 `build()` 返回空」，但实测 **27 人里 24 人是单群用户（89%）**，不给好感度就等于 89% 的人行为毫无差异，好感度系统形同虚设。已在 `build()` 里做分支：跨群内容 <2 条时仍单独返回好感度块
62. **改 `build()` 契约会撞旧测试** —— 改完 `tests_selfcheck.py` 报 `FAIL 单群用户 build 返回空串`。这不是测试写错，而是**契约变更的信号**：要判断新契约是否更合理（这里显然更合理），然后更新断言并补新用例，而不是绕过测试
63. **画像 + 好感度放在一次 LLM 调用里** —— 不要让每人每天跑两次模型。提示词要求输出「画像 ==== 好感数字」两段，用 `re.split(r"^\s*={3,}\s*$", ...)` 拆开，数字再夹到 -20~+20。实测 16 人/22 秒/0 失败
64. **好感度初始 40 是「一般熟」，不是 0** —— 0 意味着"陌生人"，但老群友会表现得很冷漠不合理。40 落在「看心情配合」档，符合真实群友关系。分档阈值：85/65/45/25
65. **DSH 的 Remote 服务返回值常被包一层** —— `ctx.sessionController.list()` 实际返回 `{ items: [...] }` 而不是数组，直接迭代报 `sessions is not iterable`。调 DSH 内部服务前先看 `lib/index.js` 里 `@Remote` 包装层的实现，别信 README 的类型签名
66. **DSH Remote 方法带 signal 的必须传** —— `prompt(request, signal)` 的包装层第一行是 `signal.throwIfAborted()`，漏传直接抛异常。用 `new AbortController().signal` 兜住
67. **`cordis.patch.yml` 新建条目要用 `insert:`** —— `- id: 新id` 是「修改已有条目」语法，会报 `patch: entry "xxx" not found`。新增插件必须写 `- insert:` 再缩进列 id/name
68. **改 DSH 服务端插件代码后必须重启 DSH** —— `patchReload: live` 只热更新客户端 bundle，Node 服务端模块不会重新加载。改完不重启，表现是「代码改了但行为没变」，极易误判成逻辑写错
69. **DSH 的 `session/event` 插件收不到** —— 它只在 Session 自己的 emitCtx 派发（`dsh-session/lib/index.js:1468`），不是全局广播。要监听回合结束改用 `agent/status`（running→idle）+ 读 `agent.session.snapshotEvents()`
70. **DSH 会话事件的业务字段在 `data` 里** —— 事件形状是 `{ type, seq, time, data }`，`turn/end` 的 reason 在 `ev.data.reason`。读 `ev.reason` 永远是 undefined，这是「回调死活不触发」的真根因
71. **重启 DSH 会清空插件内存表** —— 派发任务后若重启，`inflight` 里的回调记录全丢，那条通知永远收不到（当前无持久化）
72. **RVC 的 `protect` 只能 < 0.5 才生效** —— `pipeline.py:221` 的判断是 `if protect < 0.5`，设 0.5/0.8 会**整段跳过**保护逻辑，输出和 0.33 一模一样（实测频谱重心都是 1523Hz）。调参前先看源码条件
73. **音高检测必须用 rmvpe，不能用 pyin/yin** —— 混音音频里贝斯会带偏检测（周杰伦原曲测出 104Hz，分离后 rmvpe 实测 278Hz，差 3 倍）。rmvpe 是 RVC 自带的，与实际转换一致
74. **`songs/` 本地曲库会复用旧坏文件** —— `netease_download()` 里 `if os.path.exists(path) and size>50000: return path`，**改了音质处理逻辑后必须清空曲库**，否则永远拿旧文件，修复等于白做
75. **`_audio_stats`/`wavfile.read` 读不了 FLAC/MP3** —— scipy 只支持 RIFF，传入 FLAC 报 `File format b'fLaC' not understood`。要测原始音源必须先 `_ffmpeg_to_wav`
76. **多个插件包装同一个 provider 方法时要确认「谁在外层」** —— 包装器是层层嵌套的，后包的在最外面。实测 `llm_fallback`（内）→ `token_stats`（外），这个顺序正好：兜底成功的结果能被统计正常记录。若反过来，兜底那次调用会漏统计。写包装类插件时先看日志里的「已包装」时间戳
77. **`initialize()` 时 provider 可能还没实例化** —— 这是坑 1 的老问题，两个 provider 包装类插件（`token_stats` / `llm_fallback`）都必须补 `@filter.on_astrbot_loaded()` 才能在启动时包到。判断依据：日志里 `initialize` 阶段包装数为 0，`on_astrbot_loaded` 阶段才变成 3

---

## 九、待办 / 下一步

### ✅ 已完成：自研跨群用户记忆插件
详见「零、当前任务」。插件已上线，5 步全部完成。

常用指令（群里发）：
```
/记忆 查 123456789        # 或 /记忆 查 昵称
/记忆 搜 麻辣烫
/记忆 记住 123456789 喜欢喝冰美式
/记忆 统计
/记忆 更新画像             # 管理员，立刻重跑全部人的画像（不用等凌晨3点）
/记忆 更新画像 123456789   # 管理员，只重跑某个人
/记忆 好感                 # ⚠️ 仅 QQ 2481389971 可用，看好感度排行
/记忆 好感 123456789       # ⚠️ 仅 QQ 2481389971 可用，看单人好感详情
/记忆 忘记 123456789      # 管理员
```

### 好感度（v1.0.3 新增）

**单向**（机器人对群友），范围 0-100，**初始 40**，每天凌晨和画像一起由 deepseek-chat 评估一次。

| 分档 | 行为 |
|---|---|
| 85+ 非常亲近 | 有求必应，肉麻要求也配合 |
| 65-85 很熟 | 愿意配合，先嘴硬再照做 |
| 45-65 一般熟 | 看心情，会调侃反问 |
| 25-45 不太熟 | 敷衍、岔开话题 |
| 0-25 没好感 | 直接不配合、冷处理 |

**实测效果**（同一句「叫老公」）：

```
好感 90 → ✅ 真的叫了："老公~"
好感 46 → ⚠️ 调侃式照做："叫老公？你想得美哦"
好感 20 → ❌ 拒绝："啊？上来就叫老公，咱俩很熟吗"
```

这就是解决「模型把请求当调侃」的正解：**不是靠关键词硬匹配，而是让态度取决于关系远近**。

### 运维命令

```powershell
.\start_all.ps1 check    # 只看状态
.\start_all.ps1 start    # 启动缺失的服务
.\start_all.ps1 watch    # 守护模式，掉线自动拉起

# 桌面双击也有一键脚本（GBK 编码，勿改编码）
桌面\启动QQBot.bat
桌面\关闭QQBot.bat
```

### 待观察 / 待验证
- [ ] 表情包 80% 概率的长期实际效果
- [ ] 决策AI 关闭后是否会在不合适时机插话（若太吵调回 `disable_decision_ai: false`）
- [ ] 分段回复 + RVC 语音同开时是否太吵（长回复拆3段 + 语音）
- [ ] 若需长期记忆：`provider_ltm_settings.group_icl_enable: true`（会增加 token 消耗）
- [ ] 1121732680 群 30% 读空气概率实际体验是否合适
- [ ] 1105761562 群 80% 读空气概率是否太吵（新群首日观察，太高可回调 0.5）
- [ ] **RVC 翻唱音质**：2026-10-25 修了 4 类问题（选歌/无人声/削波/坏曲库），用户反馈「效果都还行」。
      若后续仍出现破音/发闷，需要记下**具体歌名**再排查（可能是新的音源质量问题）
- [ ] **QQ↔DSH 回调**：若派活后立刻重启 DSH，那条通知会丢（`inflight` 无持久化）。
      目前可接受，若觉得烦可以改成写磁盘

### 📋 2026-10-25 遗留待办（未做）

| 事项 | 说明 |
|---|---|
| Hindsight 记忆库不可用 | 缺 apiToken，调用返回 401。踩坑记录暂只能写 `STATUS.md` |
| QQ 插件市场重新提交 v1.0.1 | GitHub 已是 v1.0.1，市场还停 v1.0.0。**需要用户登录操作** |
| 画像 50 人上限 | 按「历史总发言数」排序，当天的活跃新人会被 2 天没说话的老群友挤掉。用户已决定**暂不改** |

### 🚫 AstrBot 升级决策（2026-10-23 决定不升级）

**当前版本 4.14.6，最新约 4.29.0，决定保持 4.14.6 不动。**

不升的理由（有据可查）：

1. **跨 15 个小版本**，大概率含破坏性变更（4.29 备注提到"模型输入图片处理"逻辑改动）
2. **核心文件被 patch**：`astrbot/core/provider/sources/whisper_api_source.py:98` 有 MiMo ASR 的
   `chat/completions + input_audio` 补丁，**pip 升级会直接覆盖**。全盘扫描确认只有这 1 个核心文件被改
3. **group_chat_plus 深度依赖内部 API**：`star_handlers_registry` / `star_map` / `func_tool` /
   `get_llm_tool_manager`。它的「逐插件注入隔离」靠**包装其他插件 handler**，这类 hack 极易失效。
   且它是第三方插件（Him666233），核心逻辑改不动
4. **今天新增的功能全压在这些 API 上**：好感度注入（on_llm_request）、李涛过滤（on_decorating_result）、
   sing_song / speak_text（@llm_tool）、跨群记忆（on_llm_request + gcp 兼容层）

**若将来必须升级**，流程：
1. 完整备份 `astrbot-venv` + `data/`（689+ 条发言、好感度、画像都在里面）
2. 单独提取 `whisper_api_source.py` 的 patch 备用（升级后要重新打）
3. 先升中间版本（如 4.20）而非直接跳 4.29，缩小变量
4. 升级后逐项验证：gcp 注入 / 记忆注入 / 好感度 / sing_song / speak_text / 语音 / 点歌
5. 不行就回滚（有备份）

**教训：不坏就不修。** 当前系统稳定可用，15 个版本的收益不确定，风险实在。

---

## 十、常用命令

```bash
# 服务检查
curl http://127.0.0.1:8899/health
curl http://127.0.0.1:8899/login/status
curl -o /dev/null -w "%{http_code}" http://127.0.0.1:6185

# 手动测试点歌（Python 避免中文乱码）
E:\qq-bot\astrbot-venv\Scripts\python.exe -c "import requests;r=requests.post('http://127.0.0.1:8899/sing',data={'keyword':'晴天','model':'youzhanv2-xi.pth','f0up_key':0},timeout=1800);open('out.mp3','wb').write(r.content);print(r.status_code,len(r.content))"

# 重新登录网易云
E:\qq-bot\astrbot-venv\Scripts\python.exe E:\qq-bot\cover_service\netease_login_tool.py
```
