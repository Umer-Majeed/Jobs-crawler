import os
import json
import time
import ssl
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import pandas as pd
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from jobspy import scrape_jobs

# ---------------------------------------------------------
# EMAIL, TELEGRAM & API CONFIG (READ FROM CLOUD SECRETS/ENV)
# ---------------------------------------------------------
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_APP_PASSWORD = os.environ.get("SENDER_APP_PASSWORD", "")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Initialize Gemini Client (automatically reads GEMINI_API_KEY from env)
client = genai.Client()

# ---------------------------------------------------------
# 1. AUTHORITATIVE SOURCE OF TRUTH (Profile & Region Constraints)
# ---------------------------------------------------------
MY_PROFILE = """
Candidate Name: Nabeel
Portfolio: https://nabeelcodes.vercel.app/
Primary Roles: Python Engineer, Backend Software Engineer, Full Stack Developer
Key Skills: Python, Django, FastAPI, Flask, PostgreSQL, REST APIs, Microservices, Cloud Architecture, Automation
Target Locations: US, USA, Canada, United States, EU, Europe, Dubai, UAE, Remote Worldwide
Constraint: ONLY open to 100% Fully Remote positions. Ignore physical/hybrid jobs outside target regions.
Rule: Never fabricate experience or skills not present in this baseline profile.
"""

# ---------------------------------------------------------
# 2. STRUCTURED MATCH SCHEMA FOR TITAN AGENTS
# ---------------------------------------------------------
class JobMatchEvaluation(BaseModel):
    match_score: int = Field(description="Compatibility score from 0 to 100 based on skill overlap, experience, and remote eligibility.")
    is_fully_remote: bool = Field(description="True if the position is 100% remote worldwide or fully remote WFH.")
    key_matching_skills: list[str] = Field(description="List of skills in the job description that match my profile.")
    missing_required_skills: list[str] = Field(description="Critical technical skills required by the job that I do not list.")
    summary_reasoning: str = Field(description="2-3 sentence justification for the match score.")
    apply_recommendation: bool = Field(description="True ONLY if match_score >= 50 AND is_fully_remote is True.")

def evaluate_job_match(job_title: str, company: str, job_description: str) -> JobMatchEvaluation:
    """Agent 1: Evaluates job compatibility with retry mechanism against freeze errors."""
    prompt = f"""
    You are the Match Engine Agent for PROJECT TITAN.
    
    MY CANDIDATE PROFILE:
    {MY_PROFILE}
    
    JOB TO EVALUATE:
    Company: {company}
    Title: {job_title}
    Description: {job_description[:3000]}
    
    Evaluate fit for a Software Developer/Python Backend Engineer.
    If the role is non-technical (e.g. Sales, Marketing, Payroll, Admin), score MUST be under 20.
    Pass apply_recommendation=True ONLY if match_score >= 50 and is_fully_remote is True.
    """

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=JobMatchEvaluation,
                    temperature=0.1,
                ),
            )
            return JobMatchEvaluation.model_validate_json(response.text)
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2)
            else:
                return JobMatchEvaluation(
                    match_score=0,
                    is_fully_remote=False,
                    key_matching_skills=[],
                    missing_required_skills=[],
                    summary_reasoning=f"API evaluation failed after {max_retries} attempts: {e}",
                    apply_recommendation=False
                )

def generate_humanized_cover_letter(company: str, job_title: str, job_description: str, matching_skills: list[str]) -> str:
    """Agent 2: Generates a natural pitch with API retry guard."""
    prompt = f"""
    You are Nabeel, writing a direct, personal email/pitch to a hiring manager at {company} for the {job_title} position.

    MY BASELINE PROFILE:
    {MY_PROFILE}
    
    JOB CONTEXT:
    Company: {company}
    Title: {job_title}
    Key Relevant Skills to Mention Naturally: {', '.join(matching_skills)}
    Description Snippet: {job_description[:1200]}
    
    CRITICAL HUMANIZE RULES (STRICT COMPLIANCE REQUIRED):
    1. NEVER sound like an AI assistant. Avoid buzzwords like "delve", "synergy", "seamlessly", "passionate".
    2. Write like a real developer sending a quick, confident email.
    3. First sentence must be casual and direct.
    4. Keep it concise (120 - 150 words max).
    5. Plug the portfolio link naturally: https://nabeelcodes.vercel.app/
    6. Ending should be a simple human sign-off (e.g., "Best, Nabeel").
    """

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                ),
            )
            return response.text.strip()
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2)
            else:
                return f"Hi Hiring Manager,\n\nI noticed the {job_title} role at {company} and would love to connect. I specialize in Python, FastAPI, and cloud backends.\n\nYou can review my work here: https://nabeelcodes.vercel.app/\n\nBest,\nNabeel"

