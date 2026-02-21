"""
One-time Gmail OAuth2 authentication script.
Run this ONCE locally to generate gmail_token.json, then deploy the token with your app.

Usage:
    python gmail_auth.py
"""

import os
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CREDENTIALS_FILE = os.getenv(
    "GMAIL_CREDENTIALS_FILE",
    os.path.join(os.path.dirname(__file__), "gmail_credentials.json"),
)
TOKEN_FILE = os.getenv(
    "GMAIL_TOKEN_FILE",
    os.path.join(os.path.dirname(__file__), "gmail_token.json"),
)


def main():
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"ERROR: Credentials file not found at: {CREDENTIALS_FILE}")
        print("Make sure gmail_credentials.json exists in the backendGOVAI folder.")
        return

    print("Opening browser for Google authentication...")
    print(f"Using credentials: {CREDENTIALS_FILE}")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    token_json = creds.to_json()

    with open(TOKEN_FILE, "w") as token_file:
        token_file.write(token_json)

    print()
    print(f"Authentication successful! Token saved to: {TOKEN_FILE}")
    print("The email poller will now be able to read your Gmail inbox.")
    print()
    print("=" * 70)
    print("FOR DEPLOYMENT (Render, Railway, etc.):")
    print("Copy the value below and set it as the GMAIL_TOKEN_JSON env var:")
    print("=" * 70)
    print(token_json)
    print("=" * 70)
    print()
    print("IMPORTANT: Add gmail_token.json and gmail_credentials.json to .gitignore")


if __name__ == "__main__":
    main()
