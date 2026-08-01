# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Install system dependencies including curl, unzip, and ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    ca-certificates \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Deno for yt-dlp JS challenge solving
RUN curl -fsSL https://deno.land/install.sh | sh -s -- -y
ENV PATH="/root/.deno/bin:$PATH"

# Set working directory inside container
WORKDIR /app

# Copy requirement list and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files into container
COPY . .

# Expose port and run gunicorn with a 120-second timeout
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "120", "app:app"]