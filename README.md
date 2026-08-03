# 旅迹编年

本地旅行相册生成工具。上传一批旅行照片后，应用按拍摄时间整理、过滤近似照片、挑选具有故事价值的画面，并使用固定版本的 `photo-revival` 规则将入选照片重新绘制为白纸手绘页。最终输出离线 HTML、朋友圈章节长图和分享 ZIP。

## 安装与启动

1. 安装并启动：

   - Windows：运行 `install.bat`，之后运行 `start.bat`。
   - macOS：双击 `install.command`，之后双击 `start.command`。首次启动也可以直接双击 `start.command`，它会自动完成安装。

   安装脚本会安装 Python 包、前端依赖和长图导出所需的 Chromium。运行前请确保系统已安装 Python 3.11+ 和 Node.js 18+。
2. 编辑项目根目录的 `.env`：

```dotenv
OPENAI_BASE_URL=你的baseURL
OPENAI_API_KEY=你的密钥
OPENAI_TEXT_MODEL=支持图片理解的文本模型
OPENAI_IMAGE_MODEL=gpt-image-1
AMAP_API_KEY=高德Web服务Key
IMAGE_GENERATION_INTERVAL_SECONDS=10
```

```text
高德 Key 的申请方式如下：
打开高德开放平台控制台并登录。
完成开发者认证。
进入“应用管理 → 我的应用”，创建一个应用，例如“旅行手记”。
在应用下添加 Key。
服务平台务必选择“Web 服务”，不是 Web 端 JS API。
申请后把 Key 放进项目根目录 .env：
```

照片含有 EXIF GPS 时，应用会使用高德逆地理编码补充城市、区域和附近景点，地点会参与选片、章节编排与旁白生成。前往[高德开放平台控制台](https://console.amap.com/dev/key/app)创建应用并添加 Key，服务平台选择“Web 服务”。配置后需要重启应用；`AMAP_API_KEY` 未配置或地址查询失败时会跳过地点增强，不影响相册生成。

EXIF 原始经纬度仅保存在本机 `.jobs/` 的任务断点中；公开的 Web 页面、`album.json`、长图和分享 ZIP 只包含简短地点名称。相距 200 米以内的照片默认共用一次地址查询，可通过 `LOCATION_CLUSTER_RADIUS_METERS` 调整。

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

## 日志

控制台日志会同时写入 `logs/travel-journal.log`。单个文件达到 5 MB 后自动轮转，默认保留 5 份历史日志；错误记录包含任务 ID、处理阶段和 Python 异常堆栈，但会脱敏 OpenAI、高德 API Key，并且不会记录照片的原始经纬度。

可在 `.env` 中用 `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR` 调整日志级别，默认是 `INFO`。排查失败任务时，优先搜索 `pipeline_failed`、`location_lookup_failed`、`image_generation_failed`、`share_image_export_failed` 或 `request_failed`。

## 测试

```shell
# Windows
.\.app-venv\Scripts\python.exe -m pytest

# macOS
./.app-venv/bin/python -m pytest

npm run build --prefix frontend
```

`third_party/photo-revival` 固定自上游提交 `ca4c3c6c0f812355bd6d815d8a78652db801b7f1`，遵循 MIT License。
