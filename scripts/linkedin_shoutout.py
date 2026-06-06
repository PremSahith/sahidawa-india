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
        "author_avatar": os.environ.get("PR_AUTHOR_AVATAR", ""),
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


def extract_linkedin_url(body: str) -> str:
    # Requires a format like "LinkedIn: https://linkedin.com/in/username" 
    # to avoid accidentally extracting a random link from the PR body.
    match = re.search(r'(?i)LinkedIn(?: Profile(?: URL)?)?:\s*(https:\/\/(www\.)?linkedin\.com\/in\/[A-Za-z0-9_-]+)', body)
    if match:
        return match.group(1)
    return ""


def check_if_commented(pr_number: str, comment_snippet: str) -> bool:
    import subprocess
    try:
        result = subprocess.run(
            ['gh', 'pr', 'view', pr_number, '--json', 'comments'], 
            capture_output=True, text=True
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for c in data.get("comments", []):
                if comment_snippet in c.get("body", ""):
                    return True
    except Exception as e:
        print(f"Failed to check comments: {e}")
    return False


def validate_linkedin_url(pr: dict) -> str:
    linkedin_url = extract_linkedin_url(pr.get("body", ""))
    if not linkedin_url:
        print("🛑 REJECTED: No LinkedIn Profile URL found in the PR description.")
        print("   Without a LinkedIn URL, we cannot properly tag/mention the contributor.")
        print("   Exiting gracefully without triggering Make.com webhook.")
        
        pr_number = pr.get("number")
        if pr_number and pr_number != "N/A":
            comment_snippet = "Your PR is approved for a LinkedIn shoutout!"
            comment_text = (
                f"👋 {comment_snippet}\n"
                f"To get featured, please add your LinkedIn ID to the PR description like this:\n"
                f"`LinkedIn: https://linkedin.com/in/your-username`"
            )
            if not check_if_commented(pr_number, comment_snippet):
                import subprocess
                subprocess.run(['gh', 'pr', 'comment', pr_number, '--body', comment_text])
        
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a") as f:
                f.write("shoutout_status=skipped\n")
        
        sys.exit(0)
    print(f"✅ Found LinkedIn URL: {linkedin_url}")
    return linkedin_url


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
        f"Contributor: {pr['author']} (LinkedIn: {pr['linkedin_url']})\n"
        f"PR Title: {pr['title']}\n"
        f"PR Number: #{pr['number']}\n"
        f"Tier: {tier_display}\n"
        f"PR Link: {pr['url']}\n"
        f"Project: {PROJECT_NAME} — {PROJECT_TAGLINE}\n"
        f"PR Description: {pr['body'] if pr['body'] else 'Not provided'}\n\n"
        f"### Technical Context (Use this to explain their impact) ###\n"
        f"{pr['diff'][:15000] if pr.get('diff') else 'No diff provided.'}\n\n"
        f"CRITICAL REQUIREMENTS:\n"
        f"1. Start by directly thanking the contributor and including their LinkedIn profile link: {pr['linkedin_url']} in a warm, personal way.\n"
        f"2. Look at the Technical Context (the code diff) and briefly summarize the technical impact they made (e.g. 'They optimized the notification module'). Make them feel proud of the exact files/logic they improved.\n"
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

    import time
    
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            print(f"🤖 Calling Gemini AI to generate post... (Attempt {attempt}/{max_retries})")
            resp = requests.post(url, headers={"Content-Type": "application/json"},
                                 json=payload, timeout=30)
            
            if resp.status_code == 429:
                print(f"⏳ Rate limit hit (429). Retrying in 20 seconds...")
                time.sleep(20)
                continue
                
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
            
            print("\n✅ Script completed successfully!")
            print("✅ Gemini post generated successfully.")
            return text
            
        except Exception as exc:
            if attempt == max_retries:
                print(f"⚠️  Gemini AI failed after {max_retries} attempts ({exc}). Using static fallback.")
                return _static_fallback(pr, tier_display)
            print(f"⚠️  Gemini AI failed ({exc}). Retrying in 10 seconds...")
            time.sleep(10)
            
    return _static_fallback(pr, tier_display)


def _static_fallback(pr: dict, tier_display: str) -> str:
    return (
        f"A massive thank you from the heart to our contributor, {pr['author']} ({pr['linkedin_url']}).\n\n"
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
def generate_and_upload_banner(pr: dict) -> str:
    """
    Generates a beautiful GSSoC Star Contributor shoutout banner image
    using Pillow, and uploads it to Catbox.moe (with tmpfiles.org fallback).
    Returns the uploaded image URL, or falls back to githubassets OG image on failure.
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    from io import BytesIO
    import requests

    width, height = 1200, 630
    
    try:
        # 1. Base image with gradient
        base = Image.new("RGBA", (width, height))
        pixels = base.load()
        # Vibe: Sleek dark-mode tech background (indigo to deep violet)
        left_color = (13, 14, 25)
        right_color = (30, 16, 60)
        
        for x in range(width):
            r = int(left_color[0] + (right_color[0] - left_color[0]) * (x / width))
            g = int(left_color[1] + (right_color[1] - left_color[1]) * (x / width))
            b = int(left_color[2] + (right_color[2] - left_color[2]) * (x / width))
            for y in range(height):
                pixels[x, y] = (r, g, b, 255)
                
        draw = ImageDraw.Draw(base)
        
        # Add abstract glowing background blobs
        glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)
        glow_draw.ellipse((800, -200, 1400, 400), fill=(235, 122, 38, 30))  # Orange GSSoC glow
        glow_draw.ellipse((-100, 300, 400, 800), fill=(100, 50, 255, 25))  # Indigo glow
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(80))
        base = Image.alpha_composite(base, glow_layer)
        draw = ImageDraw.Draw(base)

        # 2. Load custom fonts
        font_bold_url = "https://cdn.jsdelivr.net/fontsource/fonts/outfit@latest/latin-700-normal.ttf"
        font_reg_url = "https://cdn.jsdelivr.net/fontsource/fonts/outfit@latest/latin-400-normal.ttf"
        
        bold_font_data = None
        try:
            bold_font_data = requests.get(font_bold_url, timeout=8).content
            reg_font_data = requests.get(font_reg_url, timeout=8).content
            font_badge = ImageFont.truetype(BytesIO(bold_font_data), 26)
            font_title = ImageFont.truetype(BytesIO(bold_font_data), 54)
            font_body = ImageFont.truetype(BytesIO(reg_font_data), 32)
            font_repo = ImageFont.truetype(BytesIO(bold_font_data), 24)
            font_tagline = ImageFont.truetype(BytesIO(reg_font_data), 20)
        except Exception as fe:
            print(f"⚠️ Font download failed ({fe}), using default fonts")
            font_badge = font_title = font_body = font_repo = font_tagline = ImageFont.load_default()

        # 3. Contributor Avatar
        avatar_url = pr.get("author_avatar")
        if not avatar_url:
            avatar_url = f"https://github.com/{pr['author']}.png"
            
        try:
            print(f"Downloading avatar from {avatar_url}")
            r = requests.get(avatar_url, timeout=8)
            r.raise_for_status()
            avatar = Image.open(BytesIO(r.content)).convert("RGBA")
        except Exception as ae:
            print(f"⚠️ Avatar download failed ({ae}), generating fallback letter avatar")
            avatar = Image.new("RGBA", (200, 200), (255, 255, 255, 0))
            av_draw = ImageDraw.Draw(avatar)
            av_draw.ellipse((0, 0, 200, 200), fill=(235, 122, 38, 255))
            # Draw first letter of author name
            first_letter = pr['author'][0].upper() if pr.get('author') else "C"
            if bold_font_data:
                try:
                    av_draw.text((70, 40), first_letter, fill=(255, 255, 255, 255), font=ImageFont.truetype(BytesIO(bold_font_data), 100))
                except Exception:
                    av_draw.text((80, 70), first_letter, fill=(255, 255, 255, 255))
            else:
                av_draw.text((80, 70), first_letter, fill=(255, 255, 255, 255))
                
        avatar = avatar.resize((200, 200), Image.Resampling.LANCZOS)
        
        # Circular mask for avatar
        mask = Image.new("L", (200, 200), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 200, 200), fill=255)
        
        # Draw avatar borders/rings on base image
        draw.ellipse((85, 200, 315, 430), outline=(235, 122, 38, 255), width=8)  # Orange border
        draw.ellipse((92, 207, 308, 423), outline=(255, 215, 0, 255), width=3)   # Gold inner ring
        
        avatar_img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        avatar_img.paste(avatar, (0, 0), mask=mask)
        base.paste(avatar_img, (100, 215), mask=avatar_img)

        # 4. Text Content (Right column, starting X = 360)
        # GSSoC Badge Pill
        badge_text = "⚡ GSSoC 2026 Star Contributor"
        try:
            bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except Exception:
            tw, th = 340, 30
            
        badge_padding_x, badge_padding_y = 20, 10
        badge_w = tw + badge_padding_x * 2
        badge_h = th + badge_padding_y * 2
        badge_x, badge_y = 360, 120
        
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
            radius=badge_h // 2,
            fill=(235, 122, 38, 40),
            outline=(235, 122, 38, 255),
            width=2
        )
        draw.text((badge_x + badge_padding_x, badge_y + badge_padding_y - 2), badge_text, fill=(255, 165, 0, 255), font=font_badge)

        # Headline
        headline_text = f"Huge thanks, @{pr['author']}! 🎉"
        draw.text((360, 195), headline_text, fill=(255, 255, 255, 255), font=font_title)

        # Body appreciation text
        body_line1 = "For making outstanding contributions to SahiDawa."
        max_title_len = 45
        pr_title = pr['title']
        if len(pr_title) > max_title_len:
            pr_title = pr_title[:max_title_len].strip() + "..."
        body_line2 = f"Merged PR #{pr['number']}: \"{pr_title}\""
        
        draw.text((360, 280), body_line1, fill=(200, 200, 220, 255), font=font_body)
        draw.text((360, 330), body_line2, fill=(255, 215, 0, 255), font=font_body)

        # 5. Project Logo & Info in footer
        logo_url = "https://avatars.githubusercontent.com/u/244338981"
        try:
            lr = requests.get(logo_url, timeout=5)
            if lr.status_code == 200:
                logo = Image.open(BytesIO(lr.content)).convert("RGBA")
                logo = logo.resize((80, 80), Image.Resampling.LANCZOS)
                logo_mask = Image.new("L", (80, 80), 0)
                logo_mask_draw = ImageDraw.Draw(logo_mask)
                logo_mask_draw.ellipse((0, 0, 80, 80), fill=255)
                
                # Draw logo ring
                draw.ellipse((1015, 475, 1105, 565), outline=(255, 255, 255, 200), width=4)
                
                logo_img = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
                logo_img.paste(logo, (0, 0), mask=logo_mask)
                base.paste(logo_img, (1020, 480), mask=logo_img)
        except Exception as le:
            print(f"⚠️ SahiDawa logo render skipped: {le}")

        draw.text((360, 485), "SahiDawa / RatLoopz", fill=(255, 255, 255, 255), font=font_repo)
        draw.text((360, 520), "India's open-source medicine safety platform 🇮🇳", fill=(150, 150, 170, 255), font=font_tagline)

        # Save buffer
        img_buffer = BytesIO()
        base.convert("RGB").save(img_buffer, "PNG")
        img_data = img_buffer.getvalue()

        # 6. Upload to Catbox (Primary)
        print("📤 Uploading generated image to Catbox...")
        try:
            files = {
                'reqtype': (None, 'fileupload'),
                'fileToUpload': ('shoutout.png', img_data, 'image/png')
            }
            res = requests.post('https://catbox.moe/user/api.php', files=files, timeout=15)
            if res.status_code == 200 and "files.catbox.moe" in res.text:
                uploaded_url = res.text.strip()
                print(f"✅ Successfully uploaded to Catbox: {uploaded_url}")
                return uploaded_url
        except Exception as ce:
            print(f"⚠️ Catbox upload failed ({ce}). Trying tmpfiles.org fallback...")

        # 7. Upload to tmpfiles.org (Secondary Fallback)
        try:
            files = {
                'file': ('shoutout.png', img_data, 'image/png')
            }
            res = requests.post('https://tmpfiles.org/api/v1/upload', files=files, timeout=15)
            if res.status_code == 200:
                json_resp = res.json()
                if json_resp.get("status") == "success":
                    raw_url = json_resp["data"]["url"]
                    uploaded_url = raw_url.replace("https://tmpfiles.org/", "https://tmpfiles.org/dl/")
                    print(f"✅ Successfully uploaded to tmpfiles.org: {uploaded_url}")
                    return uploaded_url
        except Exception as te:
            print(f"⚠️ tmpfiles.org upload failed ({te})")

    except Exception as e:
        print(f"❌ Error during image generation process: {e}")

    # Fallback to github open graph if everything fails
    fallback_url = f"https://opengraph.githubassets.com/1/{pr['repo']}/pull/{pr['number']}"
    print(f"⚠️ Using GitHub OpenGraph fallback URL: {fallback_url}")
    return fallback_url


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
      - author_avatar : URL to contributor's GitHub avatar
    """
    webhook_url = get_env_or_exit("MAKE_WEBHOOK_URL")

    labels = pr["labels"].lower()
    tier = "level:critical" if "level:critical" in labels else "level:advanced"

    # Generate a dynamic Thank You banner image URL using local Pillow and Catbox
    image_url = generate_and_upload_banner(pr)
    
    payload = {
        "post_text": post_text,
        "pr_title": pr["title"],
        "pr_author": pr["author"],
        "author_avatar": pr.get("author_avatar", ""),
        "image_url": image_url,
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

    # Check for LinkedIn Profile Link
    pr["linkedin_url"] = validate_linkedin_url(pr)

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
    
    # Final Output Step
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write("shoutout_status=published\n")
            
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
