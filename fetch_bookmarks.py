#!/usr/bin/env python3
"""
X (Twitter) Bookmarks → 01_Raw ingestion feeder.

Goes deep on every bookmark:
  - Tweet text + author + date
  - Full quoted tweet (if quote repost)
  - Full thread context (parent tweets if it's a reply)
  - Full article content for every external URL in the tweet
  - X Articles (via API where accessible)

Auto-refreshes expired OAuth2 access tokens.

Usage:
  python3 fetch_bookmarks.py          # fetch new bookmarks only
  python3 fetch_bookmarks.py --all    # re-fetch all (ignores seen list)
  python3 fetch_bookmarks.py --limit N           # process only N bookmarks
  python3 fetch_bookmarks.py --since YYYY-MM-DD  # only fetch bookmarks on/after this date
"""

import os
import sys
import json
import base64
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

VAULT_PATH = os.getenv("VAULT_PATH", "./vault")
RAW_DIR    = os.path.join(VAULT_PATH, "01_Raw")
SEEN_FILE  = "./.seen_bookmarks.json"
ENV_FILE   = "./.env"

# ─────────────────────────────────────────────
# ENV LOADER / WRITER
# ─────────────────────────────────────────────

def load_env():
    env = {}
    if not os.path.exists(ENV_FILE):
        return env
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def update_env_tokens(access_token, refresh_token):
    with open(ENV_FILE, "r") as f:
        lines = f.readlines()
    updated = []
    for line in lines:
        if line.startswith("TWITTER_OAUTH2_ACCESS_TOKEN="):
            updated.append(f"TWITTER_OAUTH2_ACCESS_TOKEN={access_token}\n")
        elif line.startswith("TWITTER_OAUTH2_REFRESH_TOKEN="):
            updated.append(f"TWITTER_OAUTH2_REFRESH_TOKEN={refresh_token}\n")
        else:
            updated.append(line)
    with open(ENV_FILE, "w") as f:
        f.writelines(updated)
    print("  [token] Refreshed tokens saved to .env")

# ─────────────────────────────────────────────
# TOKEN REFRESH
# ─────────────────────────────────────────────

def refresh_oauth2_token(client_id, client_secret, refresh_token):
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode()
    req = urllib.request.Request(
        "https://api.twitter.com/2/oauth2/token",
        data=data,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read().decode())
    return result.get("access_token"), result.get("refresh_token")

# ─────────────────────────────────────────────
# TWITTER API
# ─────────────────────────────────────────────

def twitter_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def get_user_id(oauth2_token):
    data = twitter_get("https://api.twitter.com/2/users/me", oauth2_token)
    return data["data"]["id"]

def fetch_bookmarks(user_id, oauth2_token, max_results=100):
    params = urllib.parse.urlencode({
        "max_results": min(max_results, 100),
        "tweet.fields": "created_at,author_id,text,note_tweet,referenced_tweets,article,entities,in_reply_to_user_id,conversation_id",
        "expansions": "referenced_tweets.id,referenced_tweets.id.author_id,author_id,article.cover_media",
        "user.fields": "username,name",
    })
    url = f"https://api.twitter.com/2/users/{user_id}/bookmarks?{params}"
    return twitter_get(url, oauth2_token)

def fetch_tweet(tweet_id, bearer_token):
    """Fetch a single tweet with full fields."""
    params = urllib.parse.urlencode({
        "tweet.fields": "created_at,author_id,text,note_tweet,referenced_tweets,entities,in_reply_to_user_id",
        "expansions": "author_id",
        "user.fields": "username,name",
    })
    url = f"https://api.twitter.com/2/tweets/{tweet_id}?{params}"
    try:
        return twitter_get(url, bearer_token)
    except Exception:
        return None

def fetch_conversation_thread(tweet_id, bearer_token, max_depth=3):
    """
    Walk up the reply chain from a tweet to get parent context.
    Returns list of (username, text) tuples from oldest to newest.
    """
    thread = []
    current_id = tweet_id
    depth = 0

    while depth < max_depth:
        data = fetch_tweet(current_id, bearer_token)
        if not data:
            break
        tweet = data.get("data", {})
        users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
        author_id = tweet.get("author_id", "")
        username = users.get(author_id, {}).get("username", "?")
        thread.insert(0, (username, tweet.get("text", ""), current_id))

        # Check if this tweet is a reply
        in_reply_to = tweet.get("in_reply_to_user_id")
        refs = tweet.get("referenced_tweets", [])
        replied_to = next((r["id"] for r in refs if r.get("type") == "replied_to"), None)
        if replied_to and replied_to != current_id:
            current_id = replied_to
            depth += 1
        else:
            break

    return thread

