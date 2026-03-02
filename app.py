#!/usr/bin/env python3
import os
import queue
import re
import shutil
import subprocess
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
        self._row_file(frame, "字幕文件（简体SRT）", self.srt_path, self._pick_srt)
        self._row_file(frame, "输出文件（建议 .mp4）", self.output_path, self._pick_output)

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
        custom_crf_raw = self.custom_crf_var.get().strip()
        custom_crf: str | None = None

        if not video or not os.path.isfile(video):
            messagebox.showerror("参数错误", "请选择有效视频文件。")
            return
        if not srt or not os.path.isfile(srt):
            messagebox.showerror("参数错误", "请选择有效 SRT 文件。")
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
            args=(video, srt, output, subtitle_size_key, quality_mode_key, custom_crf),
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
    ):
        tmpdir = tempfile.mkdtemp(prefix="trad_hardsub_")
        trad_srt = os.path.join(tmpdir, "subtitle_trad.srt")

        try:
            self._log("开始：简体字幕 -> 繁体字幕")
            self._convert_to_trad(srt, trad_srt)
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
