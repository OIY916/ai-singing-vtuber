# 大肥鱼 · 抖音 AI 唱歌直播全流程

一套可跑通的 **抖音 AI 唱歌 VTuber 直播链路**：观众在弹幕点歌 → 大模型判断是「点歌」还是「放原曲」→ RVC 用指定音色翻唱（或直接放原唱）→ 输出到虚拟声卡 → 直播伴侣推流；皮套、气泡、字幕由本地网页渲染，OBS 抓浏览器源即可。

附带一个可选的 **Minecraft AI 分身**：mineflayer 机器人 + 大模型大脑，能在服务器里自己走动、挖矿、说话，并以第一人称视角投到直播画面角落。

> 仅供个人娱乐 / 技术学习。翻唱音色模型与歌曲版权归各自权利人，公开使用请自行确认授权。

---

## 一、数据流

```
抖音弹幕
   │  douyinLive (:1088, WebSocket)
   ▼
直播主服务 stream/main.py (:8790, aiohttp)
   ├─ 解析： 「点歌 X」→ 翻唱     「放 X」→ 原曲直放
   ├─ JOB_Q（待准备） ─ prep_worker ─┬─ RVC /sing  → 翻唱 mp3
   │                                 └─ RVC /raw   → 原唱 mp3
   ├─ PLAY_Q（待播） ─ play_worker ─→ 混音器 → CABLE Input
   ├─ 气泡（所有文字的统一出口）→ /avatar 页面（OBS 浏览器源）
   └─ /overlay 字幕页、/ 控制台网页、tkinter 控制台
                                    │
RVC 翻唱服务 cover_service.py (:8899, FastAPI)
   ├─ 网易云搜索 / 下载 / 解密 .ncm
   ├─ UVR5 人声分离 + 音高提取 + RVC 变声
   └─ 全局 GPU 锁（/sing 与 /speak 互斥，非阻塞抢占）
                                    │
                          直播伴侣（麦克风 = CABLE Output，摄像头 = OBS 虚拟摄像头）
                                    │
                                  抖音直播间
```

MC 分身（可选，与直播主链路解耦）：

```
MC 服务器 :25565 (RCON :25575) ← mineflayer bot (mc/bot) ← LLM 大脑 (brain.js)
                                        └─ 皮肤资源包 HTTP :8088
StreamCam 旁观者客户端 → OBS 第一人称画面
```

---

## 二、目录结构

```
.
├── stream/                 直播主服务
│   ├── main.py             全部业务逻辑（点歌/队列/气泡/接口）
│   ├── mixer.py            音频混音（WASAPI / CABLE Input）
│   ├── memory.py           观众记忆（SQLite）
│   ├── applog.py           日志
│   ├── config.example.json 配置模板（复制成 config.json 再填）
│   └── web/                avatar.html（皮套）/ index.html / overlay.html（字幕）
├── cover_service/          RVC 翻唱服务（跑在 RVC 自带 Python 环境里）
│   ├── cover_service.py    FastAPI：/sing /speak /raw /health
│   ├── netease_login_tool.py
│   └── netease_cookie.example.json
├── mc/                     Minecraft 分身（可选）
│   ├── bot/                brain.js / bot.js / hud.js / watchdog.ps1
│   ├── camera_driver.py    旁观者相机驱动
│   ├── rcon.py             RCON 客户端
│   ├── start_mc.ps1        一键起服务器 + 机器人 + 皮肤服务
│   ├── cam_skin_pack/      相机皮肤资源包
│   ├── http/               皮肤 zip（给服务器当资源包）
│   └── server/server.properties.example
├── tools/                  控制台与各类调试脚本
│   ├── control_panel.py    tkinter 控制台（切歌/暂停/点歌开关/语音开关/队列/黑名单）
│   └── ...                 登录态抓取、RVC 批量测试、开播前自检等
├── avatar/                 皮套页面素材（页面通过 /asset/ 读取）
│   ├── front_cut.png
│   └── clips_out/          idle.webm / speak.webm / sing.webm
├── scripts/
│   ├── live/               启动直播.ps1 / 直播控制台.bat
│   └── qqbot/              QQ 机器人侧的启停脚本（可选，与直播各自独立）
├── docs/                   开发过程记录、进度存档、翻唱流程复用指南
└── requirements.txt        直播主服务依赖（RVC 自己那套环境不在此列）
```

---

## 三、环境要求