def fetch_x_article(tweet_id, bearer_token):
    """Attempt to fetch full X Article text attached to a tweet."""
    url = f"https://api.twitter.com/2/tweets/{tweet_id}?expansions=article.cover_media,article.media_entities&tweet.fields=text,article"
    try:
        data = twitter_get(url, bearer_token)
        return data.get("data", {}).get("article", {}).get("plain_text", "")
    except Exception:
        return ""

# ─────────────────────────────────────────────
# URL CONTENT FETCHING
# ─────────────────────────────────────────────

def resolve_url(url, timeout=10):
    """Follow redirects (t.co etc) to get the final URL."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.url
    except Exception:
        return url

def fetch_article_content(url, timeout=20):
    """
    Fetch full article text from a URL using trafilatura.
    Falls back to basic HTML stripping if trafilatura fails.
    Returns (title, content, final_url).
    """
    if not HAS_TRAFILATURA:
        return None, None, url

    try:
        # Resolve the URL first (handles t.co redirects etc)
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final_url = r.url
            html = r.read()

        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_precision=False,
        )
        metadata = trafilatura.extract_metadata(html)
        title = metadata.title if metadata else None

        if text and len(text.strip()) > 100:
            return title, text.strip(), final_url
        return None, None, final_url
    except Exception as e:
        return None, None, url

def is_fetchable_url(url):
    """Skip social media links, images, and known non-article URLs."""
    skip_domains = [
        "twitter.com", "x.com", "t.co",
        "instagram.com", "facebook.com",
        "youtube.com", "youtu.be",
        "linkedin.com",
        "tiktok.com",
        "reddit.com",
    ]
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lower().lstrip("www.")
        return not any(skip in domain for skip in skip_domains)
    except Exception:
        return False

def extract_plaintext_urls(text, already_known):
    """
    Find https:// URLs in raw tweet text that weren't captured in entities.
    Twitter sometimes doesn't t.co-ify URLs that appear in longer tweet text
    (especially in X Articles or threads). Returns a list of new URLs to fetch.
    """
    import re
    pattern = re.compile(r'https?://[^\s\]\[()\'\"<>]+')
    found = []
    for match in pattern.finditer(text):
        url = match.group(0).rstrip('.,;:!?)')  # trim trailing punctuation
        if url not in already_known and is_fetchable_url(url):
            found.append(url)
    return found

# ─────────────────────────────────────────────
# MARKDOWN BUILDER
# ─────────────────────────────────────────────

def build_markdown(tweet, users_map, referenced_map, bearer_token):
    tweet_id = tweet["id"]
    author_id = tweet.get("author_id", "")
    author = users_map.get(author_id, {})
    username = author.get("username", "unknown")
    name = author.get("name", "unknown")
    created = tweet.get("created_at", "")[:10]

    # Prefer note_tweet.text (full long-tweet body) over the truncated text field
    note_tweet = tweet.get("note_tweet", {}) or {}
    text = note_tweet.get("text") or tweet.get("text", "")

    lines = [
        "---",
        f"source: twitter_bookmark",
        f"tweet_id: {tweet_id}",
        f"author: @{username} ({name})",
        f"date: {created}",
        f"url: https://x.com/{username}/status/{tweet_id}",
        "---",
        "",
        f"# Bookmark: @{username} ({created})",
        "",
        f"{text}",
        "",
    ]

    # ── Thread context (if this is a reply, walk up the chain) ──
    refs = tweet.get("referenced_tweets", [])
    is_reply = any(r.get("type") == "replied_to" for r in refs)
    if is_reply:
        print(f"    → fetching thread context...")
        thread = fetch_conversation_thread(tweet_id, bearer_token, max_depth=3)
        if len(thread) > 1:
            lines += ["## Thread Context (oldest → newest)", ""]
            for t_username, t_text, t_id in thread[:-1]:  # exclude the bookmark itself
                lines += [
                    f"**@{t_username}** ([source](https://x.com/{t_username}/status/{t_id}))",
                    f"> {t_text}",
                    "",
                ]

    # ── Quoted tweet (deep expansion) ──
    for ref in refs:
        if ref.get("type") == "quoted":
            quoted = referenced_map.get(ref["id"], {})
            if quoted:
                q_author_id = quoted.get("author_id", "")
                q_author = users_map.get(q_author_id, {})
                q_username = q_author.get("username", "?")
                q_text = quoted.get("text", "")
                lines += [
                    f"## Quoted Tweet (@{q_username})",
                    "",
                    q_text,
                    "",
                    f"Source: https://x.com/{q_username}/status/{ref['id']}",
                    "",
                ]

                # Pull article content from the quoted tweet if it has one
                q_article = quoted.get("article", {})
                if q_article:
                    q_article_text = q_article.get("plain_text", "")
                    q_article_title = q_article.get("title", "")
                    if q_article_text:
                        print(f"    → quoted tweet has X Article: {q_article_title!r}")
                        lines += [
                            f"### Quoted X Article: {q_article_title or 'Untitled'}",
                            "",
                            q_article_text,
                            "",
                        ]
                elif not q_article:
                    # Try fetching article separately for the quoted tweet
                    q_article_text = fetch_x_article(ref["id"], bearer_token)
                    if q_article_text:
                        print(f"    → fetched article for quoted tweet {ref['id']}")
                        lines += ["### Quoted X Article", "", q_article_text, ""]

                # Also fetch URLs inside the quoted tweet
                q_entities = quoted.get("entities", {})
                q_urls = [
                    u["expanded_url"] for u in q_entities.get("urls", [])
                    if is_fetchable_url(u.get("expanded_url", ""))
                ]
                if q_urls:
                    for q_url in q_urls[:2]:  # max 2 from quoted tweet
                        print(f"    → fetching quoted tweet URL: {q_url[:60]}...")
                        title, content, final_url = fetch_article_content(q_url)
                        if content:
                            lines += [
                                f"### Linked Article (from quoted tweet)",
                                f"**URL:** {final_url}",
                                f"**Title:** {title or 'Unknown'}",
                                "",
                                content[:4000],  # cap at 4K chars
                                "",
                            ]

    # ── X Article ──
    article = tweet.get("article")
    if not article:
        article_text = fetch_x_article(tweet_id, bearer_token)
        if article_text:
            lines += ["## X Article Content", "", article_text, ""]
    else:
        article_text = article.get("plain_text", "")
        if article_text:
            lines += ["## X Article Content", "", article_text, ""]

    # ── External URLs — fetch full content ──
    entities = tweet.get("entities", {})
    # Merge note_tweet entities (long tweets have a separate URL list there)
    note_urls = (note_tweet.get("entities") or {}).get("urls", [])
    all_urls = entities.get("urls", []) + [u for u in note_urls if u not in entities.get("urls", [])]

    # Separate fetchable articles from media/social links
    article_urls = []
    media_urls = []
    known_expanded = set()
    for u in all_urls:
        exp = u.get("expanded_url", "")
        if not exp:
            continue
        known_expanded.add(exp)
        if is_fetchable_url(exp):
            article_urls.append(exp)
        elif exp not in [f"https://x.com/{username}/status/{tweet_id}"]:
            media_urls.append(exp)

    # Also scan raw tweet text for plain-text URLs not captured in entities
    # (Twitter sometimes skips t.co conversion for URLs in longer content)
    plaintext_extras = extract_plaintext_urls(text, known_expanded)
    if plaintext_extras:
        print(f"    → found {len(plaintext_extras)} plain-text URL(s) in tweet body not in entities")
        article_urls.extend(plaintext_extras)

    if article_urls:
        for url in article_urls[:3]:  # max 3 articles per tweet
            print(f"    → fetching: {url[:70]}...")
            title, content, final_url = fetch_article_content(url)
            if content:
                lines += [
                    "## Linked Article",
                    f"**URL:** {final_url}",
                    f"**Title:** {title or 'Unknown'}",
                    "",
                    content[:6000],  # cap at 6K chars
                    "",
                ]
            else:
                lines += [
                    "## Linked URL (content not extractable)",
                    f"- {final_url}",
                    "",
                ]
    elif not article_urls and not article_text:
        # Just list any remaining URLs
        if media_urls:
            lines += ["## Links", ""]
            for u in media_urls[:5]:
                lines.append(f"- {u}")

    return "\n".join(lines)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    fetch_all = "--all" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            pass

    since_date = None
    if "--since" in sys.argv:
        try:
            since_date = sys.argv[sys.argv.index("--since") + 1]  # YYYY-MM-DD
            print(f"  [filter] Only fetching bookmarks on/after {since_date}")
        except IndexError:
            pass

    env = load_env()
    bearer_token = env.get("TWITTER_BEARER_TOKEN", "")
    oauth2_token = env.get("TWITTER_OAUTH2_ACCESS_TOKEN", "")
    refresh_token = env.get("TWITTER_OAUTH2_REFRESH_TOKEN", "")
    client_id = env.get("TWITTER_CLIENT_ID", "")
    client_secret = env.get("TWITTER_CLIENT_SECRET", "")

    if not bearer_token or not oauth2_token:
        print("ERROR: TWITTER_BEARER_TOKEN or TWITTER_OAUTH2_ACCESS_TOKEN not found in .env")
        sys.exit(1)

    # Auth: get user ID, refresh token if needed
    user_id = None
    try:
        user_id = get_user_id(oauth2_token)
        print(f"  [auth] Authenticated as user {user_id}")
    except urllib.error.HTTPError as e:
        if e.code == 401 and refresh_token and client_id and client_secret:
            print("  [token] Access token expired. Refreshing...")
            try:
                new_access, new_refresh = refresh_oauth2_token(client_id, client_secret, refresh_token)
                if new_access:
                    update_env_tokens(new_access, new_refresh or refresh_token)
                    oauth2_token = new_access
                    user_id = get_user_id(oauth2_token)
                    print(f"  [auth] Authenticated as user {user_id} (after refresh)")
                else:
                    print("ERROR: Token refresh returned no access token. Re-authorize at Twitter.")
                    sys.exit(1)
            except Exception as refresh_err:
                print(f"ERROR: Token refresh failed: {refresh_err}")
                sys.exit(1)
        else:
            print(f"ERROR authenticating: {e}")
            sys.exit(1)

    # Load seen list
    seen = set()
    if not fetch_all and os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            seen = set(json.load(f))

    print(f"Fetching bookmarks... (seen: {len(seen)})")

    try:
        data = fetch_bookmarks(user_id, oauth2_token)
    except Exception as e:
        print(f"ERROR fetching bookmarks: {e}")
        sys.exit(1)

    tweets = data.get("data", []) or []
    includes = data.get("includes", {})

    users_map = {u["id"]: u for u in includes.get("users", [])}
    referenced_map = {t["id"]: t for t in includes.get("tweets", [])}

    os.makedirs(RAW_DIR, exist_ok=True)
    new_count = 0

    for tweet in tweets:
        tweet_id = tweet["id"]
        if tweet_id in seen:
            continue
        if limit and new_count >= limit:
            break

        tweet_date = tweet.get("created_at", "")[:10]
        if since_date and tweet_date < since_date:
            print(f"  [stop] Reached tweets older than {since_date} ({tweet_date}). Done.")
            break

        author_id = tweet.get("author_id", "")
        username = users_map.get(author_id, {}).get("username", "unknown")
        date = tweet.get("created_at", "")[:10].replace("-", "")
        fname = f"tweet_{date}_{username}_{tweet_id}.md"
        fpath = os.path.join(RAW_DIR, fname)

        print(f"  + {fname}")
        md = build_markdown(tweet, users_map, referenced_map, bearer_token)

        with open(fpath, "w") as f:
            f.write(md)

        seen.add(tweet_id)
        new_count += 1

        # Be a good citizen — don't hammer article servers
        if new_count < len(tweets):
            time.sleep(0.5)

    # Save updated seen list
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

    print(f"\nDone. {new_count} new bookmarks saved to 01_Raw/.")
    if new_count > 0:
        print("Run ingest.py to compile them into the wiki.")

if __name__ == "__main__":
    main()
