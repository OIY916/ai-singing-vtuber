# 网易云 .ncm 解密工具

把网易云客户端下载的 **加密 `.ncm`** 还原成可正常播放的 **`.flac` / `.mp3`**。

> 用途：翻唱流程的第一步。RVC 需要能直接读的音频，`.ncm` 它读不了。

---

## 一、你要拿到的文件

| 文件 | 作用 |
|---|---|
| `ncmdump_standalone.py` | 主程序，单文件，不依赖第三方 ncdump 包 |
| `解密ncm-拖到这里.bat` | 双击用，把 `.ncm` 或文件夹拖到它图标上即可 |

两个放同一个文件夹里就能用。

`.bat` 会自动找 Python，顺序是：

1. 环境变量 `RVC_PY`（你自己指定的）
2. 同目录 / 上级目录 / 上上级目录下的 `Runtime\python.exe`（把包解压到 RVC 目录里就能直接命中）
3. `PATH` 里的 `python`

都没有会提示 `[ERROR] Python not found.`，并告诉你怎么设 `RVC_PY`：

```bat
set RVC_PY=E:\你的RVC\Runtime\python.exe
```

`RVC_PY` 里那个 runtime 自带 `pycryptodome` 和 `mutagen`，所以什么都不用装。

> ⚠️ 如果你用系统 `python` 而它没装 AES 库，会看到 `缺少 AES 库`。装一个即可：`pip install pycryptodome`

---

## 二、三种用法

### 1. 拖拽（最省事）

把 `.ncm` 文件，或者装着 `.ncm` 的**文件夹**，直接拖到 `解密ncm-拖到这里.bat` 上松手。

- 拖文件 → 解这一个
- 拖文件夹 → 递归把里面所有 `.ncm` 全解了

解出来的音频**和源文件放同一个目录**，名字是 `歌名-歌手.flac`（或 `.mp3`）。

### 2. 命令行

```bat
:: 解单个
python ncmdump_standalone.py D:\CloudMusic\晴天.ncm

:: 解整个文件夹
python ncmdump_standalone.py D:\CloudMusic

:: 指定输出目录
python ncmdump_standalone.py D:\CloudMusic -o D:\解好的

:: 自定义文件名：%artist% - %title%
python ncmdump_standalone.py D:\CloudMusic -o D:\out -f "%artist% - %title%"

:: 解完把源 .ncm 删掉
python ncmdump_standalone.py D:\CloudMusic -d

:: 覆盖已存在的同名文件（默认跳过）
python ncmdump_standalone.py D:\CloudMusic -c

:: 安静模式，只报结果
python ncmdump_standalone.py D:\CloudMusic -q
```

### 3. 代码里调用

```python
import ncmdump_standalone as ncm

# 只解密，不写文件（拿 bytes，方便自己处理）
info = ncm.decrypt_bytes(r"D:\CloudMusic\晴天.ncm")
print(info["format"])   # 'flac' 或 'mp3'
print(info["meta"])     # {'musicName': '晴天', 'album': '叶惠美', 'artist': [['周杰伦', 1]], ...}
audio: bytes = info["audio"]

# 解密并落盘（自动命名 + 写封面/标题/歌手标签）
out = ncm.dump(r"D:\CloudMusic\晴天.ncm")            # -> D:\CloudMusic\晴天-周杰伦.flac
out = ncm.dump(src, r"D:\out\a.flac", skip_existing=False)  # 指定路径、强制写
```

---

## 三、装什么依赖

程序会自动挑一个能用的 AES 库，**装一个就行**：

```bat
pip install pycryptodome
```

或者

```bat
pip install cryptography
```

想保留**封面图、歌名、歌手、专辑**标签，再装：

```bat
pip install mutagen
```

> 没装 mutagen 也能解出音频，只是不带封面和标签。

**最省事**：如果你机器上有 RVC，直接用 RVC 自带的 Python，什么依赖都不用装：

```bat
set RVC_PY=E:\你的RVC\Runtime\python.exe
"%RVC_PY%" ncmdump_standalone.py 你的.ncm
```

那个 runtime 里已经有 pycryptodome 和 mutagen 了。`.bat` 启动器也是这么找的——优先用 `RVC_PY` / 相邻的 `Runtime\python.exe`，找不到才退回系统 `python`。

---

## 四、它到底做了什么

`.ncm` 是网易云的加密缓存格式。结构：

```
偏移   内容
 0     8 字节 magic = CTENFDAM
 8     2 字节 gap
10     4 字节 keyLen + keyLen 字节（每字节 ^0x64）
       └─ AES-128-ECB 解密（固定 CORE_KEY），去掉 PKCS7，砍掉前 17 字节 "neteasecloudmusic"
          → 得到 RC4 密钥
      4 字节 metaLen + metaLen 字节（每字节 ^0x63）
       └─ 去掉 22 字节前缀 "163 key(Don't modify):"，base64 解码，
          AES-128-ECB 解密（固定 META_KEY），砍掉前 6 字节 "music:"
          → 得到 JSON：歌名 / 专辑 / 歌手 / 格式
      5 字节 gap
      4 字节 imageSpace + 4 字节 imageSize + imageSize 字节封面图
      剩余全部 = 加密音频流
```

