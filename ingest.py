#!/usr/bin/env python3
"""
Knowledge Base Ingest Pipeline
Single-stage: Python metadata extraction → Claude Sonnet compilation

Stage 1 (local, instant): parse frontmatter + structure from each .md file
Stage 2 (Claude Sonnet):  compile into 02_Wiki pages with wikilinks
"""

import os
import sys
import re
import json
import glob
import shutil
import urllib.request
import urllib.error
from datetime import datetime

try:
    import anthropic as _anthropic
    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False


BASE_DIR      = os.getenv("VAULT_PATH", "./vault")
RAW_DIR       = os.path.join(BASE_DIR, "01_Raw")
PROCESSED_DIR = os.path.join(RAW_DIR, "processed")
WIKI_DIR      = os.path.join(BASE_DIR, "02_Wiki")
OUTPUTS_DIR   = os.path.join(BASE_DIR, "03_Outputs")
INDEX_FILE    = os.path.join(BASE_DIR, "index.md")
LOG_FILE      = os.path.join(BASE_DIR, "log.md")
TRACKING_CSV  = os.path.join(BASE_DIR, "pipeline_tracking.csv")
ENV_FILE      = "./.env"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
SONNET_MODEL  = "claude-sonnet-4-6"
OPUS_MODEL    = "claude-opus-4-6"
SIZE_THRESHOLD_FOR_OPUS = 50000  # Use Opus for files > 50KB
CHUNK_SIZE    = 30000            # Max chars per chunk for large file processing


# ─────────────────────────────────────────────
# STAGE 1: Python metadata extraction (no LLM)
# ─────────────────────────────────────────────

def parse_frontmatter(content):
    """
    Parse YAML-ish frontmatter from --- ... --- block.
    Returns a dict of key: value pairs (values are strings).
    """
    fm = {}
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if not m:
        return fm
    for line in m.group(1).splitlines():
        if ':' in line:
            k, _, v = line.partition(':')
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def extract_h1(content):
    """First # heading in the document."""
    for line in content.splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    return ""


def detect_source_type(filename, frontmatter):
    """Infer source type from filename prefix or frontmatter source field."""
    src = frontmatter.get("source", "")
    if src:
        mapping = {
            "twitter_bookmarks": "tweet",
            "tweet":             "tweet",
            "gmail_to_wiki":     "email",
            "telegram":          "telegram",
            "youtube":           "video",
            "web_clip":          "article",
            "rss":               "newsletter",
        }
        for k, v in mapping.items():
            if k in src:
                return v

    fname = os.path.basename(filename).lower()
    if fname.startswith("tw_") or fname.startswith("tweet_"):
        return "tweet"
    if fname.startswith("email_") or fname.startswith("gmail_"):
        return "email"
    if fname.startswith("tg_"):
        return "telegram"
    if fname.startswith("yt_"):
        return "video"
    if fname.startswith("clip_"):
        return "article"
    return "other"


def extract_metadata(filepath, content):
    """
    Fast, deterministic metadata extraction — no LLM needed.
    Returns a structured dictionary of metadata.
    """
    filename = os.path.basename(filepath)
    fm = parse_frontmatter(content)

    title = fm.get("title") or extract_h1(content) or filename.replace("_", " ").split(".")[0]
    date  = fm.get("date") or fm.get("created") or datetime.now().strftime("%Y-%m-%d")
    source_type = detect_source_type(filename, fm)

    # Word count (strip frontmatter block first)
    body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, flags=re.DOTALL)
    word_count = len(body.split())

    # Pull any tags already in frontmatter
    tags_raw = fm.get("tags", "")
    if isinstance(tags_raw, str):
        tags = [t.strip().strip('"') for t in re.split(r'[,\[\]]', tags_raw) if t.strip().strip('"')]
    else:
        tags = []

    # Extract likely topic keywords: capitalized words, hashtags, @mentions
    topics = list(dict.fromkeys(
        re.findall(r'(?<!\w)#(\w+)', body)[:5] +
        tags[:5]
    ))[:5]

    # Quick entity hints: @handles, URLs domains, capitalized proper-ish nouns
    handles = re.findall(r'@(\w+)', body)[:3]
    domains = list(dict.fromkeys(
        re.findall(r'https?://(?:www\.)?([a-z0-9\-]+\.[a-z]{2,})', body)
    ))[:3]
    entities = list(dict.fromkeys(handles + domains))[:5]

    return {
        "title":        title,
        "source_type":  source_type,
        "date":         date,
        "key_topics":   topics,
        "key_entities": entities,
        "word_count":   word_count,
        "language":     fm.get("language", "en"),
        "frontmatter":  fm,
    }


# ─────────────────────────────────────────────
# STAGE 2: Claude Sonnet compilation
# ─────────────────────────────────────────────

