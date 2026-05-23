# WondeX 🛡️

A Discord **Moderation, Security & Ticket** bot built in Python and hosted **for free** using GitHub Actions.

Developed by **wantedpebkkk** — created because many servers lack proper security and moderation tools.

---

## ✨ Features

| Category | Commands |
|---|---|
| 🔨 **Moderation** | `Wa!kick`, `Wa!ban`, `Wa!unban`, `Wa!mute`, `Wa!unmute`, `Wa!warn`, `Wa!purge` |
| 🛡️ **Security** | `Wa!lockdown`, `Wa!unlock`, `Wa!serverinfo`, `Wa!userinfo` |
| 🎫 **Tickets** | `Wa!ticketpanel` (staff) → members click **Open Ticket 🎫** button → **Close 🔒 / Claim 👋** buttons |

Default prefix: **`Wa!`** (override with `BOT_PREFIX`)

---

## 🚀 Free Hosting on GitHub (no VPS needed)

The bot runs inside **GitHub Actions**, which is completely free for public repositories and gives you 2,000 free minutes/month on private repos. A scheduled workflow restarts the bot every 6 hours to stay within GitHub's job time limit.

### Step 1 — Fork this repository

Click **Fork** at the top-right of this page so you own a copy.

### Step 2 — Create your Discord bot

1. Go to <https://discord.com/developers/applications> and create a new application.
2. Navigate to **Bot** → click **Add Bot**.
3. Copy the **Token** (keep it secret!).
4. Under **OAuth2 → URL Generator** select the `bot` scope and the permissions you need, then invite the bot to your server.

### Step 3 — Add your token to GitHub Secrets

1. In your forked repository go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**.
3. Name it exactly `DISCORD_TOKEN` and paste your bot token as the value.
4. Click **Add secret**.

### Step 4 — Enable GitHub Actions

1. Go to the **Actions** tab of your fork.
2. If prompted, click **I understand my workflows, go ahead and enable them**.
3. Click on **Run WondeX Discord Bot** → **Run workflow** to start the bot immediately.

The workflow also runs automatically every 6 hours via a cron schedule, and the **`restart.yml`** workflow immediately re-launches the bot the moment a run ends — so the bot is back online within ~30 seconds, with no pinging or external services required.

---

## 🔧 Local Development

```bash
# Clone your fork
git clone https://github.com/<your-username>/WondeX.git
cd WondeX

# Install dependencies
pip install -r requirements.txt

# Create a local .env file (never commit this!)
cp .env.example .env
# Edit .env and set DISCORD_TOKEN=your_token_here

# Run the bot
python bot.py
```

---

## ⚙️ Configuration

Set these values in your `.env` (local) or as GitHub Action secrets/variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `DISCORD_TOKEN` | Bot token from the Discord Developer Portal | **Required** |
| `BOT_PREFIX` | Command prefix | `Wa!` |
| `TICKET_CATEGORY` | Category name for ticket channels | `Tickets` |
| `WELCOME_CHANNEL_ID` | Channel ID to send welcome messages | none |
| `LOG_CHANNEL_ID` | Channel ID for moderation logs | none |
| `DASHBOARD_HOST` | Dashboard bind host | `0.0.0.0` |
| `DASHBOARD_PORT` | Dashboard bind port | `5000` |

---

## 📁 Project Structure

```
WondeX/
├── bot.py                        # Main bot code
├── requirements.txt              # Python dependencies
├── .env.example                  # Template for environment variables
├── .gitignore                    # Keeps secrets & caches out of git
└── .github/
    └── workflows/
        ├── bot.yml               # GitHub Actions workflow (free VPS)
        └── restart.yml           # Auto-restart: re-dispatches bot.yml on completion
```

---

## ⚠️ Security Notes

- **Never** commit your bot token to git. Always use GitHub Secrets.
- The `.gitignore` file already excludes `.env` to protect local tokens.
- Rotate your token immediately at <https://discord.com/developers/applications> if you accidentally expose it.
