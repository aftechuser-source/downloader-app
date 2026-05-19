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
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'download_url': info.get('url'),
                'formats': [
                    {
                        'quality': f.get('format_note', 'unknown'),
                        'ext': f.get('ext'),
                        'url': f.get('url'),
                        'filesize': f.get('filesize')
                    }
                    for f in info.get('formats', [])
                    if f.get('url') and f.get('ext') == 'mp4'
                ]
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

