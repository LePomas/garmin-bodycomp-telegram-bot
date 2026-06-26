import datetime
import os
import statistics
import sys

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

# --- LLM API Configuration (all overridable via env) ---
API_KEY = os.getenv("GOOGLE_API_KEY", "")
API_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
API_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0"))
API_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "15"))
API_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "2"))

TREND_KEYS = ("weight_kg", "body_fat_percent", "muscle_mass_kg")

_llm = None


def _get_llm():
    """Build the Gemini client on first use (keeps import side-effect free)."""
    global _llm
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(
            model=API_MODEL,
            temperature=API_TEMPERATURE,
            max_tokens=None,
            timeout=API_TIMEOUT,
            max_retries=API_MAX_RETRIES,
        )
    return _llm


# --- Data fetching and trend math (pure, unit-tested in test_llmfeedback.py) ---

def _to_kg(grams):
    """Garmin reports weight/muscle mass in grams; convert to kg. None stays None."""
    return grams / 1000.0 if grams else None


def normalize_entry(entry: dict) -> dict:
    """One Garmin dateWeightList row -> normalized metrics (kg, percent)."""
    return {
        "date": entry.get("calendarDate"),
        "weight_kg": _to_kg(entry.get("weight")),
        "body_fat_percent": entry.get("bodyFat"),
        "muscle_mass_kg": _to_kg(entry.get("muscleMass")),
    }


def fetch_body_composition_series(api, days: int = 90):
    """Normalized body-comp series over the window, oldest first.

    Returns the list, or None on API error / no data.
    """
    try:
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        data = api.get_body_composition(start.isoformat(), end.isoformat())
    except Exception as e:
        print(f"LLMFeedback: Error fetching body composition data: {e}", file=sys.stderr)
        return None

    rows = (data or {}).get("dateWeightList", [])
    series = [normalize_entry(r) for r in rows if r.get("weight")]
    series.sort(key=lambda r: r["date"] or "")
    return series or None


def _metric_trend(series: list, key: str):
    """Least-squares trend for one metric over the window.

    Returns {current, change, span_days, n} where `change` is the modeled change
    (slope * span) across the window, or None if the metric has no data. `change`
    is None when there is only one point or a single day of data.
    """
    pts = [(r["date"], r[key]) for r in series if r.get(key) is not None and r.get("date")]
    if not pts:
        return None

    current = pts[-1][1]
    if len(pts) < 2:
        return {"current": current, "change": None, "span_days": 0, "n": 1}

    day0 = datetime.date.fromisoformat(pts[0][0])
    xs = [(datetime.date.fromisoformat(d) - day0).days for d, _ in pts]
    ys = [float(v) for _, v in pts]
    span = xs[-1] - xs[0]
    if span <= 0:
        return {"current": current, "change": None, "span_days": 0, "n": len(pts)}

    slope, _ = statistics.linear_regression(xs, ys)
    return {"current": current, "change": round(slope * span, 2), "span_days": span, "n": len(pts)}


def compute_trends(series: list) -> dict:
    """Per-metric trend dict keyed by TREND_KEYS."""
    return {k: _metric_trend(series, k) for k in TREND_KEYS}


def describe_trends(trends: dict) -> list:
    """Human-readable trend lines for the LLM prompt."""
    out = []

    w = trends.get("weight_kg")
    if w:
        if w["change"] is not None:
            out.append(f"Weight {w['change']:+.2f} kg over {w['span_days']} days (now {w['current']:.2f} kg).")
        else:
            out.append(f"Current weight {w['current']:.2f} kg (no trend yet).")

    bf = trends.get("body_fat_percent")
    if bf and bf["change"] is not None:
        out.append(f"Body fat {bf['change']:+.2f}% over {bf['span_days']} days.")

    mm = trends.get("muscle_mass_kg")
    if mm and mm["change"] is not None:
        out.append(f"Muscle mass {mm['change']:+.2f} kg over {mm['span_days']} days.")

    return out


def generate_feedback_message(trends: dict):
    """Ask Gemini for a short motivating message from the computed trends."""
    descriptions = describe_trends(trends)
    if not descriptions:
        return None

    user_query = (
        "The user just logged new body composition data. "
        f"Trends over the recent window: {' '.join(descriptions)} "
        "Generate a short, motivating feedback message (under 260 characters) focusing on "
        "the most positive trend, such as fat loss or muscle gain. If data is limited or "
        "neutral, focus on consistency."
    )
    system_prompt = (
        "Act as a friendly, motivating, and highly concise fitness coach. "
        "Your response MUST be under 260 characters. Do not use quotes, only the message text with some emojis."
    )
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_query)]

    try:
        ai_msg = _get_llm().invoke(messages)
        text = (ai_msg.content or "").strip()
        if text:
            return text
        print("LLMFeedback: Received empty content from LLM invocation.", file=sys.stderr)
    except Exception as e:
        print(f"LLMFeedback: LangChain invocation failed after retries: {e}", file=sys.stderr)

    return None


def get_feedback(api):
    """Programmatic helper for in-process use by the bot. Never raises."""
    if not API_KEY:
        print("LLMFeedback: GOOGLE_API_KEY not set, skipping AI feedback.", file=sys.stderr)
        return None
    try:
        series = fetch_body_composition_series(api)
        if not series:
            return None
        return generate_feedback_message(compute_trends(series))
    except Exception as e:
        print(f"LLMFeedback: get_feedback failed: {e}", file=sys.stderr)
        return None


def main(api):
    """Standalone feedback run (prints message, exits 0 on success)."""
    if not API_KEY:
        print("LLMFeedback: ERROR: GOOGLE_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    series = fetch_body_composition_series(api)
    if series:
        feedback = generate_feedback_message(compute_trends(series))
        if feedback:
            print(feedback)
            sys.exit(0)

    sys.exit(1)
