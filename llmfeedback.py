import datetime
import os
import sys
from typing import Optional, Dict, Any

from dotenv import load_dotenv
from garminconnect import Garmin

load_dotenv()
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

# --- LLM API Configuration ---
API_KEY = os.getenv("GOOGLE_API_KEY", "")
API_MODEL = "gemini-2.5-flash-lite"

# Initialize the LLM client once
llm = ChatGoogleGenerativeAI(
    model=API_MODEL,
    temperature=0,
    max_tokens=None,
    timeout=15,
    max_retries=2,
)


# --- Data Fetching and Processing ---

def fetch_latest_body_composition(api: Garmin) -> Optional[Dict[str, Any]]:
    """
    Fetches the latest body composition data (weight, body fat, muscle mass)
    over a 3-month period to determine trends.
    """
    try:
        # Define a 3-month range for reliable trend comparison (as suggested by user)
        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=90)

        # Call the body composition API
        data = api.get_body_composition(start_date.isoformat(), end_date.isoformat())

        # The relevant data is in dateWeightList
        weight_list = data.get('dateWeightList', [])

        if not weight_list:
            return None

        # Sort by calendarDate (newest first)
        weight_list.sort(key=lambda x: x.get('calendarDate', '0'), reverse=True)

        # Filter for the most recent log that contains a valid Body Fat value for a meaningful comparison
        meaningful_entries = [
            e for e in weight_list
            if e.get('bodyFat') is not None and e.get('bodyFat') > 0
        ]

        if not meaningful_entries:
            # Fallback to just the most recent entry if no full body comp data exists
            latest = weight_list[0]
            previous = None
        else:
            latest = meaningful_entries[0]
            previous = meaningful_entries[1] if len(meaningful_entries) > 1 else None

        # Data normalization (Garmin returns weight/muscle mass in grams/mg, need kg/percent)
        def normalize(entry):
            if not entry: return None
            # Weight is stored as mg/g, divide by 1000 to get kg
            weight_kg = (entry.get('weight') / 1000.0) if entry.get('weight') else None
            # Muscle mass is typically in grams/mg, convert to kg
            muscle_mass_kg = (entry.get('muscleMass') / 1000.0) if entry.get('muscleMass') else None

            return {
                "weight_kg": weight_kg,
                "body_fat_percent": entry.get('bodyFat'),
                "muscle_mass_kg": muscle_mass_kg,
                "date": entry.get('calendarDate'),
            }

        latest_data = normalize(latest)
        previous_data = normalize(previous)

        if not latest_data or not latest_data['weight_kg']:
            return None

        return {
            "latest": latest_data,
            "previous": previous_data,
        }

    except Exception as e:
        print(f"LLMFeedback: Error fetching body composition data: {e}", file=sys.stderr)
        return None


def generate_feedback_message(data: Dict[str, Any]) -> Optional[str]:
    """Calls the Gemini API (via LangChain) to generate a personalized feedback message."""
    latest = data['latest']
    previous = data['previous']

    trend_descriptions = []

    # 1. Weight Trend
    latest_w = latest['weight_kg']
    if previous and previous['weight_kg']:
        previous_w = previous['weight_kg']
        weight_diff = round(latest_w - previous_w, 2)
        trend_descriptions.append(f"Weight change: {weight_diff:+.2f} kg.")
    else:
        trend_descriptions.append(f"Current weight: {latest_w:.2f} kg. No recent weight comparison.")

    # 2. Body Fat Trend (if available)
    latest_bf = latest.get('body_fat_percent')
    if latest_bf and previous and previous.get('body_fat_percent'):
        previous_bf = previous['body_fat_percent']
        bf_diff = round(latest_bf - previous_bf, 2)
        trend_descriptions.append(f"Body Fat change: {bf_diff:+.2f}%.")

    # 3. Muscle Mass Trend (if available)
    latest_mm = latest.get('muscle_mass_kg')
    if latest_mm and previous and previous.get('muscle_mass_kg'):
        previous_mm = previous['muscle_mass_kg']
        mm_diff = round(latest_mm - previous_mm, 2)
        trend_descriptions.append(f"Muscle Mass change: {mm_diff:+.2f} kg.")

    user_query = (
        f"The user logged new body composition data on {latest.get('date')}. "
        f"Metrics: {'; '.join(trend_descriptions)}. "
        f"Generate a short, motivating feedback message (under 260 characters) focusing on the most positive trend, "
        f"such as fat loss or muscle gain. If data is limited or neutral, focus on consistency."
    )

    # Define System Instruction and LangChain Messages
    system_prompt = (
        "Act as a friendly, motivating, and highly concise fitness coach. "
        "Your response MUST be under 260 characters. Do not use quotes, only the message text with some emojis."
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_query),
    ]

    # LangChain API Call
    try:
        ai_msg = llm.invoke(messages)
        text = ai_msg.content.strip()

        if text:
            # ONLY print the final message to stdout
            return text

        print("LLMFeedback: Received empty content from LLM invocation.", file=sys.stderr)

    except Exception as e:
        print(f"LLMFeedback: LangChain invocation failed after retries: {e}", file=sys.stderr)

    return None

# >>> Add this to llmfeedback.py (near the end, before/after main)
def get_feedback(api):
    """
    Programmatic helper for in-process usage.
    Returns a feedback string (<=150 chars) or None if no feedback could be generated.
    This does not call sys.exit() so it is safe to call from a running bot.
    """
    if not API_KEY:
        # LLM optional — skip silently if API key not set
        print("LLMFeedback: GOOGLE_API_KEY not set, skipping AI feedback.", file=sys.stderr)
        return None
    try:
        data = fetch_latest_body_composition(api)
        if not data:
            return None
        feedback = generate_feedback_message(data)
        return feedback
    except Exception as e:
        print(f"LLMFeedback: get_feedback failed: {e}", file=sys.stderr)
        return None


def main(api: Garmin):
    """Main execution function for generating feedback."""
    if not API_KEY:
        print("LLMFeedback: ERROR: GOOGLE_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    # UPDATED FUNCTION CALL
    data = fetch_latest_body_composition(api)

    if data:
        feedback = generate_feedback_message(data)
        if feedback:
            print(feedback)
            sys.exit(0)

    sys.exit(1)
