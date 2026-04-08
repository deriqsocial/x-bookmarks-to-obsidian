# X Bookmarks → Obsidian Knowledge Base

Turn your X (Twitter) bookmarks into a searchable, interconnected wiki — automatically. Every bookmark becomes a structured note. Every article linked in a tweet gets fetched and summarized. Claude compiles everything into an Obsidian vault with wikilinks, tags, and a navigable knowledge graph.

83% of bookmarks are never revisited. You save things because they matter, then never see them again. This pipeline changes that — your bookmarks become knowledge you can actually use.

---

## What This Is

A two-script pipeline:

1. **`fetch_bookmarks.py`** — Pulls your X bookmarks via the API. For each bookmark, it fetches the full tweet text, any quoted tweet, thread context if it's a reply, and the full content of any linked articles (via [trafilatura](https://trafilatura.readthedocs.io/)). Saves everything as rich markdown files in `vault/01_Raw/`.

2. **`ingest.py`** — Reads those raw files and sends them to Claude Sonnet. Claude compiles each one into wiki pages with YAML frontmatter, proper wikilinks (`[[like this]]`), tags, and cross-references. Pages land in `vault/02_Wiki/` and move automatically through the pipeline.

3. **`synthesize.py`** (optional) — Reads all wiki pages created in the last 7 days and asks Claude to write a narrative synthesis: what patterns are emerging, what concepts keep appearing, what connections exist across your bookmarks. Output goes to `vault/03_Outputs/`.

The vault folder is a valid Obsidian vault. Open it in Obsidian and you get the full graph view, search, and backlinks — all populated automatically from your bookmarks.

---

## How It Works

```
Step 1: fetch
  python3 fetch_bookmarks.py
  → vault/01_Raw/tweet_20240315_username_123456789.md

Step 2: ingest
  python3 ingest.py
  → vault/02_Wiki/concepts/attention-mechanisms.md
  → vault/02_Wiki/entities/Andrej-Karpathy.md

Step 3: query (optional)
  python3 synthesize.py
  → vault/03_Outputs/synthesis_2024-03-17.md
```

Each raw file gets moved to `vault/01_Raw/processed/` after ingestion, so re-runs are safe.

---

## Setup: Desktop

**Requirements:** Python 3.8+, an X Developer account, an Anthropic API key.

```bash
git clone https://github.com/YOUR_USERNAME/x-bookmarks-to-obsidian.git
cd x-bookmarks-to-obsidian
pip install trafilatura anthropic
```

Copy the env template and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```
TWITTER_BEARER_TOKEN=your_bearer_token
TWITTER_CLIENT_ID=your_client_id
TWITTER_CLIENT_SECRET=your_client_secret
TWITTER_OAUTH2_ACCESS_TOKEN=your_access_token
TWITTER_OAUTH2_REFRESH_TOKEN=your_refresh_token
ANTHROPIC_API_KEY=sk-ant-...
VAULT_PATH=./vault
```

Get your X OAuth2 tokens by running the helper script (see [docs/setup-twitter-oauth.md](docs/setup-twitter-oauth.md)):

```bash
python3 oauth2_flow.py
```

Then run the pipeline:

```bash
python3 fetch_bookmarks.py
python3 ingest.py
python3 synthesize.py   # optional weekly synthesis
```

Open the `vault/` folder in Obsidian. Done.

---

## Setup: Mobile-Only (iPhone/iPad)

No laptop required. GitHub Actions runs the pipeline daily and syncs the vault to your repo. You read the wiki in Obsidian on your phone.

**Step 1: Fork this repo**

Click "Fork" on GitHub.

**Step 2: Add GitHub Secrets**

Go to your fork → Settings → Secrets and variables → Actions → New repository secret.

Add these secrets one by one:

| Secret | Value |
|--------|-------|
| `TWITTER_BEARER_TOKEN` | From X Developer Portal |
| `TWITTER_CLIENT_ID` | From X Developer Portal |
| `TWITTER_CLIENT_SECRET` | From X Developer Portal |
| `TWITTER_OAUTH2_ACCESS_TOKEN` | From `oauth2_flow.py` (run once on any computer) |
| `TWITTER_OAUTH2_REFRESH_TOKEN` | From `oauth2_flow.py` |
| `ANTHROPIC_API_KEY` | From console.anthropic.com |

**Step 3: Enable GitHub Actions**

Go to Actions tab → click "I understand my workflows, go ahead and enable them."

The workflow runs daily at 6am UTC. You can also trigger it manually from the Actions tab.

**Step 4: Install Obsidian + obsidian-git on your phone**

- Install [Obsidian](https://obsidian.md/) from the App Store
- Install the [obsidian-git](https://github.com/denolehov/obsidian-git) community plugin
- Point it at your forked repo
- Set it to pull on startup or on a schedule

Every morning, new bookmark wiki pages will be waiting in your vault.

---

## Usage

```bash
# Fetch only new bookmarks (skips already-seen ones)
python3 fetch_bookmarks.py

# Re-fetch everything
python3 fetch_bookmarks.py --all

# Fetch only the most recent 10 bookmarks
python3 fetch_bookmarks.py --limit 10

# Fetch bookmarks from a specific date forward
python3 fetch_bookmarks.py --since 2024-01-01

# Ingest (process all raw files → wiki pages)
python3 ingest.py

# Ingest only 5 files (useful for testing)
python3 ingest.py --limit 5

# Write a synthesis of this week's wiki pages
python3 synthesize.py
```

---

## Requirements

- Python 3.8+
- `pip install trafilatura anthropic`
- X Developer account (free tier works, just apply at developer.twitter.com)
- Anthropic API key (pay-per-use, ingesting 100 bookmarks costs roughly $0.50-2.00 depending on article length)

See [docs/setup-twitter-oauth.md](docs/setup-twitter-oauth.md) for step-by-step instructions on getting your X credentials.

---

## Vault Structure

```
vault/
  01_Raw/           ← fetched bookmarks land here
    processed/      ← moved here after ingestion
  02_Wiki/          ← Claude-compiled wiki pages
    concepts/
    entities/
    sources/
  03_Outputs/       ← synthesis notes
  index.md          ← auto-updated index
  log.md            ← ingestion log
```

---

## Token Auto-Refresh

OAuth2 access tokens expire after ~2 hours. `fetch_bookmarks.py` handles this automatically: when it gets a 401, it uses your refresh token to get a new access token and writes it back to `.env`. You never need to re-authorize manually after the initial setup.
