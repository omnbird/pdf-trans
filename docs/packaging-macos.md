# 打包 macOS App

## 前提

| 依赖 | 检查命令 |
|---|---|
| Xcode Command Line Tools | `xcode-select -p` |
| Rust（含 macOS target） | `rustc -V`、`rustup target list --installed` |
| Node + 依赖 | `node -v`、`npm ci`（或 `npm install`） |
| Python 环境（引擎用） | `python3 -c "import openai, pydantic, toml, tenacity, PIL, fitz, reportlab, babeldoc"` |

引擎依赖 `babeldoc`（排版保留）和 `pymupdf` 等，**不随 App 打包**，用的是本机 Python。

## 构建

```bash
cd ~/fullstack/pdf-trans
npm run tauri build
```

产物（Apple Silicon 默认只出本机架构）：

```
src-tauri/target/release/bundle/macos/PDF Trans.app
src-tauri/target/release/bundle/dmg/PDF Trans_0.1.0_aarch64.dmg
```

通用二进制（Intel + ARM 都能跑，体积翻倍）：

```bash
rustup target add x86_64-apple-darwin
npm run tauri build -- --target universal-apple-darwin
```

## App 去哪里找 Python 引擎

`.app` 从 Finder 启动时工作目录是 `/`，所以引擎路径不能只靠 cwd。`resolve_project_path()`
按以下顺序找 `python_engine/main.py`，命中即用：

1. `$PDF_TRANS_HOME`（显式指定，最保险）
2. 当前目录及其父目录（`npm run tauri dev` 场景）
3. 可执行文件所在目录及其上 4 级（`.app/Contents/MacOS` 场景）
4. **上次成功运行时记住的项目目录**
   （`~/Library/Application Support/pdf-trans/project_dir.txt`）
5. `~/fullstack/pdf-trans`、`~/projects/pdf-trans`、`~/Documents/pdf-trans`、`~/pdf-trans`

只要先用 `npm run tauri dev` 成功跑过一次（或项目放在上面这些路径），
打包后的 App 就能自己找到引擎，无需额外配置。

如果换了项目位置，用环境变量指定（重启 App 生效）：

```bash
launchctl setenv PDF_TRANS_HOME /Users/lula/my-workspace/pdf-trans
# 取消：launchctl unsetenv PDF_TRANS_HOME
```

Finder 双击启动的 App 看不到 shell 里的 export，所以必须用 `launchctl setenv`。

## Gatekeeper

自签/未签名的 App 会被拦截（"已损坏，无法打开"或"无法验证开发者"）：

```bash
xattr -dr com.apple.quarantine "/Applications/PDF Trans.app"
```

或者：右键 App → 打开 → 再次确认。

想正经签名（可选，需要 Apple Developer 账号）：

```bash
codesign --deep --force --options runtime \
  --sign "Developer ID Application: YOUR NAME (TEAMID)" \
  "/Applications/PDF Trans.app"
```

## 这个 App 不是自包含的

打包出来只有前端 + Rust 外壳（几十 MB），运行时仍然依赖：

- 本机 Python 3 及 `requirements.txt` 里的包（含 `babeldoc`）；
- BabelDOC 的 ONNX 模型与字体：`~/.cache/babeldoc`（首跑自动下载，约 330 MB）；
- 源码目录（`python_engine/`）。

想做真正独立分发（拖进任何 Mac 就能用），需要把引擎打成 PyInstaller 二进制当
Tauri sidecar 带上（`bundles.externalBin` + `tauri.conf.json` 的 `resources`），
体积约 +1 GB，且 BabelDOC 的模型仍需首次联网下载。以自用为目的不建议折腾。
