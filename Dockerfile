# Use lightweight Python image
FROM python:3.11-slim

# Install FFmpeg (required for video/photo/gif conversion)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source code
COPY main.py .

# Run the bot
CMD ["python", "main.py"]
