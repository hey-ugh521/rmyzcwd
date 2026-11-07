# P3 组件下载器（L3）

> 状态：2026-11-08 完成。`ops/l3/downloader.py` 按 manifest 实现 下载→校验→解压→安装→修复/卸载/升级。

## 1. 命令

```powershell
python ops\l3\downloader.py --manifest ops\l3\packs\fragments.json --root D:\Mobius list
python ops\l3\downloader.py ... install --required            # 安装全部必需组件（拓扑序）
python ops\l3\downloader.py ... install python-core python-kokoro
python ops\l3\downloader.py ... update                       # 已安装组件全部升级（--force 重装）
python ops\l3\downloader.py ... update python-core
python ops\l3\downloader.py ... repair python-core           # 校验缓存并重解压
python ops\l3\downloader.py ... uninstall python-gateway     # 有依赖时拒绝
python ops\l3\downloader.py ... verify                       # 校验 versions.json 与目录
```

选项：

| 选项 | 说明 |
|---|---|
| `--manifest` | 组件清单（默认 `ops/l3/packs/fragments.json`） |
| `--root` | 安装根（默认 `MOBIUS_ROOT` 或当前目录） |
| `--cache` | 下载缓存目录（默认 `<root>/components/cache`） |
| `--source-dir` | 本地组件目录，优先作为下载源（离线安装/测试） |
| `--sevenzip` | 7z 路径（默认 `D:\AI\napcat-native\7z.exe`，或 `MOBIUS_7Z`） |
| `--force` | 强制重装 |

## 2. 实现要点

| 能力 | 说明 |
|---|---|
| 多源 | `urls` 按顺序尝试；`--source-dir` 自动前置本地文件源 |
| 断点续传 | `.part` + HTTP `Range`；服务器不支持 Range 时自动从头下载 |
| 强制校验 | SHA256 + 大小；不匹配即判失败并删临时文件 |
| 解压 | 7z 优先（同时支持 zip）；**解压到 target 父目录再顶层 rename**，规避 Windows 超长路径 `WinError 3` |
| 安装记录 | `<root>/versions.json`（id/version/sha256/target/installed_at） |
| 本地覆盖 | `<root>/components.local.json` 合并覆盖 manifest（可改 urls/sha256，第三方组件确认机制） |
| 依赖 | `depends_on` 拓扑排序；卸载时检查反向依赖 |
| 修复 | 缓存 hash 校验 → 重解压；缓存损坏则重下 |
| 卸载 | 仅删组件 target 目录，保留缓存 |

## 3. 验收证据（本地 file:// 源）

| 测试 | 结果 |
|---|---|
| `list` | 8 个组件（required 标记正确） |
| `install python-core python-gateway` | ✅ 下载→校验→解压→versions.json |
| `install --required` | ✅ python-core/gateway/memory/langbot/python-langbot 全部安装 |
| `verify` | ✅ 5/5 OK |
| `repair python-core` | ✅ 缓存命中并重解压 |
| `uninstall python-gateway` | ✅ 目录删除、记录移除 |
| 多源失败切换 | ✅ 第一个源失败后自动换源 |
| SHA256 不匹配 | ✅ 失败并退出码 1（本地 `components.local.json` 覆盖验证） |
| 超长路径（langbot data/storage） | ✅ 父目录暂存 + 顶层 rename 后安装成功 |

## 4. 与 L3 计划的衔接

- P4 首启向导可直接调用 `Downloader` 类（或 CLI）完成组件安装/修复
- P3 剩余：组件托管（GitHub Releases/OSS）、真实 HTTP 续传实测（file:// 下为整体复制）
- manifest 初始数据：`ops/l3/packs/fragments.json`（8 组件，12.22GB）
