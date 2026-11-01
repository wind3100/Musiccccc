# nm —— 本地音乐管理工具（网易云歌单 → 本地库）

按《音乐工程项目需求文档》落地的 **可运行 MVP 骨架**：歌单手动同步、三栏状态、严格双维匹配、
双源执行器接口、人工审批熔断、元数据全清后补标签、暂存区原子落库。

当前状态：**58 个测试通过**（含端到端链路）；源码与 **单文件 `dist\nm.exe`（10.1 MB）** 均已实测跑通全流程。

---

## 1. 需求 → 实现映射

| 需求文档条目 | 实现位置 | 落地行为 |
| --- | --- | --- |
| 本地主权化（只增不减） | `db.py` `files` 表 / `staging.target_path` | 云端移除只改状态为「已取消喜欢」，本地文件永不自动删除；同名冲突加 `(2)` 序号绝不覆盖 |
| 纯手动触发 | `cli.py` | 无定时器、无后台线程、无启动联网；只有敲命令才发请求 |
| 三栏视图 + `+ - !` 标记 | `sync.py` `apply_sync` | 喜欢 / 已取消喜欢 / 失效三栏；云端顺序为唯一顺序；失效置底 |
| 误触取消又收藏回来 | `sync.py` `_bucket_for` | 再次出现在云端自动恢复为喜欢，不产生「新增」噪音 |
| 严格双维匹配 | `match.py` `strict_match` | 名称 NFKC 归一化后**完全相等** + 时长差 ≤ 容差（默认 2000ms）；艺术家/专辑不参与判定、缺失用歌单补全 |
| 零容错 / 人工审批熔断 | `pipeline.py` | 名称或时长不符、双源失败、无无损、疑似假无损 → 进 `approvals` 队列，**绝不自动降级** |
| 音质优先级 | `match.py` `pick_best` | 无损（FLAC/WAV/APE）优先，同格式按文件大小降序；无无损直接熔断 |
| 假无损警惕 | `match.py` `is_suspect_fake_lossless` | 无损容器但码率 < 阈值（默认 600kbps）→ 审批 |
| 双源冗余 | `downloaders.py` | 执行器按配置顺序串行尝试，主源失败/无无损自动切备源，对用户透明 |
| 频率安全 | `limiter.py` | 搜索/下载各自独立限速（最小间隔 + 滑动窗口 + 指数退避），连续失败达阈值即熔断 |
| 歌单信息驱动命名 | `naming.py` `build_filename` | 文件名只取歌单元数据，忽略源文件名与源内标签 |
| 元数据洁癖 | `tagging.py` | 全清标签/封面/隐藏帧 → 复核无残留 → 只回写 title/artist/album/lyrics → 复核无多余字段 |
| 歌词处理 | `tagging.py` / `netease.py` | 内嵌歌词先提取 → 全清后回写；无内嵌取网易云 LRC；都没有则跳过不阻断；wav/ape 等生成同名 `.lrc` |
| 原始文件隔离 + 原子化 | `staging.py` | 下载进独立暂存目录，清洗校验完成才 `os.replace` 落库；成功/失败都清空暂存，无垃圾残留 |
| 处理日志 | `db.py` `jobs` 表 / `nm jobs` | 每首歌记录 search/match/quality/download/tag/move 各阶段结果与耗时 |
| 绿色软件 | `config.py` `app_root` | 配置、日志、缓存、数据库全部在程序同级目录，不写 AppData/注册表 |

---

## 2. 快速开始

本机已备好隔离环境（不污染系统 Python）：

```
runtime/python311/      # Python 3.11.9（NuGet 分发包，免安装、不写注册表）
.venv/                  # 项目虚拟环境（requests / pycryptodome / mutagen / pytest）
```

命令用项目自带启动器 `nm.cmd`（等价 `PYTHONPATH=src python -m nm`）：

