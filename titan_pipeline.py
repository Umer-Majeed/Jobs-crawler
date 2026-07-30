import os
import json
import time
import sqlite3
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import pandas as pd
import smtplib
from email.message import EmailMessage
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from jobspy import scrape_jobs

# ---------------------------------------------------------
# EMAIL & API CONFIG (READ FROM CLOUD SECRETS/ENV)
# ---------------------------------------------------------
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_APP_PASSWORD = os.environ.get("SENDER_APP_PASSWORD", "")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "")

# Initialize Gemini Client (automatically reads GEMINI_API_KEY from env)
client = genai.Client()

# ---------------------------------------------------------
# SQLITE MEMORY DATABASE (DEDUPLICATION)
# ---------------------------------------------------------
def init_db():
    """SQLite Database initialize karta hai pehle se processed jobs track karne ke liye."""
    conn = sqlite3.connect("titan_memory.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_jobs (
            job_key TEXT PRIMARY KEY,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def is_job_processed(job_key: str) -> bool:
    """Check karta hai ke job pehle process ho chuki hai ya nahi."""
    conn = sqlite3.connect("titan_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_jobs WHERE job_key = ?", (job_key,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def save_processed_job(job_key: str):
    """Naye processed job key ko database mein insert karta hai."""
    conn = sqlite3.connect("titan_memory.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO processed_jobs (job_key) VALUES (?)", (job_key,))
    conn.commit()
    conn.close()

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

class CompanyEnrichmentInsights(BaseModel):
    estimated_salary_range: str = Field(description="Estimated salary range for the role if mentioned or inferred (e.g., '$120k - $150k' or 'Not Specified').")
    tech_stack_breakdown: list[str] = Field(description="List of primary backend, cloud, and database technologies required.")
    company_vibe_summary: str = Field(description="1 sentence summary about the engineering environment or company focus based on description.")

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

def enrich_job_insights(company: str, job_title: str, job_description: str) -> CompanyEnrichmentInsights:
    """Agent 1.5: Enriches job data with salary estimates, tech stack breakdown, and company insights."""
    prompt = f"""
    Analyze the following job description for {job_title} at {company}.
    
    Description:
    {job_description[:2500]}
    
    Extract:
    1. Estimated salary range (if available or standard market rate for remote python roles).
    2. Specific tech stack breakdown mentioned.
    3. Brief 1-sentence company/role vibe summary.
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CompanyEnrichmentInsights,
                temperature=0.1,
            ),
        )
        return CompanyEnrichmentInsights.model_validate_json(response.text)
    except Exception:
        return CompanyEnrichmentInsights(
            estimated_salary_range="Not Specified",
            tech_stack_breakdown=["Python", "Backend"],
            company_vibe_summary="Remote software engineering position."
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

def send_email_alert(company: str, title: str, score: int, job_url: str, cover_letter_text: str, insights: dict = None):
    """Agent 3: Sends an instant HTML Email alert with Cover Letter and Company Insights."""
    if not SENDER_EMAIL or not SENDER_APP_PASSWORD or not RECEIVER_EMAIL:
        print("   ⚠️ Email credentials or receiver email not configured in secrets. Skipping email alert.")
        return

    msg = EmailMessage()
    msg['Subject'] = f"🚨 Titan Job Match: {title} at {company} ({score}%)"
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL

    salary = insights.get('estimated_salary_range', 'Not Specified') if insights else 'Not Specified'
    techs = ", ".join(insights.get('tech_stack_breakdown', [])) if insights else 'Standard Stack'
    vibe = insights.get('company_vibe_summary', '') if insights else ''

    formatted_letter = cover_letter_text.replace('\n', '<br>')
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #f4f6f9; padding: 20px; }}
            .container {{ background-color: #ffffff; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); max-width: 600px; margin: auto; }}
            .header {{ font-size: 20px; font-weight: bold; color: #1e293b; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; margin-bottom: 15px; }}
            .badge {{ background-color: #10b981; color: white; padding: 4px 8px; border-radius: 4px; font-size: 14px; font-weight: bold; }}
            .info-table {{ width: 100%; margin-bottom: 15px; border-collapse: collapse; }}
            .info-table td {{ padding: 5px 0; color: #475569; font-size: 14px; }}
            .insights-box {{ background-color: #eff6ff; border-left: 4px solid #3b82f6; padding: 12px; border-radius: 4px; margin-bottom: 15px; font-size: 13px; color: #1e3a8a; }}
            .pitch-box {{ background-color: #f8fafc; border-left: 4px solid #2563eb; padding: 15px; border-radius: 4px; font-size: 14px; color: #334155; line-height: 1.6; font-family: monospace; }}
            .button-container {{ text-align: center; margin-top: 25px; }}
            .btn-apply {{ background-color: #2563eb; color: #ffffff !important; text-decoration: none; padding: 12px 25px; font-weight: bold; border-radius: 6px; display: inline-block; font-size: 16px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">🚨 High Match Remote Job Found!</div>
            <table class="info-table">
                <tr><td><strong>Company:</strong> {company}</td></tr>
                <tr><td><strong>Role:</strong> {title}</td></tr>
                <tr><td><strong>Match Score:</strong> <span class="badge">{score}%</span></td></tr>
            </table>

            <div class="insights-box">
                <strong>💡 Company & Role Insights:</strong><br>
                • <strong>Estimated Salary:</strong> {salary}<br>
                • <strong>Tech Stack:</strong> {techs}<br>
                • <strong>Summary:</strong> {vibe}
            </div>

            <div style="font-weight: bold; color: #1e293b; margin-bottom: 8px;">Generated Cover Letter / Pitch:</div>
            <div class="pitch-box">
                {formatted_letter}
            </div>

            <div class="button-container">
                <a href="{job_url}" class="btn-apply" target="_blank">🚀 Apply Now Direct Link</a>
            </div>
        </div>
    </body>
    </html>
    """

    msg.set_content(f"Job Match: {title} at {company} ({score}%).\nSalary: {salary}\nLink: {job_url}")
    msg.add_alternative(html_content, subtype='html')

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
            smtp.send_message(msg)
        print("   📧 Enriched HTML Email Alert Sent Successfully!")
    except Exception as e:
        print(f"   ⚠️ Email Error: {e}")

def send_daily_summary_email(summary_stats: dict):
    """Agent 3.1: Sends a daily executive summary digest of all scanned and matched jobs."""
    if not SENDER_EMAIL or not SENDER_APP_PASSWORD or not RECEIVER_EMAIL:
        return

    msg = EmailMessage()
    msg['Subject'] = f"📊 Titan Daily Digest: {summary_stats['total_scanned']} Jobs Scanned | {summary_stats['eligible_count']} Eligible"
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #f4f6f9; padding: 20px; }}
            .container {{ background-color: #ffffff; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); max-width: 600px; margin: auto; }}
            .header {{ font-size: 20px; font-weight: bold; color: #1e293b; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; margin-bottom: 15px; }}
            .stat-box {{ display: flex; justify-content: space-between; background: #f8fafc; padding: 12px; border-radius: 6px; margin-bottom: 10px; font-size: 14px; color: #334155; }}
            .badge-green {{ background-color: #10b981; color: white; padding: 2px 6px; border-radius: 4px; font-weight: bold; }}
            .badge-red {{ background-color: #ef4444; color: white; padding: 2px 6px; border-radius: 4px; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">📊 Project Titan - Daily Executive Summary</div>
            <p>Here is the automated execution report for your remote job hunting pipeline:</p>
            
            <div class="stat-box"><span>Total Jobs Scanned:</span> <strong>{summary_stats['total_scanned']}</strong></div>
            <div class="stat-box"><span>Eligible Matches (>=50% & Remote):</span> <span class="badge-green">{summary_stats['eligible_count']}</span></div>
            <div class="stat-box"><span>Skipped / Filtered Out:</span> <span class="badge-red">{summary_stats['rejected_count']}</span></div>
            
            <p style="margin-top: 20px; font-size: 13px; color: #64748b;">Detailed records and cover letters have been updated in your GitHub repository tracker and memory database.</p>
        </div>
    </body>
    </html>
    """

    msg.set_content(f"Titan Daily Summary:\nTotal Scanned: {summary_stats['total_scanned']}\nEligible: {summary_stats['eligible_count']}")
    msg.add_alternative(html_content, subtype='html')

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
            smtp.send_message(msg)
        print("   📊 Daily Summary Digest Email Sent Successfully!")
    except Exception as e:
        print(f"   ⚠️ Summary Email Error: {e}")

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
        print(f"     ✓ Total collected so far: {len(all_jobs)}")

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
    
    # SQLite Database initialize karein
    init_db()
    
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
        title = str(row.get('title', 'N/A')).strip()
        company = str(row.get('company', 'N/A')).strip()
        description = str(row.get('description', 'N/A'))
        job_url = str(row.get('job_url', 'N/A'))

        if len(description) < 50:
            continue

        # Unique Key generation (Company + Title)
        job_key = f"{company.lower()}_{title.lower()}"

        # Check if already processed in previous runs
        if is_job_processed(job_key):
            print(f"⏩ [{index+1}/{total_jobs}] Skipping (Already Processed): {title} at {company}")
            continue

        print(f"⚡ [{index+1}/{total_jobs}] Processing: {title} at {company}...")
        
        # Agent 1: Match Engine
        match_result = evaluate_job_match(title, company, description)
        
        # Save to database taake future runs mein skip ho sake
        save_processed_job(job_key)

        if match_result.apply_recommendation:
            status = "ELIGIBLE (>=50% & Fully Remote)"
        elif not match_result.is_fully_remote:
            status = "REJECTED (Not Fully Remote)"
        else:
            status = "REJECTED (Score < 50%)"

        print(f"   Score: {match_result.match_score}% | Fully Remote: {match_result.is_fully_remote} | Decision: {status}")
        
        cover_letter_file = "N/A"
        insights = None
        
        # Agent 1.5, 2 & 3 Trigger
        if match_result.apply_recommendation:
            print(f"   ✍️ Agent 1.5 Triggered: Extracting salary & tech stack insights...")
            insights = enrich_job_insights(company, title, description)
            
            print(f"   ✍️ Agent 2 Triggered: Drafting humanized pitch...")
            letter_text = generate_humanized_cover_letter(company, title, description, match_result.key_matching_skills)
            
            safe_company = "".join(c for c in company if c.isalnum() or c in (' ', '_')).strip().replace(" ", "_")
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '_')).strip().replace(" ", "_")
            
            filename = os.path.join(output_dir, f"{safe_company}_{safe_title}.md")
            
            with open(filename, "w", encoding="utf-8") as f:
                f.write(letter_text)
                
            cover_letter_file = filename
            print(f"   📄 Cover Letter saved: {filename}")
            
            # Agent 3: Send Enriched HTML Email Alert
            insights_dict = {
                "estimated_salary_range": insights.estimated_salary_range,
                "tech_stack_breakdown": insights.tech_stack_breakdown,
                "company_vibe_summary": insights.company_vibe_summary
            }
            send_email_alert(company, title, match_result.match_score, job_url, letter_text, insights=insights_dict)
            
        print("-" * 65)

        processed_jobs.append({
            "Company": company,
            "Job Title": title,
            "Match Score": match_result.match_score,
            "Estimated Salary": insights.estimated_salary_range if match_result and insights else "N/A",
            "Tech Stack": ", ".join(insights.tech_stack_breakdown) if match_result and insights else "N/A",
            "Fully Remote": match_result.is_fully_remote,
            "Apply Recommended": match_result.apply_recommendation,
            "Cover Letter File": cover_letter_file,
            "Job URL": job_url
        })

    if processed_jobs:
        df_results = pd.DataFrame(processed_jobs)
        # Check if CSV exists to append or write new with header
        csv_file = "titan_application_tracker.csv"
        file_exists = os.path.isfile(csv_file)
        df_results.to_csv(csv_file, mode='a' if file_exists else 'w', header=not file_exists, index=False)
        
        # Calculate stats for summary digest
        eligible_count = len(df_results[df_results['Apply Recommended'] == True])
        rejected_count = len(df_results) - eligible_count
        
        summary_stats = {
            "total_scanned": len(df_results),
            "eligible_count": eligible_count,
            "rejected_count": rejected_count
        }
        
        # Send Daily Summary Digest
        send_daily_summary_email(summary_stats)
        
        print("\n✅ High-Volume Pipeline Complete! New records added to 'titan_application_tracker.csv'.")
    else:
        print("\nℹ️ Pipeline Complete: All fetched jobs were already processed previously.")

if __name__ == "__main__":
    run_titan_discovery()