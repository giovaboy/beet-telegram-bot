FROM python:3.11-slim

# Install only the Docker CLI, not the full docker.io package
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
 && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-26.1.4.tgz \
 | tar xz -C /usr/local/bin --strip-components=1 docker/docker \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire application
COPY . .

# Default command
CMD ["python", "-u", "bot.py"]