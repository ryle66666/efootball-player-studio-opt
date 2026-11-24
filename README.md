# efootball-player-studio-opt

> **声明与致谢**：球员工坊-优化版基于 [@light23335](https://github.com/light23335) 的球员工坊开发，面向 [efootball_73](https://t.me/efootball_73) 制作的 **73eFootball V17**。
>
> 本项目完全免费，源码公开，保留原项目的作者署名与来源。

[English](#english)

## 📖 项目简介

球员工坊-优化版是面向 73eFootball V17 的 Windows 编辑工具。首发版本号为 **1.0.0**，可编辑球员、卡面、教练、卡包与赛事，并生成兼容的 Android 游戏 APK。优化重点是让资料库浏览、反复导出和后续更新更方便。

### ✨ 本分支主要修改与优化

- **性能优化**
  - 首页按需加载 3D、卡面和赛事模块。离开预览页时回收资源，重复预览可复用模型解码结果。大型工程和 APK 采用分块读写。
  - 资料库、我的球员和首页统一使用完整竖卡缩略图。人物、背景和队徽等资源按页批量读取。动态背景提前生成并共用静态封面，翻页直接读取图片。
  - 完整 APK 导出复用压缩结果，降低容量预估的重复开销。导出后核对 APK 签名和包内资源，中断任务可按记录恢复。
- **功能新增**
  - 默认浅色主题，可切换深色液态玻璃主题和中英文。主题和语言偏好保存在本机。
  - 新增「比赛手感」的原版、工坊版和自定义三档。原版档恢复 202 项已知比赛常量和动作时序，自定义显示原版和工坊参考值，可直接调整接球转身、传球、弧线、反弹和搓射等数值。
  - 支持按球员搜索并替换外观模板，保留已调好的 14 项体型和肤色设置。新增 8 款门将专属手套，以及左右护腕、紧身衣裤、球衣下摆、脚踝绷带和冬季外场手套选项。
  - 「我的锦标赛」可按场随机或固定时间、季节、天气和场地状况，也可单独选择草皮长度。原有球场、比赛用球和赛事进度一并保留。
  - 存储管理显示硬链接去重后的实际占用，按包名管理通过校验的完整 APK 底包。支持核对和切换资料库来源，预览清理旧 APK、过期小包和缓存。工程、签名、当前基准和恢复记录受保护。
  - 兼容基准支持用 `.73pack` 独立追加或按永久编号更新球员。可预览清单，导入时核对目标版本、基准、校验值和可用空间，并记录新增、更新、跳过和拒绝的原因。从「我的球员」也可直接把所选工程带入导出页。
  - 提供 Windows 免安装包。首页「软件更新」可检查、下载和校验 GitHub Release，确认后重启安装并保留本机数据。
- **问题修复**
  - 修复部分底包自建卡在资料库只显示小头像的问题。恢复能核实的工坊自建卡星级和基础总评，并识别外部自建卡的大图绑定。
  - 修复部分精选卡重复叠头像、前景盖住人物和小头像被拉伸的问题。窄窗口的资料库网格与侧栏也随可用宽度调整。
  - 修复浅色技能标签、英文术语、年龄输入框和实装选择操作。加强旧小包防降级、重复导入和中断恢复，避免共存版与更新版交替导出时误清理另一套底包。

完整变更见 [更新日志](更新日志.md)。原版发布信息可关注 [原作者主页](https://github.com/light23335)。

---

## 🛠️ 本地运行与使用

1. 从 [Releases](https://github.com/ryle66666/efootball-player-studio-opt/releases) 下载 Windows ZIP 并完整解压。
2. 打开 `Portable` 文件夹，双击 `球员工坊-优化版.exe`。免安装版自带运行环境。
3. 在工坊中选择自己的 **73eFootball V17** 主 APK 和配套离线资源。修改比赛手感后，需重新生成并安装游戏 APK。
4. 搬迁或手动升级前，备份 `data` 和 `library`。从源码构建请参阅 [BUILD.md](BUILD.md)。

Windows 启动、资料库、完整 APK 签名导出和重启更新已验证。多卡动态小包仍属实验功能。比赛手感、连续导入和中断恢复仍待 Android 真机验证。V32 适配、完整德甲接入、教练小包和 FM 模式仍在待办范围。

---

## 📄 开源协议 (License)

原项目的项目级许可条款尚待原作者确认。项目保留原作者署名和来源。随附第三方组件各自适用其许可，见 [第三方说明](Windows/resources/third-party-licenses) 和 [工具许可](Windows/tools)。

## 🙏 致谢

- 感谢球员工坊原作者 **[light23335](https://github.com/light23335)**。
- 感谢 73eFootball 原作者 **[efootball_73](https://t.me/efootball_73)**。

## ⚠️ 免责声明

* 本项目代码及派生修改内容**仅供个人学习、技术交流与非营利性娱乐使用，严禁用于任何商业用途**（包括但不限于二次售卖、付费定制、商业引流等）。
* 项目中所涉及的游戏界面风格、卡面视觉元素及相关素材，其版权均归原游戏开发商（KONAMI）及对应权利方所有；如涉及侵权，请联系仓库作者及时删除。
* 本工具按“现状”提供，开发者不对因使用或无法使用本工具而导致的任何直接或间接后果承担责任。

---

## English

> **Attribution and acknowledgments**
>
> This project builds on Player Studio by [@light23335](https://github.com/light23335) and supports **73eFootball V17** by [efootball_73](https://t.me/efootball_73). It is free to use, publishes its source here, and retains the original credits.

### 📖 About

球员工坊-优化版 is a Windows editor for 73eFootball V17. Its first version is **1.0.0**. It edits players, cards, managers, packs, and tournaments, and builds compatible Android game APKs. The changes focus on everyday library browsing, repeated exports, and updates.

#### ✨ Main changes

- **Performance**
  - Load 3D, card, and tournament modules on demand. Release preview resources when leaving the page and reuse model decoding on repeat visits. Stream large projects and APK files.
  - Show full portrait-format cards in the library, My Players, and the home screen. Load art and badges in batches and prepare shared stills for animated backgrounds ahead of browsing.
  - Reuse compression results to speed up space estimates for full APK exports. Verify signatures and package contents, and recover interrupted exports from their saved state.
- **New features**
  - Switch between light and dark glass themes and between Chinese and English. Preferences are saved locally.
  - Choose Original, Studio, or Custom match feel. Original restores 202 known match constants and animation timings. Custom lets you edit receiving and turning, passing, curves, rebounds, and curl shots with both reference presets visible.
  - Search for and swap appearance templates while preserving 14 body settings and skin tone. Choose from eight goalkeeper-only glove models and options for wrist tape, base layers, shirt tuck, ankle tape, and winter outfield gloves.
  - Randomize or lock match time, season, weather, and pitch condition. Set grass length separately. Stadium, match ball, and tournament progress are preserved.
  - See actual disk usage with hard links counted once. Manage verified base APKs by package ID, change the library source after verification, and preview cleanup of old APKs, packs, and caches. Projects, signing files, active bases, and recovery records are protected.
  - Append or update players by permanent ID using `.73pack` on a compatible base. Preview the manifest and check target, base, integrity, and available space. Receipts show additions, updates, skips, and rejections. My Players can open the export screen with a project already selected.
  - Run the portable Windows package and check, download, verify, and restart for GitHub updates while keeping local data.
- **Fixes**
  - Restore full card art for supported custom cards that previously showed small portraits. Restore verifiable stars and base ratings for Studio-made cards and resolve large images for externally authored cards.
  - Fix duplicate portraits, decorations covering players, and stretched small portraits. Adjust the library grid and sidebar to fit narrow windows.
  - Fix light-theme skill labels, English terms, the age input, and export selections. Improve safeguards against pack downgrades and repeated imports, and preserve the other base when alternating between side-by-side and update exports.

See the [changelog](更新日志.md) for details and the [original author's profile](https://github.com/light23335) for upstream information.

---

### 🛠️ Run locally

1. Download and extract the Windows ZIP from [Releases](https://github.com/ryle66666/efootball-player-studio-opt/releases).
2. Open `Portable` and double-click `球员工坊-优化版.exe`. The portable build includes its runtime.
3. Import your own **73eFootball V17** game APK and companion offline resources. Rebuild and install the game APK to apply match-feel changes.
4. Back up `data` and `library` before moving or manually updating the installation. See [BUILD.md](BUILD.md) for source builds.

Windows startup, library browsing, signed full-APK export, and restart updates have been verified. Multi-card animated packs are experimental. Match feel, repeated pack imports, and interruption recovery still need Android device testing. V32, full Bundesliga integration, manager packs, and an FM-style mode remain on the roadmap.

---

### 📄 License

The original project's project-wide license is pending confirmation from the original author. Credits and source attribution are retained. Bundled components are covered by their respective [third-party notices](Windows/resources/third-party-licenses) and [tool licenses](Windows/tools).

### 🙏 Credits

- **[light23335](https://github.com/light23335)** for the original Player Studio.
- **[efootball_73](https://t.me/efootball_73)** for 73eFootball.

### ⚠️ Disclaimer

* This project's code and derivative modifications are **for personal learning, technical exchange, and nonprofit entertainment only. All commercial use is prohibited**, including resale, paid customization, and commercial promotion.
* The game interface style, card visuals, and related assets referenced in this project belong to the original game developer (KONAMI) and the respective rights holders. If you believe your rights have been infringed, please contact the repository owner so the material can be removed promptly.
* This tool is provided “as is.” The developers accept no liability for any direct or indirect consequences arising from its use or the inability to use it.
