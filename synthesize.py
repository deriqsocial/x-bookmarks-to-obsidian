#!/usr/bin/env python3
"""
synthesize.py — Weekly narrative synthesis of recent wiki pages.

Reads all wiki pages modified in the last 7 days, sends them to Claude,
and asks for a narrative: what patterns are emerging, what concepts keep
appearing, what connections exist across this week's bookmarks.

Output: vault/03_Outputs/synthesis_YYYY-MM-DD.md

Usage:
  python3 synthesize.py
  python3 synthesize.py --days 14   # look back further
"""

import os
import re
import sys
import glob
from datetime import datetime, timedelta, timezone

try:
    import anthropic as _anthropic
    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False

import urllib.request
import json

VAULT_PATH   = os.getenv("VAULT_PATH", "./vault")
WIKI_DIR     = os.path.join(VAULT_PATH, "02_Wiki")
OUTPUTS_DIR  = os.path.join(VAULT_PATH, "03_Outputs")
ENV_FILE     = "./.env"
MODEL        = "claude-sonnet-4-6"
MAX_CHARS    = 120000   # total content cap sent to Claude


def load_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    try:
        with open(ENV_FILE) as f:
            text = f.read()
        m = re.search(r'ANTHROPIC_API_KEY="?(sk-ant-[^"\s]+)"?', text)
        if m:
            return m.group(1)
    except FileNotFoundError:
        pass
    return ""


def find_recent_wiki_pages(days=7):
    """Return paths of wiki pages modified within the last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    pages = []
    pattern = os.path.join(WIKI_DIR, "**", "*.md")
    for path in glob.glob(pattern, recursive=True):
        try:
            mtime = os.path.getmtime(path)
            if mtime >= cutoff_ts:
                pages.append((mtime, path))
        except OSError:
            continue
    pages.sort(reverse=True)
    return [p for _, p in pages]


def read_page(path):
    """Read a wiki page, stripping YAML frontmatter."""
    try:
        with open(path, errors="replace") as f:
            content = f.read()
        # Strip frontmatter block
        content = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, flags=re.DOTALL)
        return content.strip()
    except OSError:
        return ""


def call_claude(api_key, combined_text, page_count, days):
    system = (
        "You are a reflective thinking partner. "
        "You read a person's curated knowledge notes and write a narrative synthesis — "
        "not a summary list, but a flowing piece that finds the story in the material. "
        "Write in second person ('you'). Be insightful, not generic. "
        "Notice surprising connections. Point to emerging themes the person might not have noticed."
    )

    user = f"""I'm going to share {page_count} wiki pages from my knowledge base — all created or updated in the last {days} days from my X bookmarks.

Read through them and write a synthesis note. Tell me:
- What patterns are emerging across these bookmarks?
- What concepts or themes keep reappearing?
- What unexpected connections exist between topics?
- What does this collection say about what I'm thinking about this week?

Write a narrative (not bullet points). Aim for 300-500 words. Be specific — reference actual concepts and names from the material.

---

{combined_text}"""

    if _HAS_SDK:
        client = _anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=0.7,
        )
        return response.content[0].text
    else:
        data = json.dumps({
            "model": MODEL,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": 0.7,
            "max_tokens": 2048,
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            result = json.loads(r.read().decode())
            return result["content"][0]["text"]


def main():
    days = 7
    if "--days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        except (IndexError, ValueError):
            pass

    api_key = load_api_key()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not found in environment or .env")
        sys.exit(1)

    print(f"Looking for wiki pages modified in the last {days} days...")
    pages = find_recent_wiki_pages(days=days)

    if not pages:
        print(f"No wiki pages found in {WIKI_DIR} modified in the last {days} days.")
        sys.exit(0)

    print(f"Found {len(pages)} page(s). Reading content...")

    sections = []
    total_chars = 0
    included = 0
    for path in pages:
        name = os.path.relpath(path, WIKI_DIR)
        content = read_page(path)
        if not content:
            continue
        chunk = f"### {name}\n\n{content}"
        if total_chars + len(chunk) > MAX_CHARS:
            print(f"  (stopping at {included} pages — content cap reached)")
            break
        sections.append(chunk)
        total_chars += len(chunk)
        included += 1

    if not sections:
        print("No readable content found in recent wiki pages.")
        sys.exit(0)

    print(f"Sending {included} pages ({total_chars:,} chars) to Claude...")

    combined = "\n\n---\n\n".join(sections)
    try:
        synthesis = call_claude(api_key, combined, included, days)
    except Exception as e:
        print(f"ERROR calling Claude: {e}")
        sys.exit(1)

    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    output_path = os.path.join(OUTPUTS_DIR, f"synthesis_{today}.md")

    frontmatter = (
        f"---\n"
        f"title: Weekly Synthesis — {today}\n"
        f"type: synthesis\n"
        f"created: {today}\n"
        f"pages_analyzed: {included}\n"
        f"lookback_days: {days}\n"
        f"---\n\n"
    )

    with open(output_path, "w") as f:
        f.write(frontmatter)
        f.write(f"# Weekly Synthesis — {today}\n\n")
        f.write(synthesis)
        f.write("\n")

    print(f"\nSynthesis written to: {output_path}")


if __name__ == "__main__":
    main()
