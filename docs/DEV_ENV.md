# 本机开发环境说明（踩坑与既定方案）

记录本项目在本机（Windows，`D:\application\pc\Music`）落地时验证过的事实，避免重复踩坑。

## 1. TLS：只能用 Python / Node 发 HTTPS

| 工具 | 结果 |
| --- | --- |
| `curl.exe` | `schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS (0x8009030e)`（exit 35） |
| `Invoke-WebRequest` / .NET | 同上，`基础连接已经关闭: 接收时发生错误` |
| Python 自带 OpenSSL | 正常（`OpenSSL 3.0.13`） |
| `node` fetch | 正常 |

结论：所有下载/探测走 Python（`tools/dl.py`）或 Node，不要用 curl / Invoke-WebRequest。

## 2. Python 3.11 的安装方式（官方 MSI 不可用）

- 官方 `python-3.11.9-amd64.exe`：静默安装失败，`exit 0x5 ERROR_ACCESS_DENIED`
  （MSI 需要写 `%LOCALAPPDATA%\Package Cache`，本机受限）。
- 采用 **NuGet 分发包**：`https://globalcdn.nuget.org/packages/python.3.11.9.nupkg`
  - 下载后必须**改名成 `.zip`** 再 `Expand-Archive`（PowerShell 不认 `.nupkg` 后缀）
  - 解出的 `tools\python.exe` 支持 `-m venv`、内置 pip，且不写注册表（绿色）
- 项目内位置：`runtime/python311/tools/python.exe` → `.venv\`

国内可用镜像：

```
https://mirrors.huaweicloud.com/python/3.11.9/          # 官方安装包 / embeddable zip
https://pypi.tuna.tsinghua.edu.cn/simple                # pip 索引
https://registry.npmmirror.com/-/binary/python/3.11.9/  # npmmirror 的 python 二进制
```

```powershell
.\.venv\Scripts\python.exe -m pip install `
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple `
  --trusted-host pypi.tuna.tsinghua.edu.cn `
  requests pycryptodome mutagen pytest pyinstaller
```

> 注意：不要执行 `pip install --upgrade pip`——本机实测会长时间卡住（CPU 满载、无输出），
> 直接用虚拟环境自带的 pip 24.0 即可。

## 3. 网易云接口现状（2026-11-01 用真实账号实测）

| 调用 | 结果 |
| --- | --- |
| `POST /weapi/*`（带 Cookie） | HTTP 200 但 **body 为空** → 该加密通路在本机不可用 |
| `GET /api/nuser/account/get`（带 Cookie） | `code=200`，返回账号 id |
| `GET /api/song/like/get?uid=` | 返回 `ids[]`（「我喜欢的音乐」顺序）+ `checkPoint` |
| `GET /api/v3/song/detail?c=[{"id":..}]` | 完整元数据（`dt`/`st`/`ar`/`al`） |
| `GET /api/song/lyric?id=&lv=-1&kv=-1&tv=-1` | LRC 正常 |
| `GET /api/song/enhance/player/url/v1?level=lossless` | 返回 `type=flac` 直链（本账号 fee=0/fee=1 均有） |

结论：业务全部走 `/api/` 明文 GET + Cookie；`weapi()` 代码保留但不再使用。
`sync` 必须配 `NETEASE_COOKIE`；内置 `netease` 执行器同样依赖它。

**踩坑记录**：同步时把 `TrackDetail` 经 `as_meta()`（键名 `duration_ms`）再喂给
`entry_from_track()`（只认 `dt`）→ 时长全变 0 → 所有歌被判「时长未知」进审批。
修法：`entry_from_track` 兼容 `duration_ms`，并提供 `entries_from_details()` 直连
（不再绕 JSON 键名往返）。回归测试见 `tests/test_sync.py`。

## 4. PyInstaller 单文件打包的两个坑

1. **入口脚本不能与包同名**：入口叫 `nm.py` 时，依赖分析会把 `import nm` 当成入口自身而
   完全不收集 `nm` 包 → 产物运行时报 `ModuleNotFoundError: No module named 'nm'`。
   现方案：入口用 `pack_entry.py`，`--name nm` 只决定 exe 名。
