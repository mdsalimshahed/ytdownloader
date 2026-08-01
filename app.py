import os
import io
import shutil
import tempfile
import urllib.parse
from flask import Flask, render_template, request, send_file, jsonify
import yt_dlp

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download', methods=['GET'])
def download_media():
    video_url = request.args.get('url')
    download_format = request.args.get('format', 'mp4')  # 'mp4' or 'mp3'

    if not video_url:
        return jsonify({'error': 'No URL provided'}), 400

    # Create temporary directory
    temp_dir = tempfile.mkdtemp()

    try:
        if download_format == 'mp3':
            file_ext = 'mp3'
            mime_type = 'audio/mpeg'
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
        else:
            file_ext = 'mp4'
            mime_type = 'video/mp4'
            ydl_opts = {
                'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
            }

        # Download file to temp directory
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            file_path = ydl.prepare_filename(info)

            if download_format == 'mp3':
                file_path = os.path.splitext(file_path)[0] + '.mp3'

        if not os.path.exists(file_path):
            return jsonify({'error': 'Download failed'}), 500

        download_name = os.path.basename(file_path)

        # Read the file into RAM and immediately close/delete the file on disk
        with open(file_path, 'rb') as f:
            file_bytes = io.BytesIO(f.read())

        # Cleanup disk files before sending the response to release the Windows lock
        shutil.rmtree(temp_dir, ignore_errors=True)

        # Serve the in-memory buffer to the client
        return send_file(
            file_bytes,
            mimetype=mime_type,
            as_attachment=True,
            download_name=download_name
        )

    except Exception as e:
        # Emergency cleanup on failure
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)