# garminconnectapi.py
#!/usr/bin/env python3
"""
Minimal script for adding body composition data to Garmin Connect
Refactored for non-interactive use with Telegram bot via CLI arguments and exit codes.
Now supports multi-user profiles and extended body composition fields.
"""

import argparse
import datetime
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from garminconnect import (Garmin, GarminConnectAuthenticationError,
                           GarminConnectConnectionError)
from garth.exc import GarthException, GarthHTTPError

# --- Exit Codes for garminbot.py communication ---
EXIT_SUCCESS = 0
EXIT_SUBMISSION_ERROR = 1
EXIT_TOKEN_FAILURE = 2
EXIT_MFA_REQUIRED = 3
EXIT_TOO_MANY_MFA = 4

load_dotenv()

# Configure logging
import logging

logging.getLogger("garminconnect").setLevel(logging.CRITICAL)

# API instance placeholder
api = None

class Config:
    """Configuration class for Garmin Connect API."""

    def __init__(self, user_id: int):
        # Base token path from environment, falls back to a multi-user default
        base_token_path = os.getenv("GARMINTOKENS_BASE") or "~/.garth"

        # Unique tokenstore path per user
        user_dir = f"tg_{user_id}"

        # Use Path for reliable path construction and tilde expansion
        self.tokenstore = Path(os.path.expanduser(base_token_path)) / user_dir
        self.tokenstore.mkdir(parents=True, exist_ok=True) # Ensure directory exists

        # Date settings
        self.today = datetime.date.today()

        # Export settings (simplified)
        self.export_dir = Path("your_data")
        self.export_dir.mkdir(exist_ok=True)


def safe_api_call(api_method, *args, method_name: str = None, **kwargs):
    """
    Centralized API call wrapper with comprehensive error handling.
    """
    if method_name is None:
        method_name = getattr(api_method, "__name__", str(api_method))

    try:
        api_method(*args, **kwargs)
        return True, "Data successfully submitted"

    except GarthHTTPError as e:
        error_msg = f"HTTP error: {e}"
        # --- EMOJI REMOVAL FIX ---
        return False, f"Error: {method_name} failed: {error_msg}"

    except GarminConnectAuthenticationError as e:
        error_msg = f"Authentication issue: {e}"
        # --- EMOJI REMOVAL FIX ---
        return False, f"Error: {method_name} failed: {error_msg}"

    except GarminConnectConnectionError as e:
        error_msg = f"Connection issue: {e}"
        # --- EMOJI REMOVAL FIX ---
        return False, f"Error: {method_name} failed: {error_msg}"

    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        # --- EMOJI REMOVAL FIX ---
        return False, f"Error: {method_name} failed: {error_msg}"


def add_body_composition_data_non_interactive(api: Garmin, data: dict) -> bool:
    """
    Add body composition data using the provided data dictionary and current timestamp.
    """
    current_time = datetime.datetime.now()
    garmin_timestamp = current_time.strftime("%Y-%m-%dT%H:%M:%S.0")

    try:
        success, message = safe_api_call(
            api.add_body_composition,
            garmin_timestamp,
            weight=data["weight"],
            percent_fat=data.get("percent_fat"),
            percent_hydration=data.get("percent_hydration"),
            bone_mass=data.get("bone_mass"),
            visceral_fat_mass=data.get("visceral_fat_mass"),
            muscle_mass=data.get("muscle_mass"),
            basal_met=data.get("basal_met"),
            active_met=data.get("active_met"),
            physique_rating=data.get("physique_rating"),
            metabolic_age=data.get("metabolic_age"),
            visceral_fat_rating=data.get("visceral_fat_rating"),
            bmi=data.get("bmi"),
            method_name="add_body_composition",
        )

        if success:
            # --- EMOJI REMOVAL FIX ---
            print(f"Success: Data submitted at {garmin_timestamp}")
            return True
        else:
            # --- EMOJI REMOVAL FIX ---
            print(f"Error: {message}", file=sys.stderr)
            return False

    except Exception as e:
        # --- EMOJI REMOVAL FIX ---
        print(f"Critical Error adding body composition: {e}", file=sys.stderr)
        return False


