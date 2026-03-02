# YouTube 硬字幕发布工具（mac GUI）

一个简单的 mac GUI 工具：
- 拖入视频和简体中文 `srt`
- 无 `srt` 时可自动拾取简体中文字幕（语音识别）
- 有 `srt` 但时间轴不准时，可用自动拾取时间轴校准原字幕文字
- 自动转换为繁体中文
- 将繁体字幕硬压到视频
- 字幕大小可选（小/中/大）
- 提供两档编码模式，平衡画质和体积

## 1. 环境要求

- macOS
- Python 3.10+
- 已安装 `ffmpeg`（你当前环境已安装）
  - 推荐安装命令：`brew install ffmpeg`

## 2. 安装依赖

```bash
cd /Users/cosmo/go/src/github.com/autocaption
pip3 install -r requirements.txt
```

建议（更稳）使用项目虚拟环境：

```bash
cd /Users/cosmo/go/src/github.com/autocaption
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. 运行

```bash
python3 app.py
```

## 4. 使用方式

1. 将视频文件和简体 `srt` 拖入窗口（或点“选择文件”）。
   - 也可以不提供 `srt`，改用自动拾取。
2. 可选开启“自动拾取字幕（无SRT时）”，并选择识别模型（small/medium）。
3. 若你已提供 `srt` 且文字更可信，可开启“有SRT时用自动时间轴校准文字”。
4. 选择字幕大小（小/中/大）。
5. 选择画质模式：
   - `极致画质`：`libx264 -crf 17`
   - `体积优先`：`libx264 -crf 20 -maxrate 8M -bufsize 16M`
6. 可选填写“自定义CRF”：
   - 留空：使用画质模式预设 CRF
   - 填写整数：覆盖预设 CRF（范围 `0-51`，建议 `17-23`）
7. 选择输出路径（建议 `.mp4`）。
8. 点击“开始处理”。

## 5. 关键说明

- 硬字幕必须重编码视频，因此文件体积一定会变化。
- `极致画质`为高质量有损，通常接近原始观感，但不是数学无损。
- `体积优先`增加码率上限，能显著降低“体积暴涨”风险。
- 自定义 CRF 优先级高于画质模式中的 CRF 预设。
- 输出容器默认 `mp4`，适合 YouTube 上传。
- 自动拾取基于 `faster-whisper`，首次使用会下载模型，可能较慢。
- 时间轴校准会保留你提供的字幕文字，仅替换为自动拾取时间轴，并做防重叠与提前收口处理。

## 6. 打包为双击 App（macOS）

离线环境下可直接生成可双击 `.app`：

```bash
bash build_macos_app.sh
```

完成后产物在 `dist/TradSubtitleBurner.app`。

说明：
- 该 `.app` 会调用本机 Python 运行 `app.py`。
- 若项目目录存在 `.venv`，`.app` 会优先使用 `.venv` 的 Python 与依赖。
- 首次运行前请确保依赖已安装：`pip3 install -r requirements.txt`。

## 7. 常见问题

- 报错 `ModuleNotFoundError: No module named 'requests'`：
  - 说明 `faster-whisper` 依赖未完整安装到当前 Python 环境。
  - 运行：`.venv/bin/pip install -r requirements.txt`