| 组件 | 说明 |
|---|---|
| Windows 10/11 | 音频走 WASAPI，控制台用 tkinter |
| Python 3.10 | 直播主服务、控制台、MC 侧脚本 |
| RVC（RVCMAX / RVC-WebUI 变体） | **需单独下载**，体积几十 GB；`cover_service.py` 跑在它自带的 Python 环境里（自带 torch/ffmpeg） |
| Node.js 18+ | 只有 MC 分身需要（mineflayer） |
| Java 17/21 | 只有 MC 服务器需要 |
| OBS Studio | 浏览器源抓皮套页 |
| 抖音直播伴侣 | 推流 |
| VB-Audio Virtual Cable | 把混音器输出喂给直播伴侣麦克风 |
| 网易云音乐账号 | 翻唱要搜歌/下载，需要登录态 cookie |
| 抖音弹幕采集器 `douyinLive` | **第三方，不随仓库分发**；放进 `tools_raw\douyinLive\` 或用 `paths.json` 指定 |

---

## 四、部署步骤

### 1. 装依赖

```powershell
python -m venv astrbot-venv          # 名字随意，脚本里默认用 astrbot-venv
.\astrbot-venv\Scripts\pip install -r requirements.txt
```

MC 分身另需：

```powershell
cd mc\bot; npm install
```

### 2. 配置（都能从模板复制）

```powershell
copy stream\config.example.json            stream\config.json
copy cover_service\netease_cookie.example.json cover_service\netease_cookie.json
copy mc\server\server.properties.example   mc\server\server.properties
copy paths.example.json                    paths.json
```

`stream/config.json` 里**必须填**：

| 字段 | 说明 |
|---|---|
| `room_id` | 抖音直播间号 |
| `llm_key` / `llm_base` / `llm_model` | 大模型接口（默认 DeepSeek） |
| `sing_model` | RVC 模型文件名（翻唱用的音色，如 `nikou48K1999.pth`） |
| `tts_model` | 说话用的音色（如 `guanguanV1.pth`） |
| `cover_api` | RVC 服务地址，默认 `http://127.0.0.1:8899` |

`Cover_service/netease_cookie.json` 需要你自己登录网易云后导出（可用 `netease_login_tool.py`）。

`mc/server/server.properties` 里的 `rcon.password` 改成你自己的。

### 3. 配置本机路径（paths.json）

代码里**不再写死盘符**，所有本机路径集中在一个文件（不进仓库）：

```powershell
copy paths.example.json paths.json
```

| 键 | 含义 | 不填时的默认行为 |
|---|---|---|
| `rvc_home` | RVC 安装根目录（其下应有 `RVC_Core\`、`Runtime\`） | 翻唱服务启动时直接报错并提示怎么配 |
| `python` | 跑直播主服务的解释器 | 自动用项目内 `astrbot-venv` / `.venv`，再退回当前解释器 |
| `music_dir` | 网易云下载目录（放 `.ncm`） | `ncmdump` 工具需自己传目录参数 |
| `mc_dir` | `.minecraft` 目录（读客户端 jar 画物品栏） | `%APPDATA%\.minecraft` |
| `douyin_live_exe` | 第三方弹幕采集器 exe | `<项目根>\tools_raw\douyinLive\douyinLive.exe` |

也可以临时用环境变量覆盖：`DFY_RVC_HOME`、`DFY_PYTHON`、`DFY_MC_DIR`、`DFY_MUSIC_DIR`……

实现说明：
- Python 侧统一走 `pathcfg.py`（`rvc_core()` / `ffmpeg()` / `python()` / `mc_dir()` …）；
- PowerShell 侧每个脚本开头用 `Find-ProjectRoot` 从自身位置往上找 `stream\main.py` 定位项目根，
  所以脚本放在项目根、`scripts\live\` 或 `tools\` 下都能正常跑；
- `ffmpeg` 优先用 RVC 自带的那份，取不到再退回系统 PATH。

### 4. 起服务

```powershell
# 按顺序：弹幕采集(:1088) → RVC 翻唱(:8899) → 直播主服务(:8790)
.\scripts\live\启动直播.ps1          # 启动
.\scripts\live\启动直播.ps1 -Status   # 看状态
.\scripts\live\启动直播.ps1 -Stop     # 全停
```

控制台窗口：

```powershell
.\scripts\live\直播控制台.bat
```

### 5. OBS / 直播伴侣

- OBS 加**浏览器源**：URL `http://127.0.0.1:8790/avatar`，分辨率 **1920×1080**
- 需要字幕时再加一个：`http://127.0.0.1:8790/overlay`
- 直播伴侣：**主麦克风 = CABLE Output**，**摄像头 = OBS 虚拟摄像头**
- 抖音后台记得勾选「AI 生成」声明

### 6. 开播前自检

```powershell
python tools\prelive_check.py
```

