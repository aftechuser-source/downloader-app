from flask import Flask, request, jsonify
import yt_dlp
import os

app = Flask(__name__)

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
            # Get headers required for download
            http_headers = info.get('http_headers', {})
            return jsonify({
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'download_url': info.get('url'),
                'headers': dict(http_headers),  # ← Send headers to Android
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