解密音频流：

1. 用 RC4 密钥跑标准 KSA，得到 256 字节置换表 `S`
2. 由 `S` 生成密钥流（PRGA）。**注意：跳过密钥流第 0 字节，从索引 1 开始**
3. 密钥流与音频数据等长异或 → 得到原始 `.flac` 或 `.mp3`

### ⚠️ 那个 off-by-one 是最容易写错的地方

很多自己实现的版本把密钥流写成 `stream[:length]`，结果整首歌**错位一字节**，解出来全是噪声。

正确写法是跳过第一个字节：

```python
block = bytes(S[(S[i] + S[(i + S[i]) & 0xFF]) & 0xFF] for i in range(256))
stream = (block * (n // 256 + 1))[1 : 1 + n]   # ← [1:1+n]，不是 [:n]
```

官方 `ncmdump` 包里写的是 `(block * (len(data)//256 + 1))[1:1+len(data)]`，本文档这个独立版跟它对齐，已逐字节验证。

---

## 五、验证结果

用「按上述格式逆向合成 `.ncm` → 分别用官方包和本独立版解密 → 逐字节比对」的方式测了 4 个用例：

| 用例 | 纯解密 vs 原始音频 | 与官方实现音频帧一致 |
|---|---|---|
| FLAC + 封面 | ✅ 逐字节相同 | ✅ 相同 |
| FLAC 无封面 | ✅ 逐字节相同 | ✅ 相同 |
| MP3 + 封面 | ✅ 逐字节相同 | ✅ 相同 |
| MP3 无封面 | ✅ 逐字节相同 | ✅ 相同 |

另外用 ffmpeg 完整解码验证过：

```
flac: 解码 OK   codec=flac  duration=205.42s  bit_rate=857652  封面 codec=mjpeg ✅
mp3:  解码 OK   codec=mp3   duration=228.57s  bit_rate=325094  封面 codec=mjpeg ✅
```

> MP3 的**整文件** md5 和官方包不同——因为本版用标准 ID3 帧（TIT2/TPE1/TALB/COMM/APIC）显式构造，
> 官方包用 `RegisterTextKey`。但**纯音频帧逐字节相同**，播放效果完全一致。
>
> 顺带一提：官方 `ncmdump` 包在 mutagen ≥1.46 上处理 MP3 会直接崩（`'ID3' object has no attribute 'RegisterTextKey'`）。
> 本独立版没这个问题，所以它不只是"可替代"，在 MP3 上其实**比官方包更能用**。

---

## 六、常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `缺少 AES 库` | 没装 pycryptodome/cryptography | `pip install pycryptodome` |
| `不是 ncm 文件（magic=...）` | 文件不是 ncm，或已损坏 | 确认路径；`magic` 应为 `4354454e4644414d` |
| 解出来是噪声/怪声 | 密钥流 off-by-one（见第四节） | 用本脚本，别自己改 `[1:1+n]` |
| 没有封面/歌名 | 没装 mutagen | `pip install mutagen` |
| `密文长度不是 16 的倍数` | 文件截断/下载不完整 | 重新下载 |
| 中文路径乱码 | bat 编码不对 | 用本仓库的 `.bat`（GBK 编码 + 内容全 ASCII） |
| 拖拽没反应 | 拖到了 `.py` 上 | 拖到 `.bat` 上，或拖整个文件夹 |

---

## 七、接到翻唱流程里

`cover_service.py` 里已经有这个环节，在第 412 行：

```python
# 0. 加密格式先解密
src_audio = _ensure_playable(src_audio)   # .ncm -> .flac / .mp3
```

`_ensure_playable()` 只处理 `.ncm`，失败会抛异常而不是静默产出垃圾音频——
这是之前踩过坑后加的（静默失败会导致下游 UVR5 分离出一堆噪声，最后翻唱结果莫名其妙）。

如果你要把它换成这个独立版（不依赖 pip 装的 ncmdump），改两行：

```python
def _ensure_playable(src):
    if not src.lower().endswith(".ncm"):
        return src
    sys.path.insert(0, os.path.join(BASE_DIR, "tools"))
    import ncmdump_standalone as ncm
    out = src[:-4] + "." + ncm.decrypt_bytes(src)["format"]
    ncm.dump(src, out, skip_existing=False)
    os.remove(src)
    return out
```

---

## 八、说明

`.ncm` 是网易云用来保护**版权内容**的容器格式。这个工具只做**格式还原**——把你自己账号已经有权播放/下载的文件转成通用格式，不下载任何东西、不绕过任何权限。

请只在你自己已获得授权的音频上使用。感谢 [nondanee/ncmdump](https://github.com/nondanee/ncmdump) 提供的公开算法参考。
