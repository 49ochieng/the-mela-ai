"""
Mela AI - Enterprise Data Connectors

Connectors pull content from Microsoft 365, SharePoint, OneDrive, email,
Planner tasks, org websites, and the public web into Azure AI Search.
"""
from app.services.connectors.base import ConnectorDocument, ConnectorBase

__all__ = ["ConnectorDocument", "ConnectorBase"]
