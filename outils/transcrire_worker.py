"""Transcription mot à mot d'un média (faster-whisper), lancée par le serveur dans
un processus séparé, avec l'environnement Python qui possède faster-whisper.

    python transcrire_worker.py <media> <sortie.json> [--modele large-v3-turbo] [--langue fr]

Écrit {"language", "model", "device", "words": [{"w", "s", "e", "p"}]} (secondes source,
p = probabilité). Affiche la progression sur stdout : « PROGRESS 0.42 ».
"""
import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def charger(modele, device="auto"):
    from faster_whisper import WhisperModel
    if device == "cpu":
        return WhisperModel(modele, device="cpu", compute_type="int8"), "cpu"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return WhisperModel(modele, device="cuda", compute_type="float16"), "cuda"
    except Exception:
        pass
    return WhisperModel(modele, device="cpu", compute_type="int8"), "cpu"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media")
    ap.add_argument("sortie")
    ap.add_argument("--modele", default="large-v3-turbo")
    ap.add_argument("--langue", default="fr")
    ap.add_argument("--device", default="cpu", choices=["auto", "cpu", "cuda"])
    a = ap.parse_args()
    try:
        model, device = charger(a.modele, a.device)
        segments, info = model.transcribe(a.media, language=a.langue or None, word_timestamps=True,
                                          vad_filter=False, beam_size=5)
    except Exception as e:
        if "cuda" in str(e).lower() or "cublas" in str(e).lower() or "cudnn" in str(e).lower():
            from faster_whisper import WhisperModel
            model, device = WhisperModel(a.modele, device="cpu", compute_type="int8"), "cpu"
            segments, info = model.transcribe(a.media, language=a.langue or None, word_timestamps=True, beam_size=5)
        else:
            raise
    words = []
    dur = max(info.duration, 1e-6)
    for seg in segments:
        for w in seg.words or []:
            words.append({"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3),
                          "p": round(w.probability, 3)})
        print(f"PROGRESS {min(1.0, seg.end / dur):.3f}", flush=True)
    out = {"language": info.language, "model": a.modele, "device": device, "words": words}
    Path(a.sortie).write_text(json.dumps(out, ensure_ascii=False), "utf-8")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