2. **`pathex` 必须用绝对路径**：`nm.spec` 里 `pathex=['src']` 相对路径在本机失效，
   改成 `os.path.join(os.path.dirname(os.path.abspath(SPEC)), 'src')` 后 `nm.cli` 才入包。

验证命令：

```powershell
(Select-String -Path build\nm\Analysis-00.toc -Pattern "nm.cli" -SimpleMatch | Measure-Object).Count  # 应 >= 1
```

另：单文件 exe 首次运行会把 `VCRUNTIME140.dll` 等解包到 `%TEMP%\_MEIxxxx`；
在受限（只读/沙箱）环境下会报 `Failed to extract VCRUNTIME140.dll: fopen: Permission denied`，
这不是产物问题，普通桌面上运行正常。

## 5. 其他约定

- `config.toml` 兼容 UTF-8 BOM（记事本另存不会炸）。
- 控制台为 GBK 时中文正常；输出里刻意不用 emoji（GBK 编码会失败）。
- 所有运行时数据都在程序同级目录：`data/`（db+logs+snapshots）、`staging/`、`library/`。

## 6. PowerShell 操作纪律（本机实测踩过的 8 个坑）

| 坑 | 症状 | 铁律 |
| --- | --- | --- |
| `Start-Job` 跑长任务 | 下一次 pwsh 调用里 `Get-Job` 找不到该 job；`ScheduledJobs` 路径被拒 | **不要用 Start-Job**：长任务用工具的 `run_in_background`，或前台执行并把输出写文件 |
| `cmd \| Select-Object -Last N` | 命令跑满 600s 超时却零输出（管道缓冲，失败/进度全被吞） | 需要看输出就别套 `Select-Object`；要截断就重定向到文件再 `read` |
| `.cmd` 里写中文 `rem` | `'x不是内部或外部命令'` 刷屏 | `.cmd`/`.ps1` 里**只写 ASCII**，中文说明放 `.md` |
| `Set-Content -Encoding UTF8` | PS 5.1 写出带 BOM，`tomllib` 报 `Invalid statement (at line 1, column 1)` | 配置读取显式剥 BOM（已实现）；写文件用 `-Encoding ascii` 或 Python 的 `encoding="utf-8"` |
| `Expand-Archive *.nupkg` | `不是支持的存档文件格式` | 先 `Copy-Item x.nupkg x.zip` 再解压 |
| `python -m nm` 直接跑 | `No module named nm` | 用 `nm.cmd`（内部设 `PYTHONPATH=src`），或 `$env:PYTHONPATH='src'` |
| 相对路径参数 | `FileNotFoundError: ..\demo\playlist.json`（基准是 CWD 不是 exe 目录） | 记住两种基准：**路径参数相对 CWD**，**config/库/data 相对程序目录**；写脚本时显式拼绝对路径 |
| `Select-Object -First N` 截断管道 | 命令 exit 1、后续步骤看似"失败" | 这是断管不是真失败；验证脚本里不要用它截断子进程输出 |
| **又一次**截断管道（2026-11-01 单轮犯 3 次） | 明明输出正确，却被 env-triage 判成"连续失败" | 探针**不要再手敲 pwsh 管道**：写成 `tools/*.py`，`subprocess` 重定向到文件 + 显式解码后一次性打印（见 `tools/exe_probe.py`） |
| 只改了一处缺省值 | 已把 `DownloadRules.executors` 默认改成 `()`，但 `load_config` 里还写着 `["fake"]` → 全新安装仍报"执行器 fake 缺少 manifest" | 改缺省值/魔法字面量后，**立刻全仓 grep 同一字面量**（`Select-String -Pattern '"fake"'`）再验证 |

## 6.1 双击 exe 的坑（用户实测反馈）

**症状**：双击 `dist\nm.exe` 弹出 `nm: error: the following arguments are required: command` + 英文 usage，
窗口一闪即关——用户以为程序"报错/坏了"。

**根因**：`argparse` 的子命令是 `required=True`，双击等于无参数调用 → 直接 `parser.error()` 退出码 2。
这是 CLI 正常行为，但对 GUI 使用者是灾难性体验。

**修法（已落地，`src/nm/cli.py`）**：

