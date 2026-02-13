#!/usr/bin/env python3
"""
Morning Digest Agent
=====================
Your personal daily podcast that summarizes everything
that happened yesterday - work, news, and sports.

Usage:
    # Run once (e.g., via cron)
    python main.py

    # Run with schedule (stays alive)
    python main.py --schedule

    # Dry run (text only, no TTS)
    python main.py --dry-run

    # Only specific sources
    python main.py --sources slack,news
"""

import asyncio
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from config import Config
from collectors import (
    ALL_COLLECTORS,
    SlackCollector, GmailCollector, JiraCollector, NewsCollector,
    collect_all, DigestItem
)
from llm_processor import process_items, LLMProcessor
from delivery import TTSEngine, DeliveryManager


def load_config() -> Config:
    """Load config from environment variables or config file."""
    config = Config()

    # Override from environment variables if present
    config.llm.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", config.llm.anthropic_api_key)
    config.llm.openai_api_key = os.getenv("OPENAI_API_KEY", config.llm.openai_api_key)
    config.tts.openai_api_key = os.getenv("OPENAI_API_KEY", config.tts.openai_api_key)
    config.tts.elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", config.tts.elevenlabs_api_key)
    config.slack.bot_token = os.getenv("SLACK_BOT_TOKEN", config.slack.bot_token)
    config.jira.api_token = os.getenv("JIRA_API_TOKEN", config.jira.api_token)
    config.jira.server_url = os.getenv("JIRA_SERVER_URL", config.jira.server_url)
    config.jira.email = os.getenv("JIRA_EMAIL", config.jira.email)
    config.delivery.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", config.delivery.telegram_bot_token)
    config.delivery.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", config.delivery.telegram_chat_id)

    # Grafana
    config.grafana.base_url = os.getenv("GRAFANA_URL", config.grafana.base_url)
    config.grafana.api_key = os.getenv("GRAFANA_API_KEY", config.grafana.api_key)
    grafana_dashboards = os.getenv("GRAFANA_DASHBOARD_UIDS", "")
    if grafana_dashboards:
        config.grafana.dashboard_uids = [d.strip() for d in grafana_dashboards.split(",")]

    # Load Slack channels from env (comma-separated)
    slack_channels = os.getenv("SLACK_CHANNELS", "")
    if slack_channels:
        config.slack.channels = [c.strip() for c in slack_channels.split(",")]

    # Load Jira projects from env (comma-separated)
    jira_projects = os.getenv("JIRA_PROJECTS", "")
    if jira_projects:
        config.jira.project_keys = [p.strip() for p in jira_projects.split(",")]

    # Config file override (optional)
    config_path = Path("digest_config.json")
    if config_path.exists():
        with open(config_path) as f:
            overrides = json.load(f)
            # Apply overrides (simplified - in production use proper merging)
            if "user_name" in overrides:
                config.user_name = overrides["user_name"]
            if "podcast_max_duration_minutes" in overrides:
                config.podcast_max_duration_minutes = overrides["podcast_max_duration_minutes"]
            if "slack_channels" in overrides:
                config.slack.channels = overrides["slack_channels"]
            if "jira_project_keys" in overrides:
                config.jira.project_keys = overrides["jira_project_keys"]
            if "rss_feeds" in overrides:
                config.news.rss_feeds = overrides["rss_feeds"]
            if "sports_interests" in overrides:
                config.news.sports_interests = overrides["sports_interests"]

    return config


async def run_digest(config: Config, sources: list[str] | None = None, dry_run: bool = False):
    """Main digest pipeline."""
    start_time = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"🎙️  Morning Digest - {start_time.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    # Step 1: Collect data
    print("📥 Step 1: Collecting data...")
    if sources:
        # Only run specified collectors
        filtered = {k: v for k, v in ALL_COLLECTORS.items() if k in sources}
        collectors = [cls(config) for cls in filtered.values()]
        results = await asyncio.gather(
            *[c.collect() for c in collectors],
            return_exceptions=True
        )
        items = []
        for result in results:
            if isinstance(result, Exception):
                print(f"  Error: {result}")
                continue
            items.extend(result)
        items.sort(key=lambda x: (x.priority, x.timestamp), reverse=True)
    else:
        items = await collect_all(config)

    print(f"\n📊 Total items collected: {len(items)}")
    for source in set(item.source for item in items):
        count = sum(1 for item in items if item.source == source)
        print(f"   {source}: {count} items")

    if not items:
        print("\n⚠️  No items collected. Check your configuration.")
        return

    # Step 2: LLM Processing
    print("\n🧠 Step 2: Processing with LLM...")
    script = process_items(config, items)

    print(f"\n📜 Script preview:\n{'─'*40}")
    # Show first 500 chars
    preview = script.full_script[:500]
    print(preview)
    if len(script.full_script) > 500:
        print(f"... ({len(script.full_script)} chars total)")
    print(f"{'─'*40}")
    print(f"⏱️  Estimated duration: ~{script.estimated_duration_seconds // 60}:{script.estimated_duration_seconds % 60:02d}")

    if dry_run:
        # Save script only
        output_dir = Path(config.delivery.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        script_path = output_dir / f"digest_{start_time.strftime('%Y-%m-%d')}.txt"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script.full_script)
        print(f"\n📝 Dry run - script saved to: {script_path}")
        return

    # Step 3: Generate Audio
    print("\n🔊 Step 3: Generating audio...")
    tts = TTSEngine(config)
    audio_path = await tts.generate_audio(script)

    # Step 4: Deliver
    print("\n📤 Step 4: Delivering...")
    delivery = DeliveryManager(config)
    await delivery.deliver(audio_path, script)

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    print(f"\n✅ Done in {elapsed:.1f}s")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Morning Digest Agent")
    parser.add_argument("--schedule", action="store_true", help="Run on schedule (stays alive)")
    parser.add_argument("--dry-run", action="store_true", help="Text only, no TTS/delivery")
    parser.add_argument("--sources", type=str, help="Comma-separated sources: slack,gmail,jira,news")
    args = parser.parse_args()

    config = load_config()

    sources = None
    if args.sources:
        sources = [s.strip() for s in args.sources.split(",")]

    if args.schedule:
        import schedule
        import time

        hour = config.schedule_hour
        minute = config.schedule_minute
        schedule_time = f"{hour:02d}:{minute:02d}"

        print(f"⏰ Scheduled to run daily at {schedule_time}")
        schedule.every().day.at(schedule_time).do(
            lambda: asyncio.run(run_digest(config, sources, args.dry_run))
        )

        while True:
            schedule.run_pending()
            time.sleep(60)
    else:
        asyncio.run(run_digest(config, sources, args.dry_run))


if __name__ == "__main__":
    main()
