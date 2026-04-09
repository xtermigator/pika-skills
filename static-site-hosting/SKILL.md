---
name: static-site-hosting
description: |
  Deploy and host a static website (plain HTML/CSS/JS or any pre-built output).
  Trigger: user says "host my site", "deploy my static site", "publish my website",
  or provides a directory/folder and asks to put it online.
metadata:
  openclaw:
    requires:
      bins: ["python3"]
---

# Static Site Hosting

Script: `SKILL_DIR=skills/static-site-hosting`

Supports three free hosting platforms — choose based on what credentials the user has:

| Platform | Env var needed | Free URL pattern |
|---|---|---|
| **GitHub Pages** | `GITHUB_TOKEN` | `https://<owner>.github.io/<repo>/` |
| **Netlify** | `NETLIFY_AUTH_TOKEN` | `https://<site-name>.netlify.app` |
| **Surge.sh** | `SURGE_LOGIN` + `SURGE_TOKEN` | `https://<domain>.surge.sh` |

---

## First-Time Setup

Run once when the skill is first loaded:

```bash
pip install -r $SKILL_DIR/requirements.txt
```

---

## Deploy Flow

### Step 1 — Identify the site directory

Ask the user: `What folder contains your static site files? (e.g. \`./dist\`, \`./build\`, or \`.\` for the current directory)`

Wait for their answer. Resolve it to an absolute path. Confirm it contains at least one `.html` file; if not, warn the user and ask again.

### Step 2 — Choose a hosting platform

Ask the user: `Which platform would you like to use?
1. GitHub Pages (free, requires a GitHub repo and a GITHUB_TOKEN)
2. Netlify (free, requires a NETLIFY_AUTH_TOKEN)
3. Surge.sh (free, requires SURGE_LOGIN and SURGE_TOKEN)
Enter 1, 2, or 3:`

Do not proceed until the user responds.

### Step 3 — Collect credentials (if missing)

**GitHub Pages (option 1):**

- Check for `GITHUB_TOKEN` in the environment. If missing, ask:
  `I need a GitHub personal access token with \`repo\` scope. Create one at https://github.com/settings/tokens and paste it here (or export it as GITHUB_TOKEN).`
- Ask: `Which GitHub repo should receive the site? (format: owner/repo, e.g. alice/my-portfolio)`
- Ask: `What branch should hold the built files? [default: gh-pages]`
- Ask (optional): `Custom domain? (e.g. www.example.com) Leave blank to skip.`

**Netlify (option 2):**

- Check for `NETLIFY_AUTH_TOKEN` in the environment. If missing, ask:
  `I need a Netlify personal access token. Create one at https://app.netlify.com/user/applications#personal-access-tokens and paste it here (or export it as NETLIFY_AUTH_TOKEN).`
- Ask: `What site name would you like? (used in the URL, e.g. my-portfolio → my-portfolio.netlify.app) Leave blank to auto-generate.`

**Surge.sh (option 3):**

- Check for `SURGE_LOGIN` and `SURGE_TOKEN` in the environment. If either is missing, ask:
  `I need your Surge credentials. Run \`surge token\` in a terminal to get your token, then export SURGE_LOGIN=<email> and SURGE_TOKEN=<token>, or paste them here.`
- Ask: `What subdomain would you like? (e.g. my-portfolio → my-portfolio.surge.sh) Leave blank to auto-generate.`

### Step 4 — Deploy

Run the appropriate subcommand:

**GitHub Pages:**
```bash
python $SKILL_DIR/scripts/deploy_static_site.py github-pages \
  --dir <site-dir> \
  --repo <owner/repo> \
  --branch <branch> \
  [--cname <custom-domain>]
```

**Netlify:**
```bash
python $SKILL_DIR/scripts/deploy_static_site.py netlify \
  --dir <site-dir> \
  [--site-name <name>]
```

**Surge.sh:**
```bash
python $SKILL_DIR/scripts/deploy_static_site.py surge \
  --dir <site-dir> \
  [--domain <subdomain>.surge.sh]
```

### Step 5 — Share the result

- **Exit 0:** Read the JSON printed to stdout. It contains a `url` field. Tell the user:
  `✅ Your site is live at: <url>`
  If there is also a `note` field, display it.
- **Exit 2 (validation):** Show the error and ask the user to fix the input.
- **Exit 3 (HTTP error):** Show the status code and message. Ask the user to check their credentials and try again.
- **Any other non-zero exit:** Show stderr and ask the user how to proceed.
