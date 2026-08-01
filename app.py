import os
import io
import tempfile
import urllib.parse
from flask import Flask, render_template, request, send_file, jsonify
import yt_dlp

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download', methods=['POST'])
def download_media():
    video_url = request.form.get('url')
    download_format = request.form.get('format', 'mp4')

    # Read proxy details sent from frontend form
    proxy_ip = request.form.get('proxy_ip', '').strip()
    proxy_port = request.form.get('proxy_port', '').strip()
    proxy_user = request.form.get('proxy_user', '').strip()
    proxy_pass = request.form.get('proxy_pass', '').strip()

    # Read uploaded cookies file if provided
    cookies_file = request.files.get('cookies_file')

    if not video_url:
        return jsonify({'error': 'No URL provided'}), 400

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            base_ydl_opts = {
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'quiet': False,
                'no_warnings': False,
            }

            # 1. Build Proxy URL if IP and Port are supplied
            if proxy_ip and proxy_port:
                if proxy_user and proxy_pass:
                    proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_ip}:{proxy_port}"
                else:
                    proxy_url = f"http://{proxy_ip}:{proxy_port}"
                base_ydl_opts['proxy'] = proxy_url

            # 2. Save uploaded cookies file into temporary directory
            if cookies_file and cookies_file.filename != '':
                cookie_path = os.path.join(temp_dir, 'cookies.txt')
                cookies_file.save(cookie_path)
                base_ydl_opts['cookiefile'] = cookie_path

            # Format configuration
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
                    'format': 'bestvideo+bestaudio/best',
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

            return send_file(
                file_bytes,
                mimetype='audio/mpeg' if download_format == 'mp3' else 'video/mp4',
                as_attachment=True,
                download_name=download_name
            )

        except Exception as e:
            return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)