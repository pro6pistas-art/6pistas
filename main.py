
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import uuid, shutil, subprocess, glob, asyncio

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

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
    return {"error": "Falta Index.html en C:\\separa"}

@app.get("/stats")
def stats():
    try:
        count = len(list(OUTPUTS.glob("*")))
        return {"count": count, "separadas": count, "status": "ok"}
    except:
        return {"count": 0, "separadas": 0, "status": "ok"}

@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)

def borrar_pistas_anteriores():
    """Borra las pistas anteriores cuando ponés una nueva a separar, sin arruinar nada"""
    try:
        # Borrar todo lo de outputs excepto lo que se está creando ahora
        # Y borrar uploads viejos
        for item in OUTPUTS.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
        for item in UPLOADS.iterdir():
            if item.is_file():
                try:
                    item.unlink()
                except:
                    pass
        # También borrar las carpetas temporales de Demucs
        for d in [BASE / "outputs" / "htdemucs", BASE / "outputs" / "htdemucs_6s"]:
            if d.exists():
                # No borrar todo htdemucs, solo los contenidos viejos, dejar que Demucs cree nuevo
                pass
        print(f"[6pistas] Pistas anteriores borradas para hacer lugar a la nueva", flush=True)
    except Exception as e:
        print(f"[6pistas] Error borrando anteriores: {e}", flush=True)

@app.post("/separate")
async def separate(file: UploadFile = File(...), stems: int = 4, quality: str = "baja"):
    # Borrar pistas anteriores cuando ponés una nueva a descargar
    borrar_pistas_anteriores()
    
    job_id = uuid.uuid4().hex[:8]
    upload_path = UPLOADS / f"{job_id}_{file.filename}"
    with open(upload_path, "wb") as f:
        f.write(await file.read())
    
    dest = OUTPUTS / job_id
    dest.mkdir(exist_ok=True)
    
    # Fix 2 pistas: usar --two-stems vocals
    if stems == 2:
        model = "htdemucs"
        extra = ["--two-stems", "vocals"]
    elif stems == 6:
        model = "htdemucs_6s"
        extra = []
    else:
        model = "htdemucs"
        extra = []
    
    cmd = ["python", "-m", "demucs", "-n", model, "-o", str(OUTPUTS), str(upload_path)] + extra
    print(f"[6pistas] Ejecutando: {' '.join(cmd)}", flush=True)
    
    def run_demucs():
        subprocess.run(cmd, cwd=str(BASE))
    
    await asyncio.to_thread(run_demucs)
    
    found = glob.glob(str(OUTPUTS / "htdemucs_6s" / f"{upload_path.stem}" / "*.wav")) or glob.glob(str(OUTPUTS / "htdemucs" / f"{upload_path.stem}" / "*.wav")) or glob.glob(str(OUTPUTS / "**" / "*.wav"), recursive=True)
    files = []
    for fp in found[:6]:
        try:
            shutil.copy2(fp, dest / Path(fp).name)
            files.append(Path(fp).name)
        except:
            pass
    
    print(f"[6pistas] Archivos encontrados: {files}", flush=True)
    print(f"[6pistas] Pistas anteriores borradas, solo queda la nueva: {job_id}", flush=True)
    return {"job_id": job_id, "files": files, "download_base": f"/outputs/{job_id}/"}

@app.get("/result/{job_id}")
def result(job_id: str):
    d = OUTPUTS / job_id
    files = [p.name for p in d.glob("*.wav")] if d.exists() else []
    return {"job_id": job_id, "files": files, "download_base": f"/outputs/{job_id}/"}


@app.get("/download_all/{job_id}")
def download_all(job_id: str, background_tasks: BackgroundTasks):
    """Descarga todas y borra de mi disco cuando termina"""
    import zipfile, io, os
    from fastapi.responses import StreamingResponse
    from fastapi import BackgroundTasks
    import shutil
    
    folder = OUTPUTS / job_id
    if not folder.exists():
        return {"error": f"No existe job {job_id}"}
    
    wavs = list(folder.glob("*.wav"))
    if not wavs:
        return {"error": "No hay pistas"}
    
    # Crear zip en disco temporal para no usar RAM
    zip_path = OUTPUTS / f"{job_id}.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for wav in wavs:
            z.write(wav, wav.name)
    
    def borrar_despues():
        try:
            # Borrar carpeta de pistas y zip temporal y upload original
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
            if zip_path.exists():
                zip_path.unlink(missing_ok=True)
            # Borrar uploads de ese job
            for up in UPLOADS.glob(f"{job_id}_*"):
                try:
                    up.unlink()
                except:
                    pass
            print(f"[6pistas] Borrado de mi disco después de descargar: {job_id}", flush=True)
        except Exception as e:
            print(f"[6pistas] Error borrando {job_id}: {e}", flush=True)
    
    background_tasks.add_task(borrar_despues)
    
    return FileResponse(str(zip_path), media_type="application/zip", filename=f"6pistas-{job_id}.zip", background=background_tasks)


@app.get("/mp")
def mp_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://link.mercadopago.com.ar/6pistas", status_code=302)


@app.get("/paypal")
def paypal_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://paypal.me/6pistas", status_code=302)

@app.get("/pp")
def pp_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://paypal.me/6pistas", status_code=302)


@app.get("/cafecito")
def cafecito_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://cafecito.app/6pistas", status_code=302)

@app.get("/cafe")
def cafe_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://cafecito.app/6pistas", status_code=302)

app.mount("/outputs", StaticFiles(directory=str(OUTPUTS)), name="outputs")