1. 无参数 + 交互终端 → 打印库状态 + 交互菜单 `[1]doctor … [6]jobs [0]退出`（每项等价一条手动命令）
2. 无参数 + 管道/脚本（非 tty）→ 只打印状态页与用法，退出码 0，行为可预测
3. `_Parser.error()` 覆写为中文提示；冻结态且 tty 时 `_hold_open()` 等回车，窗口不再一闪而过
4. 顶层 `except` 兜底：异常打印原因 + 暂停（不含 traceback 一闪）

**测试锁死**（`tests/test_cli.py`，共 12 例）：无参数必须出状态页而非 argparse 报错；tty 下必须暂停；
未知命令必须 exit 2 且中文提示；菜单项分发、无效输入、EOF、命令内部 `SystemExit` 均不崩。

**验证命令**（注意不要用 `Select-Object -First` 截断，会误判成失败）：

```powershell
& dist\nm.exe *> dist\welcome.log ; $LASTEXITCODE   # 期望 0，日志末行是菜单提示符
```

## 7. 一键验证

```powershell
powershell -ExecutionPolicy Bypass -File tools\check.ps1            # 测试 + 重打包 + exe 冒烟
powershell -ExecutionPolicy Bypass -File tools\check.ps1 -SkipBuild # 只跑测试 + 已有 exe 冒烟
```

成功标志：`OK  (nm.exe xx MB, 3 files, 3 approvals, sandbox cleaned)`。任何一步失败会 `throw`
并以非 0 退出，避免"看着跑完了其实没验证"。

**冒烟必须在 `%TEMP%` 沙箱里跑**：早期版本直接在 `dist\` 里跑 demo，结果 `dist\library`、
`dist\data`、`dist\config.toml` 全被演示数据占领，用户双击后看到的是"6 首周杰伦假歌 + 3 条待审批"，
以为程序只会跳过——这是设计缺陷不是程序 bug。现在 sandbox 里跑完即删，`dist\` 只剩 `nm.exe`。

## 8. 用户实测反馈（2026-11-01 16:17）

用户在菜单里按了 `[4] download`，日志显示 `待处理 3 首`（夜曲/七里香/稻香），三首全部
`跳过 - 已在人工审批队列`，落库 0。根因有三层：

1. **数据是演示夹具**：`dist\config.toml` 里执行器是 `fake` + `demo\manifest.json`，库里 6 首全是假歌
2. **没配 Cookie**：`.env` 不存在，`sync` 不可用（当时菜单也没提示）
3. **没配真实执行器**：`executors = []` 时下载只会进审批队列（当时提示不明显）

**修法（已落地）**：

| 改动 | 位置 |
| --- | --- |
| 状态区按需打印 `[提示]`：缺 Cookie / 缺执行器 / 有 N 条待审批 | `cli._print_status` |
| 菜单 `[5]` 动态标注 `（N 条待处理）` | `cli._menu` |
| download 落库 0 时给"下一步"：`3 首卡在人工审批队列 → 先 nm approve` | `cli.cmd_download` |
| 跳过原因带可操作文案 | `pipeline.process_song` |
| `DownloadRules.executors` 与配置文件缺省值改为 `()`（不再默认 fake） | `config.py` |
| 冒烟隔离到 `%TEMP%`，`dist\` 保持干净 | `tools/check.ps1` |
| 新增 `tools/exe_probe.py`：文件重定向下探测 exe 输出并自动按 UTF-8/GBK 解码 | `tools/` |

**顺带发现**：Windows 上 `subprocess.DEVNULL`（NUL 设备）被 `isatty()` 判为 **True**（字符设备），
而重定向自普通文件时为 False。所以"双击"（真控制台）与"脚本重定向"走不同分支是预期行为，
写测试时别拿 `< file` 去验证菜单逻辑。

## 9. 首次上手（给最终用户）

```text
1) 把 dist\nm.exe 放到一个空目录（例如 D:\MusicTool\）
2) 同目录建 .env：NETEASE_COOKIE=MUSIC_U=...; __csrf=...      ← 浏览器登录 music.163.com 后复制
3) 同目录建 config.toml：填 [download].executors 与 [executors.<名>] 的搜索/下载命令
4) 双击 nm.exe → [2] sync → [3] list → [4] download → [5] approve
```

只想先体验流程、不接真实源：用 `python tools\make_demo.py` 生成的 `demo.toml`（假源 + 6 首演示歌），
它只写 `demo\` 目录，不会污染 `dist\`。


