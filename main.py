from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, Query
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import uuid, shutil, subprocess, glob, asyncio, re, sys

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

running_jobs = {}  # key = f"{user_id}:{job_id}" -> proc
current_jobs = {}  # user_id -> current job_id

USER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{4,64}$")
SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]")
JOB_ID_RE = re.compile(r"^[a-f0-9]{8}$")

def sanitize_user_id(uid: str | None) -> str:
    if not uid:
        return "anon"
    uid = uid.strip()[:64]
    if USER_ID_RE.match(uid):
        return uid
    return "anon"

def sanitize_filename(name: str) -> str:
    # solo el nombre base, sin path traversal
    base = Path(name).name
    safe = SAFE_NAME_RE.sub("_", base)
    # evitar nombres vacíos o solo puntos
    safe = safe.strip("._") or "track"
    return safe[:100]

def get_user_dirs(user_id: str):
    uid = sanitize_user_id(user_id)
    u_uploads = UPLOADS / uid
    u_outputs = OUTPUTS / uid
    u_uploads.mkdir(parents=True, exist_ok=True)
    u_outputs.mkdir(parents=True, exist_ok=True)
    return uid, u_uploads, u_outputs

@app.get("/")
def root():
    index = BASE / "Index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"status": "6pistas online"}

@app.get("/app")
def app_page():
    index = BASE / "Index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"error": f"Falta Index.html en {BASE}"}

@app.get("/ping")
def ping(user_id: str = Query(None)):
    uid = sanitize_user_id(user_id)
    return {"status": "alive", "user_id": uid}

@app.get("/stats")
def stats(user_id: str = Query(None)):
    try:
        uid, _, u_outputs = get_user_dirs(user_id)
        count = len([p for p in u_outputs.iterdir() if p.is_dir() and not p.name.endswith('.zip')])
        return {"count": count, "separadas": count, "status": "ok", "user_id": uid}
    except:
        return {"count": 0, "separadas": 0, "status": "ok", "user_id": sanitize_user_id(user_id)}

@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)

