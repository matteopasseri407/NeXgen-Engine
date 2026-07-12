from __future__ import annotations

import hashlib
import io
import os
import time
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image
from rapidocr import RapidOCR


MAX_BYTES = int(os.getenv("VAULT_OCR_MAX_BYTES", "15728640"))
READ_CHUNK_BYTES = 65536
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
ENGINE: RapidOCR | None = None

app = FastAPI(
    title="Vault OCR API",
    version="1.0.0",
    description="RapidOCR extraction service for the user's agent layer. It never writes to the vault.",
)


def get_engine() -> RapidOCR:
    global ENGINE
    if ENGINE is None:
        ENGINE = RapidOCR()
    return ENGINE


async def read_upload_within_limit(file: UploadFile, max_bytes: int) -> bytes:
    """Reads an UploadFile in bounded chunks, aborting as soon as the
    running total exceeds max_bytes instead of buffering the whole body
    first. Keeps a hostile client from forcing a full read (and full
    memory allocation) of an oversized upload before it gets rejected."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"image too large: exceeds {max_bytes} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def validate_image(data: bytes) -> dict[str, Any]:
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format or "unknown"
            width, height = img.size
            img.verify()
    except Exception as exc:
        raise HTTPException(status_code=415, detail=f"unsupported or invalid image: {exc}") from exc
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported image format: {fmt}. allowed: {sorted(ALLOWED_IMAGE_FORMATS)}",
        )
    return {"format": fmt, "width": width, "height": height}


def box_to_list(box: Any) -> list[list[float]]:
    if hasattr(box, "tolist"):
        box = box.tolist()
    return [[float(x), float(y)] for x, y in box]


def markdown_from_lines(lines: list[dict[str, Any]]) -> str:
    if not lines:
        return ""
    return "\n".join(line["text"] for line in lines)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine": "rapidocr",
        "engine_loaded": ENGINE is not None,
        "max_bytes": MAX_BYTES,
    }


@app.post("/ocr")
async def ocr(
    file: UploadFile = File(...),
    min_confidence: float = Form(0.0),
) -> dict[str, Any]:
    data = await read_upload_within_limit(file, MAX_BYTES)
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if min_confidence < 0 or min_confidence > 1:
        raise HTTPException(status_code=400, detail="min_confidence must be between 0 and 1")

    image_meta = validate_image(data)
    digest = hashlib.sha256(data).hexdigest()
    started = time.perf_counter()
    result = get_engine()(data, text_score=min_confidence or None)
    elapsed = time.perf_counter() - started

    txts_raw = getattr(result, "txts", None)
    scores_raw = getattr(result, "scores", None)
    boxes_raw = getattr(result, "boxes", None)
    txts = list(txts_raw) if txts_raw is not None else []
    scores = list(scores_raw) if scores_raw is not None else []
    boxes = list(boxes_raw) if boxes_raw is not None else []
    lines: list[dict[str, Any]] = []
    for idx, text in enumerate(txts):
        score = float(scores[idx]) if idx < len(scores) else None
        box = box_to_list(boxes[idx]) if idx < len(boxes) else None
        lines.append(
            {
                "index": idx,
                "text": str(text),
                "confidence": score,
                "box": box,
            }
        )

    avg_confidence = None
    if scores:
        avg_confidence = round(sum(float(s) for s in scores) / len(scores), 5)

    return {
        "status": "ok",
        "filename": file.filename,
        "sha256": digest,
        "bytes": len(data),
        "image": image_meta,
        "engine": "rapidocr",
        "elapsed_sec": round(elapsed, 4),
        "engine_elapsed_sec": round(float(getattr(result, "elapse", elapsed) or elapsed), 4),
        "line_count": len(lines),
        "avg_confidence": avg_confidence,
        "markdown": markdown_from_lines(lines),
        "lines": lines,
    }
