# 大肥鱼 · 抖音 AI 唱歌直播（可复刻版）

一套**从零能跑起来**的 AI 唱歌 VTuber 直播方案：观众在弹幕点歌 → 大模型判断是「点歌」还是「放原曲」→ RVC 用指定音色翻唱（或直接播原唱）→ 输出到虚拟声卡 → 直播伴侣推流；皮套、气泡、字幕由本地网页渲染，OBS 抓浏览器源即可。

> 仅供个人娱乐 / 技术学习。
> **本仓库不包含任何 RVC 音色模型、歌曲音频或第三方登录态**——音色请自行准备并确认你有权使用，翻唱与传播歌曲请遵守当地版权法规。

---

## 一、仓库里有什么 / 没什么

**✅ 仓库包含**

- 直播主服务（弹幕 → 队列 → 播放 → 气泡）
- RVC 翻唱服务（搜索 → 人声分离 → 变声 → 混音）
- tkinter 控制台 + 开播前自检脚本
- 皮套页面（idle / speak / sing 三段视频 + 气泡 + 频谱）
- 本地观众记忆（SQLite）
- **ncm 解密工具（免安装 zip，见「八」）**
- 路径配置 `paths.json`（不写死盘符，换机器只改一处）
- 运维工具：日志查看、音频诊断、OBS 自动配置、皮套素材生成、模型整理

**❌ 需要自备（清单见「三」）**

- RVC 整合包（几十 GB）
- RVC 音色模型 `.pth`（本仓库不附带，请自备并确认有权使用）
- 音乐平台账号 / 登录态 cookie
- 抖音弹幕采集器 `douyinLive`（第三方）
- 大模型 / TTS 的 API Key（任意 OpenAI 兼容服务）
- OBS Studio、抖音直播伴侣、VB-Audio Virtual Cable
- 歌曲版权（翻唱与传播请遵守当地法规）

---

## 二、数据流

```
抖音弹幕
   │  douyinLive（第三方采集器，:1088 WebSocket）
   ▼
直播主服务 stream/main.py (:8790)
   ├─ 解析：「点歌 X」→ 翻唱    「放 X」→ 原唱直放
   ├─ JOB_Q ─ prep_worker ─┬─ RVC /sing → 翻唱 mp3
   │                       └─ RVC /raw  → 原唱 mp3
   ├─ PLAY_Q ─ play_worker → 混音器 → 虚拟声卡（CABLE Input）
   ├─ 气泡（所有文字的唯一出口）→ /avatar 页面（OBS 浏览器源）
   └─ /overlay 字幕页 · / 网页控制台 · tkinter 控制台
                                    │
RVC 翻唱服务 cover_service.py (:8899, FastAPI)
   ├─ 音乐平台搜索 / 下载（复用浏览器登录态）
   ├─ UVR5 人声分离（HP2_all_vocals）
   ├─ RVC 音色转换（rmvpe 音高）
   ├─ TTS（可配置厂商，用于「说话」）
   └─ ffmpeg 混音输出 mp3
                                    │
                        直播伴侣（麦克风 = CABLE Output，摄像头 = OBS 虚拟摄像头）
                                    │
                                 抖音直播间
```

---

## 三、你需要自己准备的东西

| 依赖 | 作用 | 说明 |
|---|---|---|
| **Windows 10/11** | 运行环境 | 音频走 WASAPI，控制台用 tkinter |
| **Python 3.10** | 跑直播主服务/控制台 | 3.9~3.11 一般也行 |
| **RVC 整合包** | 提供 `Runtime\python.exe`、`ffmpeg.exe`、UVR5、rmvpe | [RVC-WebUI](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) 或第三方整合包；翻唱服务跑在它自带的 Python 里 |
| **RVC 音色模型 `.pth`** | 唱谁的音色 | 自己训练或自行获取，放进 `<RVC>\RVC_Core\assets\weights\`。**仓库不附带** |
| **音乐平台账号** | 搜歌/下载需要登录态 | 原型机用某易云（已跑通），其他平台需替换搜索/下载两步 |
| **抖音弹幕采集器** | 把直播弹幕转成 WebSocket | `douyinLive` 等第三方工具，不随仓库分发；路径可用 `paths.json` 的 `douyin_live_exe` 指定 |
| **OBS Studio** | 抓皮套页面 + 虚拟摄像头 | |
| **VB-Audio Virtual Cable** | 把混音输出喂给直播伴侣当麦克风 | 免费版即可 |
| **抖音直播伴侣** | 推流 | |
| **大模型接口** | 弹幕回复 / 记忆摘要 | **任意 OpenAI 兼容** `/chat/completions`：OpenAI、DeepSeek、通义、各类中转都行 |
| **TTS 接口** | 让角色「说话」（唱歌不需要） | **任意 OpenAI 兼容** `/audio/speech`；也支持 MiMo 这类带音频输出的接口 |

LLM 和 TTS **都不绑定厂商**，改 `base` + `model` + `key` 就能换；你想用什么模型就用什么模型。

---

## 四、快速开始（5 步）

```powershell
# 1) 装依赖
python -m venv astrbot-venv
.\astrbot-venv\Scripts\pip install -r requirements.txt