def compile_with_sonnet(raw_content, metadata, index_content, agents_content, priorities, model=SONNET_MODEL):
    focus = (priorities or "")[:300]
    index_snippet = index_content[:500]

    system = f"""You are a knowledge-base compiler. Convert raw source material into Obsidian wiki pages.

Rules:
- Use [[wikilinks]] for every named concept, person, tool, or entity.
- Every wiki page needs YAML frontmatter: title, type (concept/entity/source), tags, created, updated, sources.
- Be concise — each wiki page should be 150-400 words max.
- Return ONLY a valid JSON object. No preamble, no postamble, no markdown fences.

Current focus areas: {focus}

Existing index entries (excerpt):
{index_snippet}"""

    title  = metadata.get("title", "")[:120]
    stype  = metadata.get("source_type", "other")
    date   = metadata.get("date", "")
    topics = ", ".join(metadata.get("key_topics", []))

    user = f"""Ingest this source and return wiki pages.

Source: {stype} | Date: {date} | Title: {title}
Topics: {topics}

Content:
{raw_content[:150000]}

Return JSON:
{{"log_entry":"## [{date}] ingest | {title[:60]}","index_updates":"","wiki_files":[{{"path":"type/slug.md","mode":"create","content":"---\ntitle: ...\ntype: ...\ntags: []\ncreated: {date}\nupdated: {date}\nsources: []\n---\n\n# Title\n\nContent with [[wikilinks]]."}}]}}"""

    # Load API key
    try:
        env_text = open(ENV_FILE).read()
        m = re.search(r'ANTHROPIC_API_KEY="?(sk-ant-[^"\s]+)"?', env_text)
        api_key = m.group(1) if m else os.environ.get("ANTHROPIC_API_KEY", "")
    except Exception:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    try:
        if _HAS_SDK:
            client = _anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=system,
                messages=[{"role": "user", "content": user}],
                temperature=0.3,
            )
            content_str = response.content[0].text
        else:
            data = json.dumps({
                "model": model,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "temperature": 0.3,
                "max_tokens": 8192,
            }).encode()
            req = urllib.request.Request(
                ANTHROPIC_API_URL, data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                }
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                result = json.loads(r.read().decode())
                content_str = result["content"][0]["text"]

        # Bulletproof JSON extraction
        start_idx = content_str.find('{')
        end_idx = content_str.rfind('}')
        if start_idx != -1 and end_idx != -1:
            content_str = content_str[start_idx:end_idx+1]
        return json.loads(content_str)

    except json.JSONDecodeError as e:
        print(f"[Sonnet] JSON parse failed: {e}")
        print(f"[Sonnet] Raw (first 300): {repr(content_str[:300])}")
        return None
    except Exception as e:
        print(f"[Sonnet] Failed: {type(e).__name__}: {e}")
        return None


# ─────────────────────────────────────────────
# CHUNKED COMPILATION (large files)
# ─────────────────────────────────────────────

