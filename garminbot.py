# garminbot.py
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

import garminconnectapi

load_dotenv()

# --- Configuration ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN env var")

# --- MULTI-USER FIX: Read and parse list of IDs ---
ALLOWED_ID_STR = os.environ.get("ALLOWED_TELEGRAM_ID")
if not ALLOWED_ID_STR:
    raise RuntimeError("Set ALLOWED_TELEGRAM_ID env var")

try:
    # Convert comma-separated string to a list of integers
    ALLOWED_IDS = [int(i.strip()) for i in ALLOWED_ID_STR.split(',') if i.strip()]
except ValueError:
    raise RuntimeError("ALLOWED_TELEGRAM_ID must contain a comma-separated list of integers.")

# --- NEW: Read and parse USER_PROFILES map ---
USER_PROFILES_STR = os.environ.get("USER_PROFILES")
USER_PROFILES = {}
if USER_PROFILES_STR:
    try:
        # Format: "ID:PROFILE_KEY,ID2:PROFILE_KEY2"
        for mapping in USER_PROFILES_STR.split(','):
            if ':' in mapping:
                user_id_str, profile_key = mapping.split(':', 1)
                USER_PROFILES[int(user_id_str.strip())] = profile_key.strip().upper()
    except ValueError:
        raise RuntimeError("USER_PROFILES must be in the format 'ID:PROFILE_KEY,ID2:PROFILE_KEY2'.")

# Fallback profile if not explicitly set
DEFAULT_PROFILE = "OMRON"

# --- State Management ---
STATE_EXPECTING_CREDENTIALS = "expecting_credentials"
STATE_EXPECTING_MFA = "expecting_mfa"


# --- Utilities: parsing + validation dispatch ---
def _strip_comment_and_parse_value(line: str):
    """Strips comments and leading/trailing whitespace."""
    before = line.split("#", 1)[0].strip()
    return before

def get_user_profile_key(user_id: int) -> str:
    """Returns the profile key for a given user ID."""
    return USER_PROFILES.get(user_id, DEFAULT_PROFILE)

def validate_omron_profile(lines: list):
    """Validates and casts data for the OMRON profile (5 values)."""
    if len(lines) < 5:
        raise ValueError("Expected 5 lines/values (weight, bmi, percent_fat, percent_muscle, visceral).")

    # Parse and cast all input values
    weight = round(float(lines[0]), 2)
    bmi = float(lines[1])
    percent_fat = float(lines[2])
    percent_muscle = float(lines[3])
    visceral = int(lines[4])

    if weight <= 1:
        raise ValueError("Weight must be > 1 kg and positive.")

    # Calculate muscle mass from weight and muscle percentage (OMRON logic)
    muscle_mass = round(weight * (percent_muscle / 100.0), 2)

    return {
        "weight": weight,
        "bmi": bmi,
        "percent_fat": percent_fat,
        "percent_muscle": percent_muscle,
        "visceral_fat_rating": visceral,
        "muscle_mass": muscle_mass,
        # Mi Scale specific fields are None/omitted
        "percent_hydration": None,
        "bone_mass": None,
    }

def validate_mi_scale_profile(lines: list):
    """Validates and casts data for the MI_SCALE profile (7 values, Spanish error)."""
    # --- MI_SCALE FIX: Spanish Error Message & 7 Values ---
    if len(lines) < 7:
        raise ValueError("Se esperan 7 valores, uno por linea.\nEn este orden: \n\nPeso\nIMC\nGrasa\nAgua\nGrasa visceral\nMasa ósea\nMúsculo")

    # Parse and cast all input values
    weight = round(float(lines[0]), 2)
    bmi = float(lines[1])
    percent_fat = float(lines[2])
    # --- MI_SCALE FIX: Muscle mass is provided directly in kg ---
    percent_hydration = float(lines[3])  # Agua
    visceral = int(lines[4])
    bone_mass = round(float(lines[5]), 2) # Masa ósea
    muscle_mass = round(float(lines[6]), 2)


    if weight <= 1:
        raise ValueError("El peso debe ser > 1 kg y positivo.")

    return {
        "weight": weight,
        "bmi": bmi,
        "percent_fat": percent_fat,
        "percent_muscle": (muscle_mass / weight) * 100 if weight else 0.0, # Retained for display/logging if needed
        "visceral_fat_rating": visceral,
        "muscle_mass": muscle_mass, # Use provided muscle mass
        # New Mi Scale specific fields
        "percent_hydration": percent_hydration,
        "bone_mass": bone_mass,
    }


def _validate_and_cast_dispatch(user_id: int, lines: list):
    """Dispatches validation based on the user's profile."""
    profile_key = get_user_profile_key(user_id)

    if profile_key == "OMRON":
        return validate_omron_profile(lines)
    elif profile_key == "MI_SCALE":
        return validate_mi_scale_profile(lines)
    else:
        raise ValueError(f"Unknown profile key: {profile_key} assigned to user ID {user_id}.")


def _run_garmin_script(user_id: int, data: dict, email: str = None, password: str = None, mfa_code: str = None):
    """Submit body composition in-process via garminconnectapi.

    Returns (code, stdout, stderr) where code is one of garminconnectapi's EXIT_*
    values. LLM feedback is best-effort and never affects the submission result.
    """
    config = garminconnectapi.Config(user_id=user_id)

    try:
        api_instance = garminconnectapi.init_api(
            tokenstore_path=config.tokenstore,
            email=email,
            password=password,
            mfa_code=mfa_code,
        )
    except garminconnectapi.GarminLoginError as e:
        return e.code, None, str(e)

    if not garminconnectapi.add_body_composition_data_non_interactive(api_instance, data):
        return garminconnectapi.EXIT_SUBMISSION_ERROR, None, "Submission failed"

    # Optional LLM feedback: a failure here must not fail the submission.
    try:
        import llmfeedback
        feedback = llmfeedback.get_feedback(api_instance)
        if feedback:
            return garminconnectapi.EXIT_SUCCESS, f"Success: Data submitted. LLM: {feedback}", None
    except Exception as e:
        return garminconnectapi.EXIT_SUCCESS, "Success: Data submitted.", f"LLM call failed: {e}"

    return garminconnectapi.EXIT_SUCCESS, "Success: Data submitted.", None



