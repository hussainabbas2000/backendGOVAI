import os
import json
from datetime import datetime
from flask import Blueprint, request, jsonify
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

suggestions_bp = Blueprint('suggestions', __name__, url_prefix='/api')

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

COMPANY_PROFILE = {
    "company_name": "Apex Federal Solutions LLC",
    "naics_codes": ["541512", "541519", "541611", "541690"],
    "set_aside_qualifications": ["Small Business", "8(a)", "HUBZone"],
    "capabilities": [
        "IT modernization", "cloud migration", "cybersecurity",
        "data analytics", "software development", "DevSecOps",
        "managed IT services", "systems integration"
    ],
    "geographic_preferences": ["VA", "MD", "DC", "Remote"],
    "past_performance": [
        "DoD IT infrastructure modernization",
        "VA healthcare systems integration",
        "DHS border security analytics platform"
    ],
}

SCORE_THRESHOLD = 50
MAX_AI_CANDIDATES = 60


def _rule_based_prefilter(opportunities):
    """Fast rule-based pass over all opportunities without GPT.
    Returns top candidates sorted by preliminary rule score."""
    naics_set = set(COMPANY_PROFILE["naics_codes"])
    naics_prefixes = {code[:4] for code in COMPANY_PROFILE["naics_codes"]}
    sa_keywords = {q.lower() for q in COMPANY_PROFILE["set_aside_qualifications"]}
    cap_keywords = {w.lower() for w in COMPANY_PROFILE["capabilities"]}
    geo_prefs = {g.lower() for g in COMPANY_PROFILE["geographic_preferences"]}

    now = datetime.utcnow()
    scored = []

    for opp in opportunities:
        rule_score = 0

        naics = (opp.get("ncode") or "").strip()
        if naics in naics_set:
            rule_score += 40
        elif naics[:4] in naics_prefixes:
            rule_score += 20
        elif naics[:2] in {c[:2] for c in COMPANY_PROFILE["naics_codes"]}:
            rule_score += 5

        set_aside = (opp.get("setAside") or "").lower()
        if set_aside:
            for sq in sa_keywords:
                if sq in set_aside:
                    rule_score += 25
                    break

        title_desc = ((opp.get("title") or "") + " " + (opp.get("description") or "")).lower()
        cap_hits = sum(1 for kw in cap_keywords if kw in title_desc)
        rule_score += min(cap_hits * 5, 20)

        loc = opp.get("location") or {}
        state_code = ""
        if isinstance(loc, dict):
            st = loc.get("state") or {}
            if isinstance(st, dict):
                state_code = (st.get("code") or st.get("name") or "").strip()
        if state_code.lower() in geo_prefs or "remote" in title_desc:
            rule_score += 10

        closing = opp.get("closingDate")
        if closing:
            try:
                cd = datetime.fromisoformat(closing.replace("Z", "+00:00")).replace(tzinfo=None)
                if cd < now:
                    rule_score -= 10
            except (ValueError, TypeError):
                pass

        scored.append((rule_score, opp))

    scored.sort(key=lambda x: x[0], reverse=True)
    candidates = [(s, o) for s, o in scored if s > 0][:MAX_AI_CANDIDATES]
    return candidates


def _build_single_prompt(candidates):
    profile_text = json.dumps(COMPANY_PROFILE, indent=2)

    opp_summaries = []
    for _, opp in candidates:
        location_parts = []
        loc = opp.get("location") or {}
        if isinstance(loc, dict):
            city = loc.get("city", {})
            state = loc.get("state", {})
            if isinstance(city, dict) and city.get("name"):
                location_parts.append(city["name"])
            if isinstance(state, dict) and state.get("name"):
                location_parts.append(state["name"])
        location_str = ", ".join(location_parts) if location_parts else "Not specified"

        opp_summaries.append({
            "id": opp.get("id", ""),
            "title": opp.get("title", ""),
            "naics": opp.get("ncode", ""),
            "department": opp.get("department", ""),
            "type": opp.get("type", ""),
            "setAside": opp.get("setAside", ""),
            "psc": opp.get("classificationCode", ""),
            "location": location_str,
            "closingDate": opp.get("closingDate", ""),
            "description": (opp.get("description", "") or "")[:300],
        })

    return f"""You are a government contracting advisor. Score each opportunity on how well it fits this company profile.

COMPANY PROFILE:
{profile_text}

OPPORTUNITIES ({len(opp_summaries)} total):
{json.dumps(opp_summaries)}

For each opportunity, provide:
1. A fit score from 0-100 based on NAICS alignment, set-aside match, capability relevance, geographic fit, and past performance relevance
2. A concise 1-sentence explanation of why it matches or doesn't

Respond with ONLY a JSON array (no markdown, no code fences) where each element has:
- "id": the opportunity id
- "score": integer 0-100
- "reason": string explanation

Example: [{{"id":"abc123","score":85,"reason":"Strong NAICS match with 541512 and set-aside aligns with 8(a) qualification."}}]"""


@suggestions_bp.route('/ai-suggestions', methods=['POST'])
def ai_suggestions():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "Request body is required"}), 400

        opportunities = data.get("opportunities", [])
        if not opportunities:
            return jsonify({"suggestions": [], "profile": COMPANY_PROFILE}), 200

        candidates = _rule_based_prefilter(opportunities)

        if not candidates:
            return jsonify({
                "suggestions": [],
                "profile": COMPANY_PROFILE,
                "total_analyzed": len(opportunities),
                "total_suggestions": 0,
                "threshold": SCORE_THRESHOLD,
            })

        prompt = _build_single_prompt(candidates)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=8000,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        ai_scores = json.loads(raw)
        opp_map = {o.get("id"): o for o in opportunities}

        suggestions = []
        for scored in ai_scores:
            opp_id = scored.get("id", "")
            score = scored.get("score", 0)
            if score >= SCORE_THRESHOLD and opp_id in opp_map:
                suggestions.append({
                    **opp_map[opp_id],
                    "fitScore": score,
                    "fitReason": scored.get("reason", ""),
                })

        suggestions.sort(key=lambda x: x.get("fitScore", 0), reverse=True)

        return jsonify({
            "suggestions": suggestions,
            "profile": COMPANY_PROFILE,
            "total_analyzed": len(opportunities),
            "total_suggestions": len(suggestions),
            "threshold": SCORE_THRESHOLD,
        })

    except Exception as e:
        print(f"AI suggestions error: {e}")
        return jsonify({"error": str(e)}), 500
