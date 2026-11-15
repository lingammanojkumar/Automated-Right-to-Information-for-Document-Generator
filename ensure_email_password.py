import getpass

import keyring

SMTP_KEYRING_SERVICE = "CiviForm Portal local email"
SMTP_KEYRING_ACCOUNT = "mk4829779@gmail.com"


def main():
    if keyring.get_password(SMTP_KEYRING_SERVICE, SMTP_KEYRING_ACCOUNT):
        return

    password = getpass.getpass(
        "Enter a newly generated Gmail App Password (input hidden): "
    ).strip()
    if not password:
        raise SystemExit("A Gmail App Password is required to send email.")

    keyring.set_password(
        SMTP_KEYRING_SERVICE,
        SMTP_KEYRING_ACCOUNT,
        password.replace(" ", ""),
    )
    print("Saved securely in Windows Credential Manager.")


if __name__ == "__main__":
    main()
