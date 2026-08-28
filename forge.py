#!/usr/bin/env python3
"""
BELENTANI VIDEO FORGE
Pipeline local: tema -> guion (IA) -> voz (gratis) -> subtitulos -> clips stock -> video final.
Creado desde cero. Sin dependencias pesadas: edge-tts + requests + ffmpeg.
"""
import argparse
import asyncio
import json
import random
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
WORK = ROOT / ".work"


# ---------------------------------------------------------------- utilidades

def cargar_config():
    ruta = ROOT / "config.json"
    if not ruta.exists():
        print("ERROR: falta config.json")
        sys.exit(1)
    return json.loads(ruta.read_text(encoding="utf-8"))


def slug(texto, maxlen=40):
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:maxlen] or "video"


def run_ffmpeg(args, cwd=None):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = "\n".join(r.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"ffmpeg fallo:\n{tail}")


def duracion_audio(ruta):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(ruta)],
        capture_output=True, text=True)
    return float(r.stdout.strip())


# ---------------------------------------------------------------- guion (LLM)

PROMPT_GUION = (
    "Escribe un guion para narrar un video corto de {dur} segundos sobre: {tema}.\n"
    "Idioma: {idioma}.\n"
    "Reglas: gancho fuerte en la primera frase, datos concretos, frases cortas, "
    "sin emojis, sin encabezados, sin comillas, sin acotaciones, texto plano continuo "
    "listo para narrar, aproximadamente {palabras} palabras. "
    "Termina invitando a seguir la cuenta.\n"
    "Responde SOLO con el texto del guion."
)

PROMPT_KEYWORDS = (
    "Dame exactamente 5 terminos de busqueda EN INGLES, genericos y visuales, "
    "para encontrar videos de stock que ilustren este tema: {tema}. "
    "Responde SOLO la lista separada por comas, sin numeracion."
)


def llm_deepseek(cfg, prompt):
    g = cfg["guion"]
    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {g['deepseek_api_key']}"},
        json={"model": g.get("deepseek_model", "deepseek-chat"),
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.8},
        timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def llm_ollama(cfg, prompt):
    g = cfg["guion"]
    url = g.get("ollama_url", "http://localhost:11434")
    r = requests.post(f"{url}/api/generate",
                      json={"model": g.get("ollama_model", "llama3.2"),
                            "prompt": prompt, "stream": False},
                      timeout=300)
    r.raise_for_status()
    return r.json()["response"].strip()


def ollama_disponible(cfg):
    try:
        url = cfg["guion"].get("ollama_url", "http://localhost:11434")
        requests.get(f"{url}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def generar_texto(cfg, prompt):
    prov = cfg["guion"].get("proveedor", "auto")
    if prov in ("auto", "deepseek") and cfg["guion"].get("deepseek_api_key"):
        return llm_deepseek(cfg, prompt), "deepseek"
    if prov in ("auto", "ollama") and ollama_disponible(cfg):
        return llm_ollama(cfg, prompt), "ollama"
    return None, None


def limpiar_guion(texto):
    texto = re.sub(r"[*#>`\"“”]", "", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def obtener_guion(cfg, tema, dur, ruta_guion=None):
    if ruta_guion:
        texto = Path(ruta_guion).read_text(encoding="utf-8")
        return limpiar_guion(texto), "archivo"
    palabras = int(dur * 2.6)
    prompt = PROMPT_GUION.format(dur=dur, tema=tema,
                                 idioma=cfg.get("idioma", "es"),
                                 palabras=palabras)
    texto, fuente = generar_texto(cfg, prompt)
    if not texto:
        print("ERROR: sin generador de guion disponible.")
        print("Opciones: 1) pon deepseek_api_key en config.json")
        print("          2) arranca Ollama (ollama serve + ollama pull llama3.2)")
        print("          3) usa --guion mi_guion.txt con tu propio texto")
        sys.exit(1)
    return limpiar_guion(texto), fuente


def obtener_keywords(cfg, tema):
    texto, _ = generar_texto(cfg, PROMPT_KEYWORDS.format(tema=tema))
    if texto:
        kws = [k.strip() for k in texto.replace("\n", ",").split(",") if k.strip()]
        kws = [re.sub(r"^\d+[.)]\s*", "", k) for k in kws]
        if kws:
            return kws[:5]
    return [p for p in re.split(r"\W+", tema) if len(p) > 3][:5] or [tema]


# ---------------------------------------------------------------- voz + subs

async def _tts_edge(texto, voz, rate, out_mp3):
    import edge_tts
    com = edge_tts.Communicate(texto, voz, rate=rate)
    sub = edge_tts.SubMaker()
    with open(out_mp3, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                try:
                    sub.feed(chunk)
                except Exception:
                    pass
    try:
        return sub.get_srt()
    except Exception:
        return None


def tts_sapi(texto, out_wav, idioma):
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$v = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Culture.Name -like '{idioma}*' }} | Select-Object -First 1; "
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; "
        f"$s.SetOutputToWaveFile('{out_wav}'); "
        "$s.Speak([IO.File]::ReadAllText('__GUION__', [Text.Encoding]::UTF8)); "
        "$s.Dispose()"
    )
    guion_tmp = Path(out_wav).parent / "guion_tts.txt"
    guion_tmp.write_text(texto, encoding="utf-8")
    ps = ps.replace("__GUION__", str(guion_tmp))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   capture_output=True)


