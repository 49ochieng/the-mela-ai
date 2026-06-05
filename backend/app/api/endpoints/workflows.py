"""
Mela AI - Workflow Automation Endpoints
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.auth import UserInfo
from app.services.workflow_service import workflow_service, WORKFLOW_TEMPLATES

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────────────────────

class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    trigger_type: str = "manual"
    trigger_config: Optional[dict] = None
    actions: Optional[list] = None
    status: str = "draft"
    visibility: str = "user"


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_config: Optional[dict] = None
    actions: Optional[list] = None
    status: Optional[str] = None
    visibility: Optional[str] = None


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    trigger_type: str
    trigger_config: Optional[dict]
    actions: Optional[list]
    status: str
    visibility: str
    created_by: str
    run_count: int
    last_run_at: Optional[str]
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_safe(cls, obj):
        return cls(
            id=obj.id,
            name=obj.name,
            description=obj.description,
            trigger_type=obj.trigger_type,
            trigger_config=obj.trigger_config,
            actions=obj.actions,
            status=obj.status.value if hasattr(obj.status, 'value') else str(obj.status),
            visibility=obj.visibility,
            created_by=obj.created_by,
            run_count=obj.run_count or 0,
            last_run_at=obj.last_run_at.isoformat() if obj.last_run_at else None,
            created_at=obj.created_at.isoformat(),
            updated_at=obj.updated_at.isoformat(),
        )


class WorkflowRunResponse(BaseModel):
    id: str
    workflow_id: str
    triggered_by: Optional[str]
    trigger_type: str
    status: str
    steps_completed: int
    steps_total: int
    output_data: Optional[dict]
    error_message: Optional[str]
    started_at: Optional[str]
    finished_at: Optional[str]
    created_at: str

    @classmethod
    def from_orm_safe(cls, obj):
        return cls(
            id=obj.id,
            workflow_id=obj.workflow_id,
            triggered_by=obj.triggered_by,
            trigger_type=obj.trigger_type,
            status=obj.status.value if hasattr(obj.status, 'value') else str(obj.status),
            steps_completed=obj.steps_completed,
            steps_total=obj.steps_total,
            output_data=obj.output_data,
            error_message=obj.error_message,
            started_at=obj.started_at.isoformat() if obj.started_at else None,
            finished_at=obj.finished_at.isoformat() if obj.finished_at else None,
            created_at=obj.created_at.isoformat(),
        )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/templates")
async def get_workflow_templates(
    current_user: UserInfo = Depends(get_current_user),
):
    """Return built-in workflow templates for quick-start."""
    return {"templates": WORKFLOW_TEMPLATES}


@router.get("/", response_model=List[WorkflowResponse])
async def list_workflows(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workflows = await workflow_service.list_workflows(
        db=db,
        user_id=current_user.id,
        admin=any(r.lower() == "admin" for r in (current_user.roles or [])),
    )
    return [WorkflowResponse.from_orm_safe(w) for w in workflows]


@router.post("/", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    data: WorkflowCreate,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workflow = await workflow_service.create_workflow(
        db=db,
        user_id=current_user.id,
        **data.model_dump(exclude_none=True),
    )
    if not workflow:
        raise HTTPException(status_code=500, detail="Failed to create workflow")
    return WorkflowResponse.from_orm_safe(workflow)


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workflow = await workflow_service.get_workflow(db, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return WorkflowResponse.from_orm_safe(workflow)


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: str,
    data: WorkflowUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workflow = await workflow_service.update_workflow(
        db=db,
        workflow_id=workflow_id,
        user_id=current_user.id,
        admin=any(r.lower() == "admin" for r in (current_user.roles or [])),
        **data.model_dump(exclude_none=True),
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")
    return WorkflowResponse.from_orm_safe(workflow)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    deleted = await workflow_service.delete_workflow(
        db=db,
        workflow_id=workflow_id,
        user_id=current_user.id,
        admin=any(r.lower() == "admin" for r in (current_user.roles or [])),
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Workflow not found or access denied")


@router.post("/{workflow_id}/run", response_model=WorkflowRunResponse)
async def run_workflow(
    workflow_id: str,
    input_data: Optional[dict] = None,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a workflow run."""
    run = await workflow_service.run_workflow(
        db=db,
        workflow_id=workflow_id,
        triggered_by=current_user.id,
        input_data=input_data,
    )
    if not run:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return WorkflowRunResponse.from_orm_safe(run)


@router.get("/{workflow_id}/runs", response_model=List[WorkflowRunResponse])
async def list_workflow_runs(
    workflow_id: str,
    limit: int = 20,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    runs = await workflow_service.list_runs(db, workflow_id, limit=limit)
    return [WorkflowRunResponse.from_orm_safe(r) for r in runs]
