#!/usr/bin/env python3
# Copyright 2026 Pika Labs, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Static site hosting — deploy a local directory to GitHub Pages, Netlify, or Surge.sh.

Usage:
  python deploy_static_site.py github-pages --dir <path> --repo <owner/repo> [--branch gh-pages] [--cname <domain>]
  python deploy_static_site.py netlify --dir <path> [--site-name <name>]
  python deploy_static_site.py surge --dir <path> [--domain <subdomain>.surge.sh]

Environment variables:
  GITHUB_TOKEN        — required for github-pages
  NETLIFY_AUTH_TOKEN  — required for netlify
  SURGE_LOGIN         — required for surge
  SURGE_TOKEN         — required for surge

Exit codes:
  0 — success  (JSON with {"url": "..."} written to stdout)
  2 — validation error
  3 — HTTP / API error
  4 — git error (github-pages only)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def eprint(*args):
    print(*args, file=sys.stderr)


def die(code: int, *msg):
    eprint(*msg)
    sys.exit(code)


def out(data: dict):
    print(json.dumps(data))


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        die(2, f"Error: environment variable {name!r} is not set.")
    return value


def validate_dir(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        die(2, f"Error: {path!r} is not a directory.")
    html_files = list(p.rglob("*.html"))
    if not html_files:
        die(2, f"Error: no .html files found in {path!r}. Is this the right folder?")
    return p


# ---------------------------------------------------------------------------
# GitHub Pages
# ---------------------------------------------------------------------------

def cmd_github_pages(args: argparse.Namespace):
    token = require_env("GITHUB_TOKEN")
    site_dir = validate_dir(args.dir)

    repo = args.repo.strip()
    if not re.fullmatch(r"[^/]+/[^/]+", repo):
        die(2, f"Error: --repo must be in 'owner/repo' format, got: {repo!r}")

    branch = args.branch or "gh-pages"
    owner, repo_name = repo.split("/", 1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Verify the repo exists and we have access.
    r = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=15)
    if r.status_code == 404:
        die(3, f"Error: repository {repo!r} not found or token lacks access.")
    if not r.ok:
        die(3, f"Error: GitHub API returned {r.status_code}: {r.text}")

    repo_data = r.json()
    clone_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    default_branch = repo_data.get("default_branch", "main")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Clone the target branch (shallow); fall back to orphan if it doesn't exist.
        eprint(f"Cloning {repo} (branch: {branch}) …")
        result = subprocess.run(
            ["git", "clone", "--depth=1", "--branch", branch, clone_url, str(tmp_path / "repo")],
            capture_output=True, text=True,
        )

        repo_dir = tmp_path / "repo"

        if result.returncode != 0:
            # Branch doesn't exist — create an orphan checkout from the default branch.
            eprint(f"Branch {branch!r} not found; creating it as an orphan branch.")
            result2 = subprocess.run(
                ["git", "clone", "--depth=1", "--branch", default_branch, clone_url, str(repo_dir)],
                capture_output=True, text=True,
            )
            if result2.returncode != 0:
                # Repo may be empty; init a bare repo.
                repo_dir.mkdir()
                subprocess.run(["git", "init", str(repo_dir)], capture_output=True)
                subprocess.run(
                    ["git", "remote", "add", "origin", clone_url],
                    cwd=repo_dir, capture_output=True,
                )

            subprocess.run(
                ["git", "checkout", "--orphan", branch],
                cwd=repo_dir, capture_output=True,
            )
            subprocess.run(
                ["git", "rm", "-rf", "."],
                cwd=repo_dir, capture_output=True,
            )
        else:
            # Remove all existing tracked content, preserving .git.
            for item in repo_dir.iterdir():
                if item.name == ".git":
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

        # Copy site files into the repo.
        eprint(f"Copying site files from {site_dir} …")
        for item in site_dir.iterdir():
            dest = repo_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        # Add CNAME if requested.
        if args.cname:
            (repo_dir / "CNAME").write_text(args.cname.strip() + "\n")

        # Ensure a .nojekyll file so GitHub Pages serves files with leading underscores.
        (repo_dir / ".nojekyll").touch()

        # Configure git identity (needed in non-interactive environments).
        subprocess.run(
            ["git", "config", "user.email", "deploy-bot@pika-skills"],
            cwd=repo_dir, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Pika Static Site Deploy"],
            cwd=repo_dir, capture_output=True,
        )

        subprocess.run(["git", "add", "-A"], cwd=repo_dir, capture_output=True)
        commit = subprocess.run(
            ["git", "commit", "-m", "Deploy static site via pika-skills"],
            cwd=repo_dir, capture_output=True, text=True,
        )
        if commit.returncode not in (0, 1):  # 1 = nothing to commit
            die(4, f"git commit failed:\n{commit.stderr}")

        push = subprocess.run(
            ["git", "push", "origin", branch, "--force"],
            cwd=repo_dir, capture_output=True, text=True,
        )
        if push.returncode != 0:
            die(4, f"git push failed:\n{push.stderr}")

    # Enable GitHub Pages via API (idempotent).
    eprint("Enabling GitHub Pages …")
    pages_payload = {"source": {"branch": branch, "path": "/"}}
    pr = requests.post(
        f"https://api.github.com/repos/{repo}/pages",
        headers=headers, json=pages_payload, timeout=15,
    )
    if pr.status_code in (201, 409):  # 409 = already enabled
        pass
    elif not pr.ok:
        eprint(f"Warning: could not enable GitHub Pages automatically ({pr.status_code}). "
               "Enable it manually: Settings → Pages → Source → branch: {branch}.")

    if args.cname:
        url = f"https://{args.cname.strip()}"
        note = "DNS may take up to 48 h to propagate."
    else:
        url = f"https://{owner}.github.io/{repo_name}/"
        note = "GitHub Pages can take a few minutes to go live after the first deploy."

    out({"url": url, "note": note})


# ---------------------------------------------------------------------------
# Netlify
# ---------------------------------------------------------------------------

def cmd_netlify(args: argparse.Namespace):
    token = require_env("NETLIFY_AUTH_TOKEN")
    site_dir = validate_dir(args.dir)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    site_id: str | None = None

    # Create or look up the site.
    if args.site_name:
        name = re.sub(r"[^a-z0-9-]", "-", args.site_name.strip().lower()).strip("-")
        eprint(f"Creating / looking up Netlify site: {name} …")
        r = requests.post(
            "https://api.netlify.com/api/v1/sites",
            headers=headers,
            json={"name": name},
            timeout=15,
        )
        if r.status_code == 422:
            # Name already taken — try to find the existing site owned by us.
            eprint("Name already taken; searching for an existing site with that name …")
            list_r = requests.get("https://api.netlify.com/api/v1/sites", headers=headers, timeout=15)
            if list_r.ok:
                for site in list_r.json():
                    if site.get("name") == name:
                        site_id = site["id"]
                        break
            if not site_id:
                die(3, f"Error: Netlify site name {name!r} is taken by another account. Choose a different name.")
        elif not r.ok:
            die(3, f"Error: Netlify API returned {r.status_code}: {r.text}")
        else:
            site_id = r.json()["id"]
    else:
        eprint("Creating a new Netlify site with an auto-generated name …")
        r = requests.post("https://api.netlify.com/api/v1/sites", headers=headers, json={}, timeout=15)
        if not r.ok:
            die(3, f"Error: Netlify API returned {r.status_code}: {r.text}")
        site_id = r.json()["id"]

    # Zip the site directory.
    eprint(f"Zipping {site_dir} …")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in site_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(site_dir))
    zip_bytes = zip_buffer.getvalue()

    # Deploy.
    eprint("Deploying to Netlify …")
    deploy_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/zip",
    }
    dr = requests.post(
        f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
        headers=deploy_headers,
        data=zip_bytes,
        timeout=120,
    )
    if not dr.ok:
        die(3, f"Error: Netlify deploy returned {dr.status_code}: {dr.text}")

    deploy_data = dr.json()
    url = deploy_data.get("ssl_url") or deploy_data.get("url") or f"https://{deploy_data.get('subdomain')}.netlify.app"
    out({"url": url})


