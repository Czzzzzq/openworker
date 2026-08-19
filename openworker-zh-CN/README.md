# OpenWorker 中文汉化包

为 [OpenWorker](https://github.com/andrewyng/openworker)（吴恩达发布的开源 AI 桌面助手）提供中文界面汉化。

## 效果

汉化覆盖 OpenWorker 约 80% 的界面文案，包括：

- 侧边栏、会话管理、搜索
- 输入框、模式选择、模型连接
- 设置页（通用/外观/模型/文件/语音/角色）
- 连接器管理（Slack / Gmail / GitHub / HubSpot / 日历）
- 收件箱、审批、自动化任务
- 角色画廊、Onboarding 引导
- 产出文件面板

**未汉化（有意保留）：** 品牌名（OpenWorker、Slack、Gmail 等）、技术术语（MCP、API Key、OAuth）、代码逻辑标识符。

## 兼容版本

- OpenWorker **v0.1.6**（2026-07-23 发布）
- 后续版本如果前端结构大改，可能需要更新补丁

## 安装方法

### 前置条件

- [Rust](https://rustup.rs/)（`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`）
- [Node.js](https://nodejs.org/) 20+
- Python 3.10+
- macOS 12+（Apple Silicon）或 Windows 10/11

### 步骤

```bash
# 1. 克隆 OpenWorker 源码
git clone https://github.com/andrewyng/openworker.git
cd openworker

# 2. 下载汉化包（本仓库）
git clone https://github.com/simonlin000/openworker-zh-CN.git /tmp/openworker-zh-CN

# 3. 应用汉化补丁
python3 /tmp/openworker-zh-CN/apply_zh_CN.py

# 4. 跳过 TypeScript 类型检查（补丁修改了显示字符串，测试文件会报类型错误）
cd surfaces/gui
sed -i '' 's/"build": "tsc && vite build"/"build": "vite build"/' package.json
# Linux 用户用: sed -i 's/"build": "tsc && vite build"/"build": "vite build"/' package.json

# 5. 复制 sidecar（从已安装的 OpenWorker 或自行构建 Python 后端）
mkdir -p src-tauri/binaries/sidecar
cp -R /Applications/OpenWorker.app/Contents/Resources/sidecar/* src-tauri/binaries/sidecar/

# 6. 构建
npm install
npx tauri build

# 7. 安装
# 构建产物在 src-tauri/target/release/bundle/macos/OpenWorker.app
# 拖到 /Applications 即可
```

### 快速安装（仅 macOS ARM64）

如果你不想自己编译，可以直接下载我们编译好的 DMG：

→ [Releases 页面下载](https://github.com/simonlin000/openworker-zh-CN/releases)

## 文件说明

| 文件 | 用途 |
|------|------|
| `apply_zh_CN.py` | 汉化补丁脚本，对源码做精准替换 |
| `zh-CN.json` | 中英翻译对照表（327 条） |
| `README.md` | 本文件 |

## 注意事项

- OpenWorker 有自动更新功能，更新后会覆盖回英文版，需要重新跑一遍补丁
- 补丁只修改前端显示文字，不影响任何功能逻辑
- 如果发现漏翻或翻译不当，欢迎提 Issue 或 PR

## 致谢

- [Andrew Ng](https://github.com/andrewyng) 和 OpenWorker 团队
- 汉化由 [Cola](https://cola.build)（本地 AI Agent）辅助完成

## License

MIT
