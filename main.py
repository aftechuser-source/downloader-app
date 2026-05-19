from flask import Flask, request, jsonify, Response, stream_with_context
import yt_dlp, os, requests

app = Flask(__name__)

# ─── Extract video info ────────────────────────────────────────────
@app.route('/extract', methods=['POST'])
def extract():
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400

    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'quiet': True,
        'socket_timeout': 30,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'download_url': info.get('url'),
                'headers': dict(info.get('http_headers', {})),
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Proxy download (bypasses CDN 403) ────────────────────────────
@app.route('/download', methods=['POST'])
def download():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    video_url = data.get('url')
    req_headers = data.get('headers', {})

    if not video_url:
        return jsonify({'error': 'url required'}), 400

    # Browser-like headers to bypass CDN blocks
    req_headers.setdefault('User-Agent',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
    req_headers.setdefault('Accept', '*/*')
    req_headers.setdefault('Accept-Encoding', 'identity')

    try:
        r = requests.get(video_url, headers=req_headers, stream=True, timeout=30)
        if not r.ok:
            return jsonify({'error': f'Upstream {r.status_code}'}), 502

        return Response(
            stream_with_context(r.iter_content(chunk_size=1024 * 1024)),
            content_type='video/mp4',
            headers={
                'Content-Disposition': 'attachment; filename="video.mp4"',
                'Content-Length': r.headers.get('Content-Length', ''),
            }
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Health check ──────────────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