```powershell
nm.cmd doctor              # 自检：路径、数据库、登录态、执行器、接口连通性、磁盘

# 1) 登录（推荐扫码：手机网易云 App 扫一下即可，无需密码/无需浏览器）
nm.cmd login
nm.cmd cookie              # 也可以选：扫码 / 从浏览器读取 / 手动粘贴
nm.cmd cookie --check      # 只检查当前登录态（掩码显示 + 真连账号接口验证）

# 2) 手动同步「我喜欢的音乐」
nm.cmd sync

# 3) 看三栏（+-! 标记、已下载/待下载状态）
nm.cmd list
nm.cmd list --bucket unliked

# 4) 下载待补歌曲（双源 + 严格匹配 + 审批熔断）
nm.cmd download --only-new
nm.cmd download --dry-run          # 只看清单，不发任何请求

# 5) 处理人工审批队列（CLI 菜单：a 同意重试 / r 拒绝 / d 详情 / s 跳过 / q 退出，大写=全部）
nm.cmd approve
nm.cmd approve --list

# 6) 追溯处理日志
nm.cmd jobs -n 20
```

> 也可以直接用解释器：`.\.venv\Scripts\python.exe -m nm doctor`（需先 `set PYTHONPATH=src`）。

### 双击 `dist\nm.exe` 会看到什么

无参数运行 = 交互菜单（纯手动，不联网、不同步）：

```
nm 0.1.0 —— 本地音乐库管理工具（纯手动触发，不做后台同步）
库状态：歌单 0 首（喜欢 0 / 已取消喜欢 0），已落库文件 0 个，待审批 0 条
[提示] 未配置 NETEASE_COOKIE → [3] sync 不可用（先选 [2] 录入 Cookie）
[提示] 未配置下载执行器 → [5] download 只会把歌曲送进审批队列（见 README §4）
请选择要执行的操作：
  [1] doctor   环境自检              [5] download 下载待补歌曲（--only-new）
  [2] cookie   录入网易云 Cookie      [6] approve  处理人工审批队列
  [3] sync     手动同步              [7] jobs     查看处理日志
  [4] list     查看三栏              [0] 退出
```

细节：输入非法命令会中文提示「参数错误」并**暂停窗口**再退出；执行中出错会打印原因并暂停，
不会出现"窗口一闪而过"。管道/脚本里调用（非交互）则只打印状态页后退出，行为可预测。


## 3. 离线演示（不需要 Cookie / 不联网）

```powershell
.\.venv\Scripts\python.exe tools\make_demo.py
nm.cmd --config demo.toml sync --offline demo\playlist.json
nm.cmd --config demo.toml download --only-new
nm.cmd --config demo.toml approve --list
```

实测输出：

```
[落库]   [101] 晴天 -> 晴天 - 周杰伦.wav  wav 8000ms 1411kbps
[待审批] [102] 夜曲  所有源均无无损资源，禁止自动降级
[待审批] [103] 七里香  所有源均无名称+时长双维匹配的结果
[待审批] [104] 稻香  文件时长差 12000ms 超容差（疑似错误版本）
[落库]   [105] 告白气球 -> 告白气球 - 周杰伦.wav  wav 8000ms 1411kbps +lrc
落库 2，跳过 0，待审批 3，失败 0
```

即：正常曲目落库（文件名/标签全部来自歌单，源文件里的脏标题、假封面被彻底清除），
无无损 / 名称不符 / 时长不符三类全部熔断进审批队列，失效歌曲不进下载队列。

## 4. 接入真实下载源（双源冗余）

默认 `config.toml` 不启用任何执行器（`executors = []`），避免误触发。三种执行器：

| kind | 适用对象 | 工作方式 |
| --- | --- | --- |
| `exec` | 有命令行的下载器（**musicDownload** 等） | 调 `search_cmd` 拿 JSON 候选，再调 `fetch_cmd` 把文件下到暂存区 |
| `dir` | **洛雪音乐助手**（Electron GUI，无 CLI）等任何 GUI 下载器 | 让它把歌下到固定目录，本工具扫描该目录当候选源 |
| `fake` | 离线演示 | 读本地清单，仅供测试（`demo.toml`） |

### 4.1 musicDownload（命令行源，即主源）

先用它的真实参数跑一次 `--help`，再把两条命令填进配置（`{query}` = 歌名+歌手，`{cand_id}` = 候选里返回的 id，
`{output}` = 暂存目录）：

