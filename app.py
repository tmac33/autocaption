#!/usr/bin/env python3
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
except ImportError:
    WhisperModel = None


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
SUB_EXTS = {".srt"}
SUBTITLE_SIZE_MAP = {"small": 18, "medium": 24, "large": 30}
SUBTITLE_SIZE_LABEL_TO_KEY = {"小": "small", "中": "medium", "大": "large"}
QUALITY_LABEL_TO_KEY = {"极致画质": "quality", "体积优先": "size"}
YOUTUBE_ENCODING_PROFILES = {
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
        self.quality_mode_label = tk.StringVar(value="极致画质")
        self.custom_crf_var = tk.StringVar(value="")
        self.auto_asr_var = tk.BooleanVar(value=False)
        self.align_timeline_var = tk.BooleanVar(value=False)
        self.asr_model_label = tk.StringVar(value="快速（small）")
        self.processing = False
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.ffmpeg_bin = self._resolve_ffmpeg_bin()

        self._build_ui()
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
            text="有SRT时用自动时间轴校准文字",
            variable=self.align_timeline_var,
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

        quality_row = ttk.Frame(frame)
        quality_row.pack(fill=tk.X, pady=4)
        ttk.Label(quality_row, text="画质模式", width=18).pack(side=tk.LEFT)
        quality_combo = ttk.Combobox(
            quality_row,
            textvariable=self.quality_mode_label,
            values=["极致画质", "体积优先"],
            state="readonly",
            width=10,
        )
        quality_combo.pack(side=tk.LEFT, padx=(8, 0))

        crf_row = ttk.Frame(frame)
        crf_row.pack(fill=tk.X, pady=4)
        ttk.Label(crf_row, text="自定义CRF（可选）", width=18).pack(side=tk.LEFT)
        crf_entry = ttk.Entry(crf_row, textvariable=self.custom_crf_var, width=12)
        crf_entry.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(crf_row, text="留空使用画质模式预设；范围建议 17-23").pack(side=tk.LEFT, padx=8)

        options = ttk.Frame(frame)
        options.pack(fill=tk.X, pady=8)
        ttk.Label(
            options,
            text="编码策略：H.264 硬字幕（极致画质/体积优先），适合 YouTube 发布",
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

        video = self.video_path.get().strip()
        srt = self.srt_path.get().strip()
        output = self.output_path.get().strip()
        size_label = self.subtitle_size_label.get().strip() or "中"
        subtitle_size_key = SUBTITLE_SIZE_LABEL_TO_KEY.get(size_label, "medium")
        quality_label = self.quality_mode_label.get().strip() or "极致画质"
        quality_mode_key = QUALITY_LABEL_TO_KEY.get(quality_label, "quality")
        asr_model_label = self.asr_model_label.get().strip() or "快速（small）"
        asr_model_key = ASR_MODEL_LABEL_TO_KEY.get(asr_model_label, "small")
        auto_asr = self.auto_asr_var.get()
        align_timeline = self.align_timeline_var.get()
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
        if custom_crf_raw:
            if not custom_crf_raw.isdigit():
                messagebox.showerror("参数错误", "自定义CRF必须是整数（建议17-23）。")
                return
            crf_int = int(custom_crf_raw)
            if crf_int < 0 or crf_int > 51:
                messagebox.showerror("参数错误", "自定义CRF范围应为 0-51。")
                return
            custom_crf = str(crf_int)

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
                quality_mode_key,
                custom_crf,
                auto_asr,
                align_timeline,
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
        quality_mode_key: str,
        custom_crf: str | None,
        auto_asr: bool,
        align_timeline: bool,
        asr_model_key: str,
    ):
        tmpdir = tempfile.mkdtemp(prefix="trad_hardsub_")
        src_srt = os.path.join(tmpdir, "subtitle_src.srt")
        asr_srt = os.path.join(tmpdir, "subtitle_asr.srt")
        aligned_srt = os.path.join(tmpdir, "subtitle_aligned.srt")
        trad_srt = os.path.join(tmpdir, "subtitle_trad.srt")

        try:
            if srt and os.path.isfile(srt):
                src_srt = srt
                if align_timeline:
                    self._log("开始：自动拾取语音时间轴（用于校准提供的SRT文字）")
                    self._transcribe_video_to_srt(video, asr_srt, asr_model_key)
                    self._align_srt_timeline_by_asr(src_srt, asr_srt, aligned_srt)
                    src_srt = aligned_srt
                    self._log(f"已使用自动时间轴校准字幕: {src_srt}")
                elif auto_asr:
                    self._log("检测到已提供SRT，自动拾取已跳过。")
            else:
                self._log("开始：自动拾取简体中文字幕（语音识别）")
                self._transcribe_video_to_srt(video, src_srt, asr_model_key)
                self._log(f"自动字幕已生成: {src_srt}")
            self._log("开始：简体字幕 -> 繁体字幕")
            self._convert_to_trad(src_srt, trad_srt)
            self._log(f"繁体字幕已生成: {trad_srt}")
            self._log("开始：YouTube 硬字幕压制（H.264）")
            self._burn_subtitle_youtube(
                video,
                trad_srt,
                output,
                subtitle_size_key,
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
        quality_mode_key: str,
        custom_crf: str | None,
    ):
        video = os.path.abspath(video)
        trad_srt = os.path.abspath(trad_srt)
        output = os.path.abspath(output)
        encoding = YOUTUBE_ENCODING_PROFILES.get(quality_mode_key, YOUTUBE_ENCODING_PROFILES["quality"])
        effective_crf = custom_crf or encoding["crf"]

        font_size = SUBTITLE_SIZE_MAP.get(subtitle_size_key, 24)
        style = (
            f"FontName=Arial,FontSize={font_size},Outline=1.2,Shadow=0.8,"
            "PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&"
        )
        escaped_srt = (
            trad_srt.replace("\\", "\\\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
        )
        vf = f"subtitles='{escaped_srt}':force_style='{style}'"
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            video,
            "-vf",
            vf,
            "-c:v",
            encoding["video_codec"],
            "-crf",
            effective_crf,
            "-preset",
            encoding["preset"],
            "-pix_fmt",
            encoding["pix_fmt"],
            "-c:a",
            encoding["audio_codec"],
            "-b:a",
            encoding["audio_bitrate"],
        ]
        if encoding["maxrate"] and encoding["bufsize"]:
            cmd.extend(["-maxrate", encoding["maxrate"], "-bufsize", encoding["bufsize"]])
        cmd.append(output)
        self._log(
            f"编码参数：模式={quality_mode_key} CRF={effective_crf}"
            + (f" maxrate={encoding['maxrate']}" if encoding["maxrate"] else "")
        )

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
        for line in proc.stdout:
            if "time=" in line or "frame=" in line:
                self._log(line.strip())
        code = proc.wait()
        elapsed = time.time() - start
        if code != 0:
            raise RuntimeError("ffmpeg 压制失败，请检查字幕编码/视频格式是否可读。")
        self._log(f"ffmpeg 执行完成，用时 {elapsed:.1f}s")

    def _transcribe_video_to_srt(self, video: str, dst_srt: str, model_size: str):
        if WhisperModel is None:
            raise RuntimeError("缺少 faster-whisper 依赖，无法自动拾取字幕。")
        self._log(f"加载识别模型: {model_size}（首次使用可能会下载模型，耗时较长）")
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(video, language="zh", vad_filter=True, beam_size=5)

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
        if count == 0:
            raise RuntimeError("自动拾取未生成有效字幕，请检查视频语音是否清晰。")
        self._log(f"自动拾取完成，共 {count} 条字幕。")

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

        # Use text-length pace mapping instead of plain index mapping.
        # This gives earlier/later timing in proportion to speech density.
        asr_counts = [max(1, self._effective_text_len(x["text"])) for x in asr_entries]
        text_counts = [max(1, self._effective_text_len(x["text"])) for x in text_entries]

        asr_total = sum(asr_counts)
        text_total = sum(text_counts)
        asr_start = asr_entries[0]["start"]
        asr_end = asr_entries[-1]["end"]
        asr_span = max(0.5, asr_end - asr_start)

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

            # Show slightly earlier, hide slightly earlier to reduce lingering.
            start_sec = max(asr_start, start_sec - TIMELINE_EARLY_SHOW_SEC)
            end_sec = min(asr_end, end_sec - TIMELINE_EARLY_HIDE_SEC)
            if end_sec <= start_sec:
                end_sec = min(asr_end, start_sec + TIMELINE_MIN_DURATION_FALLBACK_SEC)

            aligned.append({"start": start_sec, "end": end_sec, "text": ent["text"]})

        # Enforce no overlap and timely disappear before next line.
        for i in range(len(aligned) - 1):
            max_end = aligned[i + 1]["start"] - TIMELINE_GAP_BEFORE_NEXT_SEC
            if aligned[i]["end"] > max_end:
                aligned[i]["end"] = max_end
            if aligned[i]["end"] <= aligned[i]["start"]:
                aligned[i]["end"] = aligned[i]["start"] + TIMELINE_MIN_DURATION_SEC
        for i in range(len(aligned)):
            if i > 0 and aligned[i]["start"] < aligned[i - 1]["end"]:
                aligned[i]["start"] = aligned[i - 1]["end"] + 0.02
            if aligned[i]["end"] <= aligned[i]["start"]:
                aligned[i]["end"] = aligned[i]["start"] + TIMELINE_MIN_DURATION_SEC
            aligned[i]["end"] = min(aligned[i]["end"], asr_start + asr_span)

        self._write_srt_entries(dst_srt, aligned)

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

    def _write_srt_entries(self, srt_path: str, entries: list[dict]):
        with open(srt_path, "w", encoding="utf-8") as f:
            for idx, ent in enumerate(entries, start=1):
                start = self._format_srt_timestamp(ent["start"])
                end = self._format_srt_timestamp(ent["end"])
                f.write(f"{idx}\n{start} --> {end}\n{ent['text']}\n\n")

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
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            return ffmpeg
        for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

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
        self.log_queue.put(msg)


def main():
    if TkinterDnD is not None:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = SubtitleBurnerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
