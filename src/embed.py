"""Page text -> bge-m3 dense vectors (the model Workers AI serves as @cf/baai/bge-m3).

Usage: python src/embed.py out/pages.jsonl out/emb      writes out/emb.npy (chunk vectors) and out/emb.idx.npy (page row per chunk)
A page gives up to MAX_CHUNKS chunks: the start of the text plus pieces spread evenly over the rest, each with the
title in front. Hijacked sites keep their old content at the top, so reading only the start misses the scam part.
"""
import json
import sys
import time

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

MODEL = "BAAI/bge-m3"
CHUNK_CHARS = 1200
MAX_CHUNKS = 4
MAX_TOKENS = 320


def chunks(row):
    text, title = row["text"], row["title"]
    if len(text) <= CHUNK_CHARS:
        starts = [0]
    else:
        last = len(text) - CHUNK_CHARS
        n = min(MAX_CHUNKS, 1 + -(-last // CHUNK_CHARS))
        starts = sorted({round(i * last / (n - 1)) for i in range(n)})
    return [(title + "\n" + text[s : s + CHUNK_CHARS]).strip() or "(empty page)" for s in starts]


def load_model():
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(MODEL, device=device)
    model.max_seq_length = MAX_TOKENS
    if device == "mps":
        model.half()  # 3.5x faster on Apple GPUs; vectors match full precision to cosine 0.9997
    return model


def encode(model, texts, batch_size=32):
    order = np.argsort([-len(t) for t in texts])  # similar lengths per batch: far less padding
    out = model.encode([texts[i] for i in order], batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
    emb = np.empty_like(out)
    emb[order] = out
    return emb.astype(np.float32)


def main(pages_path, out_prefix):
    rows = [json.loads(line) for line in open(pages_path, encoding="utf-8")]
    texts, index = [], []
    for i, row in enumerate(rows):
        for c in chunks(row):
            texts.append(c)
            index.append(i)
    model = load_model()
    start = time.time()
    emb = encode(model, texts)
    np.save(out_prefix + ".npy", emb)
    np.save(out_prefix + ".idx.npy", np.array(index, dtype=np.int32))
    print(f"embedded {len(rows)} pages as {len(texts)} chunks, dim {emb.shape[1]}, {time.time() - start:.0f} s on {model.device}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
