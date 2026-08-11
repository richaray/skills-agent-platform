"""
API routes for skills and their versions.

The versioning rules enforced here:
  * A skill always has at most one draft at a time. Fewer states, less confusion.
  * Only a draft can be edited.
  * A draft can only be published if it has no validation errors.
  * A published version is never modified again - you create a new draft from it.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas, tools, validation
from app.db import get_db
from app.logging_setup import app_log, log_event

router = APIRouter(prefix="/api", tags=["skills"])


# --- tools -------------------------------------------------------------------


@router.get("/tools")
def list_tools():
    """The bounded set of tools a skill may choose from."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "is_write": tool.is_write,
        }
        for tool in sorted(tools.TOOLS.values(), key=lambda t: t.name)
    ]


# --- helpers -----------------------------------------------------------------


def _get_skill(db: Session, skill_id: int) -> models.Skill:
    skill = db.get(models.Skill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="That skill does not exist.")
    return skill


def _get_version(db: Session, version_id: int) -> models.SkillVersion:
    version = db.get(models.SkillVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="That skill version does not exist.")
    return version


def _definition_of(version: models.SkillVersion) -> dict:
    """The parts of a version that validation cares about."""
    return {
        "instructions": version.instructions,
        "input_schema": version.input_schema,
        "output_schema": version.output_schema,
        "examples": version.examples,
        "allowed_tools": version.allowed_tools,
        "approval_required_tools": version.approval_required_tools,
        "max_steps": version.max_steps,
    }


# --- skills ------------------------------------------------------------------


@router.get("/skills")
def list_skills(db: Session = Depends(get_db)):
    skills = db.query(models.Skill).order_by(models.Skill.created_at.desc()).all()
    return [schemas.skill_to_dict(s) for s in skills]


@router.post("/skills", status_code=201)
def create_skill(payload: schemas.SkillCreate, db: Session = Depends(get_db)):
    """Creates a skill and its empty version 1 draft in one go."""
    name = payload.name.strip()

    existing = db.query(models.Skill).filter(models.Skill.name == name).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"A skill named '{name}' already exists.")

    skill = models.Skill(name=name, purpose=payload.purpose.strip())
    db.add(skill)
    db.flush()

    draft = models.SkillVersion(
        skill_id=skill.id,
        version_number=1,
        status="draft",
        instructions="",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        examples=[],
        allowed_tools=[],
        approval_required_tools=[],
        max_steps=8,
    )
    db.add(draft)
    db.commit()
    db.refresh(skill)

    log_event(app_log, "skill_created", skill_id=skill.id, name=skill.name)

    return {"skill": schemas.skill_to_dict(skill), "draft_version_id": draft.id}


@router.get("/skills/{skill_id}")
def get_skill(skill_id: int, db: Session = Depends(get_db)):
    skill = _get_skill(db, skill_id)
    return {
        "skill": schemas.skill_to_dict(skill),
        "versions": [schemas.version_to_dict(v) for v in skill.versions],
    }


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: int, db: Session = Depends(get_db)):
    skill = _get_skill(db, skill_id)

    # Refuse if any run exists: deleting would destroy execution history, and
    # history is one of the things this platform promises to preserve.
    run_count = (
        db.query(models.Execution)
        .join(models.SkillVersion)
        .filter(models.SkillVersion.skill_id == skill_id)
        .count()
    )
    if run_count:
        raise HTTPException(
            status_code=409,
            detail=f"This skill has {run_count} run(s) in its history and cannot be deleted.",
        )

    db.delete(skill)
    db.commit()
    log_event(app_log, "skill_deleted", skill_id=skill_id)


# --- versions ----------------------------------------------------------------


@router.get("/versions/{version_id}")
def get_version(version_id: int, db: Session = Depends(get_db)):
    version = _get_version(db, version_id)
    problems = validation.validate_skill_definition(_definition_of(version))
    return {
        "version": schemas.version_to_dict(version),
        "problems": [p.as_dict() for p in problems],
    }


@router.put("/versions/{version_id}")
def update_version(
    version_id: int,
    payload: schemas.SkillDefinitionInput,
    db: Session = Depends(get_db),
):
    """Saves changes to a draft and returns any validation problems.

    Saving an invalid draft is allowed on purpose - you should be able to save
    half-finished work. Publishing is where the rules are enforced.
    """
    version = _get_version(db, version_id)

    if not version.is_editable:
        raise HTTPException(
            status_code=409,
            detail=f"Version {version.version_number} is {version.status} and cannot be edited. "
            "Create a new draft instead.",
        )

    version.instructions = payload.instructions
    version.input_schema = payload.input_schema
    version.output_schema = payload.output_schema
    version.examples = payload.examples
    version.allowed_tools = payload.allowed_tools
    version.approval_required_tools = payload.approval_required_tools
    version.max_steps = payload.max_steps
    db.commit()
    db.refresh(version)

    problems = validation.validate_skill_definition(_definition_of(version))

    log_event(
        app_log,
        "version_saved",
        version_id=version.id,
        skill_id=version.skill_id,
        error_count=sum(1 for p in problems if p.severity == "error"),
    )

    return {
        "version": schemas.version_to_dict(version),
        "problems": [p.as_dict() for p in problems],
    }


