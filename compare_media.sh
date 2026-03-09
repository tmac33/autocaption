#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <source_video> <hardsub_video>"
  exit 1
fi

SRC="$1"
OUT="$2"

if [[ ! -f "$SRC" ]]; then
  echo "Source not found: $SRC"
  exit 1
fi
if [[ ! -f "$OUT" ]]; then
  echo "Output not found: $OUT"
  exit 1
fi

if command -v ffprobe >/dev/null 2>&1; then
  FFPROBE_BIN="$(command -v ffprobe)"
elif [[ -x /opt/homebrew/bin/ffprobe ]]; then
  FFPROBE_BIN="/opt/homebrew/bin/ffprobe"
elif [[ -x /usr/local/bin/ffprobe ]]; then
  FFPROBE_BIN="/usr/local/bin/ffprobe"
else
  echo "ffprobe not found. Install ffmpeg first: brew install ffmpeg"
  exit 1
fi

probe_video_stream() {
  local file="$1"
  "$FFPROBE_BIN" -v error \
    -select_streams v:0 \
    -show_entries stream=width,height,avg_frame_rate,r_frame_rate,bit_rate,pix_fmt,profile \
    -of default=noprint_wrappers=1:nokey=0 \
    "$file"
}

probe_audio_stream() {
  local file="$1"
  "$FFPROBE_BIN" -v error \
    -select_streams a:0 \
    -show_entries stream=codec_name,bit_rate,sample_rate,channels \
    -of default=noprint_wrappers=1:nokey=0 \
    "$file"
}

probe_format() {
  local file="$1"
  "$FFPROBE_BIN" -v error \
    -show_entries format=duration,bit_rate,size \
    -of default=noprint_wrappers=1:nokey=0 \
    "$file"
}

extract_value() {
  local key="$1"
  local data="$2"
  awk -F'=' -v k="$key" '$1==k {print $2}' <<<"$data" | head -n1
}

fps_from_ratio() {
  local ratio="$1"
  if [[ -z "$ratio" || "$ratio" == "0/0" ]]; then
    echo ""
    return
  fi
  awk -F'/' '{ if ($2==0) print ""; else printf "%.3f", $1/$2 }' <<<"$ratio"
}

to_kbps() {
  local bps="$1"
  if [[ -z "$bps" ]]; then
    echo ""
    return
  fi
  awk -v b="$bps" 'BEGIN { printf "%.0f", b/1000 }'
}

SRC_V="$(probe_video_stream "$SRC")"
OUT_V="$(probe_video_stream "$OUT")"
SRC_A="$(probe_audio_stream "$SRC" || true)"
OUT_A="$(probe_audio_stream "$OUT" || true)"
SRC_F="$(probe_format "$SRC")"
OUT_F="$(probe_format "$OUT")"

SRC_W="$(extract_value width "$SRC_V")"
SRC_H="$(extract_value height "$SRC_V")"
OUT_W="$(extract_value width "$OUT_V")"
OUT_H="$(extract_value height "$OUT_V")"
SRC_FPS="$(fps_from_ratio "$(extract_value avg_frame_rate "$SRC_V")")"
OUT_FPS="$(fps_from_ratio "$(extract_value avg_frame_rate "$OUT_V")")"
SRC_VBPS="$(extract_value bit_rate "$SRC_V")"
OUT_VBPS="$(extract_value bit_rate "$OUT_V")"
SRC_ABPS="$(extract_value bit_rate "$SRC_A")"
OUT_ABPS="$(extract_value bit_rate "$OUT_A")"
SRC_DBIT="$(extract_value bit_rate "$SRC_F")"
OUT_DBIT="$(extract_value bit_rate "$OUT_F")"
SRC_DUR="$(extract_value duration "$SRC_F")"
OUT_DUR="$(extract_value duration "$OUT_F")"

SRC_VKBPS="$(to_kbps "$SRC_VBPS")"
OUT_VKBPS="$(to_kbps "$OUT_VBPS")"
SRC_AKBPS="$(to_kbps "$SRC_ABPS")"
OUT_AKBPS="$(to_kbps "$OUT_ABPS")"
SRC_DKBPS="$(to_kbps "$SRC_DBIT")"
OUT_DKBPS="$(to_kbps "$OUT_DBIT")"

echo "=== Media Compare (source vs hardsub) ==="
echo "source: $SRC"
echo "hardsub: $OUT"
echo
echo "[Video]"
echo "resolution: ${SRC_W}x${SRC_H} -> ${OUT_W}x${OUT_H}"
echo "fps: ${SRC_FPS:-N/A} -> ${OUT_FPS:-N/A}"
echo "video bitrate(kbps): ${SRC_VKBPS:-N/A} -> ${OUT_VKBPS:-N/A}"
echo
echo "[Audio]"
echo "audio bitrate(kbps): ${SRC_AKBPS:-N/A} -> ${OUT_AKBPS:-N/A}"
echo
echo "[Container]"
echo "duration(s): ${SRC_DUR:-N/A} -> ${OUT_DUR:-N/A}"
echo "overall bitrate(kbps): ${SRC_DKBPS:-N/A} -> ${OUT_DKBPS:-N/A}"
echo

if [[ -n "${SRC_DKBPS:-}" && -n "${OUT_DKBPS:-}" ]]; then
  DIFF_PCT="$(awk -v a="$SRC_DKBPS" -v b="$OUT_DKBPS" 'BEGIN { if (a==0) print "0"; else printf "%.1f", ((b-a)/a)*100 }')"
  echo "overall bitrate delta: ${DIFF_PCT}%"
  if awk -v d="$DIFF_PCT" 'BEGIN { exit !(d <= 10) }'; then
    echo "评估: 推流友好（与原片接近）"
  elif awk -v d="$DIFF_PCT" 'BEGIN { exit !(d <= 25) }'; then
    echo "评估: 可接受（略高，可能稍慢）"
  else
    echo "评估: 偏慢风险高（建议降低码率或改用匹配源参数模式）"
  fi
fi
