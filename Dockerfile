FROM python:3.12-slim

# ffmpeg para a conversao de audio
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# biblioteca persistente fora da imagem
VOLUME ["/app/data", "/app/Music"]
ENV OFFLINE_LIBRARY_DIR=/app/Music

EXPOSE 8722
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8722", "--no-browser"]
