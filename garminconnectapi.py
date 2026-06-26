# garminconnectapi.py
#!/usr/bin/env python3
"""
Minimal script for adding body composition data to Garmin Connect
Refactored for non-interactive use with Telegram bot via CLI arguments and exit codes.
Now supports multi-user profiles and extended body composition fields.
"""

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


class GarminLoginError(Exception):
    """Login failed in-process; carries one of the EXIT_* status codes."""

    def __init__(self, code: int, message: str = ""):
        self.code = code
        super().__init__(message or f"login failed (code {code})")


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


def init_api(tokenstore_path: Path, email: str | None = None, password: str | None = None, mfa_code: str | None = None) -> Garmin:
    """Initialize Garmin API using a user-specific tokenstore.

    Returns a logged-in Garmin instance, or raises GarminLoginError carrying an
    EXIT_* status code the caller maps to a user-facing reply.
    """

    # 1. Try token-based login first
    try:
        garmin = Garmin()
        garmin.login(str(tokenstore_path))
        return garmin

    except (FileNotFoundError, GarthHTTPError, GarminConnectAuthenticationError, GarminConnectConnectionError):
        pass

    # No saved token and no credentials -> ask the user for credentials
    if not email or not password:
        raise GarminLoginError(EXIT_TOKEN_FAILURE)

    # 2. Try credential-based login
    try:
        garmin = Garmin(email=email, password=password, is_cn=False, return_on_mfa=True)
        result1, result2 = garmin.login()

        # Handle MFA
        if result1 == "needs_mfa":
            if not mfa_code:
                raise GarminLoginError(EXIT_MFA_REQUIRED)

            # Resume login with MFA code
            try:
                garmin.resume_login(result2, mfa_code)
            except GarthHTTPError as garth_error:
                error_str = str(garth_error)
                if "429" in error_str and "Too Many Requests" in error_str:
                    raise GarminLoginError(EXIT_TOO_MANY_MFA, "Too many MFA attempts")
                if "401" in error_str or "403" in error_str:
                    raise GarminLoginError(EXIT_MFA_REQUIRED, "Invalid MFA code")
                raise GarminLoginError(
                    EXIT_SUBMISSION_ERROR, f"MFA authentication failed: {garth_error}"
                )
            except GarthException as garth_error:
                raise GarminLoginError(
                    EXIT_MFA_REQUIRED, f"MFA authentication failed: {garth_error}"
                )

        # 3. Save tokens and return API instance
        garmin.garth.dump(str(tokenstore_path))
        return garmin

    except GarminConnectAuthenticationError:
        raise GarminLoginError(
            EXIT_SUBMISSION_ERROR, "Authentication failed: invalid username or password"
        )

    except (FileNotFoundError, GarthHTTPError, GarthException,
            GarminConnectConnectionError, requests.exceptions.HTTPError) as err:
        raise GarminLoginError(EXIT_SUBMISSION_ERROR, f"Connection error during login: {err}")
