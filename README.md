# 👁️ ARGUS - The All-Seeing Morning Briefing Agent

> *Named after the 100-eyed giant from Greek mythology who never slept.*

ARGUS is a personal AI agent that runs overnight, monitors all your data sources, and delivers a personalized podcast briefing to your phone before you wake up.

No more scrolling through 6 apps every morning. Just press play.

---

## How It Works

```
                        ┌──────────────┐
                        │  ⏰ 5:00 AM  │
                        │   CRON JOB   │
                        └──────┬───────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                 ▼
     ┌────────────────┐ ┌───────────┐ ┌──────────────────┐
     │  WORK SOURCES  │ │ MONITORING│ │  WORLD SOURCES   │
     │                │ │           │ │                   │
     │  📨 Slack      │ │ 📊 Grafana│ │  📰 News (RSS)   │
     │  ✉️  Gmail      │ │  - Alerts │ │  ⚽ Sports APIs   │
     │  🎫 Jira       │ │  - Panels │ │                   │
     │                │ │  - Deploys│ │                   │
     └───────┬────────┘ └─────┬─────┘ └────────┬─────────┘
             │                │                 │
             └────────────────┼─────────────────┘
                              │
                              ▼
                    ┌───────────────────┐
                    │   🧠 LLM LAYER    │
                    │                   │
                    │  Pass 1: Summarize │
                    │  each source       │
                    │                   │
                    │  Pass 2: Generate  │
                    │  podcast script    │
                    └─────────┬─────────┘
                              │
                              ▼
                    ┌───────────────────┐
                    │   🔊 TTS ENGINE   │
                    │  OpenAI / Eleven  │
                    │  Labs             │
                    └─────────┬─────────┘
                              │
                              ▼
                    ┌───────────────────┐
                    │   📱 DELIVERY     │
                    │  Telegram / Local │
                    └───────────────────┘
```

## Podcast Structure

```
🎙️ "Good morning! Here's what happened while you slept..."

 1. 🔴 Urgent       (30s)  - Critical alerts, blockers, things that need you NOW
 2. 📊 Monitoring   (45s)  - Grafana alerts, deploys, metric anomalies
 3. 💼 Work         (90s)  - Slack decisions, Jira updates, important emails
 4. 📰 News         (60s)  - Top headlines, industry updates
 5. ⚽ Sports       (45s)  - Scores and results

🎙️ "Have a great day!"

Total: ~5 minutes
```

## Quick Start

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/argus.git
cd argus

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your API keys (see Setup Guide below)

# 4. Test with news only (no auth needed)
python main.py --sources news --dry-run

# 5. Full run with audio
python main.py

# 6. Schedule (runs daily at 5 AM)
python main.py --schedule
```

### CLI Options

| Flag | Description | Example |
|------|-------------|---------|
| `--dry-run` | Generate text script only, skip TTS and delivery | `python main.py --dry-run` |
| `--sources` | Run only specific collectors (comma-separated) | `python main.py --sources slack,grafana` |
| `--schedule` | Keep alive and run on daily schedule | `python main.py --schedule` |

## Data Sources

### 📨 Slack
Pulls messages from configured channels over the last 24 hours. Tracks thread replies, reactions (used for priority scoring), and mentions.

**Setup:**
1. Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps)
2. Add OAuth scopes: `channels:history`, `channels:read`, `groups:history`, `groups:read`
3. Install to workspace and copy the Bot Token (`xoxb-...`)
4. Invite the bot to channels you want tracked: `/invite @argus`

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNELS=C01ABCDEF,C02GHIJKL
```

### ✉️ Gmail
Fetches unread emails from the last 24 hours. Filters out notifications and automated emails.

**Setup:**
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable Gmail API
3. Create OAuth 2.0 Client ID (Desktop app) under Credentials
4. Download `credentials.json` to the project root
5. First run opens a browser for OAuth consent (saves `token.json` for future runs)

### 🎫 Jira
Tracks ticket updates, status changes, new issues, and recent comments using JQL queries.

**Setup:**
1. Generate an API token at [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)

```env
JIRA_SERVER_URL=https://your-org.atlassian.net
JIRA_EMAIL=you@company.com
JIRA_API_TOKEN=your-api-token
JIRA_PROJECTS=MLFP,ADS
```

### 📊 Grafana
The most powerful collector. Pulls data from 4 layers:

| Layer | What it does | Priority |
|-------|-------------|----------|
| **Alerts** | Overnight alerts (firing/resolved), severity mapping | 🔴 High |
| **Annotations** | Deploy markers, incidents, manual annotations | 🟡 Medium |
| **Dashboard panels** | Queries actual datasources behind panels, computes stats + z-score anomaly detection | 🟡 Medium |
| **Custom queries** | Your own Prometheus/InfluxDB/CloudWatch queries | Configurable |

