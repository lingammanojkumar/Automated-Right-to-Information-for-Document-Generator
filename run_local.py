import os
import keyring
from main import app

SMTP_KEYRING_SERVICE = "CiviForm Portal local email"
SMTP_KEYRING_ACCOUNT = "mk4829779@gmail.com"


def main():
    # 1. Check if Render already provided the password via Environment Variable
    password = os.environ.get("SMTP_APP_PASSWORD")

    # 2. If not found on Render, fall back to checking your local Windows Keyring (for local testing)
    if not password:
        password = keyring.get_password(SMTP_KEYRING_SERVICE, SMTP_KEYRING_ACCOUNT)

    if not password:
        raise SystemExit(
            "The Gmail App Password is not saved. Run run_local.ps1 and enter "
            "a newly generated App Password when prompted."
        )
    
    # Ensure it's set in the environment for your Flask app to use
    os.environ["SMTP_APP_PASSWORD"] = password

    # 3. Handle port configuration for Render vs Localhost
    # Render automatically provides a PORT environment variable. If it's missing, default to 8080.
    port = int(os.environ.get("PORT", 8080))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"

    print(f"Complaint email API listening at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()