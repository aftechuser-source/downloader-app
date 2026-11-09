from flask import Flask, request, jsonify, Response, stream_with_context, send_file, after_this_request
import yt_dlp
import os
import uuid
import logging
import traceback
import requests
from urllib.parse import urlencode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

YDL_OPTS = {
    "format": "best[ext=mp4]/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "socket_timeout": 30,
}


def _is_tiktok_or_reel(url: str) -> bool:
    u = (url or "").lower()
    return any(x in u for x in ("tiktok", "vt.tiktok", "douyin", "instagram.com", "cdninstagram"))


# ─── Extract video info ────────────────────────────────────────────
@app.route("/extract", methods=["POST"])
def extract():
    data = request.get_json(silent=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"error": "URL required"}), 400

    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                "title": info.get("title"),
                "thumbnail": info.get("thumbnail"),
                "download_url": info.get("url"),
                "webpage_url": info.get("webpage_url") or url,
                "duration": info.get("duration"),
                "headers": dict(info.get("http_headers") or {}),
            })
    except Exception as e:
        logger.exception("extract failed")
        return jsonify({"error": str(e)}), 500


# ─── Extract all available qualities (new, additive endpoint) ─────
@app.route("/extract/formats", methods=["POST"])
def extract_formats():
    data = request.get_json(silent=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"error": "URL required"}), 400

    try:
        # No "format" selection: we want the full list, not one pick.
        opts = {k: v for k, v in YDL_OPTS.items() if k != "format"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        proto = request.headers.get("X-Forwarded-Proto", request.scheme)
        base = f"{proto}://{request.host}"
        formats = []
        for f in info.get("formats") or []:
            if not f.get("url") or f.get("vcodec") in (None, "none"):
                continue
            if f.get("protocol") in ("mhtml",):
                continue
            height = f.get("height")
            formats.append({
                "format_id": f.get("format_id"),
                "quality": f"{height}p" if height else (f.get("format_note") or "unknown"),
                "height": height,
                "width": f.get("width"),
                "fps": f.get("fps"),
                "ext": f.get("ext"),
                "protocol": f.get("protocol"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "has_audio": f.get("acodec") not in (None, "none"),
                "url": f["url"],
                "headers": dict(f.get("http_headers") or info.get("http_headers") or {}),
                "download_link": base + "/download/file?" + urlencode(
                    {"page_url": info.get("webpage_url") or url, "format_id": f.get("format_id")}),
            })

        # One entry per (height, has_audio, ext); prefer larger bitrate.
        formats.sort(key=lambda x: (x["height"] or 0, x["has_audio"], x["filesize"] or 0), reverse=True)
        seen, unique = set(), []
        for f in formats:
            key = (f["height"], f["has_audio"], f["ext"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(f)

        return jsonify({
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url") or url,
            "duration": info.get("duration"),
            "formats": unique,
        })
    except Exception as e:
        logger.exception("extract formats failed")
        return jsonify({"error": str(e)}), 500


# ─── Download: yt-dlp for TikTok/IG, proxy for others ─────────────
@app.route("/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    page_url = data.get("page_url") or data.get("source_url")
    video_url = data.get("url")
    req_headers = dict(data.get("headers") or {})

    if not video_url and not page_url:
        return jsonify({"error": "url or page_url required"}), 400

    # TikTok / Instagram: CDN blocks proxy — yt-dlp needs the original page link
    if page_url:
        return _download_via_ytdlp(page_url, data.get("format_id"))

    if _is_tiktok_or_reel(video_url or ""):
        return jsonify({"error": "page_url required for TikTok/Instagram downloads"}), 400

    return _download_via_proxy(video_url, req_headers)


def _download_via_proxy(video_url: str, req_headers: dict):
    req_headers.setdefault(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    req_headers.setdefault("Accept", "*/*")
    req_headers.setdefault("Accept-Encoding", "identity")

    try:
        logger.info("proxy download url=%s", video_url[:80])
        r = requests.get(video_url, headers=req_headers, stream=True, timeout=120)
        if not r.ok:
            return jsonify({"error": f"Upstream {r.status_code}"}), 502

        return Response(
            stream_with_context(r.iter_content(chunk_size=1024 * 1024)),
            content_type="video/mp4",
            headers={
                "Content-Disposition": 'attachment; filename="video.mp4"',
                "Content-Length": r.headers.get("Content-Length", ""),
            },
        )
    except Exception as e:
        logger.exception("proxy download failed")
        return jsonify({"error": str(e)}), 500


def _download_via_ytdlp(url: str, format_id: str = None):
    video_id = str(uuid.uuid4())
    outtmpl = os.path.join(DOWNLOAD_DIR, f"{video_id}.%(ext)s")
    opts = {**YDL_OPTS, "outtmpl": outtmpl}
    if format_id:
        # chosen format with audio, else merge it with best audio
        opts["format"] = f"{format_id}[acodec!=none]/{format_id}+bestaudio/{format_id}"
        opts["merge_output_format"] = "mp4"
    filename = None

    try:
        logger.info("yt-dlp download url=%s", url[:80])
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        if not filename or not os.path.isfile(filename):
            import glob
            matches = glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*"))
            matches = [m for m in matches if not m.endswith(".part")]
            if not matches:
                return jsonify({"error": "File not found after download"}), 500
            filename = matches[0]

        logger.info("yt-dlp done file=%s size=%s", filename, os.path.getsize(filename))

        @after_this_request
        def cleanup(response):
            try:
                if filename and os.path.exists(filename):
                    os.remove(filename)
            except OSError:
                pass
            return response

        return send_file(
            filename,
            mimetype="video/mp4",
            as_attachment=True,
            download_name=os.path.basename(filename),
        )
    except Exception as e:
        logger.exception("yt-dlp download failed")
        return jsonify({"error": str(e), "detail": traceback.format_exc()[-400:]}), 500


# ─── Direct download link for one chosen quality (GET, new) ───────
@app.route("/download/file", methods=["GET"])
def download_file():
    page_url = request.args.get("page_url")
    format_id = request.args.get("format_id")
    if not page_url or not format_id:
        return jsonify({"error": "page_url and format_id required"}), 400
    return _download_via_ytdlp(page_url, format_id)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
