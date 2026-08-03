# 旅迹编年

本地旅行相册生成工具。上传一批旅行照片后，应用按拍摄时间整理、过滤近似照片、挑选具有故事价值的画面，并使用固定版本的 `photo-revival` 规则将入选照片重新绘制为白纸手绘页。最终输出离线 HTML、朋友圈章节长图和分享 ZIP。

## 安装与启动

1. 安装并启动：

   - Windows：运行 `install.bat`，之后运行 `start.bat`。
   - macOS：双击 `install.command`，之后双击 `start.command`。首次启动也可以直接双击 `start.command`，它会自动完成安装。

   安装脚本会安装 Python 包、前端依赖和长图导出所需的 Chromium。运行前请确保系统已安装 Python 3.11+ 和 Node.js 18+。
2. 编辑项目根目录的 `.env`：

```dotenv
OPENAI_BASE_URL=https://www.hellotranfer.top/
OPENAI_API_KEY=你的密钥
OPENAI_TEXT_MODEL=支持图片理解的文本模型
OPENAI_IMAGE_MODEL=gpt-image-1
IMAGE_GENERATION_INTERVAL_SECONDS=10
```

3. 运行对应系统的 `start` 脚本，浏览器会打开 <http://127.0.0.1:8000>。

开发时 Windows 运行 `dev.bat`，macOS 双击 `dev.command`。React 使用 `5173` 端口，FastAPI 使用 `8000` 端口。

## 输出

每本相册保存在 `outputs/<旅行名称>/`：

```text
index.html              离线相册
album.json              结构化相册数据
assets/photos/          photo-revival 成片
sources/                入选原图
exports/                朋友圈章节长图
<旅行名称>.zip          不含 sources 的分享包
```

同一时间只执行一个相册任务，入选照片也会逐张串行生成。`IMAGE_GENERATION_INTERVAL_SECONDS` 控制相邻两次图片请求之间的等待秒数。刷新或关闭浏览器不会停止任务；停止 FastAPI 后，未完成任务会标记为中断，不会自动续跑。

## 测试

```shell
# Windows
.\.app-venv\Scripts\python.exe -m pytest

# macOS
./.app-venv/bin/python -m pytest

npm run build --prefix frontend
```

`third_party/photo-revival` 固定自上游提交 `ca4c3c6c0f812355bd6d815d8a78652db801b7f1`，遵循 MIT License。