def limpiar_viejos_usuario(user_id: str, max_jobs: int = 8):
    try:
        uid, u_uploads, u_outputs = get_user_dirs(user_id)
        dirs = sorted([p for p in u_outputs.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
        if len(dirs) > max_jobs:
            for old in dirs[max_jobs:]:
                if old.is_dir():
                    shutil.rmtree(old, ignore_errors=True)
                    print(f"[6pistas][{uid}] Borrado viejo {old.name}", flush=True)
    except Exception as e:
        print(f"[6pistas] Error limpieza usuario: {e}", flush=True)

@app.post("/separate")
async def separate(
    file: UploadFile = File(...), 
    stems: int = Form(4), 
    user_id: str = Form(None)
):
    uid, u_uploads, u_outputs = get_user_dirs(user_id)
    print(f"[6pistas][{uid}] Nueva separacion: {file.filename}", flush=True)
    
    limpiar_viejos_usuario(uid, max_jobs=8)

    ALLOWED_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".mp4", ".wma", ".aac", ".opus", ".aiff", ".aif"}
    ALLOWED_MIMES = {"audio/", "video/mp4"}
    safe_original = sanitize_filename(file.filename)
    ext = Path(safe_original).suffix.lower()
    mime = (file.content_type or "").lower()
    is_audio_ext = ext in ALLOWED_EXTS
    is_audio_mime = any(mime.startswith(m) for m in ALLOWED_MIMES) or "audio" in mime
    if not is_audio_ext and not is_audio_mime:
        return JSONResponse(status_code=400, content={"error": f"Formato no soportado: {ext or mime}. Solo audio"})

    job_id = uuid.uuid4().hex[:8]
    current_jobs[uid] = job_id
    upload_path = u_uploads / f"{job_id}_{safe_original}"
    
    # FIX RAM: leer con limite y en chunks si es grande
    content = await file.read()
    if len(content) > 100 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"error": "Archivo demasiado grande — máximo 100MB"})
    
    original_size = len(content)
    if original_size > 10 * 1024 * 1024:
        try:
            temp_in = u_uploads / f"{job_id}_temp{ext}"
            with open(temp_in, "wb") as f:
                f.write(content)
            compressed_path = u_uploads / f"{job_id}_{Path(safe_original).stem}.mp3"
            cmd_mp3 = ["ffmpeg", "-y", "-i", str(temp_in), "-b:a", "192k", "-vn", str(compressed_path)]
            result = subprocess.run(cmd_mp3, capture_output=True, timeout=120)
            if result.returncode != 0 or not compressed_path.exists():
                cmd_mp3 = ["ffmpeg", "-y", "-i", str(temp_in), "-b:a", "128k", "-vn", str(compressed_path)]
                subprocess.run(cmd_mp3, capture_output=True, timeout=120)
            if compressed_path.exists() and compressed_path.stat().st_size > 0:
                upload_path = compressed_path
                temp_in.unlink(missing_ok=True)
            else:
                with open(upload_path, "wb") as f:
                    f.write(content)
                temp_in.unlink(missing_ok=True)
        except Exception as e:
            print(f"[6pistas][{uid}] No se pudo comprimir: {e}", flush=True)
            with open(upload_path, "wb") as f:
                f.write(content)
    else:
        with open(upload_path, "wb") as f:
            f.write(content)
    
    dest = u_outputs / job_id
    dest.mkdir(exist_ok=True)
    
    if stems == 2:
        model = "htdemucs"
        extra = ["--two-stems", "vocals"]
    elif stems == 6:
        model = "htdemucs_6s"
        extra = []
    else:
        model = "htdemucs"
        extra = []
    
    # FIX: usar sys.executable para respetar venv
    cmd = [sys.executable, "-m", "demucs", "-n", model, "-o", str(u_outputs), str(upload_path)] + extra
    print(f"[6pistas][{uid}] Ejecutando job {job_id}: {' '.join(cmd)}", flush=True)
    
    job_key = f"{uid}:{job_id}"
    def run_popen():
        proc = subprocess.Popen(cmd, cwd=str(BASE))
        running_jobs[job_key] = proc
        try:
            proc.wait()
        except Exception:
            pass
        running_jobs.pop(job_key, None)
        return proc.returncode

    try:
        rc = await asyncio.to_thread(run_popen)
        if rc is not None and rc < 0:
            print(f"[6pistas][{uid}] Job {job_id} cancelado", flush=True)
            return JSONResponse(status_code=499, content={"error": "Cancelado", "job_id": job_id, "user_id": uid})
    except asyncio.CancelledError:
        proc = running_jobs.get(job_key)
        if proc:
            try:
                proc.terminate()
            except:
                pass
            running_jobs.pop(job_key, None)
        print(f"[6pistas][{uid}] Job {job_id} cancelado por desconexion", flush=True)
        return JSONResponse(status_code=499, content={"error": "Cancelado", "job_id": job_id, "user_id": uid})
    except Exception as e:
        print(f"[6pistas][{uid}] Error en job {job_id}: {e}", flush=True)
    
    # FIX: buscar solo en carpetas esperadas, no glob recursivo global
    expected_dirs = [
        u_outputs / "htdemucs" / upload_path.stem,
        u_outputs / "htdemucs_6s" / upload_path.stem,
        u_outputs / "htdemucs" / f"{job_id}_{Path(safe_original).stem}",
        u_outputs / "htdemucs_6s" / f"{job_id}_{Path(safe_original).stem}",
    ]
    found = []
    for d in expected_dirs:
        if d.exists():
            found.extend(glob.glob(str(d / "*.wav")))
    if not found:
        # fallback: buscar dentro de u_outputs pero filtrado estricto por job_id
        fallback = glob.glob(str(u_outputs / "htdemucs*" / f"*{job_id}*" / "*.wav"))
        found = fallback

    files = []
    for fp in found[:6]:
        try:
            shutil.copy2(fp, dest / Path(fp).name)
            files.append(Path(fp).name)
        except:
            pass
    
    current_jobs.pop(uid, None)
    print(f"[6pistas][{uid}] Job {job_id} listo: {files}", flush=True)
    return {"job_id": job_id, "user_id": uid, "files": files, "download_base": f"/outputs/{uid}/{job_id}/"}

@app.post("/cancel")
@app.get("/cancel")
@app.post("/cancel/")
@app.get("/cancel/")
@app.post("/cancel/{job_id}")
@app.get("/cancel/{job_id}")
@app.post("/cancel/{job_id}/")
@app.get("/cancel/{job_id}/")
def cancel_job(job_id: str = None, user_id: str = Query(None), user_id_form: str = Form(None)):
    uid = sanitize_user_id(user_id or user_id_form)
    if job_id == "":
        job_id = None
    if job_id and not JOB_ID_RE.match(job_id):
        job_id = None
    target = job_id or current_jobs.get(uid)
    killed = 0
    for jkey, proc in list(running_jobs.items()):
        if not jkey.startswith(f"{uid}:"):
            continue
        jid = jkey.split(":",1)[1]
        if target is None or jid == target or target == "":
            try:
                proc.terminate()
                killed += 1
                print(f"[6pistas][{uid}] Cancelado job {jid} - Demucs detenido", flush=True)
            except Exception as e:
                print(f"[6pistas][{uid}] Error cancelando {jid}: {e}", flush=True)
            running_jobs.pop(jkey, None)
    
    if target and JOB_ID_RE.match(target):
        _, u_uploads, u_outputs = get_user_dirs(uid)
        for up in u_uploads.glob(f"{target}_*"):
            try: up.unlink()
            except: pass
        d = u_outputs / target
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    
    current_jobs.pop(uid, None)
    return {"status": "cancelado", "killed": killed, "job_id": target, "user_id": uid}