def init_api(tokenstore_path: Path, email: str | None = None, password: str | None = None, mfa_code: str | None = None) -> Garmin | None:
    """Initialize Garmin API with smart error handling and recovery using user-specific tokenstore."""

    # 1. Try token-based login first
    try:
        garmin = Garmin()
        garmin.login(str(tokenstore_path))
        return garmin

    except (FileNotFoundError, GarthHTTPError, GarminConnectAuthenticationError, GarminConnectConnectionError):
        pass

    # If no credentials provided, exit with TOKEN_FAILURE
    if not email or not password:
        sys.exit(EXIT_TOKEN_FAILURE)

    # 2. Try credential-based login
    try:
        garmin = Garmin(email=email, password=password, is_cn=False, return_on_mfa=True)
        result1, result2 = garmin.login()

        # Handle MFA
        if result1 == "needs_mfa":
            if not mfa_code:
                sys.exit(EXIT_MFA_REQUIRED)

            # Resume login with MFA code
            try:
                garmin.resume_login(result2, mfa_code)
            except GarthHTTPError as garth_error:
                error_str = str(garth_error)
                if "429" in error_str and "Too Many Requests" in error_str:
                    print("❌ Too many MFA attempts", file=sys.stderr)
                    sys.exit(EXIT_TOO_MANY_MFA)
                elif "401" in error_str or "403" in error_str:
                    print("❌ Invalid MFA code", file=sys.stderr)
                    sys.exit(EXIT_MFA_REQUIRED)
                else:
                    print(f"❌ MFA authentication failed: {garth_error}", file=sys.stderr)
                    sys.exit(EXIT_SUBMISSION_ERROR)
            except GarthException as garth_error:
                print(f"❌ MFA authentication failed: {garth_error}", file=sys.stderr)
                sys.exit(EXIT_MFA_REQUIRED)

        # 3. Save tokens and return API instance
        garmin.garth.dump(str(tokenstore_path))
        return garmin

    except GarminConnectAuthenticationError:
        print("❌ Authentication failed: Invalid username or password", file=sys.stderr)
        sys.exit(EXIT_SUBMISSION_ERROR)

    except (FileNotFoundError, GarthHTTPError, GarthException,
            GarminConnectConnectionError, requests.exceptions.HTTPError) as err:
        print(f"❌ Connection error during login: {err}", file=sys.stderr)
        sys.exit(EXIT_SUBMISSION_ERROR)

# >>> Add this to garminconnectapi.py (after init_api)
def init_api_inprocess(tokenstore_path: Path, email: str | None = None, password: str | None = None, mfa_code: str | None = None):
    """
    Safe wrapper for init_api when used in-process (e.g. called by a running bot).
    Returns: (garmin_instance_or_None, exit_code)
      - garmin_instance_or_None: Garmin instance on success, else None
      - exit_code: 0 on success or one of the EXIT_* codes (matching CLI behavior)
    This prevents init_api's sys.exit() from terminating the bot process.
    """
    try:
        garmin = init_api(tokenstore_path=tokenstore_path, email=email, password=password, mfa_code=mfa_code)
        if garmin:
            return garmin, 0
        # init_api may return None in some code paths (rare)
        return None, EXIT_SUBMISSION_ERROR
    except SystemExit as se:
        # init_api uses sys.exit() to signal conditions; map those to the same codes without exiting
        code = se.code if isinstance(se.code, int) else EXIT_SUBMISSION_ERROR
        return None, code
    except Exception as e:
        # unexpected exception, map to submission error and don't crash
        print(f"init_api_inprocess unexpected error: {e}", file=sys.stderr)
        return None, EXIT_SUBMISSION_ERROR


def main():
    """Main function to add body composition data."""

    parser = argparse.ArgumentParser(description="Add body composition data to Garmin Connect.")
    parser.add_argument("--user-id", type=int, required=True, help="Telegram User ID for unique token storage.")

    # Login arguments (optional for token refresh)
    parser.add_argument("--email", type=str, help="Garmin account email address.")
    parser.add_argument("--password", type=str, help="Garmin account password.")
    parser.add_argument("--mfa-code", type=str, help="Multi-factor authentication code.")

    # Body composition data arguments (required for submission)
    parser.add_argument("--weight", type=float, required=True, help="Weight in kg.")
    parser.add_argument("--muscle-mass", type=float, required=True, help="Muscle mass in kg.")

    # Common Optional arguments
    parser.add_argument("--bmi", type=float, help="Body Mass Index.")
    parser.add_argument("--percent-fat", type=float, help="Body fat percentage.")
    parser.add_argument("--visceral-fat-rating", type=int, help="Visceral fat rating.")

    # --- NEW: Mi Scale Arguments ---
    parser.add_argument("--percent-hydration", type=float, help="Body hydration percentage.")
    parser.add_argument("--bone-mass", type=float, help="Bone mass in kg.")

    # Optional defaults from original script (can be extended)
    parser.add_argument("--metabolic-age", type=int, default=None, help="Metabolic age.")
    parser.add_argument("--basal-met", type=int, default=None, help="Basal metabolic rate.")

    args = parser.parse_args()

    # Initialize configuration with the provided user ID
    global config
    config = Config(user_id=args.user_id)

    # --- Login/API Initialization ---
    api_instance = init_api(
        tokenstore_path=config.tokenstore,
        email=args.email,
        password=args.password,
        mfa_code=args.mfa_code
    )

    if not api_instance:
        # init_api already called sys.exit() with the appropriate code
        return

    # --- Data Submission ---

    # Prepare data dictionary from arguments
    body_data = {
        "weight": args.weight,
        "bmi": args.bmi,
        "percent_fat": args.percent_fat,
        "muscle_mass": args.muscle_mass,
        "visceral_fat_rating": args.visceral_fat_rating,

        # Include new Mi Scale fields (will be None for OMRON profile)
        "percent_hydration": args.percent_hydration,
        "bone_mass": args.bone_mass,

        "metabolic_age": args.metabolic_age,
        "basal_met": args.basal_met,
    }

    if api_instance:
        if add_body_composition_data_non_interactive(api_instance, body_data):
            sys.exit(EXIT_SUCCESS)
        else:
            sys.exit(EXIT_SUBMISSION_ERROR)
    else:
        sys.exit(EXIT_SUBMISSION_ERROR)


if __name__ == "__main__":
    main()