"""Command-line jobs, run by supercronic in the scheduler container (see crontab).

python -m app.cli send-emails
python -m app.cli expire-sessions
"""

import argparse
import logging
import sys

from app.config import get_settings
from app.db import SessionLocal
from app.game.interface import expire_idle_sessions
from app.services.email_sender import SendReport, send_due_emails
from app.services.smtp_mailer import SmtpMailer

logger = logging.getLogger("app.cli")

# games expired per transaction, so one run never holds thousands of row locks
EXPIRE_BATCH_SIZE = 500


def send_emails() -> int:
    with SessionLocal() as db:
        report = send_due_emails(SmtpMailer(get_settings()), db)
    # it runs every minute, so only log when something happened
    if report != SendReport():
        logger.info("send-emails: %s", report)
    return 0


def expire_sessions() -> int:
    expired = 0
    with SessionLocal() as db:
        while True:
            count = expire_idle_sessions(db, batch_size=EXPIRE_BATCH_SIZE)
            db.commit()
            expired += count
            # a short batch means nothing else was idle, or the rest is locked until the next run
            if count < EXPIRE_BATCH_SIZE:
                break
    # it runs every 5 minutes, so only log when something happened
    if expired:
        logger.info("expire-sessions: expired %d games", expired)
    return 0


COMMANDS = {"send-emails": send_emails, "expire-sessions": expire_sessions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return COMMANDS[args.command]()


if __name__ == "__main__":
    sys.exit(main())