或用测试接口（只绑 127.0.0.1）：

```powershell
# 模拟一条弹幕，验证 AI 回复 + 语音
curl -X POST http://127.0.0.1:8790/api/test/danmaku -H "Content-Type: application/json" -d "{\"name\":\"测试\",\"text\":\"你好呀\"}"
# 直接唱一首
curl -X POST http://127.0.0.1:8790/api/test/sing -H "Content-Type: application/json" -d "{\"kw\":\"晴天\"}"
```

---

## 五、设计要点（踩过的坑都在注释里）

- **两条点歌链路分开**：`点歌 X` / `唱一首 X` → RVC 翻唱；`放 X` → 网易云原曲直放，不做任何处理。
- **气泡是所有文字的唯一出口**：回复、礼物感谢、点歌确认、翻唱安抚一律先上气泡；是否同时发声由 `voice_mode` 决定。语音型气泡等声音真正开始播那一刻才上屏，让「听到」和「看到」同步。
- **RVC 只有一块 GPU**：`/sing` 与 `/speak` 共用一把锁，且是**非阻塞抢占**（拿不到立刻返回「服务忙」）。所以唱歌流程中默认不调 `/speak`，避免把翻唱挤成 503 丢歌。
- **防静音兜底**：翻唱超时 300 秒即放弃；翻唱失败自动改放原唱；直播间连续静音超过 45 秒（`sing_starve_fallback`）就并行下载原唱顶上。宁可放原唱，也不给观众一片安静。
- **唱歌时也能语音回复**（控制台可开关）：默认关；打开后「歌在放、GPU 空闲」时直接说话（音乐自动压到 30%），「翻唱正占锁」时把这句话攒起来，等这首翻完、下一首开始前的那几秒补说。每首之间最多让麦 8 秒，攒满 6 句或超过 45 秒的丢弃。
- **队列落盘**：JOB_Q / PLAY_Q 每 2 秒拍快照，`mp3` 字节落盘时转成临时文件路径（`json` 存不了 bytes），重启后按路径恢复并保持播放顺序。
- **观众记忆**：`data/memory.db`（SQLite，不入库），记录谁来过、点什么歌、聊过什么，用于老粉欢迎与个性化回复。

---

## 六、HTTP 接口速查

| 接口 | 说明 |
|---|---|
| `GET /api/state` | 全局状态（模式/队列/在线/开关） |
| `POST /api/skip` | 切歌 |
| `POST /api/pause` | 暂停 / 继续播放 |
| `POST /api/order` | 暂停 / 恢复点歌 |
| `POST /api/sing_voice` | 唱歌时是否语音回复（`{"action":"on|off|toggle"}`） |
| `POST /api/queue/insert` | 插队（下一首播） |
| `POST /api/queue/remove` | 删队列（按歌名或序号） |
| `GET/POST /api/blacklist` | 查看 / 增删歌曲黑名单（立即生效并清掉队列里的） |
| `GET /api/memory` | 记忆库概况 |

RVC 侧：`GET /health`、`GET /models`、`POST /sing`、`POST /speak`、`POST /raw`。

---

## 七、已知限制

- 第三方弹幕采集器 `douyinLive` 不随本仓库分发，需要自己准备并放进 `tools_raw\douyinLive\`，
  或在 `paths.json` 里用 `douyin_live_exe` 指到任意位置（抖音弹幕协议本身会变，建议用较新的版本）。
- 本机路径已集中到 `paths.json`（见「部署步骤 3」），但少数**调试脚本**（`tools/rvc_batch_test.py`、
  `tools/rvc_ab_test.py`、`tools/ncmdump_standalone.py` 等）里仍留有示例路径，属于一次性实验脚本，按需自己改。
- GPU 单卡时 `/sing` 与 `/speak` 互斥；队列很长时说话要等翻唱空档。
- 皮套是无口型同步的整段视频切换（idle / speak / sing），口型不对齐属于设计取舍。
- `cover_service` 的网易云链路依赖登录态 cookie，过期需要重新导出。
- MC 分身需要额外下载 MC 服务端、客户端与皮肤包，且吃显存，和 RVC 抢 GPU 时要留意。

---

## 八、致谢 / 第三方

- [RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) 变声内核与 UVR5 人声分离
- [mineflayer](https://github.com/PrismarineJS/mineflayer) MC 机器人框架
- [aiohttp](https://github.com/aio-libs/aiohttp) / [FastAPI](https://github.com/tiangolo/fastapi) 服务框架
- 网易云音乐相关接口、抖音直播弹幕采集由第三方工具提供，请遵守其服务条款