# --- Telegram bot: message processing ---
async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # --- AUTHORIZATION CHECK ---
    if user_id not in ALLOWED_IDS:
        await update.effective_message.reply_text("⛔ Sorry, you are not authorized to use this bot.")
        return

    msg = update.effective_message
    text = (msg.text or "").strip()
    if not text:
        await msg.reply_text("No text found in message.")
        return

    user_data = context.user_data
    exit_code, stdout, stderr = None, None, None
    data = user_data.get("body_data")

    # 1. Handle MFA response (omitted for brevity, no changes)
    if user_data.get(STATE_EXPECTING_MFA):
        email = user_data.get("email")
        password = user_data.get("password")
        mfa_code = text
        await msg.reply_text("🔑 Submitting MFA code and body data...")

        exit_code, stdout, stderr = _run_garmin_script(
            user_id=user_id, data=data, email=email, password=password, mfa_code=mfa_code
        )
        # Clear temporary data
        user_data.pop(STATE_EXPECTING_MFA, None)
        user_data.pop("email", None)
        user_data.pop("password", None)


    elif user_data.get(STATE_EXPECTING_CREDENTIALS):
        lines = text.splitlines()
        if len(lines) < 2:
            await msg.reply_text("Input error: Please send your email on the first line and password on the second line.")
            return

        email = lines[0].strip()
        password = lines[1].strip()
        user_data["email"] = email
        user_data["password"] = password

        await msg.reply_text("🔑 Attempting login with credentials...")

        exit_code, stdout, stderr = _run_garmin_script(
            user_id=user_id, data=data, email=email, password=password
        )

        user_data.pop(STATE_EXPECTING_CREDENTIALS, None)

        if exit_code == 0:
            await msg.reply_text("✅ Login successful! Finalizing data submission...")
        elif exit_code == 3:
            await msg.reply_text("✅ Login credentials accepted. Proceeding to MFA check.")

    # 3. Handle New Data Submission (Initial attempt)
    else:
        lines = [_strip_comment_and_parse_value(l) for l in text.splitlines() if l.strip() != ""]
        try:
            # --- VALIDATION DISPATCH HERE ---
            data = _validate_and_cast_dispatch(user_id, lines)
        except Exception as e:
            await msg.reply_text(f"Input validation error: {e}")
            return

        user_data["body_data"] = data
        await msg.reply_text(f"✅ Data parsed successfully for profile {get_user_profile_key(user_id)}. Attempting token login for submission...")

        exit_code, stdout, stderr = _run_garmin_script(user_id=user_id, data=data)


    # --- 4. Process Exit Code and Respond (omitted for brevity, no changes) ---
    # ... (Exit code handling remains the same)

    # EXIT_SUCCESS = 0
    if exit_code == 0:
        base_msg = "🎉 SUCCESS! Body composition data submitted to Garmin Connect.\nGo check your stats now! 🚀\nconnect.garmin.com/modern/weight"
        # stdout may contain LLM feedback when using in-process path
        if stdout:
            # look for the llm token we returned earlier
            if "LLM:" in stdout:
                _, feedback = stdout.split("LLM:", 1)
                feedback = feedback.strip()
                base_msg += f"\n\n💬 Tip: {feedback}"
        await msg.reply_text(base_msg)
        user_data.pop("body_data", None)
        return


    # EXIT_TOKEN_FAILURE = 2
    elif exit_code == 2:
        user_data[STATE_EXPECTING_CREDENTIALS] = True
        await msg.reply_text(
            "🛑 **Garmin Login Required**\n\n"
            "The login token is missing or invalid for your account. Please reply to this message with:\n"
            "1. Your **Garmin Email**\n"
            "2. Your **Garmin Password**"
        )
        return

    # EXIT_MFA_REQUIRED = 3
    elif exit_code == 3:
        user_data[STATE_EXPECTING_MFA] = True
        await msg.reply_text(
            "🔑 **Multi-Factor Authentication Required**\n\n"
            "Please check your MFA app and reply to this message with your **one-time 6-digit code**."
        )
        return

    # EXIT_TOO_MANY_MFA = 4
    elif exit_code == 4:
        # Clear all temporary data
        user_data.pop(STATE_EXPECTING_MFA, None)
        user_data.pop("email", None)
        user_data.pop("password", None)
        user_data.pop("body_data", None)
        await msg.reply_text(
            "❌ **MFA Limit Exceeded**\n\n"
            "You've tried too many MFA codes. Please wait 30 minutes before trying again."
        )
        return

    # EXIT_SUBMISSION_ERROR = 1 or other errors
    else:
        error_output = (stderr or stdout).strip() or "Unknown error occurred during submission."
        await msg.reply_text(f"❌ **Submission Failed** (Code: {exit_code})\n\n`{error_output}`")
        user_data.pop("body_data", None)
        user_data.pop(STATE_EXPECTING_MFA, None)
        user_data.pop(STATE_EXPECTING_CREDENTIALS, None)


def main():
    """Starts the bot."""
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))

    print("Running bot to process body composition data...")
    print(f"Authorized IDs loaded: {len(ALLOWED_IDS)}")
    print(f"User Profiles loaded: {len(USER_PROFILES)}")
    app.run_polling(poll_interval=1.0)


if __name__ == "__main__":
    main()
