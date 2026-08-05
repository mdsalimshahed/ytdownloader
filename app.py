import os
import io
import re
import json
import time
import tempfile
import threading
from urllib.parse import urlparse, parse_qs
from flask import Flask, render_template, request, send_file, jsonify, Response
from flask_cors import CORS
import yt_dlp

# Import the isolated Deezer blueprint
from deezer_routes import deezer_bp

app = Flask(__name__)

# ENABLE CORS: This stops the browser from blocking localhost requests!
CORS(app, resources={r"/*": {"origins": "*"}})

app.register_blueprint(deezer_bp)

progress_store = {}

def clean_youtube_url(raw_url):
    """Strips playlist, tracking IDs (si), and extra URL parameters in Python."""
    if not raw_url:
        return ''
    try:
        parsed = urlparse(raw_url.strip())
        if 'youtube.com' in parsed.netloc:
            qs = parse_qs(parsed.query)
            if 'v' in qs:
                return f"https://www.youtube.com/watch?v={qs['v'][0]}"
            elif '/shorts/' in parsed.path:
                video_id = parsed.path.split('/shorts/')[1].split('/')[0]
                return f"https://www.youtube.com/watch?v={video_id}"
        elif 'youtu.be' in parsed.netloc:
            video_id = parsed.path.strip('/')
            return f"https://www.youtube.com/watch?v={video_id}"
    except Exception:
        pass
    return raw_url.strip()

def parse_proxy_input(raw_input: str) -> str:
    """
    Extracts IP, Port, Username, and Password from pasted curl commands,
    Webshare responses, or raw proxy strings.
    """
    if not raw_input:
        return None
    raw_input = raw_input.strip()
    if "http://" in raw_input or "https://" in raw_input:
        match = re.search(r'https?://[^\s\'"]+', raw_input)
        if match:
            return match.group(0)
    match = re.search(r'([^:\s\'"]+):([^:\s\'"]+)@(\d{1,3}(?:\.\d{1,3}){3}):(\d+)', raw_input)
    if match:
        user, pwd, ip, port = match.groups()
        return f"http://{user}:{pwd}@{ip}:{port}"
    match = re.search(r'(\d{1,3}(?:\.\d{1,3}){3}):(\d+):([^:\s\'"]+):([^:\s\'"]+)', raw_input)
    if match:
        ip, port, user, pwd = match.groups()
        return f"http://{user}:{pwd}@{ip}:{port}"
    match = re.search(r'([^:\s\'"]+):([^:\s\'"]+):(\d{1,3}(?:\.\d{1,3}){3}):(\d+)', raw_input)
    if match:
        user, pwd, ip, port = match.groups()
        return f"http://{user}:{pwd}@{ip}:{port}"
    match = re.search(r'(\d{1,3}(?:\.\d{1,3}){3}):(\d+)', raw_input)
    if match:
        ip, port = match.groups()
        return f"http://{ip}:{port}"
    raise ValueError("Could not parse a valid proxy from the provided text.")

def get_progress_hook(session_id):
    """yt-dlp progress hook to capture real-time percentage and download speed."""
    def hook(d):
        if session_id not in progress_store:
            return
        if d['status'] == 'downloading':
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded_bytes = d.get('downloaded_bytes', 0)
            percent = round((downloaded_bytes / total_bytes) * 100, 1) if total_bytes > 0 else 0
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            progress_store[session_id] = {
                'status': 'downloading',
                'percent': percent,
                'speed': speed,
                'eta': eta,
                'message': f"Downloading: {percent}% at {speed} (ETA: {eta})"
            }
        elif d['status'] == 'finished':
            progress_store[session_id] = {
                'status': 'processing',
                'percent': 95.0,
                'speed': '',
                'eta': '',
                'message': "Download complete! Converting file (FFmpeg)..."
            }
    return hook

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/progress/<session_id>')
def progress_stream(session_id):
    def event_stream():
        while True:
            data = progress_store.get(session_id, {
                'status': 'starting',
                'percent': 0,
                'message': 'Initializing request...'
            })
            yield f"data: {json.dumps(data)}\n\n"
            if data.get('status') == 'completed' or data.get('status') == 'error':
                break
            time.sleep(0.5)
    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/download', methods=['POST'])
def download_media():
    session_id = request.form.get('session_id')
    raw_url = request.form.get('url')
    download_format = request.form.get('format', 'mp4')
    
    video_url = clean_youtube_url(raw_url)
    raw_proxy_input = request.form.get('proxy_input', '').strip()
    cookies_file = request.files.get('cookies_file')
    
    if not video_url:
        return jsonify({'error': 'No URL provided'}), 400

    progress_store[session_id] = {
        'status': 'starting',
        'percent': 5.0,
        'message': 'Connecting to YouTube and solving JS challenge...'
    }

    proxy_url = None
    if raw_proxy_input:
        try:
            proxy_url = parse_proxy_input(raw_proxy_input)
        except ValueError as e:
            progress_store[session_id] = {'status': 'error', 'message': str(e)}
            return jsonify({'error': str(e)}), 400

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            base_ydl_opts = {
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'quiet': False,
                'no_warnings': False,
                'noplaylist': True,
                'progress_hooks': [get_progress_hook(session_id)],
            }

            if proxy_url:
                base_ydl_opts['proxy'] = proxy_url

            if cookies_file and cookies_file.filename != '':
                cookie_path = os.path.join(temp_dir, 'cookies.txt')
                cookies_file.save(cookie_path)
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
                    'format': 'bestvideo+bestaudio/best',
                }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                file_path = ydl.prepare_filename(info)
                if download_format == 'mp3':
                    file_path = os.path.splitext(file_path)[0] + '.mp3'

            if not os.path.exists(file_path):
                progress_store[session_id] = {'status': 'error', 'message': 'Download failed on server'}
                return jsonify({'error': 'Download failed on server'}), 500

            download_name = os.path.basename(file_path)
            progress_store[session_id] = {
                'status': 'completed',
                'percent': 100.0,
                'message': 'Transferring file to your device...'
            }

            with open(file_path, 'rb') as f:
                file_bytes = io.BytesIO(f.read())

            return send_file(
                file_bytes,
                mimetype='audio/mpeg' if download_format == 'mp3' else 'video/mp4',
                as_attachment=True,
                download_name=download_name
            )

        except Exception as e:
            progress_store[session_id] = {'status': 'error', 'message': str(e)}
            return jsonify({'error': str(e)}), 500
        finally:
            def cleanup_session():
                time.sleep(5)
                progress_store.pop(session_id, None)
            threading.Thread(target=cleanup_session).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)