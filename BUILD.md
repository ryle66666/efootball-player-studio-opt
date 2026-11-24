# 构建与发布 / Build and release

在 Windows x64 上使用 Python 3.12。共享源码已包含构建好的界面。修改界面后，使用 esbuild 0.25.12 重建。构建时，`--tools` 指向上一版免安装包或兼容的 V17 工坊所带的 `tools` 目录。

Use Python 3.12 on Windows x64. The repository includes the compiled web UI. Rebuild it with esbuild 0.25.12 after UI changes. Point `--tools` at the `tools` folder from an existing portable release or a compatible V17 Studio package.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r Windows/requirements.txt -r Windows/requirements-build.txt
.\.venv\Scripts\python.exe prepare_source.py
.\.venv\Scripts\python.exe -m unittest discover -s tests/quick
.\.venv\Scripts\python.exe Windows/build_windows.py --tools 'C:\PlayerStudio\tools'
.\.venv\Scripts\python.exe Windows/build_update.py --portable release/Portable
```

修改界面时 / After UI changes

```powershell
.\.venv\Scripts\python.exe Windows/build_frontend.py 'C:\BuildTools\esbuild.exe'
```

`Windows/app.py` 的 `RELEASE_VERSION` 是发布版本。发布时将 `release/*-windows-update.zip` 上传到匹配版本号的正式 GitHub Release，例如 `v1.0.0`。同一 ZIP 既能首次解压使用，也能由工坊在线更新。请保留 GitHub 上传附件生成的 SHA-256。

`RELEASE_VERSION` in `Windows/app.py` is the product version. Upload `release/*-windows-update.zip` to a matching published GitHub Release, such as `v1.0.0`. The same ZIP serves first-time installation and in-app updates. Use the SHA-256 GitHub generates for the uploaded asset.

发布前，在未配置 Python 的 Windows 环境启动成品，核对首页、资料库和更新。公开包只包含程序、默认资料与工具。个人数据、游戏 APK 和准备好的资源缓存从现有安装单独保留。

Before publishing, smoke-test the executable on Windows without Python configured, including the home screen, library, and update flow. The public package contains the program, default catalogue, and tools. Personal data, game APKs, and prepared caches stay with the existing installation.
