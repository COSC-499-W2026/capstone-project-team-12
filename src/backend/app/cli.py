"""Command-line jobs, run by supercronic in the scheduler container (see crontab).

python -m app.cli send-emails
"""

import argparse
import logging
import sys

from app.config import get_settings
from app.db import SessionLocal
from app.services.email_sender import SendReport, send_due_emails
from app.services.smtp_mailer import SmtpMailer

logger = logging.getLogger("app.cli")


def send_emails() -> int:
    with SessionLocal() as db:
        report = send_due_emails(SmtpMailer(get_settings()), db)
    if report != SendReport():  # it runs every minute, so only log when something happened
        logger.info("send-emails: %s", report)
    return 0


COMMANDS = {"send-emails": send_emails}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return COMMANDS[args.command]()


if __name__ == "__main__":
    sys.exit(main())
