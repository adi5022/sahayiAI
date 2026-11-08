from pydantic import BaseModel, Field, field_validator

CATEGORIES = ("medical", "scam", "safety", "emotional", "general")


class UserReply(BaseModel):
    text: str = Field(max_length=300)
    follow_up: str | None = None
    request_image: bool = False  # True when a photo of a visible injury would help; the pipeline asks for it


class FamilyAlert(BaseModel):
    summary: str = Field(max_length=400)
    suggested_level: int = Field(ge=1, le=3)
    send: bool
    category: str = "general"   # medical | scam | safety | emotional | general

    @field_validator("category", mode="before")
    @classmethod
    def _known_category(cls, v):
        v = str(v or "general").strip().lower()
        return v if v in CATEGORIES else "general"


class Turn(BaseModel):
    user: UserReply
    family: FamilyAlert
    note: str = Field("", max_length=200)   # short fact for the member's long-term history (health report, scam incident)