# ---------------------------------------------------------------------------
# Surge.sh
# ---------------------------------------------------------------------------

def _surge_token_header(login: str, token: str) -> dict[str, str]:
    import base64
    creds = base64.b64encode(f"{login}:{token}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def cmd_surge(args: argparse.Namespace):
    login = require_env("SURGE_LOGIN")
    token = require_env("SURGE_TOKEN")
    site_dir = validate_dir(args.dir)

    domain = args.domain
    if domain:
        domain = domain.strip().lower()
        if not domain.endswith(".surge.sh"):
            domain = f"{domain}.surge.sh"
    else:
        # Auto-generate a domain from the directory name.
        stem = re.sub(r"[^a-z0-9-]", "-", site_dir.name.lower()).strip("-") or "my-site"
        import random, string
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        domain = f"{stem}-{suffix}.surge.sh"

    eprint(f"Deploying to https://{domain} …")

    headers = _surge_token_header(login, token)

    # Collect all files.
    files_payload = {}
    for file_path in site_dir.rglob("*"):
        if file_path.is_file():
            rel = str(file_path.relative_to(site_dir))
            files_payload[rel] = open(file_path, "rb")  # noqa: WPS515

    try:
        r = requests.put(
            f"https://surge.surge.sh/{domain}/",
            headers=headers,
            files={k: (k, v) for k, v in files_payload.items()},
            timeout=120,
        )
    finally:
        for fh in files_payload.values():
            fh.close()

    if not r.ok:
        die(3, f"Error: Surge returned {r.status_code}: {r.text}")

    out({"url": f"https://{domain}"})


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Deploy a static site to a free hosting platform.")
    sub = parser.add_subparsers(dest="command", required=True)

    # github-pages
    gp = sub.add_parser("github-pages", help="Deploy to GitHub Pages.")
    gp.add_argument("--dir", required=True, help="Path to the static site directory.")
    gp.add_argument("--repo", required=True, help="GitHub repo in owner/repo format.")
    gp.add_argument("--branch", default="gh-pages", help="Branch to deploy to (default: gh-pages).")
    gp.add_argument("--cname", default="", help="Custom domain (optional).")

    # netlify
    nl = sub.add_parser("netlify", help="Deploy to Netlify.")
    nl.add_argument("--dir", required=True, help="Path to the static site directory.")
    nl.add_argument("--site-name", default="", help="Netlify site name (optional; auto-generated if omitted).")

    # surge
    sg = sub.add_parser("surge", help="Deploy to Surge.sh.")
    sg.add_argument("--dir", required=True, help="Path to the static site directory.")
    sg.add_argument("--domain", default="", help="Full domain (e.g. my-site.surge.sh; auto-generated if omitted).")

    args = parser.parse_args()

    dispatch = {
        "github-pages": cmd_github_pages,
        "netlify": cmd_netlify,
        "surge": cmd_surge,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
