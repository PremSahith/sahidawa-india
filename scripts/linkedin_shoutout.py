#!/usr/bin/env python3
"""
SahiDawa LinkedIn Automated Shoutout Script (via Make.com Webhook)
===================================================================
Flow:
  1. PR is merged with level:advanced or level:critical label
  2. GitHub Actions calls this script
  3. Script generates a unique post using Gemini AI
  4. Script sends the post as JSON to a Make.com webhook
  5. Make.com posts it to LinkedIn Company Page (no Advertising API needed)

Environment Variables Required (set as GitHub Secrets):
  - MAKE_WEBHOOK_URL  : Make.com webhook URL (https://hook.eu1.make.com/...)
  - GEMINI_API_KEY    : Google Gemini API key
  - PR_TITLE          : Title of the merged PR
  - PR_AUTHOR         : GitHub username of the contributor
  - PR_URL            : URL of the merged PR
  - PR_LABELS         : Comma-separated labels on the PR
  - PR_BODY           : Description/body of the merged PR (optional)
  - PR_NUMBER         : PR number
  - PR_REPO           : Repository name (e.g. RatLoopz/sahidawa-india)
  - PR_LINES_CHANGED  : Total lines added + deleted in the PR
  - PR_GIT_DIFF       : The actual code diff (passed from GitHub actions)
"""

import os
import sys
import re
import json
import requests

# ─────────────────────────────────────────────────────────────────────────────
# PROJECT CONFIG — Edit these to change branding
# ─────────────────────────────────────────────────────────────────────────────
PROJECT_NAME = "SahiDawa"
PROJECT_TAGLINE = "India's open-source medicine safety platform for 1.4 billion people 🇮🇳"
PROJECT_GITHUB_URL = "https://github.com/RatLoopz/sahidawa-india"
PROJECT_HASHTAGS = "#SahiDawa #OpenSource #GSSoC2026 #BuildForIndia #HealthTech #IndiaStack"

LABEL_TIER_MAP = {
    "level:critical": ("⚡ Critical-Level", "mission-critical"),
    "level:advanced": ("🔥 Advanced-Level", "highly complex"),
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def get_env_or_exit(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"❌ ERROR: Required environment variable '{key}' is missing or empty.")
        sys.exit(1)
    return val


def get_pr_metadata() -> dict:
    return {
        "title": get_env_or_exit("PR_TITLE"),
        "author": get_env_or_exit("PR_AUTHOR"),
        "url": get_env_or_exit("PR_URL"),
        "number": os.environ.get("PR_NUMBER", "N/A"),
        "labels": os.environ.get("PR_LABELS", ""),
        "body": os.environ.get("PR_BODY", "").strip()[:500],
        "repo": os.environ.get("PR_REPO", "RatLoopz/sahidawa-india"),
        "lines_changed": os.environ.get("PR_LINES_CHANGED", "0"),
        "diff": os.environ.get("PR_GIT_DIFF", ""),
    }


def determine_tier(labels_str: str) -> tuple:
    labels = [lbl.strip().lower() for lbl in labels_str.split(",")]
    for label in ["level:critical", "level:advanced"]:
        if label in labels:
            return LABEL_TIER_MAP[label]
    return ("🔥 Advanced-Level", "highly complex")


def validate_pr_size(pr: dict) -> None:
    """
    Validates if the PR is substantial enough to warrant a shoutout.
    - level:critical requires at least 300 lines changed.
    - level:advanced requires at least 200 lines changed.
    """
    labels = [lbl.strip().lower() for lbl in pr["labels"].split(",")]
    try:
        lines_changed = int(pr["lines_changed"])
    except ValueError:
        lines_changed = 0

    is_critical = "level:critical" in labels
    is_advanced = "level:advanced" in labels

    # If somehow both or neither are there, default to advanced threshold
    threshold = 300 if is_critical else 200
    tier_name = "Critical" if is_critical else "Advanced"

    if lines_changed < threshold:
        print(f"🛑 REJECTED: PR only changed {lines_changed} lines.")
        print(f"   {tier_name} shoutouts require at least {threshold} lines of code changes.")
        print("   Exiting gracefully without triggering Make.com webhook or consuming AI credits.")
        sys.exit(0)
    
    print(f"✅ PR Size Validation Passed. Lines changed: {lines_changed} (Threshold: {threshold})")


def evaluate_pr_impact(pr: dict) -> None:
    """
    Sends the PR diff to Gemini to semantically evaluate if it's a genuine 
    advanced/critical contribution, or just trivial bloat (JSON dumps, locks).
    """
    gemini_api_key = get_env_or_exit("GEMINI_API_KEY")
    diff = pr.get("diff", "")
    
    if not diff or diff == "Diff unavailable":
        print("⚠️  No Git Diff available. Bypassing semantic AI check.")
        return

    print("🧠 Semantic AI Gatekeeper: Evaluating PR quality...")

    system_prompt = (
        "You are an expert Principal Engineer. Your job is to evaluate if a Pull Request "
        "diff represents a genuinely complex/architectural contribution, or if it is "
        "trivial bloat (e.g. large JSON data dumps, package-lock.json updates, simple "
        "variable renaming across many files, or auto-generated code).\n"
        "Reply STRICTLY with exactly one word: APPROVE or REJECT."
    )

    user_prompt = (
        f"PR Title: {pr['title']}\n\n"
        f"Git Diff:\n{diff[:50000]}" # Limit context to 50k chars
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={gemini_api_key}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
    }

    try:
        resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=30)
        resp.raise_for_status()
        resp_json = resp.json()
        
        candidates = resp_json.get("candidates", [])
        if not candidates:
            raise KeyError("No candidates returned from Gemini API")
            
        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        if not parts:
            finish_reason = candidate.get("finishReason")
            raise KeyError(f"No content parts returned. finishReason: {finish_reason}")
            
        verdict = parts[0].get("text", "").strip().upper()
        
        if "REJECT" in verdict:
            print(f"🛑 AI GATEKEEPER REJECTED: This PR appears to be trivial/bloat despite its size.")
            print(f"   Verdict received: {verdict}")
            print("   Exiting gracefully to prevent a fake shoutout.")
            sys.exit(0)
            
        print("✅ AI Gatekeeper Approved: PR is a genuine contribution.")
        
    except Exception as exc:
        print(f"⚠️  AI Gatekeeper evaluation failed ({exc}). Bypassing semantic check.")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Generate post with Gemini AI (Dynamic content)
