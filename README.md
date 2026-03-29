# YouTube 硬字幕发布工具（mac GUI）

一个简单的 mac GUI 工具：
- 拖入视频和简体中文 `srt`
- 无 `srt` 时可自动拾取简体中文字幕（语音识别）
- 有 `srt` 但时间轴不准时，可用自动拾取时间轴校准原字幕文字
- 自动转换为繁体中文
- 将繁体字幕硬压到视频
- 字幕大小可选（小/中/大）
- 字幕风格可选（标准 / 大字描边 / 讲解视频风格）
- 可选强制一行字幕显示
- 提供三档编码模式，默认“匹配源参数（快速推流）”

## 1. 环境要求

- macOS
- Python 3.10+
- 已安装 `ffmpeg`（你当前环境已安装）
  - 若要硬字幕压制，推荐安装支持 `libass/subtitles` 的版本，例如 `ffmpeg-full`

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
   - 当视频时长约大于 12 分钟且需要 ASR 时：若本地已缓存 `medium`，程序会自动切换到 `medium`。
   - 若 `medium` 未缓存，程序会保持 `small`，避免首次下载造成长时间等待。
3. 若你已提供 `srt` 且文字更可信，可开启“有SRT时用自动时间轴校准文字”。
   - 可同时开启“快速对轴（推荐）”：仅在“校准时间轴”流程使用更快参数（`beam_size=1`）。
4. 选择字幕大小（小/中/大）。
   - 横屏、竖屏、近方屏会自动按画面比例修正字号，减少同一档位在竖屏里显得过大的问题。
5. 选择字幕风格：
   - `标准`：当前默认样式，适合大多数视频
   - `大字描边`：更粗、更醒目，适合短视频或信息密度高的内容
   - `讲解视频风格`：大字号粗描边 + 动态半透明底板，1 行/2 行字幕都会自动贴合
   - `底板透明度` 可调（仅对 `讲解视频风格` 生效）
   - 若勾选 `强制一行字幕`，程序会按字幕大小、字幕风格和横竖屏比例更严格地拆分超长句，尽量保持每次只显示一行
6. 选择画质模式：
   - `匹配源参数（快速推流，默认）`：自动探测源视频参数，尽量对齐码率/GOP/音频码率，减少 YouTube 推流和处理等待
   - `极致画质`：`libx264 -crf 17`
   - `体积优先`：`libx264 -crf 20 -maxrate 8M -bufsize 16M`
7. 可选填写“自定义CRF”：
   - `匹配源参数`模式：CRF 不生效（按源视频码率）
   - 其余模式留空：使用画质模式预设 CRF
   - 填写整数：覆盖预设 CRF（范围 `0-51`，建议 `17-23`）
8. 选择输出路径（建议 `.mp4`）。
9. 点击“开始处理”。

## 5. 关键说明

- 硬字幕必须重编码视频，因此文件体积一定会变化。
- 默认“匹配源参数”会用 `ffprobe` 读取源视频码率、fps、音频码率，并在安全边界内对齐：
  - 视频码率边界：`800k ~ 20M`
  - 音频码率边界：`96k ~ 192k`（缺失时回退 `128k`）
- 匹配模式会设置 YouTube 友好的 GOP：约 2 秒关键帧（`g=fps*2`）。
- 匹配模式会添加 `-movflags +faststart`，便于平台更快读取媒体头。
- `极致画质`为高质量有损，通常接近原始观感，但不是数学无损。
- `体积优先`增加码率上限，能显著降低“体积暴涨”风险。
- 自定义 CRF 优先级高于画质模式中的 CRF 预设。
- 输出容器默认 `mp4`，适合 YouTube 上传。
- 即使匹配源参数，硬字幕仍需重编码，无法做到与原片 100% 比特流一致。
- 程序会优先探测 `ffmpeg-full`，避免系统默认精简版 `ffmpeg` 缺少 `subtitles` 滤镜。
- 自动拾取基于 `faster-whisper`，首次使用会下载模型，可能较慢。
- 时间轴校准会保留你提供的字幕文字，仅替换为自动拾取时间轴，并做防重叠与提前收口处理。
- 长视频（约 12 分钟以上）会自动启用“分段锚点校准”，降低后半段累计漂移风险。
- 为提升速度，识别线程上限已提高；日志会每 20 条输出一次识别进度。

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

## 8. 推流速度本地对比（推荐）

项目内置脚本可快速比较“原片 vs 压字幕后”关键参数：

```bash
cd /Users/cosmo/go/src/github.com/autocaption
bash compare_media.sh /path/to/source.mp4 /path/to/hardsub.mp4
```

输出会显示：
- 分辨率/FPS是否一致
- 视频码率与音频码率变化
- 总码率变化百分比（delta）
- 推流友好度评估（接近/可接受/偏慢风险高）

经验上，总码率越接近原片，YouTube 上传与处理通常越快。
