import os, re, uuid, threading, time, ipaddress, socket
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify, send_file, abort
import yt_dlp

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/clipnest-downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
JOBS = {}
JOBS_LOCK = threading.Lock()
MAX_ACTIVE = int(os.environ.get("MAX_ACTIVE_DOWNLOADS", "2"))
MAX_FILE_AGE = int(os.environ.get("FILE_TTL_SECONDS", "3600"))
ALLOWED_FORMATS = {"best": "best", "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]", "720": "bestvideo[height<=720]+bestaudio/best[height<=720]", "480": "bestvideo[height<=480]+bestaudio/best[height<=480]", "360": "bestvideo[height<=360]+bestaudio/best[height<=360]", "audio": "bestaudio/best"}

def validate_url(raw):
    try:
        p = urlparse(raw.strip())
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return False
        host = p.hostname.lower().rstrip(".")
        if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal")):
            return False
        # Reject literal private/loopback/link-local/reserved IP addresses.
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return False
        except ValueError:
            pass
        # Resolve host and reject private destinations to reduce SSRF risk.
        try:
            for info in socket.getaddrinfo(host, None):
                ip = ipaddress.ip_address(info[4][0])
                if not ip.is_global:
                    return False
        except socket.gaierror:
            return False
        return True
    except Exception:
        return False

def progress_hook(job_id):
    def hook(data):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return
            if data.get("status") == "downloading":
                job["status"] = "downloading"
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                got = data.get("downloaded_bytes", 0)
                job["progress"] = min(99, int(got * 100 / total)) if total else 0
                job["speed"] = data.get("_speed_str", "")
                job["eta"] = data.get("_eta_str", "")
            elif data.get("status") == "finished":
                job["status"] = "processing"
                job["progress"] = 99
    return hook

def run_job(job_id, url, quality):
    with JOBS_LOCK:
        JOBS[job_id]["status"] = "starting"
    template = os.path.join(DOWNLOAD_DIR, job_id + ".%(ext)s")
    opts = {
        "format": ALLOWED_FORMATS[quality],
        "outtmpl": template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "max_filesize": 1024 * 1024 * 1024,
        "progress_hooks": [progress_hook(job_id)],
        "merge_output_format": "mp4",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}] if quality == "audio" else [],
        "extractor_args": {"youtube": {"player_client": ["default"]}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Prefer final output path reported by yt-dlp, with safe job-specific fallback.
            ext = "mp3" if quality == "audio" else (info.get("ext") or "mp4")
            candidates = [os.path.join(DOWNLOAD_DIR, job_id + "." + ext)]
            if quality != "audio":
                candidates += [os.path.join(DOWNLOAD_DIR, job_id + ".mp4"), os.path.join(DOWNLOAD_DIR, job_id + "." + (info.get("ext") or "mp4"))]
            final_path = next((x for x in candidates if os.path.isfile(x)), None)
            if not final_path:
                matches = [os.path.join(DOWNLOAD_DIR, n) for n in os.listdir(DOWNLOAD_DIR) if n.startswith(job_id + ".") and os.path.isfile(os.path.join(DOWNLOAD_DIR, n))]
                final_path = matches[0] if matches else None
            if not final_path:
                raise RuntimeError("The source did not produce a downloadable file.")
            with JOBS_LOCK:
                JOBS[job_id].update(status="complete", progress=100, title=(info.get("title") or "Your video")[:180],
                                    filename=os.path.basename(final_path), size=os.path.getsize(final_path))
    except Exception as e:
        message = str(e).strip().splitlines()[0][:260] or "Download failed."
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id].update(status="error", error=message)
    finally:
        with JOBS_LOCK:
            active = JOBS.get(job_id)
            if active:
                active["updated"] = time.time()

def cleanup():
    now = time.time()
    with JOBS_LOCK:
        expired = [jid for jid, job in JOBS.items() if now - job.get("updated", now) > MAX_FILE_AGE]
        for jid in expired:
            job = JOBS.pop(jid, {})
            fn = job.get("filename")
            if fn:
                try: os.remove(os.path.join(DOWNLOAD_DIR, fn))
                except OSError: pass

@app.after_request
def headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # GitHub Pages and the API are on different origins, so explicitly allow
    # only the configured Pages origin(s). Set FRONTEND_ORIGIN in Render.
    allowed_origins = [x.strip().rstrip("/") for x in os.environ.get("FRONTEND_ORIGIN", "").split(",") if x.strip()]
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "600"
    return response

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/api/download")
def create_download():
    cleanup()
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    quality = str(data.get("quality", "best"))
    if len(url) > 4096 or not validate_url(url):
        return jsonify(error="Enter a valid public HTTP or HTTPS video URL."), 400
    if quality not in ALLOWED_FORMATS:
        return jsonify(error="Choose one of the listed quality options."), 400
    with JOBS_LOCK:
        active = sum(1 for j in JOBS.values() if j.get("status") in ("queued", "starting", "downloading", "processing"))
        if active >= MAX_ACTIVE:
            return jsonify(error="The downloader is busy. Try again in a minute."), 429
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"id": job_id, "status": "queued", "progress": 0, "created": time.time(), "updated": time.time()}
    threading.Thread(target=run_job, args=(job_id, url, quality), daemon=True).start()
    return jsonify(id=job_id), 202

@app.get("/api/status/<job_id>")
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="Download job not found or expired."), 404
        safe = {k: job[k] for k in ("id", "status", "progress", "speed", "eta", "title", "size", "error") if k in job}
        if job.get("status") == "complete":
            safe["download_url"] = "/api/file/" + job_id
        return jsonify(safe)

@app.get("/api/file/<job_id>")
def get_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("status") != "complete" or not job.get("filename"):
            abort(404)
        path = os.path.join(DOWNLOAD_DIR, job["filename"])
        title = re.sub(r"[^A-Za-z0-9._-]+", "_", job.get("title", "clip"))[:80].strip("._") or "clip"
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=title + os.path.splitext(path)[1], conditional=True)

@app.get("/health")
def health():
    return jsonify(status="ok")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