# ─────────────────────────────────────────────────────────────────────────────
def generate_post_with_gemini(pr: dict, tier_display: str, tier_desc: str) -> str:
    """
    Calls Gemini 2.5 Flash to produce a unique, human-sounding LinkedIn post.
    High temperature = different text on every PR merge.
    Falls back to static template if API unavailable.
    """
    gemini_api_key = get_env_or_exit("GEMINI_API_KEY")

    system_prompt = (
        f"You are the core maintainer of '{PROJECT_NAME}'. "
        "Write a short, heartfelt, and genuine LinkedIn post thanking a contributor. "
        "It MUST sound like a real human engineer expressing sincere gratitude from the heart. "
        "Do not use corporate buzzwords. Keep it concise, high-quality, and impactful. "
        "Use minimal formatting and very few emojis. It should feel like a personal shoutout, not a bot. "
        "Never start with 'I am' or 'We are'."
    )

    user_prompt = (
        f"Write a short, genuine LinkedIn shoutout for this contributor:\n\n"
        f"Contributor: {pr['author']} (GitHub Profile: https://github.com/{pr['author']})\n"
        f"PR Title: {pr['title']}\n"
        f"PR Number: #{pr['number']}\n"
        f"Tier: {tier_display}\n"
        f"PR Link: {pr['url']}\n"
        f"Project: {PROJECT_NAME} — {PROJECT_TAGLINE}\n"
        f"PR Description: {pr['body'] if pr['body'] else 'Not provided'}\n\n"
        f"CRITICAL REQUIREMENTS:\n"
        f"1. Start by directly thanking and tagging the contributor as 'GitHub Contributor: @{pr['author']}' in a warm, personal way.\n"
        f"2. Mention briefly what they built ({pr['title']}) and why it's important for the project.\n"
        f"3. Make them feel truly valued. Tell them their hard work is making a real difference in this {tier_display} task. Motivate them to keep solving issues.\n"
        f"4. End by warmly welcoming new developers to join the journey (GSSoC2026), with the repo link: {PROJECT_GITHUB_URL}\n"
        f"5. Keep the text short and easy to read. Do NOT use heavy bullet points, bolding, or too many emojis.\n"
        f"6. Do NOT include hashtags at the end (they are added automatically later)."
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={gemini_api_key}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.8, "maxOutputTokens": 800},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
    }

    try:
        print("🤖 Calling Gemini AI to generate post...")
        resp = requests.post(url, headers={"Content-Type": "application/json"},
                             json=payload, timeout=30)
        resp.raise_for_status()
        resp_json = resp.json()
        
        candidates = resp_json.get("candidates", [])
        if not candidates:
            raise KeyError("No candidates returned from Gemini API")
            
        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        if not parts:
            finish_reason = candidate.get("finishReason")
            raise KeyError(f"No content parts returned. finishReason: {finish_reason}")
            
        text = parts[0].get("text", "").strip()
        print("✅ Gemini post generated successfully.")
        return text
    except Exception as exc:
        print(f"⚠️  Gemini AI failed ({exc}). Using static fallback.")
        return _static_fallback(pr, tier_display)


    return (
        f"A massive thank you from the heart to our contributor, @{pr['author']} (https://github.com/{pr['author']}).\n\n"
        f"They just landed PR #{pr['number']}: \"{pr['title']}\". "
        f"This was a {tier_display} contribution, and the effort put into it is truly inspiring. "
        f"Your work is directly helping {PROJECT_NAME} become a better platform for everyone. We deeply value your time and technical expertise. "
        f"Keep crushing those issues, @{pr['author']}!\n\n"
        f"If anyone else wants to make a real impact and join our open-source journey for GSSoC2026, we'd love to welcome you.\n\n"
        f"Repo: {PROJECT_GITHUB_URL}\n"
        f"View PR: {pr['url']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Assemble final post (Dynamic body + Static branding)
# ─────────────────────────────────────────────────────────────────────────────
def assemble_final_post(ai_content: str, pr: dict) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", ai_content).strip()
    return (
        f"{clean}\n\n"
        f"─────────────────────\n"
        f"{PROJECT_HASHTAGS}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Send to Make.com Webhook (Make posts to LinkedIn Company Page)
# ─────────────────────────────────────────────────────────────────────────────
def send_to_make_webhook(post_text: str, pr: dict) -> None:
    """
    Sends a JSON payload to Make.com webhook.
    Make.com handles the LinkedIn Company Page posting — no Advertising API needed.

    Payload fields Make.com will receive:
      - post_text   : Full formatted LinkedIn post
      - pr_title    : PR title (for Make filters/conditions if needed)
      - pr_author   : Contributor GitHub username
      - pr_url      : Direct link to the PR
      - pr_number   : PR number
      - tier        : "level:advanced" or "level:critical"
    """
    webhook_url = get_env_or_exit("MAKE_WEBHOOK_URL")

    labels = pr["labels"].lower()
    tier = "level:critical" if "level:critical" in labels else "level:advanced"

    payload = {
        "post_text": post_text,
        "pr_title": pr["title"],
        "pr_author": pr["author"],
        "pr_url": pr["url"],
        "pr_number": pr["number"],
        "tier": tier,
    }

    print("📤 Sending post to Make.com webhook...")
    print(f"   Webhook: {webhook_url[:50]}...")
    resp = requests.post(
        webhook_url,
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )

    if resp.status_code == 200 and resp.text.strip().lower() == "accepted":
        print("✅ Make.com accepted the payload — LinkedIn post will be published.")
    elif resp.status_code == 200:
        print(f"✅ Make.com responded 200: {resp.text[:100]}")
    else:
        print(f"❌ Make.com webhook error: {resp.status_code} — {resp.text}")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  SahiDawa LinkedIn Shoutout Bot (via Make.com)")
    print("=" * 60)

    pr = get_pr_metadata()
    print(f"\n📋 PR Details:")
    print(f"   Title  : {pr['title']}")
    print(f"   Author : @{pr['author']}")
    print(f"   Number : #{pr['number']}")
    print(f"   Labels : {pr['labels']}")
    print(f"   URL    : {pr['url']}")
    print(f"   Lines  : {pr['lines_changed']}\n")

    tier_display, tier_desc = determine_tier(pr["labels"])
    print(f"🏆 Tier: {tier_display}")

    # The Smart Gate Validations
    validate_pr_size(pr)
    evaluate_pr_impact(pr)

    ai_content = generate_post_with_gemini(pr, tier_display, tier_desc)
    final_post = assemble_final_post(ai_content, pr)

    print("\n" + "─" * 60)
    print("📝 FINAL POST PREVIEW:")
    print("─" * 60)
    print(final_post)
    print("─" * 60 + "\n")

    send_to_make_webhook(final_post, pr)
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