**Setup:**
1. Create a Service Account in Grafana (Admin → Service Accounts)
2. Assign **Viewer** role
3. Generate a token

```env
GRAFANA_URL=https://your-grafana.grafana.net
GRAFANA_API_KEY=glsa_...
GRAFANA_DASHBOARD_UIDS=abc123,def456
```

**Custom queries** (optional, in `digest_config.json`):
```json
{
  "grafana_custom_queries": [
    {
      "datasource_uid": "prometheus-uid",
      "query": "rate(http_errors_total{service='bidding'}[1h])",
      "name": "Bidding Service Error Rate"
    },
    {
      "datasource_uid": "prometheus-uid",
      "query": "histogram_quantile(0.99, rate(request_duration_seconds_bucket[5m]))",
      "name": "P99 Latency"
    }
  ]
}
```

Supports: Prometheus, InfluxDB, CloudWatch, and any SQL-based datasource.

### 📰 News & ⚽ Sports
RSS feeds for news, free APIs for sports scores.

**Default feeds** (configurable): Ynet, TechCrunch, Hacker News, Calcalist Tech

**Sports APIs:**
- [football-data.org](https://www.football-data.org/client/register) - Premier League, Champions League (free tier)
- [balldontlie.io](https://www.balldontlie.io/) - NBA scores

## LLM & TTS

### LLM Provider

| Provider | Model | Env Var |
|----------|-------|---------|
| **Anthropic** (default) | Claude Sonnet 4.5 | `ANTHROPIC_API_KEY` |
| OpenAI | GPT-4o | `OPENAI_API_KEY` |

### TTS Provider

| Provider | Quality | Hebrew Support | Cost |
|----------|---------|---------------|------|
| **OpenAI TTS** (default) | Good | Good | ~$0.015/min |
| ElevenLabs | Excellent | Excellent (multilingual v2) | ~$0.03/min |

## Delivery

### Telegram (recommended)
1. Message [@BotFather](https://t.me/botfather) → `/newbot`
2. Copy the bot token
3. Start a chat with your bot, then get your chat ID:
   ```
   curl https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

```env
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

### Local
Audio files are always saved to `./output/` regardless of other delivery methods:
```
output/
├── digest_2026-02-13.mp3    # Audio file
└── digest_2026-02-13.txt    # Script text
```

## Advanced Configuration

Create a `digest_config.json` for fine-tuning:

```json
{
  "user_name": "Raja",
  "podcast_max_duration_minutes": 5,
  "slack_channels": ["C01ABCDEF", "C02GHIJKL"],
  "jira_project_keys": ["MLFP", "ADS"],
  "sports_interests": ["Premier League", "Champions League", "NBA"],
  "rss_feeds": [
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "category": "tech"},
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage", "category": "tech"}
  ]
}
```

## Deployment

### Crontab (simplest)
```bash
crontab -e
# 5 AM daily (Israel time)
0 5 * * * cd /path/to/argus && python main.py >> /var/log/argus.log 2>&1
```

### Docker
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY *.py .
CMD ["python", "main.py"]
```

### AWS Lambda + EventBridge
```bash
pip install -r requirements.txt -t package/
cp *.py package/
cd package && zip -r ../argus-lambda.zip .
# EventBridge: cron(0 2 * * ? *)  ← 2 AM UTC = 5 AM Israel
```

## Project Structure

```
argus/
├── main.py              # CLI entry point & orchestration
├── config.py            # All configuration dataclasses
├── collectors.py        # 5 async data collectors (Slack, Gmail, Jira, Grafana, News)
├── llm_processor.py     # 2-pass LLM: source summaries → podcast script
├── delivery.py          # TTS engine + Telegram delivery
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
├── digest_config.json   # Optional overrides
└── output/              # Generated podcasts (auto-created)
```

## Privacy Note

ARGUS sends data from Slack, Gmail, Jira, and Grafana to an LLM API for summarization. Options:

1. **Accept the risk** - Use Claude/GPT API directly (recommended for POC)
2. **Redaction layer** - Strip names and sensitive data before sending
3. **Local LLM** - Run Ollama + Llama locally (lower quality, fully private)

## Inspiration

This project was inspired by [Amit Ben Dor](https://www.linkedin.com/in/amitbendor/)'s podcast, where he described building a competitive intelligence agent that monitors your competitors overnight. ARGUS takes that idea further — why just watch competitors when you can watch everything?

## License

MIT

---

*ARGUS never sleeps. You should.* 👁️
