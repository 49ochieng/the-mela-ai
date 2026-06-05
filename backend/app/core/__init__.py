"""
Mela AI - Core Module
"""

from app.core.config import settings
from app.core.database import Base, get_db, engine
from app.core.security import get_current_user, get_current_admin_user, azure_auth

__all__ = [
    "settings",
    "Base",
    "get_db",
    "engine",
    "get_current_user",
    "get_current_admin_user",
    "azure_auth",
]
