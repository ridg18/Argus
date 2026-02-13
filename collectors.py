"""
Morning Digest Agent - Source Collectors
=========================================
Each collector fetches data from the last 24 hours and returns
a standardized list of items.
"""

import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional
from abc import ABC, abstractmethod

from config import Config


@dataclass
class DigestItem:
    """A single item from any source."""
    source: str          # "slack", "gmail", "jira", "news", "sports"
    category: str        # e.g., "channel:#engineering", "inbox", "ticket", "tech", "premier_league"
    title: str
    body: str
    timestamp: datetime
    priority: int = 0    # higher = more important
    url: Optional[str] = None
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseCollector(ABC):
    def __init__(self, config: Config):
        self.config = config
        self.since = datetime.now(timezone.utc) - timedelta(hours=24)

    @abstractmethod
    async def collect(self) -> list[DigestItem]:
        pass


# =============================================================
# SLACK COLLECTOR
# =============================================================
class SlackCollector(BaseCollector):
    """Collects messages from configured Slack channels."""

    async def collect(self) -> list[DigestItem]:
        items = []
        if not self.config.slack.bot_token or not self.config.slack.channels:
            print("[Slack] Skipping - not configured")
            return items

        headers = {"Authorization": f"Bearer {self.config.slack.bot_token}"}
        oldest = str(self.since.timestamp())

        async with aiohttp.ClientSession(headers=headers) as session:
            for channel_id in self.config.slack.channels:
                try:
                    channel_items = await self._fetch_channel(session, channel_id, oldest)
                    items.extend(channel_items)
                except Exception as e:
                    print(f"[Slack] Error fetching channel {channel_id}: {e}")

        print(f"[Slack] Collected {len(items)} messages")
        return items

    async def _fetch_channel(self, session, channel_id, oldest) -> list[DigestItem]:
        items = []

        # Get channel info
        async with session.get(
            "https://slack.com/api/conversations.info",
            params={"channel": channel_id}
        ) as resp:
            data = await resp.json()
            channel_name = data.get("channel", {}).get("name", channel_id)

        # Get messages
        async with session.get(
            "https://slack.com/api/conversations.history",
            params={"channel": channel_id, "oldest": oldest, "limit": 100}
        ) as resp:
            data = await resp.json()

        if not data.get("ok"):
            print(f"[Slack] API error for {channel_id}: {data.get('error')}")
            return items

        for msg in data.get("messages", []):
            if msg.get("subtype") in ("channel_join", "channel_leave", "bot_message"):
                continue

            text = msg.get("text", "")
            if not text.strip():
                continue

            # Calculate priority based on reactions
            reactions = msg.get("reactions", [])
            reaction_count = sum(r.get("count", 0) for r in reactions)
            priority = min(reaction_count, 5)

            # Boost if user is mentioned
            if f"<@{self.config.slack.bot_token}>" in text:
                priority += 3

            ts = float(msg.get("ts", 0))

            # Fetch thread replies if configured
            thread_text = ""
            if self.config.slack.include_threads and msg.get("thread_ts") == msg.get("ts") and msg.get("reply_count", 0) > 0:
                thread_text = await self._fetch_thread(session, channel_id, msg["ts"])

            full_text = text
            if thread_text:
                full_text += f"\n--- Thread ({msg.get('reply_count', 0)} replies) ---\n{thread_text}"

            items.append(DigestItem(
                source="slack",
                category=f"#{channel_name}",
                title=f"Message in #{channel_name}",
                body=full_text[:2000],  # Truncate very long messages
                timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                priority=priority,
                url=f"https://slack.com/archives/{channel_id}/p{msg['ts'].replace('.', '')}",
                metadata={"reactions": reaction_count, "has_thread": bool(thread_text)}
            ))

        return items

    async def _fetch_thread(self, session, channel_id, thread_ts) -> str:
        async with session.get(
            "https://slack.com/api/conversations.replies",
            params={"channel": channel_id, "ts": thread_ts, "limit": 20}
        ) as resp:
            data = await resp.json()

        if not data.get("ok"):
            return ""

        replies = data.get("messages", [])[1:]  # Skip parent message
        return "\n".join(
            f"- {r.get('text', '')[:300]}"
            for r in replies[-10:]  # Last 10 replies
        )