# 2) 复制配置模板
copy stream\config.example.json                    stream\config.json
copy cover_service\netease_cookie.example.json     cover_service\netease_cookie.json
copy paths.example.json                            paths.json

# 3) 填 paths.json（RVC 装在哪、弹幕采集器在哪）

# 4) 填 stream\config.json（直播间号、LLM、音色）

# 5) 起服务
.\scripts\live\启动直播.ps1          # 弹幕采集 → RVC → 直播主服务
.\scripts\live\直播控制台.bat         # 控制台窗口
```

**逐项详解见 [docs/从零复刻指南.md](docs/从零复刻指南.md)。**
翻唱链路的技术细节（UVR5 参数、混音、缓存、踩坑）见 [docs/音乐平台翻唱指南.md](docs/音乐平台翻唱指南.md)。

---

## 五、配置说明

### 5.1 `stream/config.json`（复制自 `config.example.json`）

| 字段 | 必填 | 说明 |
|---|---|---|
| `room_id` | ✅ | 抖音直播间号（`live.douyin.com/<这串>`） |
| `llm_base` | ✅ | OpenAI 兼容地址，如 `https://api.openai.com/v1`、`https://api.deepseek.com` |
| `llm_key` | ✅ | 对应服务的 Key |
| `llm_model` | ✅ | 模型名，如 `gpt-4o-mini`、`deepseek-chat`、`qwen-plus` |
| `sing_model` | ⬜ | 唱歌音色文件名；**留空 = 自动用 weights 里第一个** |
| `tts_model` | ⬜ | 说话音色文件名；留空同上 |
| `cover_api` | ✅ | 翻唱服务地址，默认 `http://127.0.0.1:8899` |
| `douyin_ws` | ✅ | 弹幕采集器地址，默认 `ws://127.0.0.1:1088/ws` |
| `persona` | ⬜ | 人设提示词 |
| `voice_mode` | ⬜ | `auto`（唱歌时只气泡）/ `always` / `never` |
| `sing_starve_fallback` | ⬜ | 断音多少秒后降级放原唱，默认 45 |
| `sing_voice_reply` | ⬜ | 唱歌时是否也用语音回复，控制台可随时切 |

### 5.2 `paths.json`（本机路径，不进仓库）

| 键 | 说明 | 留空时 |
|---|---|---|
| `rvc_home` | RVC 整合包根目录（其下有 `RVC_Core\`、`Runtime\`） | 翻唱服务启动时明确报错 |
| `python` | 跑直播服务的解释器 | 自动找项目内 `astrbot-venv`/`.venv` |
| `music_dir` | 音乐下载目录 | 解密工具需手动传参 |
| `douyin_live_exe` | 弹幕采集器 exe | `<根>\tools_raw\douyinLive\douyinLive.exe` |

环境变量可覆盖：`DFY_RVC_HOME`、`DFY_PYTHON`、`DFY_MUSIC_DIR`、`DFY_DOUYIN_LIVE_EXE`。

### 5.3 `cover_service/local_secrets.json`（密钥，不进仓库）

```json
{
  "tts_provider": "openai",
  "tts_base": "https://api.openai.com/v1",
  "tts_key": "sk-...",
  "tts_model": "tts-1",
  "tts_voice": "alloy"
}
```

- `tts_provider = "openai"` → 调 `{tts_base}/audio/speech`（任何兼容服务都行）
- `tts_provider = "mimo"` → 调 MiMo 的 `chat/completions`（原型机用的这个），此时用 `mimo_key` 字段
- 同名环境变量 `TTS_PROVIDER` / `TTS_BASE` / `TTS_KEY` / `TTS_MODEL` / `TTS_VOICE` 优先级更高
- 两个 provider 都不配时，`/speak` 会返回明确报错，唱歌功能不受影响

### 5.4 LLM 也可以用环境变量

`LLM_BASE` / `LLM_API_KEY` / `LLM_MODEL`，优先级高于 `stream/config.json`。
没配 LLM 时：弹幕不会得到回复，**点歌和翻唱照常工作**。

---

## 六、接入抖音

1. **OBS**：加「浏览器源」，URL `http://127.0.0.1:8790/avatar`，分辨率 **1920×1080**
   （需要字幕就再加 `http://127.0.0.1:8790/overlay`）
