#!/usr/bin/env python3
"""One-time local helper: mint a YouTube refresh token for the daily workflow.

Run this on your own machine (never in CI):

  1. In Google Cloud Console, create a project and enable "YouTube Data API v3".
  2. Configure the OAuth consent screen (External), add yourself as a test user,
     then PUBLISH the app — apps left in "Testing" expire refresh tokens after
     7 days.
  3. Create an OAuth client ID of type "Desktop app"; note the client ID/secret.
  4. pip install google-auth-oauthlib   (already in requirements.txt)
  5. python scripts/youtube_oauth_setup.py
  6. Add the printed values as GitHub repo secrets:
       YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN

Caveat: until the Cloud project passes YouTube's API audit, videos uploaded
through it are forced to private — publish manually from YouTube Studio until
the audit clears.
"""

from getpass import getpass

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def main() -> None:
    client_id = input("OAuth client ID: ").strip()
    client_secret = getpass("OAuth client secret: ").strip()

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0, prompt="consent")

    print("\nAdd these GitHub repo secrets:")
    print(f"  YT_CLIENT_ID={client_id}")
    print(f"  YT_CLIENT_SECRET={client_secret}")
    print(f"  YT_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
