"""Authorization for the fork-PR ``/review`` command flow.

Pure stdlib. Used to decide whether a person who triggered a review (e.g. by
commenting ``/review`` on a PR) is trusted enough to spend the maintainer's
OpenRouter quota. Without this gate, an arbitrary fork author could drain a
BYOK user's free-model budget at will.

Two independent signals make a commenter authorized:

  * **author_association** — GitHub's relationship label on the comment/PR
    author. ``OWNER``/``MEMBER``/``COLLABORATOR`` mean the actor already has a
    standing relationship to the repo or its org.
  * **permission** — the actor's effective repo permission from the
    collaborators/{user}/permission API. ``admin``/``write``/``maintain`` all
    imply push-level trust.

Either signal alone suffices. The check is conservative: anything it does not
recognize (``CONTRIBUTOR``, ``FIRST_TIME_CONTRIBUTOR``, ``NONE``, ``read``,
``triage``, ``None``) is treated as unauthorized.
"""

from __future__ import annotations

# Comment/PR author_association values that grant access on their own.
AUTHORIZED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

# Effective repo permission levels that grant access on their own.
_AUTHORIZED_PERMISSIONS = {"admin", "write", "maintain"}


def is_authorized(author_association, permission) -> bool:
    """Return True iff the actor is trusted enough to trigger a review.

    Authorized when ``author_association`` is one of
    :data:`AUTHORIZED_ASSOCIATIONS` (``OWNER``/``MEMBER``/``COLLABORATOR``) OR
    ``permission`` is one of ``{"admin", "write", "maintain"}``. Comparison is
    exact on the association (GitHub returns upper-case) and case-insensitive on
    the permission for robustness. ``None``/missing values are unauthorized.
    """
    if author_association in AUTHORIZED_ASSOCIATIONS:
        return True
    if isinstance(permission, str) and permission.strip().lower() in _AUTHORIZED_PERMISSIONS:
        return True
    return False