# =============================================================
# GMAIL COLLECTOR
# =============================================================
class GmailCollector(BaseCollector):
    """Collects recent emails from Gmail using OAuth2."""

    async def collect(self) -> list[DigestItem]:
        items = []
        if not self.config.gmail.credentials_path:
            print("[Gmail] Skipping - not configured")
            return items

        try:
            # Gmail API requires synchronous google-auth libraries
            # We run it in a thread executor
            items = await asyncio.get_event_loop().run_in_executor(
                None, self._collect_sync
            )
        except Exception as e:
            print(f"[Gmail] Error: {e}")

        print(f"[Gmail] Collected {len(items)} emails")
        return items

    def _collect_sync(self) -> list[DigestItem]:
        """Synchronous Gmail API calls (google client library is sync)."""
        import os
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
        items = []

        creds = None
        token_path = self.config.gmail.token_path
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.config.gmail.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(token_path, "w") as token:
                token.write(creds.to_json())

        service = build("gmail", "v1", credentials=creds)

        # Query for recent emails
        after_date = self.since.strftime("%Y/%m/%d")
        query = f"after:{after_date} is:unread"

        results = service.users().messages().list(
            userId="me", q=query, maxResults=self.config.gmail.max_emails
        ).execute()

        messages = results.get("messages", [])

        for msg_ref in messages:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            sender = headers.get("From", "")
            subject = headers.get("Subject", "(no subject)")

            # Skip filtered senders
            if any(skip in sender.lower() for skip in self.config.gmail.skip_senders):
                continue

            # Get snippet as body preview
            snippet = msg.get("snippet", "")

            items.append(DigestItem(
                source="gmail",
                category="inbox",
                title=subject,
                body=f"From: {sender}\n{snippet}",
                timestamp=datetime.fromtimestamp(
                    int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc
                ),
                priority=1,
                url=f"https://mail.google.com/mail/u/0/#inbox/{msg_ref['id']}",
                metadata={"from": sender}
            ))

        return items


# =============================================================
# JIRA COLLECTOR
# =============================================================
class JiraCollector(BaseCollector):
    """Collects recent Jira activity."""

    async def collect(self) -> list[DigestItem]:
        items = []
        cfg = self.config.jira
        if not cfg.server_url or not cfg.api_token:
            print("[Jira] Skipping - not configured")
            return items

        auth = aiohttp.BasicAuth(cfg.email, cfg.api_token)
        headers = {"Accept": "application/json"}

        since_str = self.since.strftime("%Y-%m-%d %H:%M")
        project_filter = " OR ".join(f'project = "{p}"' for p in cfg.project_keys)
        jql = (
            f"({project_filter}) AND "
            f"updated >= '{since_str}' "
            f"ORDER BY updated DESC"
        )

        async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
            try:
                async with session.get(
                    f"{cfg.server_url}/rest/api/3/search",
                    params={"jql": jql, "maxResults": 30, "fields": "summary,status,assignee,comment,priority,updated,issuetype"}
                ) as resp:
                    if resp.status != 200:
                        print(f"[Jira] API error: {resp.status}")
                        return items
                    data = await resp.json()

                for issue in data.get("issues", []):
                    fields = issue.get("fields", {})
                    key = issue.get("key", "")
                    summary = fields.get("summary", "")
                    status = fields.get("status", {}).get("name", "")
                    assignee = fields.get("assignee", {})
                    assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
                    issue_type = fields.get("issuetype", {}).get("name", "")
                    priority_name = fields.get("priority", {}).get("name", "Medium")

                    # Get recent comments
                    comments = fields.get("comment", {}).get("comments", [])
                    recent_comments = [
                        c for c in comments
                        if datetime.fromisoformat(c["updated"].replace("Z", "+00:00")) > self.since
                    ]
                    comment_text = "\n".join(
                        f"- {c.get('author', {}).get('displayName', '?')}: {c.get('body', {}).get('content', [{}])[0].get('content', [{}])[0].get('text', '')[:200]}"
                        for c in recent_comments[-5:]
                    )

                    body = f"[{issue_type}] {key}: {summary}\nStatus: {status} | Assignee: {assignee_name}"
                    if comment_text:
                        body += f"\nRecent comments:\n{comment_text}"

                    # Priority mapping
                    priority_map = {"Highest": 5, "High": 4, "Medium": 2, "Low": 1, "Lowest": 0}
                    priority = priority_map.get(priority_name, 2)

                    items.append(DigestItem(
                        source="jira",
                        category=f"ticket:{key}",
                        title=f"{key}: {summary}",
                        body=body,
                        timestamp=datetime.fromisoformat(fields.get("updated", "").replace("Z", "+00:00")),
                        priority=priority,
                        url=f"{cfg.server_url}/browse/{key}",
                        metadata={"status": status, "type": issue_type, "priority": priority_name}
                    ))

            except Exception as e:
                print(f"[Jira] Error: {e}")

        print(f"[Jira] Collected {len(items)} issues")
        return items


