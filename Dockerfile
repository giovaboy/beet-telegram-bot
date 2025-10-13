FROM python:3.11-slim

# Install Docker CLI to be able to run 'docker exec'
RUN apt-get update && apt-get install -y \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Create working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire application structure
COPY . /app/

# Start the bot
CMD ["python", "-u", "bot.py"]