def split_into_chunks(text, chunk_size=CHUNK_SIZE):
    """Split text into chunks at paragraph boundaries."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Try to break at a paragraph boundary
        boundary = text.rfind('\n\n', start, end)
        if boundary == -1 or boundary <= start:
            boundary = text.rfind('\n', start, end)
        if boundary == -1 or boundary <= start:
            boundary = end
        chunks.append(text[start:boundary])
        start = boundary
    return [c.strip() for c in chunks if c.strip()]


def merge_compiled_results(results):
    """Merge multiple compiled JSON results, deduplicating wiki pages by path."""
    merged = {"log_entry": "", "index_updates": "", "wiki_files": []}
    seen_paths = {}  # path -> index in wiki_files list
    for result in results:
        if not result:
            continue
        if result.get("log_entry"):
            merged["log_entry"] = merged["log_entry"] or result["log_entry"]
        if result.get("index_updates"):
            merged["index_updates"] += "\n" + result["index_updates"]
        for wf in result.get("wiki_files", []):
            path = wf.get("path", "")
            if not path:
                continue
            if path in seen_paths:
                # Append additional content to existing page
                idx = seen_paths[path]
                existing = merged["wiki_files"][idx]
                # Strip frontmatter from additional content and append
                extra = wf.get("content", "")
                body_start = extra.find('\n---\n', 3)
                if body_start != -1:
                    extra = extra[body_start + 5:]
                existing["content"] += "\n\n" + extra.strip()
            else:
                seen_paths[path] = len(merged["wiki_files"])
                merged["wiki_files"].append(wf)
    return merged if merged["wiki_files"] else None


def compile_large_file(raw_content, metadata, index_content, agents_content, priorities, model=OPUS_MODEL):
    """Process a large file by chunking and merging results."""
    chunks = split_into_chunks(raw_content)
    print(f"  → split into {len(chunks)} chunks for processing...")

    results = []
    title = metadata.get("title", "")
    date = metadata.get("date", "")

    for i, chunk in enumerate(chunks):
        print(f"  → chunk {i+1}/{len(chunks)} ({len(chunk):,} chars)...")
        # For chunks after the first, tell Claude this is a continuation
        chunk_meta = dict(metadata)
        if i > 0:
            chunk_meta["title"] = f"{title} (part {i+1})"
        result = compile_with_sonnet(chunk, chunk_meta, index_content, agents_content, priorities, model=model)
        if result:
            results.append(result)
        else:
            print(f"  x chunk {i+1} failed")

    if not results:
        return None

    merged = merge_compiled_results(results)
    # Restore correct log entry
    if merged:
        merged["log_entry"] = f"## [{date}] ingest (chunked) | {title[:60]}"
    return merged


# ─────────────────────────────────────────────
# FILE WRITER
# ─────────────────────────────────────────────

def write_wiki_files(compiled):
    for wfile in compiled.get("wiki_files", []):
        path = wfile.get("path", "")
        if not path:
            continue
        full_path = os.path.join(WIKI_DIR, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        mode = wfile.get("mode", "create")
        if mode == "append" and os.path.exists(full_path):
            with open(full_path, "a") as f:
                f.write("\n\n---\n\n" + wfile["content"])
        else:
            with open(full_path, "w") as f:
                f.write(wfile["content"])
        print(f"  → wrote {path}")

    idx = compiled.get("index_updates", "").strip()
    if idx:
        with open(INDEX_FILE, "a") as f:
            f.write("\n" + idx)
        print("  → updated index.md")

    log = compiled.get("log_entry", "").strip()
    if log:
        with open(LOG_FILE, "a") as f:
            f.write("\n" + log)


# ─────────────────────────────────────────────
# PER-FILE PROCESSOR
# ─────────────────────────────────────────────

def process_file(filepath, priorities, index_content, agents_content):
    filename = os.path.basename(filepath)
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {filename}")

    with open(filepath, "r", errors="replace") as f:
        raw_content = f.read()

    # Stage 1: fast local extraction
    metadata = extract_metadata(filepath, raw_content)
    print(f"  type={metadata['source_type']}  words={metadata['word_count']}  date={metadata['date']}")
    print(f"  title: {metadata['title'][:80]}")

    # Choose model and strategy based on file size
    file_size = len(raw_content)
    if file_size > CHUNK_SIZE:
        model = OPUS_MODEL
        print(f"  → large file ({file_size:,} chars), using chunked Opus processing...")
        compiled = compile_large_file(raw_content, metadata, index_content, agents_content, priorities, model=model)
    elif file_size > SIZE_THRESHOLD_FOR_OPUS:
        model = OPUS_MODEL
        print(f"  → compiling with Claude Opus (file: {file_size:,} chars)...")
        compiled = compile_with_sonnet(raw_content, metadata, index_content, agents_content, priorities, model=model)
    else:
        model = SONNET_MODEL
        print(f"  → compiling with Claude Sonnet...")
        compiled = compile_with_sonnet(raw_content, metadata, index_content, agents_content, priorities, model=model)

    status = "Success" if compiled else "Failed"
    now_date = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")
    with open(TRACKING_CSV, "a") as f:
        f.write(f"{now_date},{now_time},\"{filename}\",{metadata.get('word_count', 0)},\"{metadata.get('source_type', 'other')}\",\"{status}\"\n")

    if not compiled:
        print(f"  x compilation failed — leaving in 01_Raw/")
        return False

    write_wiki_files(compiled)
    shutil.move(filepath, os.path.join(PROCESSED_DIR, filename))
    print(f"  ✓ moved to processed/")
    return True


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    os.makedirs(WIKI_DIR, exist_ok=True)
    os.makedirs(OUTPUTS_DIR, exist_ok=True)

    # Support --limit N flag
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        try:
            limit = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            pass

    files = sorted(f for f in glob.glob(os.path.join(RAW_DIR, "*.md")) if os.path.isfile(f))
    if not files:
        print("No .md files in 01_Raw/. Nothing to do.")
        sys.exit(0)

    if limit:
        files = files[:limit]

    print(f"Found {len(files)} file(s) to process.")

    # Load shared context once
    index_content  = open(INDEX_FILE).read() if os.path.exists(INDEX_FILE) else ""
    agents_content = ""

    # No external priorities — set to empty
    priorities = ""

    # Process
    success = 0
    consecutive_failures = 0
    for filepath in files:
        if process_file(filepath, priorities, index_content, agents_content):
            success += 1
            consecutive_failures = 0
            # Refresh index so each file benefits from what was just written
            if os.path.exists(INDEX_FILE):
                index_content = open(INDEX_FILE).read()
        else:
            consecutive_failures += 1
            if consecutive_failures >= 2:
                print(f"\nx 2 consecutive failures — aborting run.")
                break

    print(f"\nIngestion complete: {success}/{len(files)} files processed.")