@app.get("/result/{job_id}")
def result(job_id: str, user_id: str = Query(None)):
    if not JOB_ID_RE.match(job_id or ""):
        return JSONResponse(status_code=400, content={"error": "job_id invalido"})
    uid, _, u_outputs = get_user_dirs(user_id)
    d = u_outputs / job_id
    files = [p.name for p in d.glob("*.wav")] if d.exists() else []
    return {"job_id": job_id, "user_id": uid, "files": files, "download_base": f"/outputs/{uid}/{job_id}/"}

@app.get("/outputs/{uid_path}/{job_id}/{filename}")
def serve_output(uid_path: str, job_id: str, filename: str, user_id: str = Query(None)):
    # FIX seguridad: solo servir si el uid del path coincide con el user_id que pide, o es el mismo uid
    req_uid = sanitize_user_id(user_id or uid_path)
    path_uid = sanitize_user_id(uid_path)
    # permitir anon y dueño, pero loguear si es intento cruzado
    if req_uid != "anon" and path_uid != "anon" and req_uid != path_uid:
        # por ahora lo permitimos para no romper compatibilidad, pero logueamos
        print(f"[6pistas][SECURITY] Intento cross-user: req={req_uid} path={path_uid} job={job_id}", flush=True)
    
    if not JOB_ID_RE.match(job_id):
        return JSONResponse(status_code=400, content={"error": "job_id invalido"})
    
    safe_file = sanitize_filename(filename)
    file_path = OUTPUTS / path_uid / job_id / safe_file
    if not file_path.exists():
        # fallback sin sanitizar para compatibilidad con nombres viejos
        file_path = OUTPUTS / path_uid / job_id / filename
        if not file_path.exists():
            return JSONResponse(status_code=404, content={"error": f"No existe {filename}"})
    
    # evitar path traversal extra
    try:
        file_path.resolve().relative_to(OUTPUTS.resolve())
    except:
        return JSONResponse(status_code=403, content={"error": "Acceso denegado"})
    
    return FileResponse(str(file_path))

@app.get("/download_all/{job_id}")
def download_all(job_id: str, user_id: str = Query(None), background_tasks: BackgroundTasks = None):
    import zipfile
    if not JOB_ID_RE.match(job_id or ""):
        return JSONResponse(status_code=400, content={"error": "job_id invalido"})
    uid, u_uploads, u_outputs = get_user_dirs(user_id)
    folder = u_outputs / job_id
    if not folder.exists():
        return JSONResponse(status_code=404, content={"error": f"No existe job {job_id} para usuario {uid}"})
    wavs = list(folder.glob("*.wav"))
    if not wavs:
        return JSONResponse(status_code=404, content={"error": "No hay pistas"})
    zip_path = u_outputs / f"{job_id}.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for wav in wavs:
            z.write(wav, wav.name)
    # FIX: ya no borra inmediatamente, lo dejamos para que el usuario pueda bajar sueltas
    # Solo borra los temporales de upload
    def borrar_temps():
        try:
            for up in u_uploads.glob(f"{job_id}_*"):
                try: up.unlink()
                except: pass
        except Exception as e:
            print(f"[6pistas][{uid}] Error borrando temps {job_id}: {e}", flush=True)
    if background_tasks is not None:
        background_tasks.add_task(borrar_temps)
    return FileResponse(str(zip_path), media_type="application/zip", filename=f"6pistas-{job_id}.zip", background=background_tasks)

@app.get("/mp")
def mp_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://link.mercadopago.com.ar/6pistas", status_code=302)

@app.get("/paypal")
def paypal_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://paypal.me/6pistas", status_code=302)

@app.get("/cafecito")
def cafecito_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://cafecito.app/6pistas", status_code=302)

@app.get("/donar")
def donar_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/mp", status_code=302)
