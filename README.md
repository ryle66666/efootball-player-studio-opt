# 球员工坊 · 优化版 / Player Studio Opt

[中文](#中文) · [English](#english) · [Releases](https://github.com/ryle66666/efootball-player-studio-opt/releases)

## 中文

球员工坊 · 优化版是原项目的独立 Windows 分支，面向 eFootball V17 系列游戏底包。首发版本号为 **1.0.0**。通过工坊可以编辑球员、卡面、教练、卡包和赛事，并生成适配的游戏 APK。

### 下载与使用

1. 在 [Releases](https://github.com/ryle66666/efootball-player-studio-opt/releases) 下载 Windows ZIP，并完整解压。
2. 打开 `Portable` 文件夹，双击 `PlayerStudioOpt.exe`。Windows 免安装版自带 Python 运行环境。
3. 在工坊中选择你自己的兼容主 APK 和配套离线资源包。导入后就可以浏览资料库、编辑球员和生成游戏。

### 优化版功能

- **资料库卡面**　按底包绑定还原大卡、星级和总评。动态背景提前生成可复用封面，翻页读取静态缩略图。
- **比赛手感**　可选原版、工坊版和自定义。自定义显示原版与工坊参考值。
- **工作台**　支持亮色和深色界面、中英文切换、存储管理、导出恢复与球员小包。
- **在线更新**　在首页「软件更新」检查、下载、校验并重启。工程、签名和资料库保留在本地。

### 数据与验收

Windows 版随包提供工坊程序和所需工具。自建工程和签名在 `data`，准备好的游戏资源在 `library`。更换电脑或手动升级前，请备份这两个目录。

桌面端已验证启动、资料库、编辑、导出流程和更新恢复。比赛手感及小包连续安装仍需 Android 实机验收。V32 使用自己的版本适配。

源码构建和发布步骤见 [BUILD.md](BUILD.md)，功能记录见 [更新日志](更新日志.md)。

### 致谢

优化版基于 [light23335 的球员工坊](https://github.com/light23335/efootball-player-studio)。项目支持的游戏由 KONAMI 开发。随包第三方工具附有各自的来源和许可说明。

## English

Player Studio Opt is an independent Windows fork of the original Player Studio, built for eFootball V17-series game packages. Its first release is **1.0.0**. The desktop editor supports players, cards, managers, packs, tournaments, and compatible game APK exports.

### Download and start

1. Download the Windows ZIP from [Releases](https://github.com/ryle66666/efootball-player-studio-opt/releases) and extract it.
2. Open `Portable` and run `PlayerStudioOpt.exe`. The x64 portable edition includes its Python runtime.
3. Import your own compatible game APK and companion resource package. Then browse the library, edit players, or build a game APK.

### Features

- **Card library** restores card art, stars, and ratings from available bindings. Animated backgrounds use reusable static thumbnails in the list.
- **Match feel** offers Original, Studio, and Custom presets, including reference values for custom tuning.
- **Workspace** offers light and dark themes, Chinese and English, storage management, export recovery, and player update packs.
- **In-app updates** let you check, download, verify, and restart from the home screen. Projects, signing files, and the resource library stay on your computer.

### Data and verification

The portable build includes the editor and its tools. Back up `data` for projects and signing files, and `library` for prepared game resources, before moving or manually updating an installation.

Desktop checks cover startup, the library, editing, export workflows, and update recovery. Match feel and repeated pack installations still need Android device testing. V32 uses a separate adapter.

See [BUILD.md](BUILD.md) for source builds and release steps, and the [changelog](更新日志.md) for changes.

### Credits

Based on [Player Studio by light23335](https://github.com/light23335/efootball-player-studio). The supported game is developed by KONAMI. Bundled third-party tools include their own source and license notices.
