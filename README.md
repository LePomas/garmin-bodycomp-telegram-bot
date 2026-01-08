# Garmin Body Composition Telegram Bot (with AI Feedback)

A production-oriented Telegram bot that:
1) accepts body composition entries via chat,
2) submits them to Garmin Connect,
3) optionally generates short AI motivational feedback based on ~3 months of historical trends.

> AI feedback is an optional enhancement and never blocks the main submission flow.

---

## Features

- ✅ Telegram chat interface with validation
- ✅ Garmin Connect submission (multi-user token store)
- ✅ Optional AI feedback (<= 150 characters)
- ✅ Resilient flow: Garmin submission remains the critical path; LLM failures are ignored gracefully
- ✅ CLI support for Garmin API client remains available

---

## Architecture

**Flow**
User Input → Bot Validation → Garmin API Submission → (Optional) Trend Analysis + AI Feedback → Telegram Reply

**Modules**
- `garminbot.py` — Telegram bot (production entry point)
- `garminconnectapi.py` — Garmin API client (CLI + multi-user token storage)
- `llmfeedback.py` — Historical trend analysis + AI motivational message

---

## Requirements

- Python 3.10+ recommended
- Telegram bot token
- Garmin Connect credentials (per user)
- Optional: LLM API key (for feedback)

Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
