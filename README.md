# YouTube 硬字幕发布工具（mac GUI）

一个简单的 mac GUI 工具：
- 拖入视频和简体中文 `srt`
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

## 3. 运行

```bash
python3 app.py
```

## 4. 使用方式

1. 将视频文件和简体 `srt` 拖入窗口（或点“选择文件”）。
2. 选择字幕大小（小/中/大）。
3. 选择画质模式：
   - `极致画质`：`libx264 -crf 17`
   - `体积优先`：`libx264 -crf 20 -maxrate 8M -bufsize 16M`
4. 可选填写“自定义CRF”：
   - 留空：使用画质模式预设 CRF
   - 填写整数：覆盖预设 CRF（范围 `0-51`，建议 `17-23`）
5. 选择输出路径（建议 `.mp4`）。
6. 点击“开始处理”。

## 5. 关键说明

- 硬字幕必须重编码视频，因此文件体积一定会变化。
- `极致画质`为高质量有损，通常接近原始观感，但不是数学无损。
- `体积优先`增加码率上限，能显著降低“体积暴涨”风险。
- 自定义 CRF 优先级高于画质模式中的 CRF 预设。
- 输出容器默认 `mp4`，适合 YouTube 上传。

## 6. 打包为双击 App（macOS）

离线环境下可直接生成可双击 `.app`：

```bash
bash build_macos_app.sh
```

完成后产物在 `dist/TradSubtitleBurner.app`。

说明：
- 该 `.app` 会调用本机 Python 运行 `app.py`。
- 首次运行前请确保依赖已安装：`pip3 install -r requirements.txt`。
