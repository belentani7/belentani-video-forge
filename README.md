# Belentani Video Forge

Pipeline local de videos cortos automáticos (estilo MoneyPrinterTurbo, creado desde cero).
Tema → guion IA → voz gratis → subtítulos sincronizados → clips stock → MP4 listo (vertical Reels/TikTok u horizontal YouTube).

## Requisitos (ya instalados en esta máquina)

- Python 3.14 + `edge-tts` + `requests`
- ffmpeg (build gyan essentials)

## Uso

```powershell
.\run.ps1 --tema "3 errores que arruinan tu mezcla"
.\run.ps1 --lote temas.txt                 # varios videos seguidos
.\run.ps1 --guion mi_guion.txt --tema "titulo"   # tu propio texto, sin IA
.\run.ps1 --tema "..." --formato horizontal --duracion 60
.\run.ps1 --tema "..." --sin-musica
```

Salida en `output/`.

## Configuración (`config.json`)

### Guion (elige UNO, gratis posible)

| Proveedor | Cómo activar | Costo |
|---|---|---|
| DeepSeek | pega tu key en `deepseek_api_key` (platform.deepseek.com) | centavos por guion |
| Ollama local | `ollama serve` + `ollama pull llama3.2` | gratis |
| Manual | `--guion archivo.txt` | gratis |

### Clips de stock (opcional pero recomendado)

Sin keys el sistema usa fondos degradados animados (funciona, pero stock real rinde más):

- Pexels: key gratis en https://www.pexels.com/api/
- Pixabay: key gratis en https://pixabay.com/api/docs/

Pega las keys en `config.json` → `clips`.

### Música

Pon TUS pistas (mp3/wav/flac) en `music/`. Eres productor: tu propia música = cero problemas de copyright y marca personal en cada video. Se elige una al azar y se mezcla bajo la voz.

### Voces (edge-tts, gratis, ilimitado)

Cambia `voz` en config.json:

- Español: `es-MX-JorgeNeural`, `es-MX-DaliaNeural`, `es-ES-AlvaroNeural`, `es-ES-ElviraNeural`
- Portugués BR: `pt-BR-AntonioNeural`, `pt-BR-FranciscaNeural`, `pt-BR-ThalitaNeural`
- Lista completa: `python -m edge_tts --list-voices`

Para portugués cambia también `idioma` a `pt`.

## Honestidad

Esto automatiza la PRODUCCIÓN. Los ingresos dependen de nicho, constancia y plataforma — ninguna herramienta los garantiza. Sube contenido con valor real; las plataformas penalizan spam masivo de baja calidad.