def send_telegram_alert(company: str, title: str, score: int, job_url: str):
    """Agent 3: Telegram Alert with SSL unverified context & exponential backoff retry."""
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID

    if not token or not chat_id:
        print("   ⚠️ Telegram Alert Warning: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables.")
        return
    
    raw_message = (
        f"🚨 <b>PROJECT TITAN: NEW JOB MATCH</b> 🚨\n\n"
        f"🏢 <b>Company:</b> {company}\n"
        f"🎯 <b>Role:</b> {title}\n"
        f"📊 <b>Score:</b> {score}%\n\n"
        f"🔗 <a href='{job_url}'>Apply Here</a>"
    )
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        'chat_id': chat_id,
        'text': raw_message,
        'parse_mode': 'HTML'
    }).encode('utf-8')
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    # SSL Context Bypass to prevent local Windows SSL handshake timeouts
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as response:
                if response.status == 200:
                    print(f"   📲 Telegram Alert Sent Successfully!")
                    return
        except Exception as e:
            if attempt < max_retries:
                time.sleep(3 * attempt)  # Pause longer on retries
            else:
                print(f"   ⚠️ Telegram Alert Warning (Failed after {max_retries} tries): {e}")

def fetch_live_remote_jobs() -> list:
    """Fetches live remote engineering jobs from multiple RSS sources."""
    sources = [
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://remoteok.com/remote-python-jobs.rss",
        "https://himalayas.app/jobs/rss?specialty=python"
    ]
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    jobs = []
    
    for url in sources:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                
                for item in root.findall('./channel/item')[:15]:
                    title_full = item.find('title').text if item.find('title') is not None else ""
                    link = item.find('link').text if item.find('link') is not None else ""
                    description = item.find('description').text if item.find('description') is not None else ""
                    
                    company = "Remote Company"
                    title = title_full
                    if ":" in title_full:
                        company, title = title_full.split(":", 1)
                    elif " is hiring " in title_full:
                        company, title = title_full.split(" is hiring ", 1)
                        
                    jobs.append({
                        "title": title.strip(),
                        "company": company.strip(),
                        "description": description.strip(),
                        "job_url": link.strip()
                    })
        except Exception as e:
            print(f"   ⚠️ RSS Fetch Warning ({url}): {e}")
            
    return jobs

def fetch_all_job_sources(target_jobs: int = 100) -> pd.DataFrame:
    """Master Scraper: Targeted for USA, EU, Canada & Dubai across tech keywords."""
    all_jobs = []
    search_terms = ["Python Engineer", "Backend Developer", "Full Stack Engineer", "FastAPI Developer"]
    target_locations = ["USA", "Canada", "Europe", "Dubai", "Remote"]

    # 1. Multi-Keyword & Location JobSpy Scraping
    print("🔎 Step 1: Scraping targeted regions (USA, EU, Canada, Dubai) via JobSpy...")
    for loc in target_locations:
        for term in search_terms:
            if len(all_jobs) >= target_jobs + 30:
                break
            print(f"   -> Searching: Term '{term}' in '{loc}'...")
            try:
                jobs_df = scrape_jobs(
                    site_name=["linkedin", "zip_recruiter", "glassdoor"],
                    search_term=term,
                    location=loc,
                    results_wanted=15,
                    is_remote=True
                )
                for _, row in jobs_df.iterrows():
                    desc = str(row.get('description', 'N/A'))
                    if len(desc) > 50:
                        all_jobs.append({
                            "title": str(row.get('title', 'N/A')),
                            "company": str(row.get('company', 'N/A')),
                            "description": desc,
                            "job_url": str(row.get('job_url', 'N/A'))
                        })
            except Exception:
                pass
        print(f"      ✓ Total collected so far: {len(all_jobs)}")

    # 2. Remotive API Engine
    print("🌐 Step 2: Fetching bulk developer jobs from Remotive API...")
    try:
        url = "https://remotive.com/api/remote-jobs?category=software-dev&limit=100"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
            for job in data.get('jobs', []):
                all_jobs.append({
                    "title": job.get('title', 'N/A'),
                    "company": job.get('company_name', 'N/A'),
                    "description": job.get('description', 'N/A'),
                    "job_url": job.get('url', 'N/A')
                })
        print(f"   ✓ Remotive added. Total collected: {len(all_jobs)}")
    except Exception as e:
        print(f"   ⚠️ Remotive API error: {e}")

    # 3. Enhanced Live RSS Feeds (WeWorkRemotely, RemoteOK, Himalayas)
    print("🌐 Step 3: Fetching jobs from Live RSS Feeds (WeWorkRemotely, RemoteOK, Himalayas)...")
    rss_jobs = fetch_live_remote_jobs()
    all_jobs.extend(rss_jobs)
    print(f"   ✓ RSS feeds added. Total collected: {len(all_jobs)}")

    # Clean & Deduplicate Data
    df = pd.DataFrame(all_jobs)
    if not df.empty:
        df.drop_duplicates(subset=['title', 'company'], inplace=True)
    
    total_found = len(df)
    print(f"\n📊 Total Unique Jobs Ready for Processing: {total_found}")
    
    if total_found > target_jobs:
        df = df.head(target_jobs)
        print(f"🎯 Trimmed list to target limit of {target_jobs} jobs.")
    elif total_found < target_jobs:
        print(f"⚠️ Collected {total_found} unique jobs. Proceeding with available list.")

    return df

