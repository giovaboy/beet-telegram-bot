# Beet Telegram Bot - Multi-stage Dockerfile
# Supports both production and development/testing modes

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Stage 1: Base - Common dependencies for all stages
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FROM python:3.11-slim AS base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
#RUN apt-get update && apt-get install -y --no-install-recommends \
#    docker.io \
#    && rm -rf /var/lib/apt/lists/*


# Install only the Docker CLI, not the full docker.io package
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
 && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-26.1.4.tgz \
 | tar xz -C /usr/local/bin --strip-components=1 docker/docker \
 && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Stage 2: Production - Minimal image with only runtime dependencies
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FROM base AS production

# Install only production dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY bot.py config.py ./
COPY core/ ./core/
COPY handlers/ ./handlers/
COPY ui/ ./ui/
COPY i18n/ ./i18n/

# Create state directory
RUN mkdir -p /app/bot_state

# Run the bot
CMD ["python", "-u", "bot.py"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Stage 3: Development - Includes testing tools and dev dependencies
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FROM base AS development

# Install both production and development dependencies
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/bot_state /app/htmlcov /app/junit

# Set permissions for development user
#RUN groupadd -g 1000 botuser && \
#    useradd -u 1000 -g botuser -s /bin/bash -m botuser && \
#    chown -R botuser:botuser /app

#USER botuser

# Default command for development (can be overridden)
CMD ["python", "bot.py"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Stage 4: Testing - Dedicated stage for running tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FROM development AS testing

# Already has all dependencies from development stage

# Set testing environment variables
ENV TELEGRAM_BOT_TOKEN=test_token \
    TELEGRAM_CHAT_ID=123456 \
    BEET_CONTAINER=test_beets \
    BEET_USER=test_user \
    LANGUAGE=en \
    LOG_LEVEL=DEBUG

# Run tests by default
CMD ["pytest", "-v", "--cov=core", "--cov=ui", "--cov=handlers", \
     "--cov-report=term-missing", "--cov-report=html", "--cov-report=xml"]