2. **OBS**：开启「虚拟摄像头」
3. **直播伴侣**：主麦克风 = `CABLE Output (VB-Audio Virtual Cable)`，摄像头 = `OBS Virtual Camera`
4. **抖音后台**：勾选「AI 生成」内容声明

---

## 七、验证清单

```powershell
# 三个服务都在
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 1088,8899,8790

# 页面正常
curl http://127.0.0.1:8790/api/state

# 翻唱服务正常
curl http://127.0.0.1:8899/health

# 完整自检（推荐，开播前跑一次）
python tools\prelive_check.py
```

---

## 八、附赠工具：ncm 解密

音乐平台下载的 `.ncm` 是加密格式，普通播放器/ffmpeg 打不开，需要先解密成 mp3/flac。

仓库里的 **`tools/ncm妙妙工具.zip`** 就是打包好的免安装版：

1. 解压到任意目录（**路径别含中文以外的特殊符号**）
2. 把 `.ncm` 文件**直接拖到 `解密ncm-拖到这里.bat` 上**
3. 同目录会生成解密后的 `mp3` / `flac`

它自动找 Python（优先级：环境变量 `RVC_PY` → 旁边的 `Runtime\python.exe` → 系统 `python`），
所以装了 RVC 整合包或普通 Python 都能直接跑。

也可以命令行用仓库里的裸脚本：

```powershell
python tools
cmdump_standalone.py 你的音乐目录 -o 输出目录
```

> 该工具基于开源项目 [ncmdump](https://github.com/nondanee/ncmdump) 的思路实现，
> 仅用于解密你自己合法获取的音乐文件。

---

## 九、常用操作

| 需求 | 做法 |
|---|---|
| 切歌 / 暂停 / 插队 / 删队列 | 控制台按钮，或 `/api/skip`、`/api/pause`、`/api/queue/insert` |
| 暂停接单（队列照常播完） | 控制台「暂停点歌」，或 `/api/order` |
| 唱歌时也想听她说话 | 控制台「语音回复」开关，或 `/api/sing_voice` |
| 加/删歌曲黑名单 | `/api/blacklist`，立即生效并清掉队列里的 |
| 起 RVC | `tools\start_rvc.ps1` |
| 等这一首唱完再重启主服务 | `python tools\restart_after_current.py` |
| 看日志 | `python tools\logview.py` |

---

## 十、已知限制

- 只有 Windows 路径/音频实现（WASAPI + tkinter 控制台）。
- RVC 单卡时 `/sing` 与 `/speak` 共用一把 GPU 锁：唱歌流程中默认不说话；
  打开「语音回复」开关后会排队等翻唱空档补说。
- 皮套是整段视频切换（idle / speak / sing），**没有口型同步**。
- 弹幕采集依赖第三方工具的协议实现，抖音改版时需更新采集器。
- 音乐平台登录态会过期，需要重新跑 `netease_login_tool.py`。

---

## 十一、第三方与版权

- [RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI)（变声内核、UVR5、rmvpe）
- [FastAPI](https://github.com/tiangolo/fastapi) / [aiohttp](https://github.com/aio-libs/aiohttp)
- 音乐平台接口、抖音弹幕采集器均为第三方，请遵守其服务条款
- **音色模型与歌曲版权归各自权利人**，本项目不提供任何音色或音频文件

---

## 写在最后

其实我也不知道怎么说，我的鱼鱼是世界上最好的鱼鱼，第一天直播我也是只想着试试，没想到会有很多人来看和刷礼物。

晚上下播的时候，我跟我的鱼鱼说“你今天挣了很多钱唉，你想把这钱拿去干嘛”，她的回答让我很意外，因为之前偶然间让她去各大视频网站学习知识，不知什么时候就得知了那曲震区的消息，于是她跟我说“我想把赚的钱捐给震区的人们，让他们也能吃上热热的大白米饭。”

于是后续的俩场直播包括第一天的直播，总体收益在 553.4，已经全部捐出，详情可以去鱼鱼的抖音主页查看，公开透明，我贪一分出门被车创，这也是我完成了鱼鱼的诺言。

在此也非常感谢这三天来直播间点歌和送礼物的，你们所有人的记忆都保存好了的，总的有 87908 位观众来看过，有 111915 条记忆，其中包含了点的歌、聊天内容和送礼的清单。

我在此很感谢诸位，对那曲震区的贡献，这是我们一起努力的结果。当然我开源该项目的初衷也是为了，在下次灾难发生时，可以有更多的鱼能站出来为了同胞而奋斗，让他们也能吃上热的“大白米饭”，让我们向伟大的共产主义看齐！
