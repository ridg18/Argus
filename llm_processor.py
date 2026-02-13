"""
Morning Digest Agent - LLM Processor
======================================
Two-pass LLM processing:
  Pass 1: Summarize each source separately
  Pass 2: Create a podcast script from all summaries
"""

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from config import Config
from collectors import DigestItem


@dataclass
class SourceSummary:
    source: str
    summary: str
    item_count: int
    top_items: list[str]


@dataclass
class PodcastScript:
    title: str
    full_script: str
    sections: list[dict]
    estimated_duration_seconds: int


class LLMProcessor:
    def __init__(self, config: Config):
        self.config = config
        self.client = self._init_client()

    def _init_client(self):
        if self.config.llm.provider == "anthropic":
            import anthropic
            return anthropic.Anthropic(api_key=self.config.llm.anthropic_api_key)
        else:
            import openai
            return openai.OpenAI(api_key=self.config.llm.openai_api_key)

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        if self.config.llm.provider == "anthropic":
            response = self.client.messages.create(
                model=self.config.llm.model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            return response.content[0].text
        else:
            response = self.client.chat.completions.create(
                model=self.config.llm.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=4096
            )
            return response.choices[0].message.content

    # =========================================================
    # PASS 1: Per-source summaries
    # =========================================================
    def summarize_source(self, source_name: str, items: list[DigestItem]) -> SourceSummary:
        """Summarize items from a single source."""
        if not items:
            return SourceSummary(
                source=source_name,
                summary="אין עדכונים.",
                item_count=0,
                top_items=[]
            )

        items_text = "\n\n".join(
            f"[{i+1}] {item.title}\n{item.body}\nPriority: {item.priority}"
            for i, item in enumerate(items[:30])  # Limit to avoid token overflow
        )

        system_prompt = """You are a smart assistant summarizing daily updates.
Summarize the following items concisely in Hebrew.
Focus on: decisions made, action items, blockers, important updates.
Skip: routine messages, automated notifications, trivial updates.
Be direct and concise - no fluff.

For Grafana/monitoring data specifically:
- Highlight any alerts that fired, especially critical ones
- Note any deployments or incidents from annotations
- For metric data, focus on anomalies and significant changes
- If z-scores indicate anomalies, explain what they mean in plain language
- Mention if systems seem healthy (no alerts = good news worth noting)"""

        user_prompt = f"""Source: {source_name}
Number of items: {len(items)}
Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

Items:
{items_text}

Provide a concise Hebrew summary (3-5 bullet points max). Highlight the most important items."""

        summary = self._call_llm(system_prompt, user_prompt)

        return SourceSummary(
            source=source_name,
            summary=summary,
            item_count=len(items),
            top_items=[item.title for item in items[:5]]
        )

    def summarize_all_sources(self, items: list[DigestItem]) -> list[SourceSummary]:
        """Group items by source and summarize each."""
        grouped: dict[str, list[DigestItem]] = {}
        for item in items:
            key = item.source
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(item)

        summaries = []
        for source_name, source_items in grouped.items():
            print(f"[LLM] Summarizing {source_name} ({len(source_items)} items)...")
            summary = self.summarize_source(source_name, source_items)
            summaries.append(summary)

        return summaries

    # =========================================================
    # PASS 2: Podcast script generation
    # =========================================================
    def generate_podcast_script(self, summaries: list[SourceSummary]) -> PodcastScript:
        """Create a natural-sounding podcast script from all summaries."""

        summaries_text = "\n\n".join(
            f"=== {s.source.upper()} ({s.item_count} items) ===\n{s.summary}"
            for s in summaries
        )

        user_name = self.config.user_name
        max_minutes = self.config.podcast_max_duration_minutes

        system_prompt = f"""You are a personal podcast host creating a daily morning briefing.
Your name is "דייג'סט" (Digest).
The listener's name is {user_name}.

STYLE:
- Warm, casual, conversational Hebrew
- Like a smart friend catching you up over coffee
- Use natural spoken Hebrew (not formal/written)
- Short sentences, easy to follow while driving/exercising
- Add brief transitions between topics

STRUCTURE:
1. פתיח (5 sec) - "בוקר טוב {user_name}! הנה מה שקרה אתמול..."
2. דחוף/חשוב (30 sec) - If there are urgent items from Slack/Jira/Gmail/Grafana alerts
3. מוניטורינג (30-45 sec) - Grafana: alerts, deploys, anomalies. If all green, say so briefly
4. עבודה (60-90 sec) - Work updates summary
5. חדשות (45-60 sec) - News highlights
6. ספורט (30-45 sec) - Sports results
7. סגירה (5 sec) - "יום מעולה!" or similar

RULES:
- Total script should be readable in {max_minutes} minutes or less
- About 150 words per minute in Hebrew speech
- Total: ~{max_minutes * 150} words maximum
- Skip any section if there's nothing interesting
- Prioritize actionable/important items
- Don't read URLs
- If there are items that need attention, mention them first
- For sports, be enthusiastic but brief

OUTPUT: Write the complete script as spoken text. No stage directions, no brackets, no formatting marks.
Just the words to be spoken, flowing naturally from one topic to the next."""

        user_prompt = f"""Here are today's summaries to turn into a podcast script:

{summaries_text}

Date: {datetime.now(timezone.utc).strftime('%A, %B %d, %Y')}

Generate the complete podcast script in Hebrew."""

        script_text = self._call_llm(system_prompt, user_prompt)

        # Estimate duration (roughly 150 words/min for Hebrew)
        word_count = len(script_text.split())
        estimated_seconds = int((word_count / 150) * 60)

        return PodcastScript(
            title=f"Morning Digest - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            full_script=script_text,
            sections=[],  # Could parse sections if needed
            estimated_duration_seconds=estimated_seconds
        )


# =============================================================
# CONVENIENCE FUNCTION
# =============================================================
def process_items(config: Config, items: list[DigestItem]) -> PodcastScript:
    """Full pipeline: items -> summaries -> podcast script."""
    processor = LLMProcessor(config)

    print("[LLM] Pass 1: Summarizing sources...")
    summaries = processor.summarize_all_sources(items)

    print("[LLM] Pass 2: Generating podcast script...")
    script = processor.generate_podcast_script(summaries)

    print(f"[LLM] Script generated: ~{script.estimated_duration_seconds}s estimated duration")
    return script
