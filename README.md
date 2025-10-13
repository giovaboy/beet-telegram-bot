# 🎵 Beet Telegram Bot

Interactive Telegram bot for managing music imports with Beets.

## 📁 Project Structure

```
beet-telegram-bot/
├── bot.py                      # Entry point
├── config.py                   # Configuration
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Docker image
├── docker-compose.yml          # Orchestration
├── .env                        # Environment variables
│
├── core/                       # Business logic
│   ├── beet_manager.py        # Main manager
│   ├── directory_analyzer.py  # Directory analysis
│   └── parsers.py             # Beet output parsing
│
├── handlers/                   # Telegram handlers
│   ├── commands.py            # Commands (/start, /list, etc.)
│   ├── callbacks.py           # Inline buttons
│   └── messages.py            # User input
│
├── ui/                         # User interface
│   ├── keyboards.py           # Keyboard creation
│   └── messages.py            # Message formatting
│
├── i18n/                       # Internationalization
│   ├── translations.py        # Translation system
│   └── locales/
│       ├── it.json            # Italian
│       └── en.json            # English
│
└── bot_state/                  # Persistent data (auto-created)
```

## 🚀 Quick Start

### 1. Create Telegram Bot

```bash
# Talk to @BotFather on Telegram
/newbot
# Save the token

# Get your Chat ID from @userinfobot
```

### 2. Clone and Configure

```bash
mkdir ~/beet-telegram-bot
cd ~/beet-telegram-bot

# Create all files from the structure
# (copy contents from artifacts)
```

### 3. Configure `.env`

```env
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id
BEET_CONTAINER=beets
BEET_USER=abc
LANGUAGE=en
PUID=1000
PGID=1000
TZ=America/New_York
```

### 4. Edit `docker-compose.yml`

```yaml
volumes:
  - /home/user/imports:/downloads  # Actual path
  - /home/user/Music:/music:ro
```

### 5. Start

```bash
docker-compose up -d --build
docker-compose logs -f
```

## 🌍 Supported Languages

- 🇬🇧 **English** (`LANGUAGE=en`)
- 🇮🇹 **Italian** (`LANGUAGE=it`)

Change language in `.env` file and restart:

```bash
docker-compose restart
```

## 📋 Available Commands

- `/start` - Start the bot
- `/list` - Show directories to import
- `/status` - Current import status
- `/cancel` - Cancel current import

## ✨ Features

### 📂 Directory Management
- List directories with size
- Structure analysis (single/multi-disc)
- Audio file visualization
- Image preview (cover art)
- Directory deletion

### 🔍 Search
- Direct links to MusicBrainz
- Direct links to Discogs
- Automatic query from directory name

### 🎵 Import
- Automatic import
- Candidate selection
- Import with MusicBrainz ID
- Import with Discogs ID
- As-is import (without metadata)
- Force first match

### 💾 Persistence
- Saved import state
- Resume after restart

## 🔧 Advanced Configuration

### Different Paths Between Bot and Beet

If the beet container sees a different path:

```env
# In .env
BEET_IMPORT_PATH=/config/imports
```

### Logging

```env
LOG_LEVEL=DEBUG  # INFO, WARNING, ERROR
```

### Docker Network

If beet is on an existing network:

```yaml
networks:
  beet-network:
    external: true
    name: existing_network_name
```

## 🛠️ Development

### Adding a New Language

1. Create `i18n/locales/fr.json` (e.g., French)
2. Copy structure from `en.json`
3. Translate all strings
4. Set `LANGUAGE=fr` in `.env`

### Modifying Code

```bash
# Edit files
nano core/beet_manager.py

# Rebuild and restart
docker-compose down
docker-compose up -d --build
```

### Testing Locally (Without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Export variables
export TELEGRAM_BOT_TOKEN=...
export LANGUAGE=en

# Start
python bot.py
```

## 🐛 Troubleshooting

### Bot Not Responding

```bash
docker-compose logs beet-bot
```

### Beet Connection Error

```bash
# Check container
docker ps | grep beet

# Test command
docker exec -u abc beets beet version
```

### File Permissions

```bash
sudo chown -R 1000:1000 /path/to/imports
```

### Import Hangs

Increase timeout in `core/beet_manager.py`:

```python
result = subprocess.run(..., timeout=300)  # 5 minutes
```

## 📊 Usage Examples

### Basic Workflow

1. Copy album to `/imports/`
2. `/list` on Telegram
3. Select directory
4. View details and images
5. Click "▶️ Start Import"
6. If needed, enter MusicBrainz ID
7. ✅ Done!

### Import with ID

1. Album not recognized
2. Click "🔍 Search MusicBrainz"
3. Find the ID: `abc123...`
4. Click "🔍 MusicBrainz ID"
5. Paste the ID
6. ✅ Import with correct metadata!

### Multi-Disc

```
/imports/The_Wall/
├── CD1/        (13 tracks)
├── CD2/        (13 tracks)
└── cover.jpg
```

The bot automatically detects and shows info for each disc.

## 🤝 Contributing

To add features:

1. Modify appropriate files
2. Update translations in `i18n/locales/`
3. Test with both languages
4. Document in README

## 📝 Notes

- Bot uses `docker exec` to communicate with beet
- State is saved in `/tmp/beet_import_state.json`
- Skipped directories go to `/imports/skipped/`
- Max 10 images sent at once

## 🔐 Security

- Bot only accepts commands from configured `CHAT_ID`
- No external ports exposed
- Uses Docker socket in read-only mode (where possible)

## 📜 License

MIT License - Free to use!

## 🙏 Credits

- [Beets](https://beets.io/) - Music library manager
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- MusicBrainz & Discogs for metadata

---

**Made with ❤️ for music lovers**