```toml
[download]
executors = ["musicdownload", "luoxue"]     # 主源 musicDownload，失败/无无损自动切洛雪目录源

[executors.musicdownload]
kind = "exec"
search_cmd = "python D:/tools/musicDownload/main.py search --json {query}"
fetch_cmd  = "python D:/tools/musicDownload/main.py download --id {cand_id} -o {output}"
```

要求 `search_cmd` 的 stdout 是 JSON 数组（或 `{"results":[...]}`），每项字段：
`title` / `duration_ms` / `format` / `size_bytes` / `id`。执行器只负责"搜到什么"，
**匹配、音质判定、命名、清洗、核验、落库全由本工具统一执行**。

### 4.2 洛雪音乐助手（GUI 源，即备源）

洛雪是 Electron 桌面程序，没有官方 CLI。做法：在洛雪里把"下载目录"设成固定位置，
本工具直接把这个目录当源（按标签/文件名检索，时长由音频真实解析，仍然过严格双维匹配）：

```toml
[executors.luoxue]
kind = "dir"
path = "D:/Music/lx-download"     # 洛雪设置里的下载目录
recursive = true
```

于是 `nm download` 的流程变成：先在 musicDownload 上搜+下；搜不到 / 没有无损 / 校验不过 →
自动切到洛雪目录找同一首歌的成品，仍然严格校验后才落库。

### 4.3 内置 netease 源（零外部依赖，推荐先跑通）

不装任何第三方工具也能真下载：用同一份 Cookie 走网易云 `/api/` 明文通路，
搜索阶段就把「实际能拿到的音质」查清楚（`/api/song/enhance/player/url/v1?level=lossless`）：

```toml
[download]
executors = ["netease"]

[executors.netease]
kind = "netease"
```

- 拿到 `flac` → 候选进 `pick_best` → 下载 → 校验时长/格式/假无损 → 清洗 → 原子落库
- 只拿得到 `mp3` → 判定「无无损资源」→ 进人工审批，**绝不自动降级**

**实测（2026-11-01，本机真实账号）**：同步 17 首 → 17/17 全部落库为 FLAC（共 534.5 MB），
元数据体检 17/17 只有 `title/artist/album/lyrics` 四个字段，暂存区零残留。

> 注意：weapi（AES+RSA 加密 POST）在这台机器上返回空 body，明文 `/api/` 通路正常；
> 客户端已按实测结果固定走 `/api/`，weapi 加密代码保留但不再用于业务。

## 5. 目录结构

```
Music/
├─ nm.cmd                   # 启动器（源码运行）
├─ pack_entry.py            # PyInstaller 打包入口（避免与 nm 包同名）
├─ nm.spec                  # 单文件打包规格
├─ config.toml              # 主配置（路径/命名/容差/限速/执行器）
├─ .env                     # NETEASE_COOKIE（git 忽略）
├─ src/nm/
│  ├─ cli.py                # 命令解析与输出
│  ├─ config.py             # toml + .env 加载，路径全部相对程序目录
│  ├─ db.py                 # SQLite：songs/playlist_state/files/jobs/approvals/sync_runs
│  ├─ netease.py            # weapi 客户端（我喜欢的音乐 / 歌曲详情 / LRC）
│  ├─ sync.py               # 云端快照 → 三栏状态机 + 差异(+-!)
│  ├─ naming.py             # 名称归一化 / 文件名生成
│  ├─ match.py              # 严格双维匹配 + 音质闸门 + 假无损
│  ├─ downloaders.py        # 执行器接口（fake / exec）+ 注册装配
│  ├─ limiter.py            # 限速 + 滑动窗口 + 退避
│  ├─ pipeline.py           # 下载流水线与熔断归因
│  ├─ tagging.py            # 全清后补标签 + 歌词侧车
│  ├─ staging.py            # 暂存区 / 原子移动 / 哈希
│  └─ approval.py           # 审批队列 CLI 菜单
├─ tests/                   # 58 个测试（含端到端）
├─ tools/                   # make_demo / probe_netease / dl（HTTPS 下载器）/ build_exe.ps1
├─ docs/DEV_ENV.md          # 本机环境踩坑与既定方案
├─ runtime/python311/       # 内置 Python 解释器（绿色，不写注册表）
├─ demo/                    # 演示数据（歌单快照、假源清单、脏源音频）
└─ dist/nm.exe              # 单文件可执行程序（构建产物）
```

