#!/usr/bin/env python3
import difflib
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from opencc import OpenCC
except ImportError:
    OpenCC = None

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    TkinterDnD = None
    DND_FILES = None

try:
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model as whisper_download_model
except ImportError:
    WhisperModel = None
    whisper_download_model = None

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
SUB_EXTS = {".srt"}
SUBTITLE_SIZE_MAP = {"small": 18, "medium": 24, "large": 30}
SUBTITLE_SIZE_LABEL_TO_KEY = {"小": "small", "中": "medium", "大": "large"}
SUBTITLE_STYLE_LABEL_TO_KEY = {
    "标准": "standard",
    "大字描边": "bold_outline",
    "讲解视频风格": "lecture",
}
QUALITY_LABEL_TO_KEY = {
    "匹配源参数（快速推流）": "match",
    "极致画质": "quality",
    "体积优先": "size",
}
YOUTUBE_ENCODING_PROFILES = {
    "match": {
        "video_codec": "libx264",
        "preset": "medium",
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate_fallback": "128k",
        "video_bitrate_min": 800_000,
        "video_bitrate_max": 20_000_000,
        "audio_bitrate_min": 96_000,
        "audio_bitrate_max": 192_000,
    },
    "quality": {
        "video_codec": "libx264",
        "crf": "17",
        "preset": "medium",
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "192k",
        "maxrate": None,
        "bufsize": None,
    },
    "size": {
        "video_codec": "libx264",
        "crf": "20",
        "preset": "medium",
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "160k",
        "maxrate": "8M",
        "bufsize": "16M",
    },
}
ASR_MODEL_LABEL_TO_KEY = {"快速（small）": "small", "平衡（medium）": "medium"}
TIMELINE_EARLY_SHOW_SEC = 0.08
TIMELINE_EARLY_HIDE_SEC = 0.03
TIMELINE_GAP_BEFORE_NEXT_SEC = 0.05
TIMELINE_MIN_DURATION_SEC = 0.15
TIMELINE_MIN_DURATION_FALLBACK_SEC = 0.25
TIMELINE_LONG_VIDEO_THRESHOLD_SEC = 12 * 60
TIMELINE_ANCHOR_SEGMENT_SEC = 90
TIMELINE_MIN_SEGMENT_LINES = 6
FORCE_SINGLE_LINE_CHAR_LIMIT = {
    "small": 18,
    "medium": 14,
    "large": 10,
}
LECTURE_BOX_OPACITY_DEFAULT = 60
SINGLE_LINE_MIN_CHUNK_DURATION_SEC = 0.9
DEFAULT_WATERMARK_TEXT = "www.youtube.com/@PunkGrampsLin"
DEFAULT_SUBSCRIBE_PROMPT_TEXT = "訂閱老林:前瞻科學抗老早知道!"
DEFAULT_SUBSCRIBE_PROMPT_OPACITY = 92
TEXT_CORRECTION_CONFIDENT_SIM = 0.42
TEXT_CORRECTION_SAFE_SIM = 0.24
TEXT_CORRECTION_SAFE_LENGTH_RATIO = 1.8
TEXT_CORRECTION_SAFE_LENGTH_DELTA = 10
TEXT_CORRECTION_TERM_MAX_CHARS = 6
TEXT_CORRECTION_PREFERRED_TERM_MAX_CHARS = 10
TEXT_CORRECTION_PUNCT_INSERT_MAX_CHARS = 2
TEXT_CORRECTION_LOG_SAMPLE_LIMIT = 8
BUILTIN_PREFERRED_TERMS = [
    "牙周",
    "牙龈",
    "髋关节",
    "膝关节",
    "关节炎",
    "骨质疏松",
    "阿尔茨海默",
    "血糖",
    "胰岛素",
    "胆固醇",
    "高血压",
    "前列腺",
    "心血管",
    "抗老",
    "科学抗老",
    "老林",
]


def parse_drop_files(raw: str) -> list[str]:
    return re.findall(r"\{([^}]*)\}|([^\s]+)", raw)


def normalize_drop_files(raw: str) -> list[str]:
    files = []
    for match in parse_drop_files(raw):
        path = match[0] if match[0] else match[1]
        if path:
            files.append(path)
    return files


class SubtitleBurnerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("YouTube 硬字幕发布工具")
        self.root.geometry("760x560")

        self.video_path = tk.StringVar()
        self.srt_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.subtitle_size_label = tk.StringVar(value="中")
        self.subtitle_style_label = tk.StringVar(value="标准")
        self.subtitle_box_opacity_var = tk.StringVar(value=str(LECTURE_BOX_OPACITY_DEFAULT))
        self.quality_mode_label = tk.StringVar(value="匹配源参数（快速推流）")
        self.custom_crf_var = tk.StringVar(value="")
        self.force_single_line_var = tk.BooleanVar(value=False)
        self.watermark_enabled_var = tk.BooleanVar(value=True)
        self.watermark_text_var = tk.StringVar(value=DEFAULT_WATERMARK_TEXT)
        self.subscribe_prompt_enabled_var = tk.BooleanVar(value=True)
        self.subscribe_prompt_text_var = tk.StringVar(value=DEFAULT_SUBSCRIBE_PROMPT_TEXT)
        self.subscribe_prompt_opacity_var = tk.StringVar(value=str(DEFAULT_SUBSCRIBE_PROMPT_OPACITY))
        self.auto_asr_var = tk.BooleanVar(value=False)
        self.align_timeline_var = tk.BooleanVar(value=False)
        self.fast_align_var = tk.BooleanVar(value=True)
        self.asr_model_label = tk.StringVar(value="快速（small）")
        self.processing = False
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.ffmpeg_bin = self._resolve_ffmpeg_bin()
        self.ffprobe_bin = self._resolve_ffprobe_bin()
        self.ffmpeg_subtitles_supported: bool | None = None
        self.whisper_models: dict[str, WhisperModel] = {}
        self.whisper_model_lock = threading.Lock()
        self._once_log_keys: set[str] = set()

        self._build_ui()
        self.quality_mode_label.trace_add("write", self._on_quality_mode_changed)
        self._tick_logs()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(
            frame,
            text="拖入视频 + 简体SRT，自动转繁体并硬字幕导出（YouTube）",
            font=("Helvetica", 15, "bold"),
        )
        title.pack(anchor="w", pady=(0, 10))

        if TkinterDnD is None:
            drag_tip = (
                "拖拽功能不可用：缺少 tkinterdnd2。\n"
                "可先用下方“选择文件”按钮继续使用。"
            )
        else:
            drag_tip = "把视频文件和简体 SRT 拖到下方区域"

        self.drop_area = tk.Label(
            frame,
            text=drag_tip,
            relief=tk.RIDGE,
            borderwidth=2,
            height=6,
            bg="#f7f7f7",
        )
        self.drop_area.pack(fill=tk.X, pady=(0, 12))

        if TkinterDnD is not None:
            self.drop_area.drop_target_register(DND_FILES)
            self.drop_area.dnd_bind("<<Drop>>", self._on_drop)

        self._row_file(frame, "视频文件", self.video_path, self._pick_video)
        self._row_file(frame, "字幕文件（简体SRT，可选）", self.srt_path, self._pick_srt)
        self._row_file(frame, "输出文件（建议 .mp4）", self.output_path, self._pick_output)

        asr_row = ttk.Frame(frame)
        asr_row.pack(fill=tk.X, pady=4)
        ttk.Label(asr_row, text="自动拾取字幕", width=18).pack(side=tk.LEFT)
        ttk.Checkbutton(
            asr_row,
            text="无SRT时自动识别简体中文语音",
            variable=self.auto_asr_var,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(
            asr_row,
            text="使用提供的SRT做术语纠错（保留自动时间轴）",
            variable=self.align_timeline_var,
        ).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Checkbutton(
            asr_row,
            text="快速对轴（推荐）",
            variable=self.fast_align_var,
        ).pack(side=tk.LEFT, padx=(12, 0))

        asr_model_row = ttk.Frame(frame)
        asr_model_row.pack(fill=tk.X, pady=4)
        ttk.Label(asr_model_row, text="识别模型", width=18).pack(side=tk.LEFT)
        asr_model_combo = ttk.Combobox(
            asr_model_row,
            textvariable=self.asr_model_label,
            values=["快速（small）", "平衡（medium）"],
            state="readonly",
            width=12,
        )
        asr_model_combo.pack(side=tk.LEFT, padx=(8, 0))

        size_row = ttk.Frame(frame)
        size_row.pack(fill=tk.X, pady=4)
        ttk.Label(size_row, text="字幕大小", width=18).pack(side=tk.LEFT)
        size_combo = ttk.Combobox(
            size_row,
            textvariable=self.subtitle_size_label,
            values=["小", "中", "大"],
            state="readonly",
            width=10,
        )
        size_combo.pack(side=tk.LEFT, padx=(8, 0))

        subtitle_style_row = ttk.Frame(frame)
        subtitle_style_row.pack(fill=tk.X, pady=4)
        ttk.Label(subtitle_style_row, text="字幕风格", width=18).pack(side=tk.LEFT)
        subtitle_style_combo = ttk.Combobox(
            subtitle_style_row,
            textvariable=self.subtitle_style_label,
            values=["标准", "大字描边", "讲解视频风格"],
            state="readonly",
            width=18,
        )
        subtitle_style_combo.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(
            subtitle_style_row,
            text="强制一行字幕",
            variable=self.force_single_line_var,
        ).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(subtitle_style_row, text="底板透明度", width=12).pack(side=tk.LEFT, padx=(18, 0))
        subtitle_opacity_combo = ttk.Combobox(
            subtitle_style_row,
            textvariable=self.subtitle_box_opacity_var,
            values=["20", "30", "40", "50", "60", "70", "80"],
            state="readonly",
            width=6,
        )
        subtitle_opacity_combo.pack(side=tk.LEFT, padx=(8, 0))

        watermark_row = ttk.Frame(frame)
        watermark_row.pack(fill=tk.X, pady=4)
        ttk.Label(watermark_row, text="视频水印", width=18).pack(side=tk.LEFT)
        ttk.Checkbutton(
            watermark_row,
            text="横屏视频加水印",
            variable=self.watermark_enabled_var,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(
            watermark_row,
            textvariable=self.watermark_text_var,
            width=42,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))

        subscribe_row = ttk.Frame(frame)
        subscribe_row.pack(fill=tk.X, pady=4)
        ttk.Label(subscribe_row, text="订阅提示", width=18).pack(side=tk.LEFT)
        ttk.Checkbutton(
            subscribe_row,
            text="左下角浮动提示",
            variable=self.subscribe_prompt_enabled_var,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(
            subscribe_row,
            textvariable=self.subscribe_prompt_text_var,
            width=34,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))
        ttk.Label(subscribe_row, text="透明度", width=8).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Combobox(
            subscribe_row,
            textvariable=self.subscribe_prompt_opacity_var,
            values=["40", "50", "60", "70", "80", "90", "100"],
            state="readonly",
            width=6,
        ).pack(side=tk.LEFT, padx=(8, 0))

        quality_row = ttk.Frame(frame)
        quality_row.pack(fill=tk.X, pady=4)
        ttk.Label(quality_row, text="画质模式", width=18).pack(side=tk.LEFT)
        quality_combo = ttk.Combobox(
            quality_row,
            textvariable=self.quality_mode_label,
            values=["匹配源参数（快速推流）", "极致画质", "体积优先"],
            state="readonly",
            width=24,
        )
        quality_combo.pack(side=tk.LEFT, padx=(8, 0))

        crf_row = ttk.Frame(frame)
        crf_row.pack(fill=tk.X, pady=4)
        ttk.Label(crf_row, text="自定义CRF（可选）", width=18).pack(side=tk.LEFT)
        self.crf_entry = ttk.Entry(crf_row, textvariable=self.custom_crf_var, width=12)
        self.crf_entry.pack(side=tk.LEFT, padx=(8, 0))
        self.crf_hint_label = ttk.Label(crf_row, text="留空使用画质模式预设；范围建议 17-23")
        self.crf_hint_label.pack(side=tk.LEFT, padx=8)

        options = ttk.Frame(frame)
        options.pack(fill=tk.X, pady=8)
        ttk.Label(
            options,
            text="编码策略：默认匹配源参数（快速推流）+ H.264 硬字幕，适合 YouTube 发布",
        ).pack(anchor="w")

        btns = ttk.Frame(frame)
        btns.pack(fill=tk.X, pady=(8, 6))
        self.start_btn = ttk.Button(btns, text="开始处理", command=self._start)
        self.start_btn.pack(side=tk.LEFT)
        ttk.Button(btns, text="清空日志", command=self._clear_logs).pack(side=tk.LEFT, padx=8)

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(0, 8))

        self.log_box = tk.Text(frame, height=14, wrap=tk.WORD)
        self.log_box.pack(fill=tk.BOTH, expand=True)
        self._on_quality_mode_changed()

    def _on_quality_mode_changed(self, *_args):
        label = self.quality_mode_label.get().strip() or "匹配源参数（快速推流）"
        mode = QUALITY_LABEL_TO_KEY.get(label, "match")
        if mode == "match":
            self.crf_entry.configure(state=tk.DISABLED)
            self.crf_hint_label.configure(text="匹配源参数模式下 CRF 不生效（按源视频码率）")
        else:
            self.crf_entry.configure(state=tk.NORMAL)
            self.crf_hint_label.configure(text="留空使用画质模式预设；范围建议 17-23")

    def _row_file(self, parent, label, var, callback):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text=label, width=18).pack(side=tk.LEFT)
        entry = ttk.Entry(row, textvariable=var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Button(row, text="选择文件", command=callback).pack(side=tk.RIGHT)

    def _pick_video(self):
        path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.m4v *.webm"), ("All", "*.*")],
        )
        if path:
            self.video_path.set(path)
            if not self.output_path.get():
                self.output_path.set(self._default_output(path))
            self._auto_select_asr_model_for_video(path)

    def _pick_srt(self):
        path = filedialog.askopenfilename(
            title="选择SRT字幕文件",
            filetypes=[("SubRip", "*.srt"), ("All", "*.*")],
        )
        if path:
            self.srt_path.set(path)

    def _pick_output(self):
        path = filedialog.asksaveasfilename(
            title="选择输出文件",
            defaultextension=".mp4",
            filetypes=[("MP4", "*.mp4"), ("All", "*.*")],
        )
        if path:
            self.output_path.set(path)

    def _default_output(self, video_file: str) -> str:
        p = Path(video_file)
        return str(p.with_name(f"{p.stem}.trad.hardsub.youtube.mp4"))

    def _on_drop(self, event):
        dropped = normalize_drop_files(event.data)
        for path in dropped:
            ext = Path(path).suffix.lower()
            if ext in VIDEO_EXTS and not self.video_path.get():
                self.video_path.set(path)
                if not self.output_path.get():
                    self.output_path.set(self._default_output(path))
                self._log(f"已识别视频: {path}")
                self._auto_select_asr_model_for_video(path)
            elif ext in SUB_EXTS and not self.srt_path.get():
                self.srt_path.set(path)
                self._log(f"已识别字幕: {path}")
            else:
                self._log(f"忽略文件: {path}")

    def _start(self):
        if self.processing:
            return
        if OpenCC is None:
            messagebox.showerror(
                "缺少依赖",
                "未安装 opencc。请先执行:\n\npip3 install -r requirements.txt",
            )
            return
        if not self.ffmpeg_bin:
            messagebox.showerror(
                "缺少依赖",
                "未找到 ffmpeg。\n请先安装：brew install ffmpeg",
            )
            return
        if not self._ffmpeg_supports_subtitles_filter():
            messagebox.showerror(
                "ffmpeg 功能缺失",
                "当前 ffmpeg 不支持 subtitles 滤镜（缺少 libass）。\n\n"
                "请安装支持 libass 的 ffmpeg，再重新运行。\n"
                "你当前的 ffmpeg 无法硬压字幕。",
            )
            return

        video = self.video_path.get().strip()
        srt = self.srt_path.get().strip()
        output = self.output_path.get().strip()
        size_label = self.subtitle_size_label.get().strip() or "中"
        subtitle_size_key = SUBTITLE_SIZE_LABEL_TO_KEY.get(size_label, "medium")
        subtitle_style_label = self.subtitle_style_label.get().strip() or "标准"
        subtitle_style_key = SUBTITLE_STYLE_LABEL_TO_KEY.get(subtitle_style_label, "standard")
        subtitle_box_opacity = self._parse_subtitle_box_opacity(self.subtitle_box_opacity_var.get())
        force_single_line = self.force_single_line_var.get()
        watermark_enabled = self.watermark_enabled_var.get()
        watermark_text = self.watermark_text_var.get().strip()
        subscribe_prompt_enabled = self.subscribe_prompt_enabled_var.get()
        subscribe_prompt_text = self.subscribe_prompt_text_var.get().strip()
        subscribe_prompt_opacity = self._parse_percent_value(
            self.subscribe_prompt_opacity_var.get(),
            DEFAULT_SUBSCRIBE_PROMPT_OPACITY,
        )
        quality_label = self.quality_mode_label.get().strip() or "匹配源参数（快速推流）"
        quality_mode_key = QUALITY_LABEL_TO_KEY.get(quality_label, "match")
        auto_asr = self.auto_asr_var.get()
        align_timeline = self.align_timeline_var.get()
        fast_align = self.fast_align_var.get()
        custom_crf_raw = self.custom_crf_var.get().strip()
        custom_crf: str | None = None

        if not video or not os.path.isfile(video):
            messagebox.showerror("参数错误", "请选择有效视频文件。")
            return
        if srt and not os.path.isfile(srt):
            messagebox.showerror("参数错误", "请选择有效 SRT 文件。")
            return
        if not srt and not auto_asr:
            messagebox.showerror("参数错误", "请提供 SRT，或启用“自动拾取字幕”。")
            return
        need_asr = (not srt and auto_asr) or (bool(srt) and align_timeline)
        if need_asr:
            self._auto_select_asr_model_for_video(video)
        asr_model_label = self.asr_model_label.get().strip() or "快速（small）"
        asr_model_key = ASR_MODEL_LABEL_TO_KEY.get(asr_model_label, "small")
        if need_asr and WhisperModel is None:
            messagebox.showerror(
                "缺少依赖",
                "未安装 faster-whisper。\n"
                f"当前Python: {sys.executable}\n\n"
                "请先执行:\n"
                "1) .venv/bin/pip install -r requirements.txt\n"
                "2) 重新执行 bash build_macos_app.sh",
            )
            return
        if not output:
            messagebox.showerror("参数错误", "请选择输出路径。")
            return
        if custom_crf_raw and quality_mode_key != "match":
            if not custom_crf_raw.isdigit():
                messagebox.showerror("参数错误", "自定义CRF必须是整数（建议17-23）。")
                return
            crf_int = int(custom_crf_raw)
            if crf_int < 0 or crf_int > 51:
                messagebox.showerror("参数错误", "自定义CRF范围应为 0-51。")
                return
            custom_crf = str(crf_int)
        elif custom_crf_raw and quality_mode_key == "match":
            self._log("提示：匹配源参数模式下，已忽略自定义CRF。")

        self.processing = True
        self.start_btn.configure(state=tk.DISABLED)
        self.progress.start(12)

        t = threading.Thread(
            target=self._run_pipeline,
            args=(
                video,
                srt,
                output,
                subtitle_size_key,
                subtitle_style_key,
                subtitle_box_opacity,
                force_single_line,
                watermark_enabled,
                watermark_text,
                subscribe_prompt_enabled,
                subscribe_prompt_text,
                subscribe_prompt_opacity,
                quality_mode_key,
                custom_crf,
                auto_asr,
                align_timeline,
                fast_align,
                asr_model_key,
            ),
            daemon=True,
        )
        t.start()

    def _run_pipeline(
        self,
        video: str,
        srt: str,
        output: str,
        subtitle_size_key: str,
        subtitle_style_key: str,
        subtitle_box_opacity: int,
        force_single_line: bool,
        watermark_enabled: bool,
        watermark_text: str,
        subscribe_prompt_enabled: bool,
        subscribe_prompt_text: str,
        subscribe_prompt_opacity: int,
        quality_mode_key: str,
        custom_crf: str | None,
        auto_asr: bool,
        align_timeline: bool,
        fast_align: bool,
        asr_model_key: str,
    ):
        tmpdir = tempfile.mkdtemp(prefix="trad_hardsub_")
        src_srt = os.path.join(tmpdir, "subtitle_src.srt")
        asr_srt = os.path.join(tmpdir, "subtitle_asr.srt")
        corrected_srt = os.path.join(tmpdir, "subtitle_corrected.srt")
        trad_srt = os.path.join(tmpdir, "subtitle_trad.srt")

        try:
            if srt and os.path.isfile(srt):
                src_srt = srt
                if align_timeline:
                    self._log("开始：自动拾取语音时间轴（保留自动时间轴，用提供的SRT修正文案）")
                    self._transcribe_video_to_srt(
                        video,
                        asr_srt,
                        asr_model_key,
                        fast_for_timeline=fast_align,
                    )
                    self._correct_asr_text_with_srt(src_srt, asr_srt, corrected_srt)
                    src_srt = corrected_srt
                    self._log(f"已生成“自动时间轴 + SRT修正文案”字幕: {src_srt}")
                elif auto_asr:
                    self._log("检测到已提供SRT，自动拾取已跳过。")
            else:
                self._log("开始：自动拾取简体中文字幕（语音识别）")
                self._transcribe_video_to_srt(video, src_srt, asr_model_key, fast_for_timeline=False)
                self._log(f"自动字幕已生成: {src_srt}")
            self._log("开始：简体字幕 -> 繁体字幕")
            self._convert_to_trad(src_srt, trad_srt)
            self._log(f"繁体字幕已生成: {trad_srt}")
            media = self._probe_source_media(video)
            if force_single_line:
                self._log("开始：强制一行字幕整理")
                self._force_single_line_subtitles(
                    trad_srt,
                    subtitle_size_key,
                    subtitle_style_key,
                    media,
                )
                self._log("强制一行字幕已完成")
            self._log("开始：YouTube 硬字幕压制（H.264）")
            self._burn_subtitle_youtube(
                video,
                trad_srt,
                output,
                subtitle_size_key,
                subtitle_style_key,
                subtitle_box_opacity,
                watermark_enabled,
                watermark_text,
                subscribe_prompt_enabled,
                subscribe_prompt_text,
                subscribe_prompt_opacity,
                quality_mode_key,
                custom_crf,
            )
            self._log(f"完成: {output}")
            self.root.after(0, lambda: messagebox.showinfo("完成", f"处理完成:\n{output}"))
        except Exception as e:
            self._log(f"失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("处理失败", str(e)))
        finally:
            self.root.after(0, self._reset_ui)

    def _convert_to_trad(self, src_srt: str, dst_srt: str):
        cc = OpenCC("s2t")
        with open(src_srt, "r", encoding="utf-8-sig", errors="ignore") as f:
            data = f.read()
        converted = cc.convert(data)
        with open(dst_srt, "w", encoding="utf-8") as f:
            f.write(converted)

    def _burn_subtitle_youtube(
        self,
        video: str,
        trad_srt: str,
        output: str,
        subtitle_size_key: str,
        subtitle_style_key: str,
        subtitle_box_opacity: int,
        watermark_enabled: bool,
        watermark_text: str,
        subscribe_prompt_enabled: bool,
        subscribe_prompt_text: str,
        subscribe_prompt_opacity: int,
        quality_mode_key: str,
        custom_crf: str | None,
    ):
        video = os.path.abspath(video)
        trad_srt = os.path.abspath(trad_srt)
        output = os.path.abspath(output)
        encoding = YOUTUBE_ENCODING_PROFILES.get(quality_mode_key, YOUTUBE_ENCODING_PROFILES["quality"])
        media = self._probe_source_media(video)

        vf = self._build_subtitle_filter(
            trad_srt,
            subtitle_size_key,
            subtitle_style_key,
            subtitle_box_opacity,
            media,
            watermark_enabled,
            watermark_text,
            subscribe_prompt_enabled,
            subscribe_prompt_text,
            subscribe_prompt_opacity,
        )
        self._log(f"字幕风格：{subtitle_style_key} 底板透明度={subtitle_box_opacity}%")
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            video,
            "-vf",
            vf,
            "-c:v",
            encoding["video_codec"],
            "-preset",
            encoding["preset"],
            "-pix_fmt",
            encoding["pix_fmt"],
            "-c:a",
            encoding["audio_codec"],
        ]
        if quality_mode_key == "match":
            matched = media
            if matched:
                video_bitrate = matched["video_bitrate"]
                audio_bitrate = matched["audio_bitrate"]
                fps = matched["fps"]
                gop = max(24, int(round(fps * 2)))
                self._log(
                    "源参数探测："
                    f"{matched['width']}x{matched['height']} fps={fps:.3f} "
                    f"v={self._format_bitrate(video_bitrate)} a={self._format_bitrate(audio_bitrate)}"
                )
                cmd.extend(
                    [
                        "-b:v",
                        self._to_ffmpeg_bitrate_k(video_bitrate),
                        "-maxrate",
                        self._to_ffmpeg_bitrate_k(video_bitrate),
                        "-bufsize",
                        self._to_ffmpeg_bitrate_k(video_bitrate * 2),
                        "-g",
                        str(gop),
                        "-keyint_min",
                        str(gop),
                        "-sc_threshold",
                        "0",
                        "-b:a",
                        self._to_ffmpeg_bitrate_k(audio_bitrate),
                        "-movflags",
                        "+faststart",
                    ]
                )
                self._log(
                    "编码参数：模式=match "
                    f"b:v={self._to_ffmpeg_bitrate_k(video_bitrate)} "
                    f"maxrate={self._to_ffmpeg_bitrate_k(video_bitrate)} "
                    f"bufsize={self._to_ffmpeg_bitrate_k(video_bitrate * 2)} "
                    f"gop={gop} b:a={self._to_ffmpeg_bitrate_k(audio_bitrate)}"
                )
            else:
                self._log("匹配源参数失败：缺少 ffprobe 或关键字段，已回退极致画质模式。")
                quality_mode_key = "quality"
                encoding = YOUTUBE_ENCODING_PROFILES["quality"]
                effective_crf = custom_crf or encoding["crf"]
                cmd.extend(["-crf", effective_crf, "-b:a", encoding["audio_bitrate"]])
                self._log(f"编码参数：模式={quality_mode_key} CRF={effective_crf}")
        else:
            effective_crf = custom_crf or encoding["crf"]
            cmd.extend(["-crf", effective_crf, "-b:a", encoding["audio_bitrate"]])
            if encoding["maxrate"] and encoding["bufsize"]:
                cmd.extend(["-maxrate", encoding["maxrate"], "-bufsize", encoding["bufsize"]])
            self._log(
                f"编码参数：模式={quality_mode_key} CRF={effective_crf}"
                + (f" maxrate={encoding['maxrate']}" if encoding["maxrate"] else "")
            )
        cmd.append(output)
        self._log(f"ffmpeg 命令: {' '.join(self._shell_quote(x) for x in cmd)}")

        start = time.time()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        assert proc.stdout is not None
        output_tail: list[str] = []
        for line in proc.stdout:
            clean = line.strip()
            if not clean:
                continue
            output_tail.append(clean)
            if len(output_tail) > 30:
                output_tail.pop(0)
            if "time=" in clean or "frame=" in clean:
                self._log(clean)
            elif any(
                token in clean.lower()
                for token in ("error", "failed", "invalid", "unable", "could not", "no such file")
            ):
                self._log(f"ffmpeg: {clean}")
        code = proc.wait()
        elapsed = time.time() - start
        if code != 0:
            if output_tail:
                self._log("ffmpeg 失败尾部日志：")
                for item in output_tail[-12:]:
                    self._log(f"ffmpeg: {item}")
                raise RuntimeError(f"ffmpeg 压制失败：{output_tail[-1]}")
            raise RuntimeError("ffmpeg 压制失败，请检查字幕编码/视频格式是否可读。")
        self._log(f"ffmpeg 执行完成，用时 {elapsed:.1f}s")

    def _transcribe_video_to_srt(
        self,
        video: str,
        dst_srt: str,
        model_size: str,
        fast_for_timeline: bool = False,
    ):
        if WhisperModel is None:
            raise RuntimeError("缺少 faster-whisper 依赖，无法自动拾取字幕。")
        model = self._get_whisper_model(model_size)
        transcribe_args = {
            "language": "zh",
            "vad_filter": True,
            "beam_size": 1 if fast_for_timeline else 5,
        }
        if fast_for_timeline:
            transcribe_args["condition_on_previous_text"] = False
            transcribe_args["temperature"] = 0.0
            self._log("快速对轴已启用：beam_size=1（仅用于自动时间轴 + SRT文案修正）")
        segments, _info = model.transcribe(video, **transcribe_args)

        count = 0
        with open(dst_srt, "w", encoding="utf-8") as f:
            for seg in segments:
                text = (seg.text or "").strip()
                if not text:
                    continue
                start = self._format_srt_timestamp(seg.start)
                end = self._format_srt_timestamp(seg.end)
                count += 1
                f.write(f"{count}\n{start} --> {end}\n{text}\n\n")
                if count % 20 == 0:
                    self._log(f"识别进度：已生成 {count} 条（到 {end}）")
        if count == 0:
            raise RuntimeError("自动拾取未生成有效字幕，请检查视频语音是否清晰。")
        self._log(f"自动拾取完成，共 {count} 条字幕。")

    def _get_whisper_model(self, model_size: str) -> WhisperModel:
        if WhisperModel is None:
            raise RuntimeError("缺少 faster-whisper 依赖。")
        with self.whisper_model_lock:
            cached = self.whisper_models.get(model_size)
            if cached is not None:
                self._log(f"识别模型命中缓存: {model_size}")
                return cached
            cpu_threads = max(1, min((os.cpu_count() or 4), 16))
            num_workers = max(1, min(cpu_threads // 2, 8))
            if model_size == "medium" and not self._is_whisper_model_cached("medium"):
                self._log_once(
                    "medium_download_hint",
                    "medium 模型未缓存，首次会在线下载，网络慢时可能耗时较长。",
                )
            self._log(
                f"加载识别模型: {model_size}（首次使用可能会下载模型）"
                f" cpu_threads={cpu_threads} num_workers={num_workers}"
            )
            model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads,
                num_workers=num_workers,
            )
            self.whisper_models[model_size] = model
            return model

    def _align_srt_timeline_by_asr(self, text_srt: str, asr_srt: str, dst_srt: str):
        text_entries = self._parse_srt_entries(text_srt)
        asr_entries = self._parse_srt_entries(asr_srt)
        if not text_entries:
            raise RuntimeError("提供的SRT没有可用字幕内容。")
        if not asr_entries:
            raise RuntimeError("自动拾取未生成可用时间轴，无法校准。")

        t_count = len(text_entries)
        a_count = len(asr_entries)
        self._log(f"校准时间轴：原字幕 {t_count} 条，自动时间轴 {a_count} 条")

        align_units = self._collapse_entries_for_alignment(text_entries)
        asr_units = self._collapse_entries_for_alignment(asr_entries)
        if len(align_units) != len(text_entries):
            self._log(f"字幕校准预处理：已将 {t_count} 条碎片字幕合并为 {len(align_units)} 个语义块")
        if len(asr_units) != len(asr_entries):
            self._log(f"ASR 预处理：已将 {a_count} 条识别字幕合并为 {len(asr_units)} 个语义块")

        asr_start = asr_entries[0]["start"]
        asr_end = asr_entries[-1]["end"]
        try:
            self._log("校准策略：文本相似度匹配对齐")
            aligned_units = self._align_units_by_text_matching(align_units, asr_units)
        except Exception as exc:
            self._log(f"文本匹配对齐失败，回退字符节奏映射：{exc}")
            asr_span = max(0.5, asr_end - asr_start)
            long_video = asr_span >= TIMELINE_LONG_VIDEO_THRESHOLD_SEC
            if long_video:
                self._log(
                    f"长视频回退策略：分段锚点（阈值 {TIMELINE_LONG_VIDEO_THRESHOLD_SEC}s，"
                    f"段长≈{TIMELINE_ANCHOR_SEGMENT_SEC}s）"
                )
                aligned_units = self._align_timeline_segmented_anchor(align_units, asr_entries)
            else:
                self._log("短视频回退策略：全片字符节奏映射")
                aligned_units = self._align_timeline_by_char_counts(align_units, asr_entries)

        aligned = self._expand_aligned_units_to_entries(text_entries, align_units, aligned_units)

        self._post_process_aligned_entries(aligned, asr_start, asr_end)

        self._write_srt_entries(dst_srt, aligned)

    def _correct_asr_text_with_srt(self, text_srt: str, asr_srt: str, dst_srt: str):
        text_entries = self._parse_srt_entries(text_srt)
        asr_entries = self._parse_srt_entries(asr_srt)
        if not text_entries:
            raise RuntimeError("提供的SRT没有可用字幕内容。")
        if not asr_entries:
            raise RuntimeError("自动拾取未生成可用字幕，无法修正文案。")

        t_count = len(text_entries)
        a_count = len(asr_entries)
        self._log(f"术语纠错：提供SRT {t_count} 条，自动识别 {a_count} 条")

        text_units = self._collapse_entries_for_alignment(text_entries)
        asr_units = self._collapse_entries_for_alignment(asr_entries)
        if len(text_units) != len(text_entries):
            self._log(f"SRT 预处理：已将 {t_count} 条碎片字幕合并为 {len(text_units)} 个语义块")
        if len(asr_units) != len(asr_entries):
            self._log(f"ASR 预处理：已将 {a_count} 条识别字幕合并为 {len(asr_units)} 个语义块")

        self._log("修正策略：保留自动时间轴，用提供的SRT做术语/短语级纠错")
        corrected_entries = [dict(entry) for entry in asr_entries]
        try:
            matches, avg_sim, unit_sims = self._match_units_by_text_similarity(text_units, asr_units)
        except Exception as exc:
            self._log(f"SRT 文案修正失败，回退自动识别文字：{exc}")
            self._write_srt_entries(dst_srt, corrected_entries)
            return

        corrected_unit_count = 0
        corrected_entry_count = 0
        confident_replace_count = 0
        safe_replace_count = 0
        corrected_term_count = 0
        skipped_low_conf = 0
        used_indices: set[int] = set()
        correction_samples: list[str] = []

        for unit_idx, match in enumerate(matches):
            start, end = match
            unit_sim = unit_sims[unit_idx]
            asr_source_indices: list[int] = []
            for asr_unit in asr_units[start:end]:
                for src_idx in asr_unit.get("source_indices") or []:
                    if src_idx not in used_indices:
                        asr_source_indices.append(src_idx)
            asr_source_indices = sorted(asr_source_indices)
            if not asr_source_indices:
                skipped_low_conf += 1
                continue

            should_replace, replace_mode = self._should_apply_reference_text(
                text_units[unit_idx]["text"],
                [corrected_entries[i] for i in asr_source_indices],
                unit_sim,
            )
            if not should_replace:
                skipped_low_conf += 1
                continue

            replacement_chunks = self._split_text_for_reference_entries(
                text_units[unit_idx]["text"],
                [corrected_entries[i] for i in asr_source_indices],
            )
            if not replacement_chunks:
                skipped_low_conf += 1
                continue

            replaced_here = 0
            for src_idx, chunk in zip(asr_source_indices, replacement_chunks):
                if not chunk.strip():
                    continue
                merged_text, term_count, samples = self._merge_reference_text_conservatively(
                    corrected_entries[src_idx]["text"],
                    chunk,
                )
                if merged_text == corrected_entries[src_idx]["text"]:
                    continue
                corrected_entries[src_idx]["text"] = merged_text
                used_indices.add(src_idx)
                replaced_here += 1
                corrected_term_count += term_count
                for sample in samples:
                    if len(correction_samples) >= TEXT_CORRECTION_LOG_SAMPLE_LIMIT:
                        break
                    correction_samples.append(sample)
            if replaced_here == 0:
                skipped_low_conf += 1
                continue
            corrected_unit_count += 1
            corrected_entry_count += replaced_here
            if replace_mode == "confident":
                confident_replace_count += 1
            else:
                safe_replace_count += 1

        self._write_srt_entries(dst_srt, corrected_entries)
        self._log(
            "SRT 文案修正完成："
            f"语义块 {corrected_unit_count}/{len(text_units)}，"
            f"字幕条 {corrected_entry_count}/{len(asr_entries)}，"
            f"平均相似度={avg_sim:.3f}，"
            f"高置信替换 {confident_replace_count} 块，"
            f"保守替换 {safe_replace_count} 块，"
            f"术语纠错 {corrected_term_count} 处，"
            f"低置信跳过 {skipped_low_conf} 块"
        )
        if correction_samples:
            self._log("术语纠错样例：" + "；".join(correction_samples))

    def _align_timeline_segmented_anchor(
        self, text_entries: list[dict], asr_entries: list[dict]
    ) -> list[dict]:
        src_start = text_entries[0]["start"]
        src_end = text_entries[-1]["end"]
        src_span = max(0.5, src_end - src_start)
        asr_start = asr_entries[0]["start"]
        asr_end = asr_entries[-1]["end"]
        asr_span = max(0.5, asr_end - asr_start)

        seg_count = max(1, int(math.ceil(src_span / TIMELINE_ANCHOR_SEGMENT_SEC)))
        self._log(f"分段锚点：共 {seg_count} 段")

        text_mid = [0.5 * (x["start"] + x["end"]) for x in text_entries]
        asr_mid = [0.5 * (x["start"] + x["end"]) for x in asr_entries]
        aligned: list[dict | None] = [None] * len(text_entries)

        for seg_idx in range(seg_count):
            seg_src_start = src_start + seg_idx * TIMELINE_ANCHOR_SEGMENT_SEC
            if seg_idx == seg_count - 1:
                seg_src_end = src_end
            else:
                seg_src_end = min(src_end, seg_src_start + TIMELINE_ANCHOR_SEGMENT_SEC)

            r0 = (seg_src_start - src_start) / src_span
            r1 = (seg_src_end - src_start) / src_span
            seg_asr_start = asr_start + r0 * asr_span
            seg_asr_end = asr_start + r1 * asr_span

            if seg_idx == seg_count - 1:
                t_idx = [i for i, t in enumerate(text_mid) if seg_src_start <= t <= seg_src_end]
                a_idx = [i for i, t in enumerate(asr_mid) if seg_asr_start <= t <= seg_asr_end]
            else:
                t_idx = [i for i, t in enumerate(text_mid) if seg_src_start <= t < seg_src_end]
                a_idx = [i for i, t in enumerate(asr_mid) if seg_asr_start <= t < seg_asr_end]

            if not t_idx:
                continue

            # Segment too sparse: fallback to nearest ASR slice so every block can be mapped.
            if len(a_idx) < TIMELINE_MIN_SEGMENT_LINES:
                center_ratio = max(0.0, min(1.0, (r0 + r1) * 0.5))
                center = int(round(center_ratio * max(0, len(asr_entries) - 1)))
                radius = max(TIMELINE_MIN_SEGMENT_LINES, len(t_idx) // 2 + 3)
                left = max(0, center - radius)
                right = min(len(asr_entries), center + radius + 1)
                a_idx = list(range(left, right))
            if not a_idx:
                continue

            seg_text_entries = [text_entries[i] for i in t_idx]
            seg_asr_entries = [asr_entries[i] for i in a_idx]
            seg_aligned = self._align_timeline_by_char_counts(seg_text_entries, seg_asr_entries)

            for local_i, global_i in enumerate(t_idx):
                aligned[global_i] = seg_aligned[local_i]

        if any(x is None for x in aligned):
            self._log("分段锚点补全：个别分段缺口，使用全片映射补齐")
            fallback = self._align_timeline_by_char_counts(text_entries, asr_entries)
            for i in range(len(aligned)):
                if aligned[i] is None:
                    aligned[i] = fallback[i]

        if any(x is None for x in aligned):
            raise RuntimeError("分段锚点校准失败：无法完成全部字幕映射。")
        return [x for x in aligned if x is not None]

    def _align_units_by_text_matching(
        self, text_units: list[dict], asr_units: list[dict]
    ) -> list[dict]:
        m = len(text_units)
        n = len(asr_units)
        matches, avg_sim, _unit_sims = self._match_units_by_text_similarity(text_units, asr_units)

        aligned_units: list[dict] = []
        for idx, match in enumerate(matches):
            start, end = match
            unit_asr = asr_units[start:end]
            aligned_units.append(
                {
                    "start": unit_asr[0]["start"],
                    "end": unit_asr[-1]["end"],
                    "text": text_units[idx]["text"],
                }
            )

        self._log(f"文本匹配对齐完成：语义块 {m}->{n} 平均相似度={avg_sim:.3f}")
        if avg_sim < 0.38:
            raise RuntimeError(f"文本匹配置信度过低（{avg_sim:.3f}）。")
        return aligned_units

    def _match_units_by_text_similarity(
        self,
        source_units: list[dict],
        target_units: list[dict],
    ) -> tuple[list[tuple[int, int]], float, list[float]]:
        if not source_units or not target_units:
            raise RuntimeError("文本匹配失败：缺少可用语义块。")

        source_norm = [self._normalize_alignment_text(x["text"]) for x in source_units]
        target_norm = [self._normalize_alignment_text(x["text"]) for x in target_units]
        m = len(source_units)
        n = len(target_units)
        max_span = 3
        skip_penalty = 0.08
        neg_inf = -10**9

        dp = [[neg_inf] * (n + 1) for _ in range(m + 1)]
        back: list[list[tuple[str, int] | None]] = [[None] * (n + 1) for _ in range(m + 1)]
        for j in range(n + 1):
            dp[0][j] = -skip_penalty * j

        for i in range(1, m + 1):
            for j in range(1, n + 1):
                best_score = dp[i][j - 1] - skip_penalty
                best_choice: tuple[str, int] | None = ("skip", 1)

                for span in range(1, min(max_span, j) + 1):
                    prev = dp[i - 1][j - span]
                    if prev <= neg_inf / 2:
                        continue
                    target_joined = "".join(target_norm[j - span:j])
                    sim = self._alignment_similarity(source_norm[i - 1], target_joined)
                    position_penalty = abs((i / m) - (j / n)) * 0.35
                    length_penalty = abs(len(source_norm[i - 1]) - len(target_joined)) / max(
                        1,
                        len(source_norm[i - 1]),
                        len(target_joined),
                    ) * 0.12
                    score = prev + sim - position_penalty - length_penalty
                    if score > best_score:
                        best_score = score
                        best_choice = ("match", span)

                dp[i][j] = best_score
                back[i][j] = best_choice

        end_j = max(range(1, n + 1), key=lambda j: dp[m][j])
        if dp[m][end_j] <= neg_inf / 2:
            raise RuntimeError("文本匹配失败：未找到可用匹配路径。")

        matches: list[tuple[int, int] | None] = [None] * m
        i = m
        j = end_j
        while i > 0 and j >= 0:
            choice = back[i][j]
            if choice is None:
                break
            kind, step = choice
            if kind == "skip":
                j -= step
                continue
            start = j - step
            matches[i - 1] = (start, j)
            i -= 1
            j = start

        if any(match is None for match in matches):
            raise RuntimeError("文本匹配失败：部分语义块未匹配到识别结果。")

        resolved_matches = [match for match in matches if match is not None]
        unit_sims: list[float] = []
        for idx, match in enumerate(resolved_matches):
            start, end = match
            joined = "".join(target_norm[start:end])
            unit_sims.append(self._alignment_similarity(source_norm[idx], joined))
        avg_sim = sum(unit_sims) / max(1, len(unit_sims))
        return resolved_matches, avg_sim, unit_sims

    def _alignment_similarity(self, left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        seq_ratio = difflib.SequenceMatcher(None, left, right).ratio()
        left_grams = self._char_ngrams(left)
        right_grams = self._char_ngrams(right)
        overlap = len(left_grams & right_grams) / max(1, len(left_grams | right_grams))
        prefix_bonus = 0.08 if left[:2] and left[:2] == right[:2] else 0.0
        return 0.65 * seq_ratio + 0.35 * overlap + prefix_bonus

    def _char_ngrams(self, text: str) -> set[str]:
        if len(text) <= 1:
            return {text} if text else set()
        return {text[i : i + 2] for i in range(len(text) - 1)}

    def _align_timeline_by_char_counts(
        self, text_entries: list[dict], asr_entries: list[dict]
    ) -> list[dict]:
        asr_counts = [max(1, self._effective_text_len(x["text"])) for x in asr_entries]
        text_counts = [max(1, self._effective_text_len(x["text"])) for x in text_entries]

        asr_total = sum(asr_counts)
        text_total = sum(text_counts)
        asr_start = asr_entries[0]["start"]
        asr_end = asr_entries[-1]["end"]

        cum_asr = [0]
        for n in asr_counts:
            cum_asr.append(cum_asr[-1] + n)

        def asr_ratio_to_time(ratio: float) -> float:
            target = ratio * asr_total
            for i in range(1, len(cum_asr)):
                if target <= cum_asr[i]:
                    seg_chars = max(1, cum_asr[i] - cum_asr[i - 1])
                    local = (target - cum_asr[i - 1]) / seg_chars
                    s = asr_entries[i - 1]["start"]
                    e = asr_entries[i - 1]["end"]
                    return s + (e - s) * local
            return asr_end

        aligned = []
        run = 0
        for i, ent in enumerate(text_entries):
            start_ratio = run / text_total
            run += text_counts[i]
            end_ratio = run / text_total

            start_sec = asr_ratio_to_time(start_ratio)
            end_sec = asr_ratio_to_time(end_ratio)

            start_sec = max(asr_start, start_sec - TIMELINE_EARLY_SHOW_SEC)
            end_sec = min(asr_end, end_sec - TIMELINE_EARLY_HIDE_SEC)
            if end_sec <= start_sec:
                end_sec = min(asr_end, start_sec + TIMELINE_MIN_DURATION_FALLBACK_SEC)

            aligned.append({"start": start_sec, "end": end_sec, "text": ent["text"]})
        return aligned

    def _post_process_aligned_entries(self, aligned: list[dict], asr_start: float, asr_end: float):
        for i in range(len(aligned) - 1):
            max_end = aligned[i + 1]["start"] - TIMELINE_GAP_BEFORE_NEXT_SEC
            if aligned[i]["end"] > max_end:
                aligned[i]["end"] = max_end
            if aligned[i]["end"] <= aligned[i]["start"]:
                aligned[i]["end"] = aligned[i]["start"] + TIMELINE_MIN_DURATION_SEC
        for i in range(len(aligned)):
            aligned[i]["start"] = max(asr_start, aligned[i]["start"])
            if i > 0 and aligned[i]["start"] < aligned[i - 1]["end"]:
                aligned[i]["start"] = aligned[i - 1]["end"] + 0.02
            if aligned[i]["end"] <= aligned[i]["start"]:
                aligned[i]["end"] = aligned[i]["start"] + TIMELINE_MIN_DURATION_SEC
            aligned[i]["end"] = min(aligned[i]["end"], asr_end)

    def _parse_srt_entries(self, srt_path: str) -> list[dict]:
        with open(srt_path, "r", encoding="utf-8-sig", errors="ignore") as f:
            raw = f.read().strip()
        if not raw:
            return []
        blocks = re.split(r"\n\s*\n", raw)
        entries = []
        for block in blocks:
            lines = [ln.rstrip() for ln in block.splitlines() if ln.strip() != ""]
            if len(lines) < 2:
                continue
            timeline_line = lines[1] if "-->" in lines[1] else lines[0]
            if "-->" not in timeline_line:
                continue
            parts = [x.strip() for x in timeline_line.split("-->")]
            if len(parts) != 2:
                continue
            start = self._parse_srt_timestamp(parts[0])
            end = self._parse_srt_timestamp(parts[1])
            if end <= start:
                continue
            text_lines = lines[2:] if "-->" in lines[1] else lines[1:]
            text = "\n".join(text_lines).strip()
            if not text:
                continue
            entries.append({"start": start, "end": end, "text": text})
        return entries

    def _collapse_entries_for_alignment(self, entries: list[dict]) -> list[dict]:
        if not entries:
            return []

        units: list[dict] = []
        current = {
            "start": entries[0]["start"],
            "end": entries[0]["end"],
            "text_parts": [entries[0]["text"]],
            "source_indices": [0],
        }

        for idx in range(1, len(entries)):
            ent = entries[idx]
            prev = entries[idx - 1]
            gap = max(0.0, ent["start"] - prev["end"])
            current_text = self._normalize_alignment_text("".join(current["text_parts"]))
            should_break = (
                self._ends_alignment_sentence(current_text)
                or self._effective_text_len(current_text) >= 18
                or gap >= 0.45
            )
            if should_break:
                units.append(
                    {
                        "start": current["start"],
                        "end": current["end"],
                        "text": self._normalize_alignment_text("".join(current["text_parts"])),
                        "source_indices": current["source_indices"][:],
                    }
                )
                current = {
                    "start": ent["start"],
                    "end": ent["end"],
                    "text_parts": [ent["text"]],
                    "source_indices": [idx],
                }
            else:
                current["end"] = ent["end"]
                current["text_parts"].append(ent["text"])
                current["source_indices"].append(idx)

        units.append(
            {
                "start": current["start"],
                "end": current["end"],
                "text": self._normalize_alignment_text("".join(current["text_parts"])),
                "source_indices": current["source_indices"][:],
            }
        )
        return units

    def _expand_aligned_units_to_entries(
        self,
        original_entries: list[dict],
        align_units: list[dict],
        aligned_units: list[dict],
    ) -> list[dict]:
        if len(align_units) != len(aligned_units):
            raise RuntimeError("时间轴校准失败：语义块数量不一致。")

        expanded: list[dict] = []
        for unit, aligned_unit in zip(align_units, aligned_units):
            indices = unit.get("source_indices") or []
            if not indices:
                continue
            if len(indices) == 1:
                src_ent = original_entries[indices[0]]
                expanded.append(
                    {
                        "start": aligned_unit["start"],
                        "end": aligned_unit["end"],
                        "text": src_ent["text"],
                    }
                )
                continue

            source_entries = [original_entries[i] for i in indices]
            unit_duration = max(TIMELINE_MIN_DURATION_FALLBACK_SEC, aligned_unit["end"] - aligned_unit["start"])
            weights = [max(1, self._effective_text_len(ent["text"])) for ent in source_entries]
            total_weight = max(1, sum(weights))
            cursor = aligned_unit["start"]
            for idx, src_ent in enumerate(source_entries):
                if idx == len(source_entries) - 1:
                    seg_end = aligned_unit["end"]
                else:
                    seg_duration = max(
                        TIMELINE_MIN_DURATION_SEC,
                        unit_duration * (weights[idx] / total_weight),
                    )
                    seg_end = min(aligned_unit["end"], cursor + seg_duration)
                expanded.append({"start": cursor, "end": seg_end, "text": src_ent["text"]})
                cursor = seg_end
        return expanded

    def _normalize_alignment_text(self, text: str) -> str:
        merged = re.sub(r"\s*\n\s*", "", text)
        merged = re.sub(r"\s+", "", merged)
        return merged.strip(" \"'“”‘’")

    def _ends_alignment_sentence(self, text: str) -> bool:
        stripped = text.rstrip()
        return bool(stripped) and stripped[-1] in "。！？!?；;：:"

    def _write_srt_entries(self, srt_path: str, entries: list[dict]):
        with open(srt_path, "w", encoding="utf-8") as f:
            for idx, ent in enumerate(entries, start=1):
                start = self._format_srt_timestamp(ent["start"])
                end = self._format_srt_timestamp(ent["end"])
                f.write(f"{idx}\n{start} --> {end}\n{ent['text']}\n\n")

    def _force_single_line_subtitles(
        self,
        srt_path: str,
        subtitle_size_key: str,
        subtitle_style_key: str,
        media: dict | None,
    ):
        entries = self._parse_srt_entries(srt_path)
        if not entries:
            return
        char_limit = self._compute_single_line_char_limit(
            subtitle_size_key,
            subtitle_style_key,
            media,
        )
        flattened: list[dict] = []

        for ent in entries:
            merged = self._normalize_single_line_text(ent["text"])
            if not merged:
                continue
            chunks = self._split_text_for_single_line(merged, char_limit)
            chunks = self._merge_short_single_line_chunks(
                chunks,
                max(TIMELINE_MIN_DURATION_FALLBACK_SEC, ent["end"] - ent["start"]),
            )
            if len(chunks) <= 1:
                flattened.append({"start": ent["start"], "end": ent["end"], "text": merged})
                continue

            total_duration = max(TIMELINE_MIN_DURATION_FALLBACK_SEC, ent["end"] - ent["start"])
            weights = [max(1, self._effective_text_len(chunk)) for chunk in chunks]
            weight_total = max(1, sum(weights))
            cursor = ent["start"]

            for idx, chunk in enumerate(chunks):
                if idx == len(chunks) - 1:
                    chunk_end = ent["end"]
                else:
                    portion = total_duration * (weights[idx] / weight_total)
                    chunk_end = cursor + max(TIMELINE_MIN_DURATION_SEC, portion)
                flattened.append({"start": cursor, "end": chunk_end, "text": chunk})
                cursor = chunk_end

        for i in range(len(flattened) - 1):
            if flattened[i]["end"] > flattened[i + 1]["start"]:
                flattened[i]["end"] = flattened[i + 1]["start"]
            if flattened[i]["end"] <= flattened[i]["start"]:
                flattened[i]["end"] = flattened[i]["start"] + TIMELINE_MIN_DURATION_SEC
        self._write_srt_entries(srt_path, flattened)

    def _normalize_single_line_text(self, text: str) -> str:
        merged = re.sub(r"\s*\n\s*", "", text)
        merged = re.sub(r"\s+", " ", merged).strip()
        return merged

    def _split_text_for_reference_entries(
        self,
        text: str,
        reference_entries: list[dict],
    ) -> list[str]:
        merged = self._normalize_single_line_text(text)
        if not merged:
            return []
        if len(reference_entries) <= 1:
            return [merged]

        weights = [max(1, self._effective_text_len(ent["text"])) for ent in reference_entries]
        chunks: list[str] = []
        cursor = 0
        total_weight = sum(weights)

        for idx, weight in enumerate(weights):
            remaining_parts = len(weights) - idx
            if idx == len(weights) - 1:
                chunk = merged[cursor:].strip()
                chunks.append(chunk or merged[cursor:])
                break

            remaining_weight = sum(weights[idx:])
            visible_total = self._visible_char_count(merged[cursor:])
            if visible_total <= remaining_parts:
                next_cursor = min(len(merged), cursor + 1)
            else:
                desired_visible = max(1, int(round(visible_total * (weight / max(1, remaining_weight)))))
                next_cursor = self._find_split_index_by_visible_chars(merged, cursor, desired_visible)
                next_cursor = self._clamp_split_index_for_remaining_text(
                    merged,
                    cursor,
                    next_cursor,
                    remaining_parts - 1,
                )

            chunk = merged[cursor:next_cursor].strip()
            if not chunk:
                continue
            chunks.append(chunk)
            cursor = next_cursor

        while len(chunks) < len(reference_entries):
            chunks.append("")
        if len(chunks) > len(reference_entries):
            head = chunks[: len(reference_entries) - 1]
            tail = "".join(chunks[len(reference_entries) - 1 :]).strip()
            chunks = head + [tail]
        return chunks

    def _should_apply_reference_text(
        self,
        source_text: str,
        target_entries: list[dict],
        similarity: float,
    ) -> tuple[bool, str]:
        if similarity >= TEXT_CORRECTION_CONFIDENT_SIM:
            return True, "confident"
        if similarity < TEXT_CORRECTION_SAFE_SIM:
            return False, "skip"

        source_norm = self._normalize_alignment_text(source_text)
        target_norm = self._normalize_alignment_text("".join(ent["text"] for ent in target_entries))
        source_len = max(1, self._effective_text_len(source_norm))
        target_len = max(1, self._effective_text_len(target_norm))
        length_ratio = max(source_len, target_len) / max(1, min(source_len, target_len))
        length_delta = abs(source_len - target_len)
        if (
            length_ratio <= TEXT_CORRECTION_SAFE_LENGTH_RATIO
            and length_delta <= TEXT_CORRECTION_SAFE_LENGTH_DELTA
        ):
            return True, "safe"
        return False, "skip"

    def _merge_reference_text_conservatively(
        self, asr_text: str, reference_text: str
    ) -> tuple[str, int, list[str]]:
        source = self._normalize_single_line_text(asr_text)
        target = self._normalize_single_line_text(reference_text)
        if not source or not target:
            return source or asr_text, 0, []

        matcher = difflib.SequenceMatcher(None, source, target)
        chunks: list[str] = []
        replace_count = 0
        samples: list[str] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            src_seg = source[i1:i2]
            tgt_seg = target[j1:j2]
            if tag == "equal":
                chunks.append(src_seg)
                continue
            if tag == "replace":
                if self._should_replace_text_segment(src_seg, tgt_seg):
                    chunks.append(tgt_seg)
                    replace_count += 1
                    if src_seg.strip() and tgt_seg.strip() and src_seg != tgt_seg:
                        samples.append(f"{src_seg}->{tgt_seg}")
                else:
                    chunks.append(src_seg)
                continue
            if tag == "delete":
                chunks.append(src_seg)
                continue
            if tag == "insert":
                if self._is_small_punctuation_insert(tgt_seg):
                    chunks.append(tgt_seg)
                    replace_count += 1
                continue

        merged = "".join(chunks).strip()
        if not merged:
            return source, 0, []
        return merged, replace_count, samples[:TEXT_CORRECTION_LOG_SAMPLE_LIMIT]

    def _should_replace_text_segment(self, source_segment: str, target_segment: str) -> bool:
        source = self._normalize_alignment_text(source_segment)
        target = self._normalize_alignment_text(target_segment)
        if not source or not target:
            return False

        source_len = self._effective_text_len(source)
        target_len = self._effective_text_len(target)
        if source_len == 0 or target_len == 0:
            return False

        max_chars = TEXT_CORRECTION_TERM_MAX_CHARS
        max_delta = 2
        if self._contains_preferred_term(target):
            max_chars = TEXT_CORRECTION_PREFERRED_TERM_MAX_CHARS
            max_delta = 3

        if max(source_len, target_len) > max_chars:
            return False
        if abs(source_len - target_len) > max_delta:
            return False
        return True

    def _is_small_punctuation_insert(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        if len(stripped) > TEXT_CORRECTION_PUNCT_INSERT_MAX_CHARS:
            return False
        return all(ch in "，。！？；：、,.!?;:()（）“”‘’\"' " for ch in stripped)

    def _contains_preferred_term(self, text: str) -> bool:
        return any(term in text for term in BUILTIN_PREFERRED_TERMS)

    def _visible_char_count(self, text: str) -> int:
        return sum(1 for ch in text if not ch.isspace())

    def _find_split_index_by_visible_chars(self, text: str, start: int, desired_visible: int) -> int:
        visible = 0
        exact_index = len(text)
        for idx in range(start, len(text)):
            if text[idx].isspace():
                continue
            visible += 1
            if visible >= desired_visible:
                exact_index = idx + 1
                break

        punct = "，。！？；、,.!?;"
        for idx in range(exact_index, min(len(text), exact_index + 5)):
            if text[idx] in punct:
                return idx + 1
        for idx in range(exact_index - 1, max(start, exact_index - 5) - 1, -1):
            if text[idx] in punct:
                return idx + 1
        return exact_index

    def _clamp_split_index_for_remaining_text(
        self,
        text: str,
        start: int,
        split_index: int,
        remaining_parts: int,
    ) -> int:
        split_index = max(start + 1, min(len(text), split_index))
        while split_index < len(text) and self._visible_char_count(text[split_index:]) < remaining_parts:
            split_index -= 1
            if split_index <= start:
                return min(len(text), start + 1)
        return split_index

    def _split_text_for_single_line(self, text: str, char_limit: int) -> list[str]:
        if self._effective_text_len(text) <= char_limit:
            return [text]

        punctuated = self._split_text_by_punctuation(text)
        if len(punctuated) > 1:
            chunks: list[str] = []
            for piece in punctuated:
                chunks.extend(self._split_text_for_single_line(piece, char_limit))
            return [chunk for chunk in chunks if chunk]

        chunks: list[str] = []
        current = ""
        for char in text:
            candidate = current + char
            if current and self._effective_text_len(candidate) > char_limit:
                chunks.append(current.strip())
                current = char
            else:
                current = candidate
        if current.strip():
            chunks.append(current.strip())
        return [chunk for chunk in chunks if chunk]

    def _split_text_by_punctuation(self, text: str) -> list[str]:
        pieces: list[str] = []
        current = ""
        for char in text:
            current += char
            if char in "，。！？；、,.!?;":
                pieces.append(current.strip())
                current = ""
        if current.strip():
            pieces.append(current.strip())
        return [piece for piece in pieces if piece]

    def _merge_short_single_line_chunks(self, chunks: list[str], total_duration: float) -> list[str]:
        if len(chunks) <= 1:
            return chunks
        avg_duration = total_duration / max(1, len(chunks))
        if avg_duration >= SINGLE_LINE_MIN_CHUNK_DURATION_SEC:
            return chunks

        target_count = max(1, int(total_duration / SINGLE_LINE_MIN_CHUNK_DURATION_SEC))
        target_count = min(target_count, len(chunks))
        if target_count >= len(chunks):
            return chunks

        merged: list[str] = []
        step = len(chunks) / target_count
        start = 0.0
        for idx in range(target_count):
            end = round((idx + 1) * step)
            if end <= int(start):
                end = int(start) + 1
            piece = "".join(chunks[int(start):end]).strip()
            if piece:
                merged.append(piece)
            start = float(end)

        if not merged:
            return chunks
        return merged

    def _compute_single_line_char_limit(
        self,
        subtitle_size_key: str,
        subtitle_style_key: str,
        media: dict | None,
    ) -> int:
        limit = FORCE_SINGLE_LINE_CHAR_LIMIT.get(subtitle_size_key, 14)
        if subtitle_style_key == "lecture":
            limit -= 2
        elif subtitle_style_key == "bold_outline":
            limit -= 1

        if media:
            width = int(media.get("width") or 0)
            height = int(media.get("height") or 0)
            if width > 0 and height > width:
                limit -= 4
            elif width > 0 and width / max(1, height) < 1.45:
                limit -= 2

        adjusted = max(6, limit)
        self._log(f"强制一行阈值：{adjusted} 字")
        return adjusted

    def _effective_text_len(self, text: str) -> int:
        cleaned = re.sub(r"\s+", "", text)
        cleaned = re.sub(r"[，。！？、；：,.!?;:\"'“”‘’（）()\\[\\]{}<>《》-]", "", cleaned)
        return len(cleaned)

    def _format_srt_timestamp(self, seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours = total_ms // 3600000
        total_ms %= 3600000
        minutes = total_ms // 60000
        total_ms %= 60000
        secs = total_ms // 1000
        ms = total_ms % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    def _parse_srt_timestamp(self, ts: str) -> float:
        m = re.match(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})$", ts.strip())
        if not m:
            raise RuntimeError(f"无效SRT时间戳: {ts}")
        h = int(m.group(1))
        minute = int(m.group(2))
        sec = int(m.group(3))
        ms = int(m.group(4).ljust(3, "0"))
        return h * 3600 + minute * 60 + sec + ms / 1000.0

    def _resolve_ffmpeg_bin(self) -> str | None:
        for candidate in (
            "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
            "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            return ffmpeg
        for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    def _resolve_ffprobe_bin(self) -> str | None:
        for candidate in (
            "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe",
            "/usr/local/opt/ffmpeg-full/bin/ffprobe",
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            return ffprobe
        for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    def _ffmpeg_supports_subtitles_filter(self) -> bool:
        if self.ffmpeg_subtitles_supported is not None:
            return self.ffmpeg_subtitles_supported
        if not self.ffmpeg_bin:
            self.ffmpeg_subtitles_supported = False
            return False
        try:
            output = subprocess.check_output(
                [self.ffmpeg_bin, "-filters"],
                stderr=subprocess.STDOUT,
                text=True,
            )
            supported = re.search(r"\bsubtitles\b", output) is not None
        except Exception:
            supported = False
        self.ffmpeg_subtitles_supported = supported
        if not supported:
            self._log_once(
                "ffmpeg_no_subtitles_filter",
                "检测到当前 ffmpeg 不支持 subtitles 滤镜，硬字幕压制会失败。",
            )
        return supported

    def _probe_source_media(self, video: str) -> dict | None:
        if not self.ffprobe_bin:
            return None
        cmd = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            (
                "format=bit_rate,duration:"
                "stream=index,codec_type,bit_rate,avg_frame_rate,r_frame_rate,width,height,"
                "pix_fmt,profile,level,sample_rate,channels,codec_name"
            ),
            "-of",
            "json",
            video,
        ]
        try:
            raw = subprocess.check_output(cmd, text=True)
            data = json.loads(raw)
        except Exception:
            return None
        streams = data.get("streams") or []
        fmt = data.get("format") or {}
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if not video_stream:
            return None

        match_cfg = YOUTUBE_ENCODING_PROFILES["match"]
        format_bitrate = self._safe_int(fmt.get("bit_rate"))
        video_bitrate = self._clamp_int(
            self._safe_int(video_stream.get("bit_rate")) or format_bitrate,
            match_cfg["video_bitrate_min"],
            match_cfg["video_bitrate_max"],
        )
        if video_bitrate is None:
            return None
        fps = self._parse_fps(video_stream.get("avg_frame_rate")) or self._parse_fps(
            video_stream.get("r_frame_rate")
        )
        if fps is None:
            fps = 25.0

        source_audio_bitrate = self._safe_int(audio_stream.get("bit_rate")) if audio_stream else None
        audio_bitrate = self._clamp_int(
            source_audio_bitrate,
            match_cfg["audio_bitrate_min"],
            match_cfg["audio_bitrate_max"],
        )
        if audio_bitrate is None:
            audio_bitrate = self._parse_k_bitrate(match_cfg["audio_bitrate_fallback"])

        return {
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
            "duration_sec": self._safe_float(fmt.get("duration")),
            "fps": fps,
            "video_bitrate": video_bitrate,
            "audio_bitrate": audio_bitrate,
            "pix_fmt": video_stream.get("pix_fmt") or "",
            "profile": video_stream.get("profile") or "",
            "level": video_stream.get("level") or "",
        }

    def _auto_select_asr_model_for_video(self, video: str):
        current_label = self.asr_model_label.get().strip() or "快速（small）"
        if current_label != "快速（small）":
            return
        media = self._probe_source_media(video)
        if not media:
            return
        duration_sec = media.get("duration_sec")
        if not duration_sec:
            return
        if duration_sec >= TIMELINE_LONG_VIDEO_THRESHOLD_SEC:
            if self._is_whisper_model_cached("medium"):
                self.asr_model_label.set("平衡（medium）")
                self._log(
                    f"检测到长视频（{duration_sec/60:.1f} 分钟），"
                    "识别模型已自动切换为 medium。"
                )
            else:
                self._log_once(
                    "keep_small_no_medium_cache",
                    f"检测到长视频（{duration_sec/60:.1f} 分钟），"
                    "但 medium 尚未缓存。为避免首次下载等待，暂保持 small。",
                )

    def _is_whisper_model_cached(self, model_size: str) -> bool:
        if whisper_download_model is None:
            return False
        try:
            whisper_download_model(model_size, local_files_only=True)
            return True
        except Exception:
            return False

    def _log_once(self, key: str, msg: str):
        if key in self._once_log_keys:
            return
        self._once_log_keys.add(key)
        self._log(msg)

    def _safe_int(self, value) -> int | None:
        try:
            if value is None:
                return None
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _safe_float(self, value) -> float | None:
        try:
            if value is None:
                return None
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _clamp_int(self, value: int | None, minimum: int, maximum: int) -> int | None:
        if value is None:
            return None
        if value < minimum:
            return minimum
        if value > maximum:
            return maximum
        return value

    def _parse_fps(self, raw: str | None) -> float | None:
        if not raw:
            return None
        if "/" in raw:
            parts = raw.split("/", 1)
            den = self._safe_int(parts[1])
            num = self._safe_int(parts[0])
            if not den or not num:
                return None
            return num / den
        val = self._safe_int(raw)
        if val is None:
            return None
        return float(val)

    def _parse_k_bitrate(self, raw: str) -> int:
        m = re.match(r"^(\d+)k$", raw.strip().lower())
        if not m:
            return 128_000
        return int(m.group(1)) * 1000

    def _to_ffmpeg_bitrate_k(self, bps: int) -> str:
        kbps = max(1, int(round(bps / 1000)))
        return f"{kbps}k"

    def _format_bitrate(self, bps: int) -> str:
        return self._to_ffmpeg_bitrate_k(bps)

    def _shell_quote(self, value: str) -> str:
        return subprocess.list2cmdline([value])

    def _escape_subtitles_filter_value(self, value: str) -> str:
        return (
            value.replace("\\", r"\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace("[", r"\[")
            .replace("]", r"\]")
            .replace(",", r"\,")
        )

    def _escape_drawtext_value(self, value: str) -> str:
        return (
            value.replace("\\", r"\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace(",", r"\,")
            .replace("[", r"\[")
            .replace("]", r"\]")
        )

    def _escape_filter_path(self, value: str) -> str:
        return (
            value.replace("\\", r"\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace(",", r"\,")
            .replace("[", r"\[")
            .replace("]", r"\]")
        )

    def _resolve_drawtext_fontfile(self, kind: str = "cjk") -> str | None:
        if kind == "youtube":
            candidates = [
                "/Library/Fonts/Roboto-Bold.ttf",
                "/Library/Fonts/Roboto-Regular.ttf",
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                "/Library/Fonts/Arial Unicode.ttf",
                "/System/Library/Fonts/Hiragino Sans GB.ttc",
                "/System/Library/Fonts/STHeiti Medium.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
            ]
        else:
            candidates = [
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                "/Library/Fonts/Arial Unicode.ttf",
                "/System/Library/Fonts/Hiragino Sans GB.ttc",
                "/System/Library/Fonts/STHeiti Medium.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
            ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return None

    def _resolve_subtitle_font_name(self, bold: bool = False) -> str:
        candidates = [
            ("/System/Library/Fonts/Hiragino Sans GB.ttc", "Hiragino Sans GB"),
            ("/System/Library/Fonts/STHeiti Medium.ttc", "STHeiti"),
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "Arial Unicode MS"),
            ("/Library/Fonts/Arial Unicode.ttf", "Arial Unicode MS"),
        ]
        for path, family in candidates:
            if os.path.isfile(path):
                if bold and family == "Hiragino Sans GB":
                    return "Hiragino Sans GB W6"
                return family
        return "Arial" if not bold else "Arial Bold"

    def _resolve_asset_path(self, *parts: str) -> Path | None:
        base_dir = Path(__file__).resolve().parent
        candidates = [
            base_dir.joinpath(*parts),
            base_dir.parent.joinpath(*parts),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _create_subscribe_prompt_badge(
        self,
        text: str,
        fontfile: str,
        fontsize: int,
        opacity: int,
    ) -> dict[str, int | str] | None:
        if Image is None or ImageDraw is None or ImageFont is None:
            self._log("订阅提示：缺少 Pillow，回退普通矩形底板。")
            return None

        icon_path = self._resolve_asset_path("assets", "subscribe_bell.png")
        icon = None
        icon_size = max(22, min(40, int(round(fontsize * 1.20))))
        if icon_path and icon_path.is_file():
            try:
                icon = Image.open(icon_path).convert("RGBA").resize((icon_size, icon_size))
            except Exception:
                icon = None

        try:
            font = ImageFont.truetype(fontfile, fontsize)
        except Exception:
            font = ImageFont.load_default()

        measure = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        measure_draw = ImageDraw.Draw(measure)
        text_bbox = measure_draw.textbbox((0, 0), text, font=font, stroke_width=1)
        text_width = max(1, text_bbox[2] - text_bbox[0])
        text_height = max(1, text_bbox[3] - text_bbox[1])

        pad_x = max(20, int(round(fontsize * 0.82)))
        pad_y = max(12, int(round(fontsize * 0.52)))
        gap = max(12, int(round(fontsize * 0.45))) if icon is not None else 0
        icon_block_width = icon_size + gap if icon is not None else 0
        text_area_width = text_width + max(24, int(round(fontsize * 0.95)))
        badge_width = pad_x * 2 + icon_block_width + text_area_width
        badge_height = max(text_height, icon_size if icon is not None else 0) + pad_y * 2
        radius = max(10, int(round(badge_height * 0.22)))

        badge = self._create_glass_badge_image(
            badge_width,
            badge_height,
            radius,
            (220, 27, 35),
            opacity,
            border_alpha=110,
            highlight_alpha=88,
            shadow_alpha=36,
        )
        if icon is not None:
            icon_y = (badge_height - icon_size) // 2
            icon_x = pad_x
            badge.alpha_composite(icon, (icon_x, icon_y))
            text_area_x = icon_x + icon_block_width
        else:
            text_area_x = pad_x

        fd, out_path = tempfile.mkstemp(prefix="subscribe_prompt_badge_", suffix=".png")
        os.close(fd)
        badge.save(out_path)
        return {
            "path": out_path,
            "width": badge_width,
            "height": badge_height,
            "text_area_x": text_area_x,
            "text_area_width": text_area_width,
        }

    def _create_text_badge(
        self,
        text: str,
        fontfile: str,
        fontsize: int,
        opacity: int,
        fill_rgba: tuple[int, int, int],
    ) -> dict[str, int | str] | None:
        if Image is None or ImageDraw is None or ImageFont is None:
            return None

        try:
            font = ImageFont.truetype(fontfile, fontsize)
        except Exception:
            font = ImageFont.load_default()

        measure = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        measure_draw = ImageDraw.Draw(measure)
        text_bbox = measure_draw.textbbox((0, 0), text, font=font, stroke_width=1)
        text_width = max(1, text_bbox[2] - text_bbox[0])
        text_height = max(1, text_bbox[3] - text_bbox[1])

        pad_x = max(18, int(round(fontsize * 0.72)))
        pad_y = max(10, int(round(fontsize * 0.42)))
        badge_width = pad_x * 2 + text_width
        badge_height = text_height + pad_y * 2
        radius = max(8, int(round(badge_height * 0.22)))

        badge = self._create_glass_badge_image(
            badge_width,
            badge_height,
            radius,
            fill_rgba,
            opacity,
            border_alpha=95,
            highlight_alpha=74,
            shadow_alpha=28,
        )
        text_x = (badge_width - text_width) // 2 - text_bbox[0]
        text_y = (badge_height - text_height) // 2 - text_bbox[1]

        fd, out_path = tempfile.mkstemp(prefix="text_badge_", suffix=".png")
        os.close(fd)
        badge.save(out_path)
        return {
            "path": out_path,
            "width": badge_width,
            "height": badge_height,
            "text_x": text_x,
            "text_y": text_y,
        }

    def _create_glass_badge_image(
        self,
        width: int,
        height: int,
        radius: int,
        base_rgb: tuple[int, int, int],
        opacity: int,
        border_alpha: int,
        highlight_alpha: int,
        shadow_alpha: int,
    ):
        alpha = int(round(max(0, min(100, opacity)) / 100 * 255))
        badge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(badge)
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=radius,
            fill=(*base_rgb, alpha),
            outline=(255, 255, 255, border_alpha),
            width=1,
        )
        return badge

    def _build_subtitle_filter(
        self,
        trad_srt: str,
        subtitle_size_key: str,
        subtitle_style_key: str,
        subtitle_box_opacity: int,
        media: dict | None,
        watermark_enabled: bool,
        watermark_text: str,
        subscribe_prompt_enabled: bool,
        subscribe_prompt_text: str,
        subscribe_prompt_opacity: int,
    ) -> str:
        font_size = self._compute_adaptive_font_size(subtitle_size_key, media)
        escaped_srt = self._escape_subtitles_filter_value(trad_srt)
        subtitle_font = self._resolve_subtitle_font_name()
        subtitle_font_bold = self._resolve_subtitle_font_name(bold=True)

        if subtitle_style_key == "bold_outline":
            style = (
                f"FontName={subtitle_font_bold},FontSize={font_size + 4},Bold=1,"
                "Outline=2.2,Shadow=0.2,Spacing=0.4,MarginV=24,"
                "PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&"
            )
            escaped_style = self._escape_subtitles_filter_value(style)
            subtitle_filter = f"subtitles=filename='{escaped_srt}':force_style='{escaped_style}'"
        elif subtitle_style_key == "lecture":
            back_alpha = self._opacity_percent_to_ass_alpha(subtitle_box_opacity)
            style = (
                f"FontName={subtitle_font_bold},FontSize={font_size},Bold=1,"
                "Outline=2.2,Shadow=0,Spacing=0.1,MarginV=30,MarginL=60,MarginR=60,"
                "Alignment=2,BorderStyle=3,WrapStyle=0,PrimaryColour=&H00FFFFFF&,"
                f"OutlineColour=&H{back_alpha}000000&,BackColour=&H{back_alpha}000000&"
            )
            escaped_style = self._escape_subtitles_filter_value(style)
            subtitle_filter = f"subtitles=filename='{escaped_srt}':force_style='{escaped_style}'"
        else:
            style = (
                f"FontName={subtitle_font},FontSize={font_size},Outline=1.2,Shadow=0.8,"
                "MarginV=20,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&"
            )
            escaped_style = self._escape_subtitles_filter_value(style)
            subtitle_filter = f"subtitles=filename='{escaped_srt}':force_style='{escaped_style}'"

        watermark = self._build_watermark_filter(media, watermark_enabled, watermark_text)
        subscribe_prompt = self._build_subscribe_prompt_filter(
            media,
            subscribe_prompt_enabled,
            subscribe_prompt_text,
            subscribe_prompt_opacity,
        )

        if not watermark and not subscribe_prompt:
            return subtitle_filter

        segments: list[str] = [f"{subtitle_filter}[v0]"]
        current_label = "v0"
        next_index = 1

        def append_intermediate(filter_str: str, final_stage: bool = False) -> None:
            nonlocal current_label, next_index
            if final_stage:
                final_filter = filter_str.replace("[{output_label}]", "")
                segments.append(final_filter.format(input_label=current_label, output_label=""))
                return
            output_label = f"v{next_index}"
            segments.append(filter_str.format(input_label=current_label, output_label=output_label))
            current_label = output_label
            next_index += 1

        def append_text(filter_str: str, final_stage: bool) -> None:
            nonlocal current_label, next_index
            if not filter_str:
                return
            if final_stage:
                segments.append(f"[{current_label}]{filter_str}")
                return
            output_label = f"v{next_index}"
            segments.append(f"[{current_label}]{filter_str}[{output_label}]")
            current_label = output_label
            next_index += 1

        if watermark:
            if watermark.get("overlay_filter"):
                append_intermediate(
                    watermark["overlay_filter"],
                    final_stage=not watermark.get("text_filter") and subscribe_prompt is None,
                )
            append_text(watermark["text_filter"], final_stage=subscribe_prompt is None)

        if subscribe_prompt:
            if subscribe_prompt.get("icon_filter"):
                append_intermediate(
                    subscribe_prompt["icon_filter"],
                    final_stage=not subscribe_prompt.get("text_filter"),
                )
            append_text(subscribe_prompt["text_filter"], final_stage=True)

        return ";".join(segments)

    def _build_watermark_filter(
        self,
        media: dict | None,
        watermark_enabled: bool,
        watermark_text: str,
    ) -> dict[str, str] | None:
        if not watermark_enabled:
            self._log("水印：已关闭")
            return None
        if not watermark_text.strip():
            self._log("水印：文本为空，已跳过")
            return None
        if not media:
            self._log("水印：缺少媒体信息，已跳过")
            return None

        width = int(media.get("width") or 0)
        height = int(media.get("height") or 0)
        duration_sec = float(media.get("duration_sec") or 0.0)
        if width <= 0 or height <= 0:
            self._log("水印：分辨率未知，已跳过")
            return None
        if height > width:
            self._log("水印：竖屏视频，已跳过")
            return None

        fontsize = max(16, min(30, int(round(width * 0.015))))
        margin_x = max(24, int(round(width * 0.025)))
        margin_y = max(24, int(round(height * 0.04)))
        escaped_text = self._escape_drawtext_value(watermark_text.strip())
        text_fontfile = self._resolve_drawtext_fontfile("youtube") or self._resolve_drawtext_fontfile("cjk")
        if not text_fontfile:
            self._log("水印：未找到可用字体，已跳过")
            return None
        escaped_text_fontfile = self._escape_drawtext_value(text_fontfile)
        self._log(
            f"水印：已启用（横屏视频 {width}x{height} {duration_sec/60:.1f} 分钟）"
        )
        self._log("水印：使用纯文字样式。")
        text_filter = (
            "drawtext="
            f"text='{escaped_text}':"
            f"fontfile='{escaped_text_fontfile}':"
            "expansion=none:"
            f"fontsize={fontsize}:"
            "fontcolor=white@0.30:"
            "borderw=0.8:"
            "bordercolor=black@0.22:"
            f"x=w-tw-{margin_x}:"
            f"y={margin_y}"
        )
        return {"text_filter": text_filter, "overlay_filter": ""}

    def _build_subscribe_prompt_filter(
        self,
        media: dict | None,
        subscribe_prompt_enabled: bool,
        subscribe_prompt_text: str,
        subscribe_prompt_opacity: int,
    ) -> dict[str, str] | None:
        if not subscribe_prompt_enabled:
            self._log("订阅提示：已关闭")
            return None
        if not subscribe_prompt_text.strip():
            self._log("订阅提示：文本为空，已跳过")
            return None
        if not media:
            self._log("订阅提示：缺少媒体信息，已跳过")
            return None

        width = int(media.get("width") or 0)
        height = int(media.get("height") or 0)
        if width <= 0 or height <= 0:
            self._log("订阅提示：分辨率未知，已跳过")
            return None

        fontsize = max(16, min(30, int(round(width * 0.015))))
        margin_x = max(24, int(round(width * 0.025)))
        margin_y = max(24, int(round(height * 0.04)))
        float_amplitude = max(6, int(round(height * 0.007)))
        escaped_text = self._escape_drawtext_value(subscribe_prompt_text.strip())
        text_fontfile = self._resolve_drawtext_fontfile("youtube")
        if not text_fontfile:
            self._log("订阅提示：未找到可用中文字体，已跳过")
            return None
        escaped_text_fontfile = self._escape_drawtext_value(text_fontfile)
        self._log(
            "订阅提示：已启用（左下角，圆角红色标签，每10秒出现5秒）"
            f" 透明度={subscribe_prompt_opacity}%"
        )
        badge = self._create_subscribe_prompt_badge(
            subscribe_prompt_text.strip(),
            text_fontfile,
            fontsize,
            subscribe_prompt_opacity,
        )
        enable_expr = "lt(mod(t\\,10)\\,5)"
        if not badge:
            self._log("订阅提示：圆角底板生成失败，回退普通矩形底板。")
            prompt_alpha = self._opacity_percent_to_drawtext_alpha(subscribe_prompt_opacity)
            y_expr = f"h-th-{margin_y}+{float_amplitude}*sin(2*PI*t/2.4)"
            text_filter = (
                "drawtext="
                f"text='{escaped_text}':"
                f"fontfile='{escaped_text_fontfile}':"
                "expansion=none:"
                f"fontsize={fontsize}:"
                "fontcolor=white@0.98:"
                "borderw=1.0:"
                "bordercolor=white@0.22:"
                "box=1:"
                f"boxcolor=red@{prompt_alpha}:"
                "boxborderw=14:"
                f"x={margin_x}:"
                f"y={y_expr}:"
                f"enable='{enable_expr}'"
            )
            return {"text_filter": text_filter, "icon_filter": ""}

        self._log("订阅提示：使用圆角 PNG 底板。")
        badge_path = self._escape_filter_path(str(badge["path"]))
        badge_height = int(badge["height"])
        text_area_x = int(badge["text_area_x"])
        text_area_width = int(badge["text_area_width"])
        text_x = f"{margin_x + text_area_x}+({text_area_width}-tw)/2"
        text_y = (
            f"h-{badge_height}-{margin_y}+({badge_height}-th)/2"
            f"+{float_amplitude}*sin(2*PI*t/2.4)"
        )
        badge_filter = (
            f"movie='{badge_path}',format=rgba[subscribe_badge];"
            f"[{{input_label}}][subscribe_badge]overlay="
            f"x={margin_x}:"
            f"y=H-h-{margin_y}+{float_amplitude}*sin(2*PI*t/2.4):"
            f"enable='{enable_expr}'[{{output_label}}]"
        )
        text_filter = (
            "drawtext="
            f"text='{escaped_text}':"
            f"fontfile='{escaped_text_fontfile}':"
            "expansion=none:"
            f"fontsize={fontsize}:"
            "fontcolor=white@0.98:"
            "borderw=0.8:"
            "bordercolor=black@0.14:"
            f"x={text_x}:"
            f"y={text_y}:"
            f"enable='{enable_expr}'"
        )
        return {"text_filter": text_filter, "icon_filter": badge_filter}

    def _compute_adaptive_font_size(self, subtitle_size_key: str, media: dict | None) -> int:
        base = SUBTITLE_SIZE_MAP.get(subtitle_size_key, 24)
        if not media:
            return base

        width = int(media.get("width") or 0)
        height = int(media.get("height") or 0)
        if width <= 0 or height <= 0:
            return base

        if height > width:
            ratio = 0.62
            orientation = "竖屏"
        elif width / max(1, height) < 1.45:
            ratio = 0.80
            orientation = "近方屏"
        else:
            ratio = 1.0
            orientation = "横屏"

        adaptive = max(14, int(round(base * ratio)))
        self._log(
            f"字幕字号自适应：{orientation} {width}x{height} "
            f"base={base} adjusted={adaptive}"
        )
        return adaptive

    def _parse_subtitle_box_opacity(self, raw: str) -> int:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return LECTURE_BOX_OPACITY_DEFAULT
        return max(0, min(100, value))

    def _parse_percent_value(self, raw: str, fallback: int) -> int:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return fallback
        return max(0, min(100, value))

    def _opacity_percent_to_drawtext_alpha(self, opacity_percent: int) -> str:
        return f"{max(0, min(100, opacity_percent)) / 100:.2f}"

    def _opacity_percent_to_ass_alpha(self, opacity_percent: int) -> str:
        opacity = max(0, min(100, opacity_percent))
        alpha = int(round(255 * (100 - opacity) / 100))
        return f"{alpha:02X}"

    def _reset_ui(self):
        self.processing = False
        self.start_btn.configure(state=tk.NORMAL)
        self.progress.stop()

    def _clear_logs(self):
        self.log_box.delete("1.0", tk.END)

    def _tick_logs(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_box.insert(tk.END, msg + "\n")
                self.log_box.see(tk.END)
        except queue.Empty:
            pass
        self.root.after(120, self._tick_logs)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}")


def main():
    if TkinterDnD is not None:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = SubtitleBurnerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