def srt_estimado(texto, dur_total):
    """Sin timestamps reales: reparte frases proporcional a su longitud."""
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+", texto) if f.strip()]
    total_chars = sum(len(f) for f in frases) or 1
    cues, t = [], 0.0
    for f in frases:
        d = dur_total * len(f) / total_chars
        cues.append((t, t + d, f))
        t += d
    return cues


def parse_srt(srt):
    cues = []
    for bloque in re.split(r"\n\s*\n", srt.strip()):
        lineas = bloque.strip().splitlines()
        if len(lineas) < 3:
            continue
        m = re.match(r"(\S+)\s*-->\s*(\S+)", lineas[1])
        if not m:
            continue
        cues.append((srt_a_seg(m.group(1)), srt_a_seg(m.group(2)),
                     " ".join(lineas[2:]).strip()))
    return cues


def srt_a_seg(t):
    h, m, resto = t.split(":")
    s, ms = resto.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def seg_a_srt(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def agrupar_cues(cues, por_linea):
    """Une cues palabra-a-palabra en grupos legibles."""
    grupos = []
    for i in range(0, len(cues), por_linea):
        lote = cues[i:i + por_linea]
        grupos.append((lote[0][0], lote[-1][1],
                       " ".join(c[2] for c in lote)))
    return grupos


def escribir_srt(cues, ruta):
    lineas = []
    for i, (a, b, txt) in enumerate(cues, 1):
        lineas.append(f"{i}\n{seg_a_srt(a)} --> {seg_a_srt(b)}\n{txt}\n")
    ruta.write_text("\n".join(lineas), encoding="utf-8")


def generar_voz(cfg, texto, workdir):
    voz = cfg.get("voz", "es-MX-JorgeNeural")
    rate = cfg.get("velocidad_voz", "+8%")
    mp3 = workdir / "voz.mp3"
    srt_raw = None
    try:
        srt_raw = asyncio.run(_tts_edge(texto, voz, rate, mp3))
        motor = f"edge-tts ({voz})"
    except Exception as e:
        print(f"  edge-tts fallo ({e}); uso voz Windows local")
        wav = workdir / "voz.wav"
        tts_sapi(texto, wav, cfg.get("idioma", "es"))
        run_ffmpeg(["-i", str(wav), str(mp3)])
        motor = "SAPI Windows"
    dur = duracion_audio(mp3)
    scfg = cfg.get("subtitulos", {})
    por_linea = int(scfg.get("palabras_por_linea", 3))
    if srt_raw:
        cues = parse_srt(srt_raw)
        media = sum(len(c[2].split()) for c in cues) / max(len(cues), 1)
        if media <= 1.5:
            cues = agrupar_cues(cues, por_linea)
    else:
        cues = srt_estimado(texto, dur)
    escribir_srt(cues, workdir / "subs.srt")
    return mp3, dur, motor


# ---------------------------------------------------------------- clips stock

def buscar_pexels(key, query, vertical, n):
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": n,
                    "orientation": "portrait" if vertical else "landscape"},
            timeout=30)
        r.raise_for_status()
        urls = []
        for v in r.json().get("videos", []):
            archivos = [f for f in v.get("video_files", [])
                        if f.get("file_type") == "video/mp4"
                        and (f.get("height") or 0) >= 720]
            if archivos:
                mejor = min(archivos, key=lambda f: abs((f.get("height") or 0) - 1440))
                urls.append((f"px{v['id']}", mejor["link"]))
        return urls
    except Exception as e:
        print(f"  pexels error '{query}': {e}")
        return []


