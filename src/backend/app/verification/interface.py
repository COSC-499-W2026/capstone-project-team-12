"""Public interface of app.verification: the only module other components may import.

Planned (Interface card #14, Stage 2 handoff): the session location status, attempts used and
photo result returned to the game session. Agree on each signature with its users before adding
it here.

delete_user_photos: deleting (anonymising) an account deletes the user's photos with the same
deletion the purge-media job uses for old photos. Not implemented yet; see the function.
"""

from uuid import UUID

from sqlalchemy.orm import Session


def delete_user_photos(student_id: UUID, db: Session) -> None:
    """Delete every photo the student took: clear each photo attempt's photo path, OCR text,
    hash and response, and delete its file from media storage, as purging old photos does. The
    files are deleted directly, not through signed media links. Doesn't commit.

    Not implemented yet: photo attempts aren't stored and the purge-media job whose deletion
    this will reuse doesn't exist, so there are no photos to delete and this does nothing.
    """
    # TODO: call the purge-media job's photo deletion for this student's attempts once it exists