# =============================================================
# NEWS & SPORTS COLLECTOR
# =============================================================
class NewsCollector(BaseCollector):
    """Collects news from RSS feeds and sports scores."""

    async def collect(self) -> list[DigestItem]:
        items = []

        async with aiohttp.ClientSession() as session:
            # Fetch RSS feeds in parallel
            tasks = [
                self._fetch_rss(session, feed)
                for feed in self.config.news.rss_feeds
            ]
            # Fetch sports
            tasks.append(self._fetch_sports(session))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    print(f"[News] Error: {result}")
                    continue
                items.extend(result)

        print(f"[News] Collected {len(items)} items")
        return items

    async def _fetch_rss(self, session, feed: dict) -> list[DigestItem]:
        items = []
        try:
            async with session.get(feed["url"], timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return items
                text = await resp.text()

            root = ET.fromstring(text)

            # Handle both RSS 2.0 and Atom formats
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            # RSS 2.0
            for item in root.findall(".//item"):
                title = item.findtext("title", "")
                description = item.findtext("description", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")

                parsed_date = self._parse_date(pub_date)
                if parsed_date and parsed_date < self.since:
                    continue

                items.append(DigestItem(
                    source="news",
                    category=feed.get("category", "general"),
                    title=title.strip(),
                    body=self._strip_html(description)[:500],
                    timestamp=parsed_date or datetime.now(timezone.utc),
                    priority=1,
                    url=link,
                    metadata={"feed": feed["name"]}
                ))

            # Atom format
            for entry in root.findall("atom:entry", ns):
                title = entry.findtext("atom:title", "", ns)
                summary = entry.findtext("atom:summary", "", ns) or entry.findtext("atom:content", "", ns) or ""
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                updated = entry.findtext("atom:updated", "", ns) or entry.findtext("atom:published", "", ns)

                parsed_date = self._parse_date(updated)
                if parsed_date and parsed_date < self.since:
                    continue

                items.append(DigestItem(
                    source="news",
                    category=feed.get("category", "general"),
                    title=title.strip(),
                    body=self._strip_html(summary)[:500],
                    timestamp=parsed_date or datetime.now(timezone.utc),
                    priority=1,
                    url=link,
                    metadata={"feed": feed["name"]}
                ))

        except Exception as e:
            print(f"[News] RSS error for {feed['name']}: {e}")

        return items[:self.config.news.max_news_items]

    async def _fetch_sports(self, session) -> list[DigestItem]:
        """Fetch sports scores from a free API."""
        items = []
        if not self.config.news.sports_interests:
            return items

        try:
            # Using free football-data.org API for football
            # and NBA API for basketball
            for sport in self.config.news.sports_interests:
                if sport in ("Premier League", "Champions League"):
                    sport_items = await self._fetch_football(session, sport)
                    items.extend(sport_items)
                elif sport == "NBA":
                    sport_items = await self._fetch_nba(session)
                    items.extend(sport_items)

        except Exception as e:
            print(f"[Sports] Error: {e}")

        return items[:self.config.news.max_sports_items]

    async def _fetch_football(self, session, competition) -> list[DigestItem]:
        """Fetch football scores from football-data.org (free tier)."""
        items = []
        comp_map = {"Premier League": "PL", "Champions League": "CL"}
        comp_code = comp_map.get(competition, "PL")

        # Note: football-data.org requires a free API key
        # Register at https://www.football-data.org/client/register
        headers = {"X-Auth-Token": "YOUR_FOOTBALL_DATA_API_KEY"}

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            async with session.get(
                f"https://api.football-data.org/v4/competitions/{comp_code}/matches",
                params={"dateFrom": yesterday, "dateTo": today},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return items
                data = await resp.json()

            for match in data.get("matches", []):
                home = match.get("homeTeam", {}).get("shortName", "?")
                away = match.get("awayTeam", {}).get("shortName", "?")
                score = match.get("score", {})
                ft = score.get("fullTime", {})
                home_goals = ft.get("home", "?")
                away_goals = ft.get("away", "?")
                status = match.get("status", "")

                if status == "FINISHED":
                    title = f"⚽ {home} {home_goals}-{away_goals} {away}"
                    body = f"{competition}: {home} {home_goals} - {away_goals} {away} (Full Time)"
                elif status in ("IN_PLAY", "PAUSED"):
                    title = f"⚽ LIVE: {home} {home_goals}-{away_goals} {away}"
                    body = f"{competition}: {home} {home_goals} - {away_goals} {away} (Live)"
                else:
                    continue

                items.append(DigestItem(
                    source="sports",
                    category="football",
                    title=title,
                    body=body,
                    timestamp=datetime.now(timezone.utc),
                    priority=2,
                    metadata={"competition": competition, "status": status}
                ))

        except Exception as e:
            print(f"[Sports] Football error: {e}")

        return items

    async def _fetch_nba(self, session) -> list[DigestItem]:
        """Fetch NBA scores from free balldontlie API."""
        items = []
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            async with session.get(
                "https://api.balldontlie.io/v1/games",
                params={"dates[]": yesterday},
                headers={"Authorization": "YOUR_BALLDONTLIE_API_KEY"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return items
                data = await resp.json()

            for game in data.get("data", []):
                home = game.get("home_team", {}).get("abbreviation", "?")
                away = game.get("visitor_team", {}).get("abbreviation", "?")
                home_score = game.get("home_team_score", 0)
                away_score = game.get("visitor_team_score", 0)

                if game.get("status") == "Final":
                    winner = home if home_score > away_score else away
                    title = f"🏀 {away} {away_score} @ {home} {home_score}"
                    body = f"NBA: {away} {away_score} at {home} {home_score} (Final) - {winner} wins"

                    items.append(DigestItem(
                        source="sports",
                        category="nba",
                        title=title,
                        body=body,
                        timestamp=datetime.now(timezone.utc),
                        priority=2,
                        metadata={"sport": "NBA"}
                    ))

        except Exception as e:
            print(f"[Sports] NBA error: {e}")

        return items

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        """Try to parse various date formats from RSS feeds."""
        if not date_str:
            return None
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    @staticmethod
    def _strip_html(text: str) -> str:
        """Basic HTML tag removal."""
        import re
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"\s+", " ", clean)
        return clean.strip()


# =============================================================
# GRAFANA COLLECTOR
# =============================================================
class GrafanaCollector(BaseCollector):
    """
    Collects overnight data from Grafana:
    1. Alerts that fired (alerting API)
    2. Annotations (deploys, incidents, manual markers)
    3. Dashboard panel data (queries the actual datasources
       behind panels to get time-series data for LLM analysis)
    """

    async def collect(self) -> list[DigestItem]:
        items = []
        cfg = self.config.grafana
        if not cfg.base_url or not cfg.api_key:
            print("[Grafana] Skipping - not configured")
            return items

        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        base = cfg.base_url.rstrip("/")

        async with aiohttp.ClientSession(headers=headers) as session:
            tasks = []

            if cfg.include_alerts:
                tasks.append(self._fetch_alerts(session, base))

            if cfg.include_annotations:
                tasks.append(self._fetch_annotations(session, base))

            if cfg.include_panel_analysis and cfg.dashboard_uids:
                tasks.append(self._fetch_dashboard_panels(session, base))

            if cfg.custom_queries:
                tasks.append(self._run_custom_queries(session, base))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    print(f"[Grafana] Error: {result}")
                    continue
                items.extend(result)

        print(f"[Grafana] Collected {len(items)} items")
        return items

    # ---------------------------------------------------------
    # 1. ALERTS - fired/resolved overnight
    # ---------------------------------------------------------
    async def _fetch_alerts(self, session, base: str) -> list[DigestItem]:
        """Fetch alerts from Grafana Alerting (unified alerting API)."""
        items = []
        since_ms = int(self.since.timestamp() * 1000)

        # Try unified alerting API first (Grafana 9+)
        try:
            # Get alert instances (current state)
            async with session.get(
                f"{base}/api/v1/provisioning/alert-rules",
            ) as resp:
                if resp.status == 200:
                    rules = await resp.json()
                else:
                    rules = []

            # Get alert state history (what fired overnight)
            # Using the Prometheus-compatible alertmanager API
            async with session.get(
                f"{base}/api/alertmanager/grafana/api/v2/alerts",
                params={"silenced": "false", "inhibited": "false"}
            ) as resp:
                if resp.status == 200:
                    alerts = await resp.json()
                else:
                    # Fallback to legacy alerting API
                    alerts = await self._fetch_legacy_alerts(session, base)

            for alert in alerts:
                # Unified alerting format
                labels = alert.get("labels", {})
                annotations = alert.get("annotations", {})
                status = alert.get("status", {})
                state = status.get("state", alert.get("state", "unknown"))

                alert_name = labels.get("alertname", "Unknown Alert")
                summary = annotations.get("summary", annotations.get("description", ""))
                severity = labels.get("severity", "warning")

                # Parse timestamps
                starts_at = alert.get("startsAt", "")
                ends_at = alert.get("endsAt", "")

                try:
                    start_dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00")) if starts_at else None
                except (ValueError, AttributeError):
                    start_dt = None

                # Skip alerts that started before our window and are already resolved
                if start_dt and start_dt < self.since and state == "resolved":
                    continue

                # Priority based on severity
                severity_priority = {
                    "critical": 5,
                    "high": 4,
                    "warning": 3,
                    "info": 1,
                }.get(severity.lower(), 2)

                state_emoji = {
                    "firing": "🔴",
                    "active": "🔴",
                    "resolved": "✅",
                    "normal": "✅",
                    "pending": "🟡",
                }.get(state.lower(), "⚠️")

                body = f"{state_emoji} Alert: {alert_name}\n"
                body += f"State: {state} | Severity: {severity}\n"
                if summary:
                    body += f"Summary: {summary}\n"
                if starts_at:
                    body += f"Started: {starts_at}\n"
                if ends_at and state.lower() in ("resolved", "normal"):
                    body += f"Resolved: {ends_at}\n"

                # Add relevant labels
                skip_labels = {"alertname", "severity", "__alert_rule_uid__"}
                extra_labels = {k: v for k, v in labels.items() if k not in skip_labels}
                if extra_labels:
                    body += f"Labels: {', '.join(f'{k}={v}' for k, v in extra_labels.items())}\n"

                items.append(DigestItem(
                    source="grafana",
                    category=f"alert:{severity}",
                    title=f"{state_emoji} {alert_name} [{state}]",
                    body=body,
                    timestamp=start_dt or datetime.now(timezone.utc),
                    priority=severity_priority,
                    url=f"{self.config.grafana.base_url}/alerting/list",
                    metadata={
                        "type": "alert",
                        "state": state,
                        "severity": severity,
                        "labels": labels,
                    }
                ))

        except Exception as e:
            print(f"[Grafana] Alerts error: {e}")

        return items

    async def _fetch_legacy_alerts(self, session, base: str) -> list:
        """Fallback for Grafana <9 legacy alerting."""
        async with session.get(
            f"{base}/api/alerts",
            params={"state": "all"}
        ) as resp:
            if resp.status != 200:
                return []
            alerts = await resp.json()

        # Convert legacy format to unified format
        converted = []
        for alert in alerts:
            state = alert.get("state", "unknown")
            converted.append({
                "labels": {"alertname": alert.get("name", ""), "severity": "warning"},
                "annotations": {"summary": alert.get("message", "")},
                "status": {"state": "firing" if state == "alerting" else state},
                "startsAt": alert.get("newStateDate", ""),
                "endsAt": "",
            })
        return converted

    # ---------------------------------------------------------
    # 2. ANNOTATIONS - deploys, incidents, manual markers
    # ---------------------------------------------------------
    async def _fetch_annotations(self, session, base: str) -> list[DigestItem]:
        """Fetch annotations (deploy markers, incidents, etc.)."""
        items = []
        since_ms = int(self.since.timestamp() * 1000)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        try:
            async with session.get(
                f"{base}/api/annotations",
                params={"from": since_ms, "to": now_ms, "limit": 50}
            ) as resp:
                if resp.status != 200:
                    return items
                annotations = await resp.json()

            for ann in annotations:
                text = ann.get("text", "")
                tags = ann.get("tags", [])
                created = ann.get("created", 0)
                dashboard_title = ann.get("dashboardTitle", "")

                # Detect type from tags
                is_deploy = any(t in ["deploy", "deployment", "release"] for t in tags)
                is_incident = any(t in ["incident", "outage", "downtime"] for t in tags)

                if is_deploy:
                    category = "deploy"
                    priority = 2
                    emoji = "🚀"
                elif is_incident:
                    category = "incident"
                    priority = 4
                    emoji = "🚨"
                else:
                    category = "annotation"
                    priority = 1
                    emoji = "📌"

                body = f"{emoji} {text}"
                if tags:
                    body += f"\nTags: {', '.join(tags)}"
                if dashboard_title:
                    body += f"\nDashboard: {dashboard_title}"

                items.append(DigestItem(
                    source="grafana",
                    category=f"annotation:{category}",
                    title=f"{emoji} {text[:100]}",
                    body=body,
                    timestamp=datetime.fromtimestamp(created / 1000, tz=timezone.utc) if created else datetime.now(timezone.utc),
                    priority=priority,
                    url=f"{self.config.grafana.base_url}/d/{ann.get('dashboardUID', '')}",
                    metadata={"type": "annotation", "tags": tags}
                ))

        except Exception as e:
            print(f"[Grafana] Annotations error: {e}")

        return items

    # ---------------------------------------------------------
    # 3. DASHBOARD PANEL DATA - query actual metrics
    # ---------------------------------------------------------
    async def _fetch_dashboard_panels(self, session, base: str) -> list[DigestItem]:
        """
        For each configured dashboard, fetch panel definitions,
        then query the underlying datasources to get actual metric
        data from overnight. The LLM will analyze this for anomalies.
        """
        items = []
        since_ms = int(self.since.timestamp() * 1000)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        for uid in self.config.grafana.dashboard_uids:
            try:
                # Get dashboard definition
                async with session.get(f"{base}/api/dashboards/uid/{uid}") as resp:
                    if resp.status != 200:
                        print(f"[Grafana] Dashboard {uid} not found: {resp.status}")
                        continue
                    dash_data = await resp.json()

                dashboard = dash_data.get("dashboard", {})
                dash_title = dashboard.get("title", uid)
                panels = self._extract_panels(dashboard)

                panel_summaries = []

                for panel in panels[:10]:  # Limit panels per dashboard
                    panel_title = panel.get("title", "Untitled")
                    panel_id = panel.get("id")
                    datasource = panel.get("datasource", {})
                    targets = panel.get("targets", [])

                    if not targets:
                        continue

                    # Query panel data via Grafana's query API
                    query_result = await self._query_panel(
                        session, base, datasource, targets, since_ms, now_ms
                    )

                    if query_result:
                        # Compute basic stats for the LLM
                        stats = self._compute_stats(query_result)
                        panel_summaries.append(
                            f"Panel: {panel_title}\n"
                            f"  Stats: {stats}\n"
                            f"  Data points: {query_result.get('data_points', 'N/A')}"
                        )

                if panel_summaries:
                    body = f"Dashboard: {dash_title}\n\n" + "\n\n".join(panel_summaries)

                    items.append(DigestItem(
                        source="grafana",
                        category="metrics",
                        title=f"📊 Dashboard: {dash_title}",
                        body=body[:3000],
                        timestamp=datetime.now(timezone.utc),
                        priority=2,
                        url=f"{self.config.grafana.base_url}/d/{uid}",
                        metadata={
                            "type": "dashboard_metrics",
                            "dashboard_uid": uid,
                            "panel_count": len(panel_summaries),
                        }
                    ))

            except Exception as e:
                print(f"[Grafana] Dashboard {uid} error: {e}")

        return items

    async def _query_panel(self, session, base: str, datasource: dict, targets: list, from_ms: int, to_ms: int) -> dict:
        """Query Grafana's unified query API to get panel data."""
        try:
            # Build query payload for Grafana's ds/query endpoint
            queries = []
            for i, target in enumerate(targets):
                query = {**target}
                # Ensure datasource info is present
                if datasource:
                    if isinstance(datasource, dict):
                        query["datasource"] = datasource
                    else:
                        query["datasource"] = {"type": datasource}
                query["refId"] = target.get("refId", chr(65 + i))  # A, B, C...
                queries.append(query)

            payload = {
                "queries": queries,
                "from": str(from_ms),
                "to": str(to_ms),
            }

            async with session.post(
                f"{base}/api/ds/query",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json()

            # Extract frame data
            results = data.get("results", {})
            all_values = []
            data_points = 0

            for ref_id, result in results.items():
                frames = result.get("frames", [])
                for frame in frames:
                    schema = frame.get("schema", {})
                    frame_data = frame.get("data", {})
                    values = frame_data.get("values", [])
                    if len(values) >= 2:
                        # values[0] = timestamps, values[1+] = metric values
                        for val_series in values[1:]:
                            numeric = [v for v in val_series if v is not None and isinstance(v, (int, float))]
                            all_values.extend(numeric)
                            data_points += len(numeric)

            return {
                "values": all_values,
                "data_points": data_points,
            }

        except Exception as e:
            print(f"[Grafana] Query error: {e}")
            return {}

    def _compute_stats(self, query_result: dict) -> str:
        """Compute basic statistics for LLM to analyze."""
        values = query_result.get("values", [])
        if not values:
            return "No data"

        import statistics
        try:
            min_val = min(values)
            max_val = max(values)
            avg_val = statistics.mean(values)
            stdev_val = statistics.stdev(values) if len(values) > 1 else 0

            # Simple anomaly detection: check if latest values deviate significantly
            recent = values[-10:] if len(values) > 10 else values
            recent_avg = statistics.mean(recent)

            anomaly_note = ""
            if stdev_val > 0:
                z_score = abs(recent_avg - avg_val) / stdev_val
                if z_score > 2:
                    direction = "higher" if recent_avg > avg_val else "lower"
                    anomaly_note = f" ⚠️ Recent values are significantly {direction} than average (z={z_score:.1f})"

            return (
                f"min={min_val:.2f}, max={max_val:.2f}, avg={avg_val:.2f}, "
                f"stdev={stdev_val:.2f}, latest_avg={recent_avg:.2f}"
                f"{anomaly_note}"
            )
        except Exception:
            return f"Data points: {len(values)}"

    def _extract_panels(self, dashboard: dict) -> list[dict]:
        """Recursively extract panels from dashboard JSON (handles rows)."""
        panels = []
        for panel in dashboard.get("panels", []):
            if panel.get("type") == "row":
                # Row panels contain nested panels
                panels.extend(panel.get("panels", []))
            elif panel.get("targets"):
                panels.append(panel)
        return panels

    # ---------------------------------------------------------
    # 4. CUSTOM QUERIES - direct datasource queries
    # ---------------------------------------------------------
    async def _run_custom_queries(self, session, base: str) -> list[DigestItem]:
        """Run user-defined queries against specific datasources."""
        items = []
        since_ms = int(self.since.timestamp() * 1000)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        for query_def in self.config.grafana.custom_queries:
            try:
                ds_uid = query_def.get("datasource_uid", "")
                query_expr = query_def.get("query", "")
                query_name = query_def.get("name", query_expr[:50])

                if not ds_uid or not query_expr:
                    continue

                # Get datasource info
                async with session.get(f"{base}/api/datasources/uid/{ds_uid}") as resp:
                    if resp.status != 200:
                        continue
                    ds_info = await resp.json()

                ds_type = ds_info.get("type", "")

                # Build query based on datasource type
                target = {"refId": "A"}
                if ds_type == "prometheus":
                    target["expr"] = query_expr
                    target["range"] = True
                    target["instant"] = False
                elif ds_type == "influxdb":
                    target["query"] = query_expr
                elif ds_type == "cloudwatch":
                    target.update(query_def.get("cloudwatch_params", {}))
                else:
                    target["rawSql"] = query_expr  # Generic SQL-based

                target["datasource"] = {"uid": ds_uid, "type": ds_type}

                result = await self._query_panel(
                    session, base,
                    {"uid": ds_uid, "type": ds_type},
                    [target], since_ms, now_ms
                )

                if result and result.get("values"):
                    stats = self._compute_stats(result)
                    body = f"Query: {query_name}\nExpression: {query_expr}\nStats: {stats}"

                    items.append(DigestItem(
                        source="grafana",
                        category="custom_query",
                        title=f"📈 {query_name}",
                        body=body,
                        timestamp=datetime.now(timezone.utc),
                        priority=2,
                        url=f"{self.config.grafana.base_url}/explore",
                        metadata={
                            "type": "custom_query",
                            "datasource": ds_type,
                            "query": query_expr,
                        }
                    ))

            except Exception as e:
                print(f"[Grafana] Custom query error: {e}")

        return items


# =============================================================
# COLLECTOR REGISTRY
# =============================================================
ALL_COLLECTORS = {
    "slack": SlackCollector,
    "gmail": GmailCollector,
    "jira": JiraCollector,
    "news": NewsCollector,
    "grafana": GrafanaCollector,
}


async def collect_all(config: Config) -> list[DigestItem]:
    """Run all collectors in parallel and return combined items."""
    collectors = [cls(config) for cls in ALL_COLLECTORS.values()]
    results = await asyncio.gather(
        *[c.collect() for c in collectors],
        return_exceptions=True
    )

    all_items = []
    for result in results:
        if isinstance(result, Exception):
            print(f"Collector error: {result}")
            continue
        all_items.extend(result)

    # Sort by priority (desc) then timestamp (desc)
    all_items.sort(key=lambda x: (x.priority, x.timestamp), reverse=True)
    return all_items
