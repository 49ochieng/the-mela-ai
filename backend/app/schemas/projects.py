"""
Mela AI - Project Schemas
"""

from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    system_prompt: Optional[str] = None
    context_type: str = "personal"  # 'org' | 'personal'
    workspace_id: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    system_prompt: Optional[str] = None
    is_archived: Optional[bool] = None


class ProjectMemoryItem(BaseModel):
    id: str
    fact: str
    source_conversation_id: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    system_prompt: Optional[str] = None
    is_archived: bool
    context_type: str = "personal"
    workspace_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    conversation_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class ProjectDetail(ProjectResponse):
    memories: list[ProjectMemoryItem] = []


class AddMemoryRequest(BaseModel):
    fact: str


class ProjectFileResponse(BaseModel):
    id: str
    project_id: str
    filename: str
    file_type: str
    file_size: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ProjectInstructionsUpdate(BaseModel):
    system_prompt: str


class ProjectConversationResponse(BaseModel):
    id: str
    title: str
    model: str
    is_private: bool = False
    message_count: int = 0
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
