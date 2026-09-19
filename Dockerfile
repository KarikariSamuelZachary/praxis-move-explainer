FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git stockfish \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 120 -r requirements.txt

COPY . .

# Pre-download and validate the Maia-3 5M checkpoint from Hugging Face so a
# user's first request doesn't trigger a download at runtime. Fails the build
# if huggingface.co is unreachable from the build host.
RUN python scripts/prewarm_maia3.py

# Pre-download and validate the full 3-, 4- and 5-man Syzygy tablebases
# (~940 MB; the sourced Endgame Trainer content has <=5-man drill starts)
# so endgame drills never fetch locally covered material at runtime. Fails
# the build if neither mirror is reachable from the build host. 6/7-man
# positions are deliberately served by the Lichess API fallback in
# services/tablebase.py, not baked in.
RUN python scripts/prewarm_syzygy.py

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
