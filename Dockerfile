# Two-stage build: compile the React frontend with Node, then run everything
# from a small Python image. The final image contains no Node and no frontend
# source - just the built files and the Python app.

# --- stage 1: build the frontend ---
FROM node:20-alpine AS frontend

WORKDIR /build

# Copy the dependency manifests first. Docker caches this layer, so changing
# application code does not force a full reinstall of node_modules.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install

COPY frontend/ ./
RUN npm run build


# --- stage 2: the application image ---
FROM python:3.12-slim

# Hugging Face Spaces runs containers as a non-root user with id 1000.
# Creating that user ourselves keeps file permissions correct.
RUN useradd -m -u 1000 appuser

WORKDIR /home/appuser/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# The built frontend goes where main.py expects to find it.
COPY --from=frontend /build/dist ./app/static

RUN chown -R appuser:appuser /home/appuser
USER appuser

# The host tells us which port to listen on via $PORT (Render does this).
# 7860 is the fallback so the image also runs locally with no configuration.
ENV PORT=7860
EXPOSE 7860

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
