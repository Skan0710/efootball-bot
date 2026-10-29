# eFootball Co-op Discord Statistics Bot

A private, self-hosted Discord bot engineered for eFootball co-op groups. It automates match record tracking by reading post-match **Details** screenshots with offline OCR, gives players an interactive preview to verify or correct extracted stats, saves confirmed matches into an Excel workbook, and provides real-time leaderboards, career records, and match histories.

---

## 1. Project Overview

Playing eFootball co-op matches with friends is thrilling, but manually recording who scored, who assisted, and how many matches each player won is tedious and error-prone.

This bot streamlines the entire lifecycle:
1. After a co-op match, take a screenshot of the in-game **Details** screen (which shows player names at the top and statistics like Goals and Assists below).
2. Run `/add-match-image` in Discord with the screenshot and match result (**Win**, **Loss**, or **Draw**).
3. The bot extracts individual player names and their stats using a layout-aware OCR engine.
4. An ephemeral preview is returned only to you with **Confirm & Save**, **Edit Stats**, and **Cancel** buttons.
5. Once confirmed, the match is atomically saved to an Excel workbook (`data/matches.xlsx`), immediately updating leaderboards and career stats.

### Core Philosophy
- **100% Free**: Operates using standard Python libraries and offline Tesseract OCR.
- **Zero Paid APIs / Clouds**: No OpenAI, Google Cloud Vision, or AWS charges.
- **No Complex Databases**: Excel (`.xlsx`) acts as the single source of truth, making data easy to inspect, edit, or back up.
- **Privacy & Security**: Enforces guild-level isolation and administrative role restrictions.

---

## 2. Features

- **Automated OCR Extraction**: Automatically detects player columns and extracts Goals and Assists from post-match screenshots.
- **Variable Player Column Support**: Works seamlessly whether you play with 2 or 3 co-op teammates.
- **Interactive Verification**: Private ephemeral previews allow submitting players to review and correct stats before saving.
- **Manual Edit Modal**: If OCR encounters glare or unusual in-game typography, click **Edit Stats** to manually enter player figures.
- **Single Source of Truth**: Data is persisted in Excel (`data/matches.xlsx`) with frozen header panes, auto-filters, and styled tables.
- **Concurrency & Backup Protection**: Asynchronous locks prevent race conditions during simultaneous match submissions, and automatic timestamped backups are preserved in `data/backups/`.
- **Comprehensive Statistics**: Computes win rates, loss rates, draw rates, total goals, total assists, and career records.
- **Six Specialized Slash Commands**: All commands use intuitive Discord slash commands registered directly to your private guild.

---

## 3. Architecture

```
                    +--------------------------------+
                    |    Discord Server (Guild)      |
                    +---------------+----------------+
                                    |
                           Slash Interactions
                                    |
                                    v
+-----------------------------------------------------------------------+
|                             bot.py                                    |
|   (EFootballBot Client, Guild Syncing, Global AppCommand Error Handler)|
+-----------------------------------+-----------------------------------+
                                    |
                     +--------------+--------------+
                     |                             |
                     v                             v
+-------------------------------+   +-----------------------------------+
|      commands_handler.py      |   |             ocr.py                |
|  - /add-match-image           |   |  - Image validation & size checks |
|  - /delete-match              |   |  - OpenCV contrast & CLAHE filter |
|  - /leaderboard               |   |  - Tesseract OCR data parsing     |
|  - /record                    |   |  - Column center x-alignment      |
|  - /matches                   |   |  - Diagnostic reporting & fallback|
|  - /teammates                 |   +-----------------------------------+
|  - MatchPreviewView (Buttons) |
|  - EditStatsModal (Modal)     |
+---------------+---------------+
                |
                v
+-----------------------------------------------------------------------+
|                          excel_manager.py                             |
|  - Thread/Async lock concurrency protection                           |
|  - Unique Match ID generation (M-YYYYMMDD-XXXXXX)                     |
|  - Matches & PlayerStats sheet persistence                            |
|  - Atomic match deletion & Leaderboard sheet synchronization          |
|  - Automatic rolling backups (data/backups/)                          |
+-------------------------------+---------------------------------------+
                                |
                                v
+-----------------------------------------------------------------------+
|                             stats.py                                  |
|  - Career aggregations (Win/Loss/Draw rates, Goals, Assists)          |
|  - Case-insensitive player matching                                   |
|  - Deterministic leaderboard sorting for all 7 categories             |
+-----------------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------------+
|                        data/matches.xlsx                              |
|           [Matches]       [PlayerStats]      [Leaderboard]            |
+-----------------------------------------------------------------------+
```

