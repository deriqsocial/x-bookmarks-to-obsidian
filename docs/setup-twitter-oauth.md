# Setting Up X (Twitter) OAuth2 Credentials

Getting X API credentials is the most friction-heavy part of this setup. This guide walks through every step.

---

## Step 1: Apply for an X Developer Account

1. Go to [developer.twitter.com](https://developer.twitter.com)
2. Click "Sign up" (or "Apply" if prompted)
3. Log in with your X account
4. Answer the use case questions. For this project, say something like: "I'm building a personal tool to read and organize my own bookmarks." This is true and unambiguous — approval is usually instant or within a day.

---

## Step 2: Create an App

1. Once approved, go to the [Developer Portal](https://developer.twitter.com/en/portal/dashboard)
2. Click "Projects & Apps" → "Overview"
3. Click "Create App" (or create a Project first if prompted, then add an app inside it)
4. Give the app a name — anything works, e.g., "bookmarks-personal"

---

## Step 3: Configure OAuth 2.0 Settings

1. Inside your app, go to "Settings" → "User authentication settings" → "Edit"
2. Enable **OAuth 2.0**
3. Set **App permissions** to "Read" (bookmarks only requires read)
4. Set **Type of App** to "Web App, Automated App or Bot"
5. Set **Callback URI**: `http://localhost:3000/callback`
6. Set **Website URL**: any valid URL, e.g., `https://github.com`
7. Save

---

## Step 4: Get Your Keys

1. Go to "Keys and Tokens" tab inside your app
2. Copy your **Bearer Token** — this goes in `.env` as `TWITTER_BEARER_TOKEN`
3. Go to "OAuth 2.0 Client ID and Client Secret"
4. Copy **Client ID** → `TWITTER_CLIENT_ID`
5. Generate and copy **Client Secret** → `TWITTER_CLIENT_SECRET`

Store these in your `.env` file now:

```
TWITTER_BEARER_TOKEN=AAAA...
TWITTER_CLIENT_ID=abc123...
TWITTER_CLIENT_SECRET=xyz789...
```

---

## Step 5: Get the Initial Access and Refresh Tokens

This is a one-time step. You need to authorize the app against your own X account to get the OAuth2 access token that lets it read your bookmarks.

Run the helper script:

```bash
python3 oauth2_flow.py
```

The script will:

1. Print an authorization URL and open it in your browser
2. You click "Authorize app" on X
3. X redirects to `http://localhost:3000/callback` with an authorization code
4. The script catches that code, exchanges it for tokens, and prints them

Copy the printed tokens into your `.env`:

```
TWITTER_OAUTH2_ACCESS_TOKEN=...
TWITTER_OAUTH2_REFRESH_TOKEN=...
```

That's it. You never need to do this again — `fetch_bookmarks.py` auto-refreshes the access token when it expires.

---

## Step 6: For GitHub Actions (Mobile Setup)

When using GitHub Actions, you can't run `oauth2_flow.py` on the server. You need to run it once on any computer (even a friend's laptop for 5 minutes), copy the tokens, then add them as GitHub Secrets.

The refresh token is long-lived. As long as the workflow runs at least once every 60 days, it will keep refreshing automatically.

If the refresh token ever expires (e.g., after a long gap), just run `oauth2_flow.py` again locally and update the GitHub Secrets with the new values.

---

## Troubleshooting

**"401 Unauthorized" on first run**
Double-check that OAuth 2.0 is enabled in the app settings (Step 3) and that you've copied all three keys correctly.

**"403 Forbidden" when fetching bookmarks**
The bookmarks endpoint requires the user's own OAuth2 token (not just Bearer). Make sure `TWITTER_OAUTH2_ACCESS_TOKEN` is set and was generated for the account whose bookmarks you want to read.

**Callback URL mismatch error**
The callback URL in your X app settings must exactly match what `oauth2_flow.py` uses: `http://localhost:3000/callback`. Check for trailing slashes or `https` vs `http` mismatches.

**oauth2_flow.py hangs waiting for callback**
Make sure port 3000 is free on your machine. If another app is using it, edit `oauth2_flow.py` and change `PORT = 3000` to any free port, then update the callback URL in X app settings to match.
