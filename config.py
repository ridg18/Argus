"""
Morning Digest Agent - Configuration
=====================================
Set your API keys and preferences here.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SlackConfig:
    bot_token: str = ""  # xoxb-... from Slack App
    channels: list[str] = field(default_factory=lambda: [
        # Add channel IDs you want to track
        # "C01ABCDEF",  # #general
        # "C02GHIJKL",  # #engineering
    ])
    include_threads: bool = True
    min_reactions: int = 2  # highlight messages with 2+ reactions


@dataclass
class GmailConfig:
    credentials_path: str = "credentials.json"  # OAuth2 credentials from Google Cloud
    token_path: str = "token.json"
    max_emails: int = 20
    # Labels to include (None = INBOX only)
    labels: list[str] = field(default_factory=lambda: ["INBOX"])
    # Skip emails matching these senders
    skip_senders: list[str] = field(default_factory=lambda: [
        "noreply@",
        "no-reply@",
        "notifications@",
    ])


@dataclass
class JiraConfig:
    server_url: str = ""  # https://your-org.atlassian.net
    email: str = ""
    api_token: str = ""  # from https://id.atlassian.com/manage-profile/security/api-tokens
    # JQL filter for relevant issues
    project_keys: list[str] = field(default_factory=lambda: [
        # "MLFP", "ADS"
    ])


@dataclass
class NewsConfig:
    rss_feeds: list[dict] = field(default_factory=lambda: [
        {"name": "Ynet", "url": "https://www.ynet.co.il/Integration/StoryRss1854.xml", "category": "news"},
        {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "category": "tech"},
        {"name": "Hacker News", "url": "https://hnrss.org/frontpage", "category": "tech"},
        {"name": "Calcalist Tech", "url": "https://www.calcalist.co.il/GeneralRSS/0,16335,L-4,00.xml", "category": "tech_il"},
    ])
    sports_interests: list[str] = field(default_factory=lambda: [
        "Premier League",
        "Champions League",
        "NBA",
    ])
    max_news_items: int = 15
    max_sports_items: int = 10


@dataclass
class GrafanaConfig:
    base_url: str = ""  # https://your-grafana.com or http://localhost:3000
    api_key: str = ""  # Service Account token (sa-...) or API key
    # Dashboard UIDs to check for panel data / anomalies
    dashboard_uids: list[str] = field(default_factory=lambda: [
        # "abc123",  # Infrastructure Overview
        # "def456",  # ML Pipeline Metrics
    ])
    # Also pull alerts (recommended - this is the easiest win)
    include_alerts: bool = True
    # Also pull annotation events (deploys, incidents, etc.)
    include_annotations: bool = True
    # Query panel data and detect anomalies via LLM
    include_panel_analysis: bool = True
    # Datasource UIDs to query directly (optional, advanced)
    # e.g., Prometheus, InfluxDB, CloudWatch
    datasource_uids: list[str] = field(default_factory=list)
    # Specific queries to run against datasources (optional)
    # Format: {"datasource_uid": "xxx", "query": "rate(http_errors_total[1h])", "name": "Error Rate"}
    custom_queries: list[dict] = field(default_factory=list)


@dataclass
class LLMConfig:
    provider: str = "anthropic"  # "anthropic" or "openai"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    model: str = "claude-sonnet-4-5-20250514"
    summary_language: str = "hebrew"  # "hebrew" or "english"


@dataclass
class TTSConfig:
    provider: str = "openai"  # "openai" or "elevenlabs"
    openai_api_key: str = ""  # reuses LLMConfig key if empty
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    # OpenAI TTS settings
    openai_voice: str = "onyx"  # alloy, echo, fable, onyx, nova, shimmer
    openai_model: str = "tts-1"  # tts-1 or tts-1-hd


@dataclass
class DeliveryConfig:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Also save locally
    output_dir: str = "./output"


@dataclass
class Config:
    slack: SlackConfig = field(default_factory=SlackConfig)
    gmail: GmailConfig = field(default_factory=GmailConfig)
    jira: JiraConfig = field(default_factory=JiraConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    grafana: GrafanaConfig = field(default_factory=GrafanaConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)

    # Podcast settings
    user_name: str = "רג'א"
    podcast_max_duration_minutes: int = 5
    schedule_hour: int = 5  # 5 AM
    schedule_minute: int = 0
