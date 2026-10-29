# eFootball Co-op Discord Statistics Bot

A private, self-hosted Discord bot designed for eFootball co-op gaming groups. It processes post-match player statistics screenshots using local OCR, allows players to review and edit extracted figures, persists confirmed records into an Excel workbook, and provides on-demand career statistics, head-to-head records, match history, and leaderboards.

---

## 1. Project Overview

When playing eFootball co-op matches, reviewing performance and keeping track of goals, assists, and win rates adds friendly competition to the game. Manually transcribing numbers after every match is tedious and prone to typos.

This bot automates the workflow:
1. Players drop their post-match details screenshot into Discord with `/add-match-image`.
2. An offline, free OCR engine (Tesseract) extracts player names and their respective goals and assists.
3. The submitting player receives an ephemeral, private interactive preview to verify and adjust detected stats if needed.
4. Confirmed matches are safely recorded in a local Excel workbook (`data/matches.xlsx`).
5. Slash commands compute real-time leaderboards, career records, teammate statistics, and match history.

### Completely Free & Local Execution
- **Zero Paid APIs**: Operates locally using Tesseract OCR and open-source Python libraries.
- **No Paid Databases**: Uses a local `.xlsx` workbook with atomic write locks and automatic backups.
- **Strict Privacy**: Restricts bot interactions to your private Discord server.

---

## 2. Technology Stack

- **Python 3.11+**
- **discord.py 2.x** (Application slash commands with interactive buttons and modals)
- **Tesseract OCR & pytesseract** (Offline optical character recognition)
- **OpenCV & Pillow** (Image preprocessing, adaptive thresholding, and validation)
- **openpyxl** (Structured Excel workbook management)
- **python-dotenv** (Environment variable isolation)
- **pytest** (Comprehensive automated test suite)

---

## 3. Initial Setup & Prerequisites

### Prerequisites
- Python 3.11 or newer installed and accessible on PATH.
- Tesseract OCR installed on the host machine.
- A Discord Application and Bot account created via the Discord Developer Portal.

### Repository Layout
```text
efootball-discord-stats-bot/
  bot.py               # Discord bot entry point and slash command synchronization
  config.py            # Environment configuration and logging setup
  models.py            # Domain data structures (MatchRecord, PlayerStat, etc.)
  excel_manager.py     # Workbook initialization, transaction locking, and queries
  stats.py             # Career record calculation and leaderboard aggregation
  ocr.py               # Layout-aware image preprocessing and OCR extraction
  requirements.txt     # Python package dependencies
  .env.example         # Environment template with explanatory comments
  .gitignore           # Exclusion rules for virtual environments, secrets, and local data
  README.md            # Comprehensive documentation
  data/                # Excel storage directory (matches.xlsx)
  tests/               # Automated unit and integration test suite
```

### Quick Start
1. Clone this repository:
   ```bash
   git clone https://github.com/Skan0710/efootball-bot.git
   cd efootball-bot
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   # On Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   # On Linux / macOS:
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy the environment configuration template:
   ```bash
   cp .env.example .env
   # On Windows PowerShell:
   Copy-Item .env.example .env
   ```
5. Edit `.env` to insert your Discord bot token and Server (Guild) ID.

---

*Detailed command documentation, OCR tuning guides, backup strategies, and deployment options are covered in subsequent sections.*
