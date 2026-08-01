import os
import io
import shutil
import tempfile
from flask import Flask, render_template, request, send_file, jsonify
import yt_dlp

app = Flask(__name__)


def get_cookie_file_path(temp_dir):
    """Checks if cookies exist in Environment Variables and writes them to a temporary file."""
    cookies_content = os.environ.get('YOUTUBE_COOKIES')
    if cookies_content:
        cookie_path = os.path.join(temp_dir, 'cookies.txt')
        with open(cookie_path, 'w', encoding='utf-8') as f:
            f.write(cookies_content)
        return cookie_path
    return None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download', methods=['GET'])
def download_media():
    video_url = request.args.get('url')
    download_format = request.args.get('format', 'mp4')

    if not video_url:
        return jsonify({'error': 'No URL provided'}), 400

    temp_dir = tempfile.mkdtemp()

    try:
        base_ydl_opts = {
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }

        # Dynamically load cookies if configured on Render
        cookie_path = get_cookie_file_path(temp_dir)
        if cookie_path:
            base_ydl_opts['cookiefile'] = cookie_path

        if download_format == 'mp3':
            ydl_opts = {
                **base_ydl_opts,
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
        else:
            ydl_opts = {
                **base_ydl_opts,
                'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            file_path = ydl.prepare_filename(info)

            if download_format == 'mp3':
                file_path = os.path.splitext(file_path)[0] + '.mp3'

        if not os.path.exists(file_path):
            return jsonify({'error': 'Download failed on server'}), 500

        download_name = os.path.basename(file_path)

        with open(file_path, 'rb') as f:
            file_bytes = io.BytesIO(f.read())

        # Cleanup temporary files (including generated cookie file)
        shutil.rmtree(temp_dir, ignore_errors=True)

        return send_file(
            file_bytes,
            mimetype='audio/mpeg' if download_format == 'mp3' else 'video/mp4',
            as_attachment=True,
            download_name=download_name
        )

    except Exception as e:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)