---

## 4. Prerequisites

- **Operating System**: Windows 10/11, Linux (Ubuntu/Debian), or macOS.
- **Python**: Version 3.11 or newer (Python 3.11 - 3.14 tested).
- **Tesseract OCR**: 5.x installed locally.
- **Discord Account**: With administrative permissions on your target Discord server.

---

## 5. Installing Python

### Windows
1. Download Python from [python.org/downloads](https://www.python.org/downloads/).
2. Run the installer.
3. **Important**: Check the box that says **"Add python.exe to PATH"** before clicking **Install Now**.
4. Verify installation in PowerShell:
   ```powershell
   python --version
   ```

---

## 6. Installing Tesseract on Windows

Tesseract performs the offline optical character recognition.

1. Download the Windows installer from the official UB-Mannheim repository:
   [https://github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki)
   *(e.g. `tesseract-ocr-w64-setup-5.4.0.exe`)*
2. Run the installer. By default, it installs to:
   ```text
   C:\Program Files\Tesseract-OCR\tesseract.exe
   ```
3. If installed to that default location, the bot automatically detects it.
4. If you install Tesseract to a custom folder, add its path to your `.env` file:
   ```env
   TESSERACT_CMD=C:\Your\Custom\Path\tesseract.exe
   ```

---

## 7. Creating the Discord Application and Bot

1. Navigate to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** (top right) and name it (e.g. `eFootball Co-op Bot`).
3. Click on the **Bot** tab on the left sidebar:
   - Click **Add Bot** (or **Reset Token** to generate a new token).
   - Copy the bot token. **Keep this secret. Never share or commit it.**
4. Under **Privileged Gateway Intents**:
   - The bot uses Discord slash commands and attachments. You **do not** need to enable `Message Content Intent` or any privileged intents.

---

## 8. Generating an Invite Link with Minimum Permissions

1. In the Discord Developer Portal, navigate to **OAuth2** -> **URL Generator**.
2. Under **Scopes**, select:
   - `bot`
   - `applications.commands`
3. Under **Bot Permissions**, select:
   - `Send Messages`
   - `Embed Links`
   - `Attach Files`
   - `Read Message History`
   - `Use Slash Commands`
4. Copy the generated URL at the bottom and open it in your browser.
5. Select your private eFootball Discord server and click **Authorize**.

---

## 9. Enabling Developer Mode and Finding IDs

To get your Discord Server (Guild) ID:
1. Open Discord, click the gear icon (⚙️) for **User Settings**.
2. Go to **Advanced** (under App Settings).
3. Toggle on **Developer Mode**.
4. In Discord, right-click on your server's icon in the left channel list.
5. Click **Copy Server ID**. This number is your `DISCORD_GUILD_ID`.
6. *(Optional)* To restrict commands to a specific channel, right-click that channel and click **Copy Channel ID** (`ALLOWED_CHANNEL_ID`).
7. *(Optional)* To restrict `/delete-match` to an admin role, right-click that role in Server Settings -> Roles and click **Copy Role ID** (`ADMIN_ROLE_ID`).

---

## 10. Configuring `.env`

Copy the sample environment file:
```powershell
Copy-Item .env.example .env
```

Open `.env` in any text editor and fill in your values:
```env
# Required: Bot token from Discord Developer Portal
DISCORD_BOT_TOKEN=MTE5OTg0NDAz...your_token_here...

# Required: Target Discord Server ID
DISCORD_GUILD_ID=123456789012345678

# Storage location
WORKBOOK_PATH=data/matches.xlsx

# Maximum upload size in MB (default: 10)
MAX_IMAGE_MB=10

# Optional: Restrict slash commands to this specific channel ID
ALLOWED_CHANNEL_ID=

# Optional: Role allowed to run /delete-match (if blank, requires Server Administrator)
ADMIN_ROLE_ID=

# Optional: Path to Tesseract OCR executable on Windows
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe

# Logging level
LOG_LEVEL=INFO
```

---

## 11. Creating and Activating a Virtual Environment

Open PowerShell or Command Prompt in the repository root:
```powershell
# Create virtual environment
python -m venv venv

# Activate on Windows PowerShell:
.\venv\Scripts\Activate.ps1

# (If PowerShell blocks scripts, run: Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned)
```

---

## 12. Installing Requirements

Install all necessary dependencies:
```powershell
pip install -r requirements.txt
```

---

## 13. Running the Bot Locally

Start the bot:
```powershell
python bot.py
```

Expected startup output:
```text
2026-10-29 12:00:00 [INFO] efootball_bot: Starting eFootball Discord Bot...
2026-10-29 12:00:01 [INFO] efootball_bot.bot: Running setup hook. Ensuring target storage directory exists...
2026-10-29 12:00:01 [INFO] efootball_bot.bot: Syncing slash commands exclusively to Guild ID: 123456789012345678
2026-10-29 12:00:02 [INFO] efootball_bot.bot: Synced 6 slash commands to guild 123456789012345678
2026-10-29 12:00:02 [INFO] efootball_bot.bot: Logged in as eFootball Bot#1234 (ID: 119984403...)
2026-10-29 12:00:02 [INFO] efootball_bot.bot: Bot is active and listening in target guild: 123456789012345678
```

> **Note on Free Local Hosting**: Running the bot on your local PC costs \$0. However, the PC must remain powered on with an active internet connection for the bot to respond in Discord. If your PC goes to sleep, the bot will appear offline until resumed.

---

## 14. Testing Each Slash Command

All six commands are registered directly to your configured guild for instant availability:

### 1. `/add-match-image`
- **Arguments**:
  - `image` *(Optional attachment)*: 1st Image / Home team post-match screenshot (paste with `Ctrl+V` or upload).
  - `image2` *(Optional attachment)*: 2nd Image / Away team post-match screenshot (paste with `Ctrl+V` or upload).
  - `result` *(Optional choice)*: Manual outcome override (`Win`, `Loss`, or `Draw`). If omitted and 2 images are provided, the outcome is auto-calculated by comparing 1st image (Home score) vs 2nd image (Away score)!
  - `image_url` *(Optional string)*: Directly pasted URL/link to Home team screenshot.
  - `image2_url` *(Optional string)*: Directly pasted URL/link to Away team screenshot.
- **Direct Clipboard Paste (Ctrl+V)**:
  - When selecting the `image` or `image2` fields in Discord, simply press `Ctrl+V` to paste screenshots straight from your clipboard without saving them to a file first.
  - **Direct Chat Paste**: You can also press `Ctrl+V` in the authorized Discord channel to paste 1 or 2 screenshots directly into chat—the bot automatically detects the images and triggers the match preview!
- **Home vs Away Score & Result**:
  - **1st Image** = Home Team score (sum of goals scored by Home players).
  - **2nd Image** = Away Team score (sum of goals scored by Away players).
  - The bot automatically compares Home vs Away:
    - Home > Away ➔ **Win**
    - Home < Away ➔ **Loss**
    - Home == Away ➔ **Draw**
- **Workflow**:
  - Responds with an interactive preview displaying the match score, detected players, and result.
  - Controls:
    - **Confirm & Save**: Permanently records the match and updates stats.
    - **Edit Stats**: Opens a popup modal allowing manual correction of player names, goals, assists, scores, and result.
    - **Cancel**: Discards the preview without saving.

### 2. `/delete-match`
- **Arguments**:
  - `match_id` *(Required string)*: The unique ID of the match to delete (e.g. `M-20261029-A1B2C3`).
- **Security**: Restricted to users with Discord Administrator privileges or the configured `ADMIN_ROLE_ID`.
- **Workflow**: Asks for confirmation before atomically removing the match and associated player rows, then recalculates career aggregates.

### 3. `/leaderboard`
- **Arguments**:
  - `category` *(Required choice)*: Choose from:
    - `Goals`
    - `Assists`
    - `Wins`
    - `Losses`
    - `Draws`
    - `Win rate`
    - `Matches played`
- **Output**: Embed with rankings, medal badges (🥇, 🥈, 🥉), and tie-breaking metrics.

### 4. `/record`
- **Arguments**:
  - `player` *(Required string)*: Name of the player (case-insensitive, e.g. `skanrocky` or `SkanRocky`).
- **Output**: Career card displaying matches played, goals, assists, wins, losses, draws, win percentage, loss percentage, and draw percentage.

### 5. `/matches`
- **Arguments**:
  - `limit` *(Optional integer, 1 to 25, default 10)*: Number of recent matches to list.
- **Output**: Chronological list of recent matches, their results, and player performances.

### 6. `/teammates`
- **Arguments**: None.
- **Output**: Alphabetical roster of all registered players and their cumulative records.

---

## 15. Understanding and Correcting OCR Results

The OCR pipeline uses OpenCV to preprocess screenshots:
1. Resizes images to a normalized 1080p standard width (1920px).
2. Applies CLAHE (Contrast Limited Adaptive Histogram Equalization) and bilateral filtering.
3. Isolates player name columns across the upper section.
4. Locates the `Goals` and `Assists` rows and matches detected numeric values to the corresponding player column centers.

### What to do if OCR misreads or misses stats:
- In-game fonts, custom gamer tags, or compressed screenshots might occasionally confuse generic OCR.
- **Never Worry**: The bot never saves automatically without confirmation.
- Simply click **Edit Stats** on the preview embed.
- A modal opens with each player's stats pre-filled:
  ```text
  SkanRocky, 2, 0
  lillym, 0, 1
  ```
- Adjust any misspelled name or incorrect number, then click Submit. The preview updates instantly with your verified values.

---

## 16. Excel Workbook Structure

The workbook is saved at `data/matches.xlsx`. It can be opened directly in Microsoft Excel, Google Sheets, or LibreOffice Calc.

### Sheet 1: `Matches`
| Column | Description | Example |
|---|---|---|
| `MatchID` | Unique collision-free ID | `M-20261029-A1B2C3` |
| `Date` | Timestamp of submission | `2026-10-29 12:30:00` |
| `Result` | Match outcome | `Win` / `Loss` / `Draw` |
| `SubmittedBy` | Discord username | `Skan0710` |
| `ScreenshotReference` | SHA-256 fingerprint | `sha256:7f83b1657f...` |
| `Notes` | Optional match notes | `Clean sheet derby` |

### Sheet 2: `PlayerStats`
| Column | Description | Example |
|---|---|---|
| `MatchID` | Foreign key referencing Matches | `M-20261029-A1B2C3` |
| `Player` | Player in-game tag | `SkanRocky` |
| `Goals` | Individual goals scored | `2` |
| `Assists` | Individual assists recorded | `0` |
| `Result` | Team match outcome | `Win` |

### Sheet 3: `Leaderboard`
Dynamically refreshed whenever matches are saved or deleted so that inspecting the file directly in Excel shows current career figures:
- `Player`, `MatchesPlayed`, `Goals`, `Assists`, `Wins`, `Losses`, `Draws`, `WinRate%`, `LossRate%`, `DrawRate%`.

---

## 17. Backing Up and Restoring the Workbook

### Automatic Rolling Backups
Every time a match is confirmed or deleted, `excel_manager.py` creates a timestamped copy in:
```text
data/backups/matches_YYYYMMDD_HHMMSS.xlsx
```
The manager automatically retains the 10 most recent backups to prevent disk bloat.

### Restoring from a Backup
1. Stop the bot (`Ctrl + C`).
2. Navigate to `data/backups/`.
3. Copy your desired backup file and replace `data/matches.xlsx`.
4. Restart the bot.

---

## 18. Troubleshooting Common Problems

### Problem 1: `TesseractNotFoundError`
- **Cause**: Tesseract OCR is not installed or not in PATH.
- **Solution**: Install Tesseract via UB-Mannheim installer, then add `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` to `.env`.

### Problem 2: Slash commands do not appear in Discord
- **Cause**: Bot invited without `applications.commands` scope, or `DISCORD_GUILD_ID` in `.env` is incorrect.
- **Solution**: Verify `DISCORD_GUILD_ID` matches your server ID. Re-invite the bot using the URL generated in Step 8 with the `applications.commands` scope selected.

### Problem 3: "You do not have permission or are in the wrong channel"
- **Cause**: `ALLOWED_CHANNEL_ID` is set and you ran a command in another channel, or a non-admin attempted `/delete-match`.
- **Solution**: Run commands in the designated channel, or clear `ALLOWED_CHANNEL_ID` in `.env` to permit commands server-wide.

### Problem 4: `PermissionError` when saving match
- **Cause**: `data/matches.xlsx` is currently open and locked in Microsoft Excel.
- **Solution**: Close Excel so openpyxl can write to the file.

---

## 19. Free Hosting Options & Limitations

### 1. Local PC (Recommended)
- **Cost**: \$0.00.
- **Pros**: Easy setup, direct access to the Excel workbook, zero external latency.
- **Limitation**: Bot only runs when your computer is awake and connected to the internet.

### 2. Free Cloud Tiers (e.g. Render, Railway, Fly.io, Oracle Cloud Free Tier)
- **Persistent Storage Warning**: If deploying on container platforms (like Docker or free PaaS tiers), note that container filesystems are typically **ephemeral**. If the container restarts or sleeps, unmounted files (like `matches.xlsx`) are erased.
- **Requirement**: If using a cloud host, you **must** attach a persistent storage volume mounted at `/data` so `data/matches.xlsx` persists across container restarts.
- **Free Tier Limitations**: Most free cloud tiers have monthly execution hour limits or sleep after inactivity. There is no guarantee that any third-party cloud service remains permanently free.

---

## 20. How to Push the Project to GitHub Safely

### Safety Checklist
Before pushing to a public or private GitHub repository, ensure:
1. `.env` is listed in `.gitignore` (Verified).
2. `data/*.xlsx` is listed in `.gitignore` (Verified).
3. No bot tokens, credentials, or personal files are tracked in git commits.

### Verification Command
Run this command in PowerShell to confirm no sensitive files are tracked:
```powershell
git status --ignored
```
Confirm `.env` and `data/matches.xlsx` show under **Ignored files**.

### Pushing to GitHub
If pushing to your existing GitHub remote (`origin`):
```powershell
# Check current remote
git remote -v

# Push main branch commits
git push -u origin main
```
If prompted for GitHub credentials, use a **GitHub Personal Access Token (classic)** with `repo` scope or sign in via **Git Credential Manager**.

---

## Automated Test Suite

Run the full automated test suite anytime using pytest:
```powershell
python -m pytest tests/ -v
```

All 38 automated unit and integration tests validate calculations, Excel persistence, OCR algorithms, security restrictions, and edge-case handling.