def buscar_pixabay(key, query, vertical, n):
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": key, "q": query, "per_page": max(n, 3)},
            timeout=30)
        r.raise_for_status()
        urls = []
        for v in r.json().get("hits", []):
            vids = v.get("videos", {})
            f = vids.get("large") or vids.get("medium") or vids.get("small")
            if f and f.get("url"):
                urls.append((f"pb{v['id']}", f["url"]))
        return urls
    except Exception as e:
        print(f"  pixabay error '{query}': {e}")
        return []


def descargar(url, destino, max_mb=120):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        escrito = 0
        with open(destino, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                escrito += len(chunk)
                if escrito > max_mb * 1024 * 1024:
                    break


def conseguir_clips(cfg, keywords, vertical, workdir):
    ccfg = cfg.get("clips", {})
    n_obj = int(ccfg.get("num_clips", 6))
    candidatos, vistos = [], set()
    for kw in keywords:
        if ccfg.get("pexels_api_key"):
            candidatos += buscar_pexels(ccfg["pexels_api_key"], kw, vertical, 4)
        if ccfg.get("pixabay_api_key"):
            candidatos += buscar_pixabay(ccfg["pixabay_api_key"], kw, vertical, 4)
    unicos = []
    for cid, url in candidatos:
        if cid not in vistos:
            vistos.add(cid)
            unicos.append((cid, url))
    random.shuffle(unicos)
    rutas = []
    for cid, url in unicos:
        if len(rutas) >= n_obj:
            break
        destino = workdir / f"clip_{len(rutas)}.mp4"
        try:
            print(f"  bajando clip {len(rutas)+1}/{n_obj} ({cid})")
            descargar(url, destino)
            rutas.append(destino)
        except Exception as e:
            print(f"  descarga fallo: {e}")
    return rutas


PALETAS = [
    ("0x0f0c29", "0x302b63"), ("0x1a2a6c", "0xb21f1f"),
    ("0x000428", "0x004e92"), ("0x2c003e", "0x8e2de2"),
    ("0x0f2027", "0x2c5364"), ("0x232526", "0x414345"),
]


def fondos_gradiente(n, seg, vertical, workdir):
    """Sin API keys: fondos degradados animados generados por ffmpeg."""
    w, h = (1080, 1920) if vertical else (1920, 1080)
    rutas = []
    for i in range(n):
        c0, c1 = PALETAS[i % len(PALETAS)]
        destino = workdir / f"clip_{i}.mp4"
        run_ffmpeg([
            "-f", "lavfi",
            "-i", f"gradients=s={w}x{h}:c0={c0}:c1={c1}:speed=0.03:duration={seg:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", str(destino)])
        rutas.append(destino)
    return rutas


# ---------------------------------------------------------------- montaje

def normalizar_clip(origen, destino, seg, vertical):
    w, h = (1080, 1920) if vertical else (1920, 1080)
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
          f"crop={w}:{h},fps=30,setsar=1")
    run_ffmpeg(["-stream_loop", "-1", "-i", str(origen), "-t", f"{seg:.2f}",
                "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "21", "-pix_fmt", "yuv420p", str(destino)])


def elegir_musica(cfg):
    carpeta = ROOT / cfg.get("musica", {}).get("carpeta", "music")
    pistas = [p for p in carpeta.glob("*") if p.suffix.lower() in
              (".mp3", ".wav", ".m4a", ".flac", ".ogg")]
    return random.choice(pistas) if pistas else None


def montar(cfg, clips, voz_mp3, dur, vertical, workdir, salida, sin_musica):
    seg = (dur + 0.8) / len(clips)
    normalizados = []
    for i, c in enumerate(clips):
        destino = workdir / f"norm_{i}.mp4"
        normalizar_clip(c, destino, seg, vertical)
        normalizados.append(destino)
    lista = workdir / "lista.txt"
    lista.write_text("\n".join(f"file '{p.name}'" for p in normalizados),
                     encoding="utf-8")
    base = workdir / "base.mp4"
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", "lista.txt",
                "-c", "copy", str(base)], cwd=workdir)

    scfg = cfg.get("subtitulos", {})
    estilo = (f"FontName={scfg.get('fuente', 'Arial')},"
              f"FontSize={scfg.get('tamano', 16)},"
              f"PrimaryColour={scfg.get('color_primario', '&H00FFFFFF')},"
              f"OutlineColour={scfg.get('color_borde', '&H00000000')},"
              f"BorderStyle=1,Outline=2,Shadow=1,Bold=1,Alignment=2,"
              f"MarginV={scfg.get('margen_v', 80)}")
    vf = f"subtitles=subs.srt:force_style='{estilo}'"

    musica = None if sin_musica else elegir_musica(cfg)
    total = dur + 0.8
    if musica:
        vol = cfg.get("musica", {}).get("volumen", 0.12)
        run_ffmpeg(["-i", "base.mp4", "-i", str(voz_mp3),
                    "-stream_loop", "-1", "-i", str(musica),
                    "-filter_complex",
                    f"[2:a]volume={vol}[m];"
                    f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=3[a]",
                    "-map", "0:v", "-map", "[a]", "-vf", vf,
                    "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium",
                    "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "192k", str(salida)], cwd=workdir)
    else:
        run_ffmpeg(["-i", "base.mp4", "-i", str(voz_mp3),
                    "-map", "0:v", "-map", "1:a", "-vf", vf,
                    "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium",
                    "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "192k", str(salida)], cwd=workdir)
    return musica


# ---------------------------------------------------------------- pipeline

def producir(cfg, tema, args):
    t0 = time.time()
    vertical = (args.formato or cfg.get("formato", "vertical")) == "vertical"
    dur_obj = args.duracion or int(cfg.get("duracion_objetivo_seg", 45))

    import shutil
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)

    print(f"\n=== {tema} ===")
    print("[1/5] guion")
    guion, fuente = obtener_guion(cfg, tema, dur_obj, args.guion)
    print(f"  fuente: {fuente} | {len(guion.split())} palabras")
    (WORK / "guion.txt").write_text(guion, encoding="utf-8")

    print("[2/5] voz + subtitulos")
    voz_mp3, dur, motor = generar_voz(cfg, guion, WORK)
    print(f"  {motor} | {dur:.1f}s")

    print("[3/5] clips")
    keywords = obtener_keywords(cfg, tema)
    print(f"  busqueda: {', '.join(keywords)}")
    n_clips = int(cfg.get("clips", {}).get("num_clips", 6))
    clips = conseguir_clips(cfg, keywords, vertical, WORK)
    if not clips:
        print("  sin clips stock (faltan API keys o sin resultados); "
              "uso fondos degradados")
        clips = fondos_gradiente(n_clips, (dur + 0.8) / n_clips, vertical, WORK)

    print("[4/5] montaje")
    nombre = f"{slug(tema)}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    salida = OUTPUT / nombre
    musica = montar(cfg, clips, voz_mp3, dur, vertical, WORK, salida,
                    args.sin_musica)
    print(f"  musica: {musica.name if musica else 'ninguna (pon pistas en music/)'}")

    print("[5/5] listo")
    mb = salida.stat().st_size / 1024 / 1024
    print(f"  {salida}  ({mb:.1f} MB, {time.time()-t0:.0f}s)")
    return salida


def main():
    ap = argparse.ArgumentParser(description="Belentani Video Forge")
    ap.add_argument("--tema", help="tema del video")
    ap.add_argument("--lote", help="archivo con un tema por linea")
    ap.add_argument("--guion", help="usa tu propio guion (archivo .txt)")
    ap.add_argument("--formato", choices=["vertical", "horizontal"])
    ap.add_argument("--duracion", type=int, help="segundos objetivo")
    ap.add_argument("--sin-musica", action="store_true")
    args = ap.parse_args()

    cfg = cargar_config()
    if args.lote:
        temas = [l.strip() for l in Path(args.lote).read_text(
            encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        print(f"Lote: {len(temas)} videos")
        hechos = []
        for tema in temas:
            try:
                hechos.append(producir(cfg, tema, args))
            except Exception as e:
                print(f"  ERROR en '{tema}': {e}")
        print(f"\nTerminados {len(hechos)}/{len(temas)} videos en output/")
    elif args.tema:
        producir(cfg, args.tema, args)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
