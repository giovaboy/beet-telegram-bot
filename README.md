# 🎵 Beet Telegram Bot

Bot Telegram per gestire import musicali con Beets in modo interattivo.

## 📁 Struttura del progetto

```
beet-telegram-bot/
├── bot.py # Entry point
├── config.py # Configurazione
├── requirements.txt # Dipendenze Python
├── Dockerfile # Immagine Docker
├── docker-compose.yml # Orchestrazione
├── .env # Variabili ambiente
│
├── core/ # Logica business
│ ├── beet_manager.py # Manager principale
│ ├── directory_analyzer.py # Analisi directory
│ └── parsers.py # Parse output beet
│
├── handlers/ # Handler Telegram
│ ├── commands.py # Comandi (/start, /list, etc.)
│ ├── callbacks.py # Bottoni inline
│ └── messages.py # Input utente
│
├── ui/ # Interfaccia utente
│ ├── keyboards.py # Creazione tastiere
│ └── messages.py # Formattazione messaggi
│
├── i18n/ # Internazionalizzazione
│ ├── translations.py # Sistema traduzione
│ └── locales/
│ ├── it.json # Italiano
│ └── en.json # Inglese
│
└── bot_state/ # Dati persistenti (creato automaticamente)
```

## 🚀 Quick Start

### 1. Crea il bot Telegram

```bash
# Parla con @BotFather su Telegram
/newbot
# Salva il token

# Ottieni il tuo Chat ID da @userinfobot
```

### 2. Clona e configura

```bash
mkdir ~/beet-telegram-bot
cd ~/beet-telegram-bot

# Crea tutti i file della struttura
# (copia i contenuti dagli artifacts)
```

### 3. Configura `.env`

```env
TELEGRAM_BOT_TOKEN=il_tuo_token
TELEGRAM_CHAT_ID=il_tuo_chat_id
BEET_CONTAINER=beets
BEET_USER=abc
LANGUAGE=it
PUID=1000
PGID=1000
TZ=Europe/Rome
```

### 4. Modifica `docker-compose.yml`

```yaml
volumes:
- /home/user/imports:/downloads # Path reale
- /home/user/Music:/music:ro
```

### 5. Avvia

```bash
docker-compose up -d --build
docker-compose logs -f
```

## 🌍 Lingue supportate

- 🇮🇹 **Italiano** (`LANGUAGE=it`)
- 🇬🇧 **Inglese** (`LANGUAGE=en`)

Cambia lingua nel file `.env` e riavvia:

```bash
docker-compose restart
```

## 📋 Comandi disponibili

- `/start` - Avvia il bot
- `/list` - Mostra directory da importare
- `/status` - Stato import corrente
- `/cancel` - Annulla import corrente

## ✨ Funzionalità

### 📂 Gestione directory
- Lista directory con dimensione
- Analisi struttura (singolo/multi-disco)
- Visualizzazione file audio
- Anteprima immagini (copertine)
- Eliminazione directory

### 🔍 Ricerca
- Link diretti a MusicBrainz
- Link diretti a Discogs
- Query automatica dal nome directory

### 🎵 Import
- Import automatico
- Selezione candidati
- Import con MusicBrainz ID
- Import con Discogs ID
- Import as-is (senza metadata)
- Forza primo match

### 💾 Persistenza
- Stato import salvato
- Riprendi dopo riavvio

## 🔧 Configurazione avanzata

### Path diversi tra bot e beet

Se il container beet vede un path diverso:

```env
# Nel .env
BEET_IMPORT_PATH=/config/imports
```

### Logging

```env
LOG_LEVEL=DEBUG # INFO, WARNING, ERROR
```

### Network Docker

Se beet è su una network esistente:

```yaml
networks:
beet-network:
external: true
name: nome_network_esistente
```

## 🛠️ Sviluppo

### Aggiungere una nuova lingua

1. Crea `i18n/locales/fr.json` (esempio francese)
2. Copia la struttura da `it.json`
3. Traduci tutte le stringhe
4. Imposta `LANGUAGE=fr` nel `.env`

### Modificare il codice

```bash
# Modifica i file
nano core/beet_manager.py

# Rebuild e riavvia
docker-compose down
docker-compose up -d --build
```

### Testare in locale (senza Docker)

```bash
# Installa dipendenze
pip install -r requirements.txt

# Esporta variabili
export TELEGRAM_BOT_TOKEN=...
export LANGUAGE=it

# Avvia
python bot.py
```

## 🐛 Troubleshooting

### Bot non risponde

```bash
docker-compose logs beet-bot
```

### Errore connessione a beet

```bash
# Verifica container
docker ps | grep beet

# Testa comando
docker exec -u abc beets beet version
```

### Permessi file

```bash
sudo chown -R 1000:1000 /path/to/imports
```

### Import si blocca

Aumenta timeout in `core/beet_manager.py`:

```python
result = subprocess.run(..., timeout=300) # 5 minuti
```

## 📊 Esempi di utilizzo

### Workflow base

1. Copi album in `/imports/`
2. `/list` su Telegram
3. Selezioni directory
4. Vedi dettagli e immagini
5. Click "▶️ Avvia Import"
6. Se serve, inserisci MusicBrainz ID
7. ✅ Completato!

### Import con ID

1. Album non riconosciuto
2. Click "🔍 Cerca MusicBrainz"
3. Trovi l'ID: `abc123...`
4. Click "🔍 MusicBrainz ID"
5. Incolli l'ID
6. ✅ Import con metadata corretti!

### Multi-disco

```
/imports/The_Wall/
├── CD1/ (13 tracce)
├── CD2/ (13 tracce)
└── cover.jpg
```

Il bot rileva automaticamente e mostra info per ogni disco.

## 🤝 Contribuire

Per aggiungere funzionalità:

1. Modifica i file appropriati
2. Aggiorna le traduzioni in `i18n/locales/`
3. Testa con entrambe le lingue
4. Documenta nel README

## 📝 Note

- Il bot usa `docker exec` per comunicare con beet
- Lo stato è salvato in `/tmp/beet_import_state.json`
- Le directory skippate vanno in `/imports/skipped/`
- Max 10 immagini inviate per volta

## 🔐 Sicurezza

- Il bot accetta comandi solo dal `CHAT_ID` configurato
- Non espone porte esterne
- Usa socket Docker in read-only (dove possibile)

## 📜 Licenza

MIT License - Usa liberamente!

## 🙏 Credits

- [Beets](https://beets.io/) - Music library manager
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- MusicBrainz & Discogs per i metadata

---

**Made with ❤️ for music lovers**