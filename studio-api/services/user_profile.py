"""Four-question user profile for the early-user feedback program.

Asked once at the end of the first-run tour and editable in Settings. Every
answer is optional: the point is to be able to read the onboarding funnel and
the feedback ratings *by cohort* (a faculty member who has run HTCondor for a
decade and a first-year student who has never seen a terminal are not the same
data point), not to gate anything on it.

Its own settings row rather than a field in `UIPreferences`, for the reasons
`services/telemetry.py` gives for consent: `PUT /api/settings/ui` replaces the
preferences wholesale, so a stale cache saving an unrelated toggle could wipe
the answers, and `GET /api/settings/ui` silently defaults values that no longer
validate. A profile answer that quietly reverts to "unanswered" would look, in
the collected CSVs, exactly like a user who skipped the question.

`collect_feedback.py` reads this row straight out of each user's SQLite file,
so the stored shape (`_SETTING_KEY`, the field names, `answered_at`) is a
contract with that script.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, ValidationError

log = structlog.get_logger()

_SETTING_KEY = "user_profile"

Role = Literal[
    "graduate_student",
    "postdoc",
    "research_scientist",
    "faculty",
    "research_software_engineer",
    "other",
]

PegasusExperience = Literal["never", "tried", "regular"]


class UserProfile(BaseModel):
    """What the user told us about themselves. Every field may be None."""

    research_field: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    pegasus_experience: PegasusExperience | None = None
    coding_comfort: int | None = Field(default=None, ge=1, le=5)
    # Server-set. Present means "the question was put to them and they
    # answered" (even if every answer is blank), which is how the tour knows
    # not to ask again. A client-supplied value is ignored.
    answered_at: str | None = None


async def load(db: Any) -> UserProfile:
    """The stored profile, or an empty one.

    A stored value that no longer validates (an option renamed between
    releases) drops that one field rather than the whole profile — losing a
    user's role because their coding-comfort answer is stale would be a worse
    outcome than the stale answer.
    """
    stored = await db.get_setting(_SETTING_KEY, None)
    if not isinstance(stored, dict):
        return UserProfile()
    known = {k: v for k, v in stored.items() if k in UserProfile.model_fields}
    try:
        return UserProfile(**known)
    except ValidationError:
        safe: dict[str, Any] = {}
        for key, value in known.items():
            try:
                UserProfile(**{key: value})
            except ValidationError:
                log.warning("user_profile_field_invalid", key=key)
                continue
            safe[key] = value
        return UserProfile(**safe)


async def save(db: Any, profile: UserProfile) -> UserProfile:
    """Persist the answers and stamp `answered_at`."""
    field = (profile.research_field or "").strip() or None
    saved = profile.model_copy(
        update={
            "research_field": field,
            "answered_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    await db.set_setting(_SETTING_KEY, saved.model_dump())
    # The research field is free text and stays out of the log: the log
    # pipeline is cluster-wide and retained, and a user who typed their lab or
    # project name did not agree to it ending up there. Whether they answered
    # it is enough for the aggregate view.
    log.info(
        "user_profile_set",
        role=saved.role,
        pegasus_experience=saved.pegasus_experience,
        coding=saved.coding_comfort,
        research_field_given=field is not None,
    )
    return saved