运行期自动生成（git 忽略）：`data/`（nm.sqlite3、logs/、snapshots/）、`staging/`、`library/`。

## 6. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q     # 74 passed
powershell -ExecutionPolicy Bypass -File tools\check.ps1   # 测试 + 重打包 + exe 隔离冒烟
```

覆盖：名称归一化与文件名安全、严格双维匹配边界（容差 ±1ms）、三栏状态机（新增/移除/失效/
误触恢复/版权恢复/云端重排）、WAV 脏标签全清与四字段回写、限速与退避、weapi 加密往返、
审批菜单交互（单条/批量/跳过/拒绝）、CLI 双击/菜单/暂停行为、端到端链路
（同步→下载→清洗→落库→审批）与幂等性、暂存区无残留、`verify_only_allowed` 字段洁癖。

> `check.ps1` 的 exe 冒烟**跑在 `%TEMP%` 的临时沙箱**里，不会往 `dist\` 里塞演示歌曲/数据库——
> 交付目录始终是干净的（`dist\nm.exe` 一个文件）。

## 7. 打包与分发（干净版，不含任何人的 Cookie）

```powershell
powershell -ExecutionPolicy Bypass -File tools\make_release.ps1   # 产物在 release\
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1      # 只出单文件 exe
```

`release\` 就是发给别人的东西，只有三个文件：

```
release\
├─ nm.exe            11.77 MB，单文件，控制台程序
├─ config.toml       默认启用内置 netease 源，路径全部相对自身
└─ 使用说明.txt       三步上手 + 常见问题
```

`make_release.ps1` 会强制校验：**不含 `.env`、不含 `data/`、不含 `library/`**，并在
`%TEMP%` 沙箱里跑一次全新启动，确认首屏给出登录引导且不会自己生成 `.env`。

全新环境首次双击看到的引导（实测）：

```
库状态  ：歌单 0 首（喜欢 0 / 已取消喜欢 0），已落库文件 0 个，待审批 0 条
[提示] 未登录网易云 → 先选 [2] 登录（手机扫码，10 秒搞定）
请选择要执行的操作：
  [1] doctor   环境自检          [5] download 下载待补歌曲
  [2] login    登录网易云        [6] approve  处理人工审批队列
  [3] sync     手动同步          [7] jobs     查看处理日志
  [4] list     查看三栏          [0] 退出
```

登录三种方式（`nm cookie` 里选，或分别用命令）：

| 方式 | 命令 | 适用 |
| --- | --- | --- |
| 手机扫码（推荐） | `nm login` | 任何环境；自动打开二维码页面，手机确认后自动写入 `.env` 并回读昵称验证 |
| 读取浏览器 | `nm cookie --from-browser` | 浏览器已登录 music.163.com；Chromium 运行时会独占锁 Cookies 库，需先退出浏览器 |
| 手动粘贴 | `nm cookie` 选 3 | 兜底；不回显、只存本机 `.env` |

打包两个坑（已解决，详见 `docs/DEV_ENV.md` §4）：入口脚本不能与包同名；`pathex` 必须绝对路径。

## 8. 已知限制（MVP 范围外，下一步可补）

- **网易云接口需要登录 Cookie**：匿名请求当前返回 `{"code":20001}` / 空 body，填 `NETEASE_COOKIE`
  后 `sync` 才可用；`download`（不取歌词时）不依赖 Cookie。
- **真实下载源未内置**：不猜测第三方工具 CLI，统一用 `exec` 执行器接入（见 §4）。
- 未实现：播放器、U 盘增量同步、Git 自动备份、GUI/Web 审批界面。
- 文档待确认项处理：审批载体采用 **CLI 菜单**；「已取消喜欢」默认 **永久保留**（`unliked` 栏手动删除）。

## 9. 环境说明

本机 Windows 上 `curl.exe` / `Invoke-WebRequest` 的 HTTPS 全部失败（Schannel
`SEC_E_NO_CREDENTIALS`），因此下载与接口调用一律走 Python 自带 OpenSSL；解释器用 NuGet 分发包
解压（官方 MSI 在受限目录下不可用）。完整踩坑记录见 `docs/DEV_ENV.md`。