# ---------------------------------------------------------
# 3. TITAN ORCHESTRATOR
# ---------------------------------------------------------
def run_titan_discovery():
    print("🚀 [PROJECT TITAN] Starting High-Volume Region-Filtered Pipeline...\n")
    
    output_dir = os.path.abspath("generated_cover_letters")
    os.makedirs(output_dir, exist_ok=True)
    
    # Target 100 jobs
    jobs = fetch_all_job_sources(target_jobs=100)
    
    if jobs.empty:
        print("❌ Could not fetch any jobs from listed sources.")
        return

    total_jobs = len(jobs)
    print(f"\n⚡ Starting Evaluation Pipeline for {total_jobs} jobs...\n" + "="*65)

    processed_jobs = []

    for index, row in jobs.iterrows():
        title = str(row.get('title', 'N/A'))
        company = str(row.get('company', 'N/A'))
        description = str(row.get('description', 'N/A'))
        job_url = str(row.get('job_url', 'N/A'))

        if len(description) < 50:
            continue

        print(f"⚡ [{index+1}/{total_jobs}] Processing: {title} at {company}...")
        
        # Agent 1: Match Engine
        match_result = evaluate_job_match(title, company, description)
        
        if match_result.apply_recommendation:
            status = "ELIGIBLE (>=50% & Fully Remote)"
        elif not match_result.is_fully_remote:
            status = "REJECTED (Not Fully Remote)"
        else:
            status = "REJECTED (Score < 50%)"

        print(f"   Score: {match_result.match_score}% | Fully Remote: {match_result.is_fully_remote} | Decision: {status}")
        
        cover_letter_file = "N/A"
        
        # Agent 2 & 3 Trigger
        if match_result.apply_recommendation:
            print(f"   ✍️ Agent 2 Triggered: Drafting humanized pitch...")
            letter_text = generate_humanized_cover_letter(company, title, description, match_result.key_matching_skills)
            
            safe_company = "".join(c for c in company if c.isalnum() or c in (' ', '_')).strip().replace(" ", "_")
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '_')).strip().replace(" ", "_")
            
            filename = os.path.join(output_dir, f"{safe_company}_{safe_title}.md")
            
            with open(filename, "w", encoding="utf-8") as f:
                f.write(letter_text)
                
            cover_letter_file = filename
            print(f"   📄 Cover Letter saved: {filename}")
            
            # Agent 3: Send Alert
            send_telegram_alert(company, title, match_result.match_score, job_url)
            
        print("-" * 65)

        processed_jobs.append({
            "Company": company,
            "Job Title": title,
            "Match Score": match_result.match_score,
            "Fully Remote": match_result.is_fully_remote,
            "Apply Recommended": match_result.apply_recommendation,
            "Matching Skills": ", ".join(match_result.key_matching_skills),
            "Cover Letter File": cover_letter_file,
            "Reasoning": match_result.summary_reasoning,
            "Job URL": job_url
        })

    df_results = pd.DataFrame(processed_jobs)
    df_results.to_csv("titan_application_tracker.csv", index=False)
    print("\n✅ High-Volume Pipeline Complete! Output saved to 'titan_application_tracker.csv'.")

if __name__ == "__main__":
    run_titan_discovery()