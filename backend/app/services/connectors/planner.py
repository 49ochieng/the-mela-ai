"""
Mela AI - Microsoft Planner Connector
Indexes plan tasks from all org groups the user belongs to.
"""

from __future__ import annotations

import hashlib
import logging
from typing import AsyncIterator, Dict, Optional

from app.services.connectors.base import ConnectorBase, ConnectorDocument, SOURCE_TYPE_PLANNER
from app.services.connectors.graph_client import GraphClient
from app.services.connectors.sharepoint import _parse_dt

logger = logging.getLogger(__name__)


def _doc_id(plan_id: str, task_id: str) -> str:
    return hashlib.sha256(f"planner:{plan_id}:{task_id}".encode()).hexdigest()[:40]


class PlannerConnector(ConnectorBase):
    source_type = SOURCE_TYPE_PLANNER

    def __init__(
        self,
        workspace_id: str,
        context_type: str = "org",
        delegated_token: str = "",
    ) -> None:
        super().__init__(workspace_id, context_type)
        self._client = GraphClient(delegated_token=delegated_token or None)

    async def sync(self, full: bool = False) -> AsyncIterator[ConnectorDocument]:
        try:
            groups = await self._client.list_my_groups()
        except Exception as e:
            self._logger.error("Cannot list groups: %s", str(e))
            return

        for group in groups:
            group_id = group.get("id", "")
            group_name = group.get("displayName", "")
            plans = await self._client.get_group_plans(group_id)

            for plan in plans:
                plan_id = plan["id"]
                plan_title = plan.get("title", "")
                try:
                    tasks = await self._client.list_plan_tasks(plan_id)
                except Exception as e:
                    self._logger.warning("Cannot list tasks for plan %s: %s", plan_id, str(e))
                    continue

                for task in tasks:
                    doc = self._build_document(task, plan_id, plan_title, group_name)
                    if doc:
                        yield doc

    def _build_document(
        self, task: Dict, plan_id: str, plan_title: str, group_name: str
    ) -> Optional[ConnectorDocument]:
        task_id = task.get("id", "")
        title = task.get("title", "")
        due = task.get("dueDateTime")
        pct = task.get("percentComplete", 0)
        created = task.get("createdDateTime")
        assigned_users = list(task.get("assignments", {}).keys())

        lines = [f"Task: {title}", f"Plan: {plan_title}", f"Group: {group_name}"]
        if due:
            lines.append(f"Due: {due}")
        lines.append(f"Progress: {pct}%")
        if assigned_users:
            lines.append(f"Assigned to: {', '.join(assigned_users)}")

        return ConnectorDocument(
            id=_doc_id(plan_id, task_id),
            source_type=self.source_type,
            source_id=plan_id,
            workspace_id=self.workspace_id,
            context_type=self.context_type,
            title=title,
            content="\n".join(lines),
            url="",
            file_type="task",
            created_at=_parse_dt(created),
            acl_users=assigned_users,
            citation={
                "source": "Planner",
                "plan": plan_title,
                "group": group_name,
                "task": title,
                "due": due or "",
            },
        )

    async def health_check(self) -> bool:
        try:
            await self._client.list_my_groups()
            return True
        except Exception as e:
            self._logger.warning("Planner health check failed: %s", str(e))
            return False