@router.post("/versions/{version_id}/publish")
def publish_version(version_id: int, db: Session = Depends(get_db)):
    """Freezes a draft. This is the gate that validation exists for."""
    version = _get_version(db, version_id)

    if version.status == "published":
        raise HTTPException(status_code=409, detail="This version is already published.")

    problems = validation.validate_skill_definition(_definition_of(version))
    if validation.has_errors(problems):
        # 422 rather than 400: the request was well-formed, the content was not.
        raise HTTPException(
            status_code=422,
            detail={
                "message": "This skill cannot be published until its errors are fixed.",
                "problems": [p.as_dict() for p in problems],
            },
        )

    version.status = "published"
    version.published_at = models.utcnow()
    db.commit()
    db.refresh(version)

    log_event(
        app_log,
        "version_published",
        version_id=version.id,
        skill_id=version.skill_id,
        version_number=version.version_number,
    )

    return {
        "version": schemas.version_to_dict(version),
        "problems": [p.as_dict() for p in problems],
    }


@router.post("/skills/{skill_id}/versions", status_code=201)
def create_draft(skill_id: int, copy_from_version_id: int | None = None, db: Session = Depends(get_db)):
    """Starts a new draft, copying an existing version as the starting point."""
    skill = _get_skill(db, skill_id)

    existing_draft = next((v for v in skill.versions if v.status == "draft"), None)
    if existing_draft is not None:
        raise HTTPException(
            status_code=409,
            detail=f"This skill already has a draft (version {existing_draft.version_number}). "
            "Publish or delete it before starting another.",
        )

    if copy_from_version_id is not None:
        source = _get_version(db, copy_from_version_id)
        if source.skill_id != skill_id:
            raise HTTPException(status_code=400, detail="That version belongs to a different skill.")
    else:
        # Default to copying the newest version so you rarely start from nothing.
        source = skill.versions[-1] if skill.versions else None

    next_number = max((v.version_number for v in skill.versions), default=0) + 1

    draft = models.SkillVersion(
        skill_id=skill.id,
        version_number=next_number,
        status="draft",
        instructions=source.instructions if source else "",
        input_schema=source.input_schema if source else {"type": "object", "properties": {}},
        output_schema=source.output_schema if source else {"type": "object", "properties": {}},
        examples=list(source.examples) if source else [],
        allowed_tools=list(source.allowed_tools) if source else [],
        approval_required_tools=list(source.approval_required_tools) if source else [],
        max_steps=source.max_steps if source else 8,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    log_event(
        app_log,
        "draft_created",
        version_id=draft.id,
        skill_id=skill.id,
        version_number=next_number,
        copied_from=source.id if source else None,
    )

    return schemas.version_to_dict(draft)


@router.delete("/versions/{version_id}", status_code=204)
def delete_draft(version_id: int, db: Session = Depends(get_db)):
    version = _get_version(db, version_id)

    if version.status != "draft":
        raise HTTPException(
            status_code=409, detail="Only drafts can be deleted. Published versions are permanent."
        )

    db.delete(version)
    db.commit()
    log_event(app_log, "draft_deleted", version_id=version_id)


# --- comparing versions ------------------------------------------------------

# The fields we diff, in the order they should appear in the UI.
COMPARABLE_FIELDS = [
    "instructions",
    "input_schema",
    "output_schema",
    "examples",
    "allowed_tools",
    "approval_required_tools",
    "max_steps",
]


@router.get("/skills/{skill_id}/compare")
def compare_versions(skill_id: int, left: int, right: int, db: Session = Depends(get_db)):
    """Field-by-field comparison of two versions of the same skill.

    `left` and `right` are version ids. Returns one entry per field with both
    values and whether they differ, so the frontend just renders the result
    rather than working out the diff itself.
    """
    _get_skill(db, skill_id)

    left_version = _get_version(db, left)
    right_version = _get_version(db, right)

    for version in (left_version, right_version):
        if version.skill_id != skill_id:
            raise HTTPException(
                status_code=400,
                detail=f"Version {version.id} does not belong to skill {skill_id}.",
            )

    differences = []
    for field in COMPARABLE_FIELDS:
        left_value = getattr(left_version, field)
        right_value = getattr(right_version, field)
        differences.append(
            {
                "field": field,
                "left": left_value,
                "right": right_value,
                "changed": left_value != right_value,
            }
        )

    return {
        "left": schemas.version_to_dict(left_version),
        "right": schemas.version_to_dict(right_version),
        "differences": differences,
        "changed_count": sum(1 for d in differences if d["changed"]),
    }
