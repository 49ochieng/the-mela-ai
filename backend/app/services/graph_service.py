"""
Mela AI - Microsoft Graph API Service
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class GraphAPIService:
    """Service for Microsoft Graph API operations."""

    def __init__(self):
        self.base_url = settings.GRAPH_API_ENDPOINT

    async def _request(
        self,
        method: str,
        endpoint: str,
        access_token: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Make a request to Graph API."""
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                json=data,
                params=params,
                timeout=30.0,
            )

            if response.status_code >= 400:
                logger.error(f"Graph API error: {response.status_code} - {response.text}")
                response.raise_for_status()

            if response.status_code == 204:
                return {"success": True}

            return response.json()

    # ==================== User Operations ====================

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        """Get current user's profile."""
        return await self._request("GET", "/me", access_token)

    async def get_user_photo(self, access_token: str) -> Optional[bytes]:
        """Get current user's photo."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/me/photo/$value",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if response.status_code == 200:
                    return response.content
        except Exception as e:
            logger.warning(f"Failed to get user photo: {e}")
        return None

    # ==================== Email Operations ====================

    async def send_email_app_only(
        self,
        sender_email: str,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        is_html: bool = True,
    ) -> Dict[str, Any]:
        """Send email using app-only token via /users/{sender}/sendMail.
        Requires Mail.Send application permission on the enterprise registration."""
        from app.services.connectors.graph_client import GraphClient
        client = GraphClient()  # uses app-only client-credentials token
        auth = await client._auth_header()
        message: Dict[str, Any] = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML" if is_html else "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": e}} for e in to],
            }
        }
        if cc:
            message["message"]["ccRecipients"] = [
                {"emailAddress": {"address": e}} for e in cc
            ]
        async with httpx.AsyncClient(timeout=30) as client_http:
            r = await client_http.post(
                f"https://graph.microsoft.com/v1.0/users/{sender_email}/sendMail",
                headers={"Authorization": auth, "Content-Type": "application/json"},
                json=message,
            )
            if r.status_code >= 400:
                logger.error("App-only sendMail error %s: %s", r.status_code, r.text)
                r.raise_for_status()
        return {"success": True}

    async def send_email(
        self,
        access_token: str,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        is_html: bool = True,
        attachments: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Send an email (delegated token — /me/sendMail)."""
        message = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML" if is_html else "Text",
                    "content": body,
                },
                "toRecipients": [{"emailAddress": {"address": email}} for email in to],
            }
        }

        if cc:
            message["message"]["ccRecipients"] = [
                {"emailAddress": {"address": email}} for email in cc
            ]

        if bcc:
            message["message"]["bccRecipients"] = [
                {"emailAddress": {"address": email}} for email in bcc
            ]

        if attachments:
            message["message"]["attachments"] = attachments

        return await self._request("POST", "/me/sendMail", access_token, data=message)

    async def get_emails(
        self,
        access_token: str,
        folder: str = "inbox",
        top: int = 10,
        filter_query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get emails from a folder."""
        params = {"$top": top, "$orderby": "receivedDateTime desc"}
        if filter_query:
            params["$filter"] = filter_query

        return await self._request(
            "GET",
            f"/me/mailFolders/{folder}/messages",
            access_token,
            params=params,
        )

    async def create_draft(
        self,
        access_token: str,
        to: List[str],
        subject: str,
        body: str,
        is_html: bool = True,
    ) -> Dict[str, Any]:
        """Create a draft email."""
        message = {
            "subject": subject,
            "body": {
                "contentType": "HTML" if is_html else "Text",
                "content": body,
            },
            "toRecipients": [{"emailAddress": {"address": email}} for email in to],
        }

        return await self._request("POST", "/me/messages", access_token, data=message)

    # ==================== Calendar Operations ====================

    async def get_calendar_events(
        self,
        access_token: str,
        start_datetime: datetime,
        end_datetime: datetime,
    ) -> Dict[str, Any]:
        """Get calendar events in a time range."""
        params = {
            "startDateTime": start_datetime.isoformat() + "Z",
            "endDateTime": end_datetime.isoformat() + "Z",
            "$orderby": "start/dateTime",
        }

        return await self._request(
            "GET",
            "/me/calendarView",
            access_token,
            params=params,
        )

    async def create_event(
        self,
        access_token: str,
        subject: str,
        start: datetime,
        end: datetime,
        attendees: Optional[List[str]] = None,
        body: Optional[str] = None,
        location: Optional[str] = None,
        is_online_meeting: bool = False,
        timezone: str = "UTC",
    ) -> Dict[str, Any]:
        """Create a calendar event."""
        event = {
            "subject": subject,
            "start": {
                "dateTime": start.isoformat(),
                "timeZone": timezone,
            },
            "end": {
                "dateTime": end.isoformat(),
                "timeZone": timezone,
            },
        }

        if body:
            event["body"] = {
                "contentType": "HTML",
                "content": body,
            }

        if location:
            event["location"] = {"displayName": location}

        if attendees:
            event["attendees"] = [
                {
                    "emailAddress": {"address": email},
                    "type": "required",
                }
                for email in attendees
            ]

        if is_online_meeting:
            event["isOnlineMeeting"] = True
            event["onlineMeetingProvider"] = "teamsForBusiness"

        return await self._request("POST", "/me/events", access_token, data=event)

    async def get_free_busy(
        self,
        access_token: str,
        schedules: List[str],
        start: datetime,
        end: datetime,
    ) -> Dict[str, Any]:
        """Get free/busy information for schedules."""
        data = {
            "schedules": schedules,
            "startTime": {
                "dateTime": start.isoformat(),
                "timeZone": "UTC",
            },
            "endTime": {
                "dateTime": end.isoformat(),
                "timeZone": "UTC",
            },
            "availabilityViewInterval": 30,
        }

        return await self._request(
            "POST",
            "/me/calendar/getSchedule",
            access_token,
            data=data,
        )

    # ==================== Teams Operations ====================

    async def create_online_meeting(
        self,
        access_token: str,
        subject: str,
        start: datetime,
        end: datetime,
        attendees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a Teams online meeting."""
        meeting = {
            "subject": subject,
            "startDateTime": start.isoformat() + "Z",
            "endDateTime": end.isoformat() + "Z",
        }

        if attendees:
            meeting["participants"] = {
                "attendees": [
                    {
                        "identity": {
                            "user": {"id": email}
                        },
                        "role": "attendee",
                    }
                    for email in attendees
                ]
            }

        return await self._request(
            "POST",
            "/me/onlineMeetings",
            access_token,
            data=meeting,
        )

    # ==================== Planner Operations ====================

    async def get_planner_tasks(
        self,
        access_token: str,
        plan_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get Planner tasks."""
        if plan_id:
            return await self._request(
                "GET",
                f"/planner/plans/{plan_id}/tasks",
                access_token,
            )
        return await self._request("GET", "/me/planner/tasks", access_token)

    async def create_planner_task(
        self,
        access_token: str,
        plan_id: str,
        bucket_id: str,
        title: str,
        due_date: Optional[datetime] = None,
        assigned_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a Planner task."""
        task = {
            "planId": plan_id,
            "bucketId": bucket_id,
            "title": title,
        }

        if due_date:
            task["dueDateTime"] = due_date.strftime("%Y-%m-%dT%H:%M:%SZ")

        if assigned_to:
            task["assignments"] = {
                assigned_to: {
                    "@odata.type": "#microsoft.graph.plannerAssignment",
                    "orderHint": " !",
                }
            }

        return await self._request("POST", "/planner/tasks", access_token, data=task)

    async def update_planner_task(
        self,
        access_token: str,
        task_id: str,
        etag: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update a Planner task."""
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{self.base_url}/planner/tasks/{task_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "If-Match": etag,
                },
                json=updates,
            )
            response.raise_for_status()
            return response.json() if response.content else {"success": True}

    # ==================== App-only Identity Management ====================

    async def _app_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """App-only Graph API request (client credentials token)."""
        from app.services.connectors.graph_client import GraphClient
        gc = GraphClient()
        auth = await gc._auth_header()
        url = f"https://graph.microsoft.com/v1.0{endpoint}"
        headers = {"Authorization": auth, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(method, url, headers=headers, json=data, params=params)
            if r.status_code >= 400:
                logger.error(
                    "Graph app-only %s %s → %s: %s",
                    method, endpoint, r.status_code, r.text[:300],
                )
                r.raise_for_status()
            # 202 Accepted (sendMail) and 204 No Content both have empty bodies
            if r.status_code in (202, 204) or not r.content:
                return {"success": True}
            return r.json()

    async def get_user_app(self, user_id_or_upn: str) -> Optional[Dict[str, Any]]:
        """Get a user by ID or UPN using app-only token. Returns None if not found."""
        try:
            return await self._app_request("GET", f"/users/{user_id_or_upn}")
        except Exception:
            return None

    async def user_exists(self, upn: str) -> bool:
        """Check if a UPN already exists in the directory."""
        result = await self.get_user_app(upn)
        return result is not None

    async def create_entra_user(
        self,
        display_name: str,
        user_principal_name: str,
        mail_nickname: str,
        given_name: str,
        surname: str,
        job_title: Optional[str] = None,
        department: Optional[str] = None,
        usage_location: str = "US",
        password: Optional[str] = None,
        force_change_password: bool = True,
    ) -> Dict[str, Any]:
        """Create a new Entra user via app-only token."""
        import secrets
        import string
        if not password:
            # Generate a secure temporary password
            chars = string.ascii_letters + string.digits + "!@#$"
            password = "".join(secrets.choice(chars) for _ in range(16))

        body: Dict[str, Any] = {
            "accountEnabled": True,
            "displayName": display_name,
            "userPrincipalName": user_principal_name,
            "mailNickname": mail_nickname,
            "givenName": given_name,
            "surname": surname,
            "usageLocation": usage_location,
            "passwordProfile": {
                "forceChangePasswordNextSignIn": force_change_password,
                "password": password,
            },
        }
        if job_title:
            body["jobTitle"] = job_title
        if department:
            body["department"] = department

        result = await self._app_request("POST", "/users", data=body)
        result["_temp_password"] = password  # return temp password to admin
        return result

    async def set_user_manager(self, user_id: str, manager_upn: str) -> Dict[str, Any]:
        """Assign manager to user (app-only)."""
        manager = await self.get_user_app(manager_upn)
        if not manager:
            raise ValueError(f"Manager not found: {manager_upn}")
        manager_id = manager["id"]
        body = {"@odata.id": f"https://graph.microsoft.com/v1.0/users/{manager_id}"}
        return await self._app_request("PUT", f"/users/{user_id}/manager/$ref", data=body)

    async def add_user_to_group(self, user_id: str, group_id: str) -> Dict[str, Any]:
        """Add user to a group (app-only). Ignores 'already exists' errors."""
        body = {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"}
        try:
            return await self._app_request("POST", f"/groups/{group_id}/members/$ref", data=body)
        except Exception as e:
            if "already exists" in str(e).lower() or "One or more added object references" in str(e):
                return {"success": True, "note": "already_member"}
            raise

    async def assign_licenses(self, user_id: str, sku_ids: List[str]) -> Dict[str, Any]:
        """Assign licenses to a user (app-only)."""
        body = {
            "addLicenses": [{"skuId": sku_id} for sku_id in sku_ids],
            "removeLicenses": [],
        }
        return await self._app_request("POST", f"/users/{user_id}/assignLicense", data=body)

    async def get_user_licenses_app(self, user_id: str) -> List[Dict[str, Any]]:
        """Get a user's assigned licenses (app-only)."""
        try:
            result = await self._app_request("GET", f"/users/{user_id}/licenseDetails")
            return result.get("value", [])
        except Exception:
            return []

    async def get_user_groups_app(self, user_id: str) -> List[Dict[str, Any]]:
        """Get a user's group memberships (app-only)."""
        try:
            result = await self._app_request("GET", f"/users/{user_id}/memberOf")
            return result.get("value", [])
        except Exception:
            return []

    async def set_account_enabled(self, user_id: str, enabled: bool) -> Dict[str, Any]:
        """Enable or disable a user account (app-only)."""
        return await self._app_request("PATCH", f"/users/{user_id}", data={"accountEnabled": enabled})

    async def revoke_sign_in_sessions(self, user_id: str) -> Dict[str, Any]:
        """Revoke all active sign-in sessions for a user (app-only)."""
        return await self._app_request("POST", f"/users/{user_id}/revokeSignInSessions")

    async def remove_licenses(self, user_id: str, sku_ids: List[str]) -> Dict[str, Any]:
        """Remove licenses from a user (app-only)."""
        body = {
            "addLicenses": [],
            "removeLicenses": sku_ids,
        }
        return await self._app_request("POST", f"/users/{user_id}/assignLicense", data=body)

    async def remove_from_group(self, user_id: str, group_id: str) -> Dict[str, Any]:
        """Remove user from a group (app-only). Ignores 'not found' errors."""
        try:
            return await self._app_request("DELETE", f"/groups/{group_id}/members/{user_id}/$ref")
        except Exception as e:
            if "404" in str(e) or "does not exist" in str(e).lower():
                return {"success": True, "note": "not_member"}
            raise

    async def delete_entra_user(self, user_id: str) -> Dict[str, Any]:
        """Permanently delete a user from Entra ID (app-only). IRREVERSIBLE."""
        return await self._app_request("DELETE", f"/users/{user_id}")

    async def list_groups_app(self, search: Optional[str] = None, top: int = 50) -> List[Dict[str, Any]]:
        """List groups in the directory (app-only)."""
        try:
            params: Dict[str, Any] = {"$top": top, "$select": "id,displayName,description,mail"}
            if search:
                params["$search"] = f'"displayName:{search}"'
            # $search requires ConsistencyLevel header
            from app.services.connectors.graph_client import GraphClient
            gc = GraphClient()
            auth = await gc._auth_header()
            headers = {"Authorization": auth, "Content-Type": "application/json", "ConsistencyLevel": "eventual"}
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get("https://graph.microsoft.com/v1.0/groups", headers=headers, params=params)
                if r.status_code >= 400:
                    return []
                return r.json().get("value", [])
        except Exception as e:
            logger.warning("list_groups_app failed: %s", e)
            return []

    async def list_subscribed_skus(self) -> List[Dict[str, Any]]:
        """List subscribed SKUs (available licenses) in the tenant (app-only)."""
        try:
            result = await self._app_request("GET", "/subscribedSkus")
            return result.get("value", [])
        except Exception as e:
            logger.warning("list_subscribed_skus failed: %s", e)
            return []

    async def schedule_meeting(
        self,
        access_token: str,
        subject: str,
        start: str,
        end: str,
        attendees: List[str],
        body: str = "",
        is_online_meeting: bool = True,
    ) -> Dict[str, Any]:
        """Schedule a meeting (delegated token)."""
        event = {
            "subject": subject,
            "start": {"dateTime": start, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"},
            "body": {"contentType": "HTML", "content": body},
            "attendees": [
                {"emailAddress": {"address": a}, "type": "required"}
                for a in attendees
            ],
            "isOnlineMeeting": is_online_meeting,
            "onlineMeetingProvider": "teamsForBusiness" if is_online_meeting else None,
        }
        if not is_online_meeting:
            del event["onlineMeetingProvider"]
        return await self._request("POST", "/me/events", access_token, data=event)

    async def create_task(
        self,
        access_token: str,
        title: str,
        due_date: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a To Do task (delegated token)."""
        body: Dict[str, Any] = {"title": title, "status": "notStarted"}
        if due_date:
            body["dueDateTime"] = {"dateTime": due_date, "timeZone": "UTC"}
        if notes:
            body["body"] = {"content": notes, "contentType": "text"}
        try:
            # Get or create default task list
            lists_resp = await self._request("GET", "/me/todo/lists", access_token)
            lists = lists_resp.get("value", [])
            list_id = lists[0]["id"] if lists else None
            if not list_id:
                new_list = await self._request("POST", "/me/todo/lists", access_token, data={"displayName": "Onboarding Tasks"})
                list_id = new_list["id"]
            return await self._request("POST", f"/me/todo/lists/{list_id}/tasks", access_token, data=body)
        except Exception as e:
            logger.warning("create_task fallback: %s", e)
            return {"success": False, "error": str(e)}

    # ==================== SharePoint Operations ====================

    async def get_sharepoint_sites(self, access_token: str) -> Dict[str, Any]:
        """Get SharePoint sites."""
        return await self._request("GET", "/sites?search=*", access_token)

    async def get_drive_items(
        self,
        access_token: str,
        site_id: str,
        drive_id: str,
        folder_path: str = "root",
    ) -> Dict[str, Any]:
        """Get items from SharePoint drive."""
        endpoint = f"/sites/{site_id}/drives/{drive_id}/root/children"
        if folder_path != "root":
            endpoint = f"/sites/{site_id}/drives/{drive_id}/root:/{folder_path}:/children"

        return await self._request("GET", endpoint, access_token)

    async def download_file(
        self,
        access_token: str,
        site_id: str,
        drive_id: str,
        item_id: str,
    ) -> bytes:
        """Download a file from SharePoint."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/sites/{site_id}/drives/{drive_id}"
                f"/items/{item_id}/content",
                headers={"Authorization": f"Bearer {access_token}"},
                follow_redirects=True,
            )
            response.raise_for_status()
            return response.content

    # ==================== App-only User Productivity Methods ====================
    # Use AZURE_CLIENT_ID (enterprise app) with /users/{email}/... endpoints.
    # These replace delegated /me/... calls — no OBO token needed.

    # ── Phase 5 (CR-2): OBO routing ──────────────────────────────────────
    # Per-category delegated scope sets. When USE_OBO_FOR_GRAPH is on and
    # the caller passes a user_assertion, _user_request acquires a
    # delegated token with these scopes via msal.acquire_token_on_behalf_of
    # so the action is attributed to the real user in Microsoft 365 audit
    # logs. Otherwise it transparently falls back to the app-only token,
    # preserving the existing behaviour.
    _MAIL_SCOPES = ["https://graph.microsoft.com/Mail.ReadWrite",
                    "https://graph.microsoft.com/Mail.Send"]
    _CALENDAR_SCOPES = ["https://graph.microsoft.com/Calendars.ReadWrite",
                        "https://graph.microsoft.com/Schedule.Read"]
    _TASKS_SCOPES = ["https://graph.microsoft.com/Tasks.ReadWrite",
                     "https://graph.microsoft.com/Group.ReadWrite.All"]

    async def _auth_header_for(
        self,
        user_assertion: Optional[str],
        scopes: Optional[List[str]],
    ) -> str:
        """Return an Authorization header value (OBO or app-only)."""
        from app.services.obo_service import (
            get_graph_token_app_only,
            get_graph_token_obo,
        )
        token: Optional[str] = None
        if user_assertion:
            token = await get_graph_token_obo(
                user_assertion=user_assertion, scopes=scopes,
            )
        if not token:
            token = await get_graph_token_app_only()
        if not token:
            raise RuntimeError("Microsoft Graph token unavailable")
        return f"Bearer {token}"

    async def _user_request(
        self,
        method: str,
        endpoint: str,
        *,
        user_assertion: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Graph request that honours the OBO flag + user assertion.

        Falls back to ``_app_request`` semantics when no assertion is
        present or the flag is off — return shape matches ``_app_request``
        exactly (``{"success": True}`` for 202/204).
        """
        auth = await self._auth_header_for(user_assertion, scopes)
        url = f"https://graph.microsoft.com/v1.0{endpoint}"
        headers = {"Authorization": auth, "Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(
                method, url, headers=headers, json=data, params=params,
            )
            if r.status_code >= 400:
                logger.error(
                    "Graph %s %s → %s: %s",
                    method, endpoint, r.status_code, r.text[:300],
                )
                r.raise_for_status()
            if r.status_code in (202, 204) or not r.content:
                return {"success": True}
            return r.json()

    async def get_emails_for_user(
        self,
        user_email: str,
        folder: str = "inbox",
        top: int = 10,
        filter_query: Optional[str] = None,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get emails for a user (OBO when available, else app-only)."""
        params: Dict[str, Any] = {
            "$top": top,
            "$orderby": "receivedDateTime desc",
        }
        if filter_query:
            params["$filter"] = filter_query
        return await self._user_request(
            "GET",
            f"/users/{user_email}/mailFolders/{folder}/messages",
            params=params,
            user_assertion=user_assertion,
            scopes=self._MAIL_SCOPES,
        )

    async def send_email_for_user(
        self,
        user_email: str,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        is_html: bool = True,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send email as a user (OBO when available, else app-only)."""
        message: Dict[str, Any] = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML" if is_html else "Text",
                    "content": body,
                },
                "toRecipients": [
                    {"emailAddress": {"address": e}} for e in to
                ],
            }
        }
        if cc:
            message["message"]["ccRecipients"] = [
                {"emailAddress": {"address": e}} for e in cc
            ]
        if bcc:
            message["message"]["bccRecipients"] = [
                {"emailAddress": {"address": e}} for e in bcc
            ]
        return await self._user_request(
            "POST", f"/users/{user_email}/sendMail", data=message,
            user_assertion=user_assertion, scopes=self._MAIL_SCOPES,
        )

    async def create_draft_for_user(
        self,
        user_email: str,
        to: List[str],
        subject: str,
        body: str,
        is_html: bool = True,
        cc: Optional[List[str]] = None,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save a draft email for a user (OBO when available, else app-only)."""
        message: Dict[str, Any] = {
            "subject": subject,
            "body": {
                "contentType": "HTML" if is_html else "Text",
                "content": body,
            },
            "toRecipients": [
                {"emailAddress": {"address": e}} for e in to
            ],
        }
        if cc:
            message["ccRecipients"] = [
                {"emailAddress": {"address": e}} for e in cc
            ]
        return await self._user_request(
            "POST", f"/users/{user_email}/messages", data=message,
            user_assertion=user_assertion, scopes=self._MAIL_SCOPES,
        )

    async def send_draft_for_user(
        self,
        user_email: str,
        draft_id: str,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send an existing draft email by its message ID (OBO when available, else app-only).
        Calls POST /users/{user}/messages/{id}/send — Graph returns 202 No Content."""
        from urllib.parse import quote
        safe_id = quote(draft_id, safe="")
        return await self._user_request(
            "POST",
            f"/users/{user_email}/messages/{safe_id}/send",
            user_assertion=user_assertion, scopes=self._MAIL_SCOPES,
        )

    async def get_calendar_events_for_user(
        self,
        user_email: str,
        start_dt: datetime,
        end_dt: datetime,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get calendar events for a user (OBO when available, else app-only)."""
        params = {
            "startDateTime": start_dt.isoformat() + "Z",
            "endDateTime": end_dt.isoformat() + "Z",
            "$orderby": "start/dateTime",
        }
        return await self._user_request(
            "GET", f"/users/{user_email}/calendarView", params=params,
            user_assertion=user_assertion, scopes=self._CALENDAR_SCOPES,
        )

    async def create_event_for_user(
        self,
        user_email: str,
        subject: str,
        start: datetime,
        end: datetime,
        attendees: Optional[List[str]] = None,
        body: Optional[str] = None,
        location: Optional[str] = None,
        is_online_meeting: bool = False,
        timezone: str = "UTC",
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a calendar event for a user (OBO when available, else app-only)."""
        event: Dict[str, Any] = {
            "subject": subject,
            "start": {"dateTime": start.isoformat(), "timeZone": timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone},
        }
        if body:
            event["body"] = {"contentType": "HTML", "content": body}
        if location:
            event["location"] = {"displayName": location}
        if attendees:
            event["attendees"] = [
                {"emailAddress": {"address": e}, "type": "required"}
                for e in attendees
            ]
        if is_online_meeting:
            event["isOnlineMeeting"] = True
            event["onlineMeetingProvider"] = "teamsForBusiness"
        return await self._user_request(
            "POST", f"/users/{user_email}/events", data=event,
            user_assertion=user_assertion, scopes=self._CALENDAR_SCOPES,
        )

    async def get_free_busy_for_user(
        self,
        user_email: str,
        schedules: List[str],
        start: datetime,
        end: datetime,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get free/busy schedule (OBO when available, else app-only)."""
        data = {
            "schedules": schedules,
            "startTime": {
                "dateTime": start.isoformat(),
                "timeZone": "UTC",
            },
            "endTime": {
                "dateTime": end.isoformat(),
                "timeZone": "UTC",
            },
            "availabilityViewInterval": 30,
        }
        return await self._user_request(
            "POST",
            f"/users/{user_email}/calendar/getSchedule",
            data=data,
            user_assertion=user_assertion, scopes=self._CALENDAR_SCOPES,
        )

    async def get_planner_tasks_for_user(
        self,
        plan_id: Optional[str] = None,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get Planner tasks (OBO when available, else app-only).
        Requires a plan_id — app-only tokens cannot call /me/planner/tasks.
        """
        if plan_id:
            return await self._user_request(
                "GET", f"/planner/plans/{plan_id}/tasks",
                user_assertion=user_assertion, scopes=self._TASKS_SCOPES,
            )
        return {
            "value": [],
            "note": (
                "No plan_id provided. Set GRAPH_DEFAULT_PLANNER_PLAN_ID "
                "or ask the user to specify a plan."
            ),
        }

    async def create_planner_task_for_user(
        self,
        plan_id: str,
        title: str,
        bucket_id: str = "",
        due_date: Optional[datetime] = None,
        assigned_to: Optional[str] = None,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a Planner task (OBO when available, else app-only)."""
        task: Dict[str, Any] = {"planId": plan_id, "title": title}
        if bucket_id:
            task["bucketId"] = bucket_id
        if due_date:
            task["dueDateTime"] = due_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        if assigned_to:
            task["assignments"] = {
                assigned_to: {
                    "@odata.type": "#microsoft.graph.plannerAssignment",
                    "orderHint": " !",
                }
            }
        return await self._user_request(
            "POST", "/planner/tasks", data=task,
            user_assertion=user_assertion, scopes=self._TASKS_SCOPES,
        )

    # ── Rich email operations (app-only) ──────────────────────────────────────

    _EMAIL_SELECT = (
        "id,conversationId,subject,from,toRecipients,ccRecipients,"
        "bodyPreview,receivedDateTime,isRead,hasAttachments,importance,flag"
    )
    _EMAIL_SELECT_FULL = _EMAIL_SELECT + ",body"

    async def search_emails_for_user(
        self,
        user_email: str,
        query: str = "",
        from_address: str = "",
        from_name: str = "",
        subject_contains: str = "",
        is_important: bool = False,
        is_unread: Optional[bool] = None,
        is_flagged: Optional[bool] = None,
        has_attachments: Optional[bool] = None,
        date_from: str = "",
        date_to: str = "",
        folder: str = "",
        top: int = 20,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Multi-criteria email search via $search (KQL) and/or $filter.

        Supports: free-text, sender address/name, subject, date range,
        importance, unread status, flagged status, and attachments.

        Strategy:
        - Text queries use $search (KQL) with ConsistencyLevel: eventual.
        - Structured-only criteria use $filter + $orderby.
        - $search and $filter cannot be combined on /messages — all
          criteria are expressed in whichever path is chosen.
        - is_flagged always requires $filter (flag/flagStatus not KQL).
          When combined with text search, post-filter in Python.
        """
        from app.services.connectors.graph_client import GraphClient
        gc = GraphClient()
        auth = await self._auth_header_for(user_assertion, self._MAIL_SCOPES)
        _ = gc  # retained for backwards-compat import side-effects

        kql_parts: list[str] = []
        filter_parts: list[str] = []
        # Post-filters applied in Python (when mixing KQL + non-KQL criteria)
        post_filter_flagged = False

        # ── Build KQL criteria ────────────────────────────────────────────────
        if from_address:
            safe_addr = from_address.replace('"', "'")
            kql_parts.append(f"from:{safe_addr}")
        if from_name:
            safe_name = from_name.replace('"', "'")
            kql_parts.append(f'from:"{safe_name}"')
        if query:
            safe_q = query.replace('"', "'")
            kql_parts.append(f'"{safe_q}"')
        if subject_contains:
            safe_s = subject_contains.replace('"', "'")
            kql_parts.append(f'subject:"{safe_s}"')

        if kql_parts:
            # KQL path — express all supported criteria in KQL to avoid
            # mixing $search and $filter (which Graph rejects on /messages).
            if date_from:
                kql_parts.append(f"received>={date_from}")
            if date_to:
                kql_parts.append(f"received<={date_to}")
            if is_important:
                kql_parts.append("importance:high")
            if is_unread is True:
                kql_parts.append("isread:false")
            elif is_unread is False:
                kql_parts.append("isread:true")
            if has_attachments is True:
                kql_parts.append("hasAttachments:true")
            elif has_attachments is False:
                kql_parts.append("hasAttachments:false")
            # flag/flagStatus is not a KQL property — post-filter in Python
            if is_flagged is True:
                post_filter_flagged = True
        else:
            # Pure $filter path — no KQL criteria, $filter + $orderby fine.
            if is_important:
                filter_parts.append("importance eq 'high'")
            if is_unread is True:
                filter_parts.append("isRead eq false")
            elif is_unread is False:
                filter_parts.append("isRead eq true")
            if is_flagged is True:
                filter_parts.append("flag/flagStatus eq 'flagged'")
            if has_attachments is not None:
                val = "true" if has_attachments else "false"
                filter_parts.append(f"hasAttachments eq {val}")
            if date_from:
                filter_parts.append(f"receivedDateTime ge {date_from}T00:00:00Z")
            if date_to:
                filter_parts.append(f"receivedDateTime le {date_to}T23:59:59Z")

        # Endpoint: folder-scoped or cross-folder
        if folder:
            path = f"/users/{user_email}/mailFolders/{folder}/messages"
        else:
            path = f"/users/{user_email}/messages"

        params: Dict[str, Any] = {
            "$top": min(top, 50),
            "$select": self._EMAIL_SELECT,
        }
        headers_extra: Dict[str, str] = {
            "Authorization": auth,
            "Content-Type": "application/json",
        }

        if kql_parts:
            params["$search"] = " ".join(kql_parts)
            headers_extra["ConsistencyLevel"] = "eventual"
            params["$count"] = "true"
            # $orderby must NOT be combined with $search
        else:
            params["$orderby"] = "receivedDateTime desc"
            if filter_parts:
                params["$filter"] = " and ".join(filter_parts)

        url = f"https://graph.microsoft.com/v1.0{path}"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url, headers=headers_extra, params=params)
            if r.status_code >= 400:
                logger.error(
                    "search_emails error %s %s → %s: %s",
                    "GET", path, r.status_code, r.text[:400],
                )
                r.raise_for_status()
        data = r.json()
        # Post-filter: flagged status can't be expressed in KQL, so we filter here
        if post_filter_flagged and data.get("value"):
            data["value"] = [
                m for m in data["value"]
                if (m.get("flag") or {}).get("flagStatus") == "flagged"
            ]
        return data

    async def get_email_by_id_for_user(
        self,
        user_email: str,
        message_id: str,
        include_body: bool = True,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch a single email message by ID, including the full body.
        Required application permission: Mail.Read
        """
        select = self._EMAIL_SELECT_FULL if include_body else self._EMAIL_SELECT
        return await self._user_request(
            "GET",
            f"/users/{user_email}/messages/{message_id}",
            params={"$select": select},
            user_assertion=user_assertion, scopes=self._MAIL_SCOPES,
        )

    async def get_email_thread_for_user(
        self,
        user_email: str,
        message_id: str,
        top: int = 20,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retrieve all messages in a conversation thread.
        First resolves the conversationId from the given message, then
        fetches all messages sharing that conversationId (across folders).
        """
        # Step 1: get the message to extract conversationId
        msg = await self._user_request(
            "GET",
            f"/users/{user_email}/messages/{message_id}",
            params={"$select": "id,conversationId,subject,receivedDateTime"},
            user_assertion=user_assertion, scopes=self._MAIL_SCOPES,
        )
        conv_id = msg.get("conversationId")
        if not conv_id:
            return {"value": [msg], "error": "conversationId not found"}

        # Step 2: fetch all messages with that conversationId
        safe_id = conv_id.replace("'", "''")
        return await self._user_request(
            "GET",
            f"/users/{user_email}/messages",
            params={
                "$filter": f"conversationId eq '{safe_id}'",
                "$select": self._EMAIL_SELECT_FULL,
                "$orderby": "receivedDateTime asc",
                "$top": min(top, 50),
            },
            user_assertion=user_assertion, scopes=self._MAIL_SCOPES,
        )

    async def reply_to_email_for_user(
        self,
        user_email: str,
        message_id: str,
        body: str,
        reply_all: bool = False,
        cc: Optional[List[str]] = None,
        is_html: bool = True,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a reply (or reply-all) to a message using the app-only token.
        Uses the Graph /reply action which maintains In-Reply-To threading.
        Required application permission: Mail.Send
        """
        action = "replyAll" if reply_all else "reply"
        payload: Dict[str, Any] = {
            "message": {
                "body": {
                    "contentType": "HTML" if is_html else "Text",
                    "content": body,
                }
            },
            "comment": "",
        }
        if cc:
            payload["message"]["ccRecipients"] = [
                {"emailAddress": {"address": e}} for e in cc
            ]
        return await self._user_request(
            "POST",
            f"/users/{user_email}/messages/{message_id}/{action}",
            data=payload,
            user_assertion=user_assertion, scopes=self._MAIL_SCOPES,
        )

    async def create_draft_reply_for_user(
        self,
        user_email: str,
        message_id: str,
        body: str,
        reply_all: bool = False,
        is_html: bool = True,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a draft reply without sending — saves to Drafts folder.
        Returns the draft message (includes id for later send/edit).
        """
        action = "createReplyAll" if reply_all else "createReply"
        payload = {
            "message": {
                "body": {
                    "contentType": "HTML" if is_html else "Text",
                    "content": body,
                }
            }
        }
        return await self._user_request(
            "POST",
            f"/users/{user_email}/messages/{message_id}/{action}",
            data=payload,
            user_assertion=user_assertion, scopes=self._MAIL_SCOPES,
        )

    async def mark_email_read_for_user(
        self,
        user_email: str,
        message_id: str,
        is_read: bool = True,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark a message read or unread."""
        return await self._user_request(
            "PATCH",
            f"/users/{user_email}/messages/{message_id}",
            data={"isRead": is_read},
            user_assertion=user_assertion, scopes=self._MAIL_SCOPES,
        )

    async def create_todo_task_for_user(
        self,
        user_email: str,
        title: str,
        due_date: Optional[str] = None,
        notes: Optional[str] = None,
        user_assertion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a To Do task for a user (OBO when available, else app-only)."""
        body: Dict[str, Any] = {"title": title, "status": "notStarted"}
        if due_date:
            body["dueDateTime"] = {"dateTime": due_date, "timeZone": "UTC"}
        if notes:
            body["body"] = {"content": notes, "contentType": "text"}
        try:
            lists_resp = await self._user_request(
                "GET", f"/users/{user_email}/todo/lists",
                user_assertion=user_assertion, scopes=self._TASKS_SCOPES,
            )
            lists = lists_resp.get("value", [])
            list_id = lists[0]["id"] if lists else None
            if not list_id:
                new_list = await self._user_request(
                    "POST",
                    f"/users/{user_email}/todo/lists",
                    data={"displayName": "Tasks"},
                    user_assertion=user_assertion, scopes=self._TASKS_SCOPES,
                )
                list_id = new_list["id"]
            return await self._user_request(
                "POST",
                f"/users/{user_email}/todo/lists/{list_id}/tasks",
                data=body,
                user_assertion=user_assertion, scopes=self._TASKS_SCOPES,
            )
        except Exception as e:
            logger.warning("create_todo_task_for_user failed: %s", e)
            return {"success": False, "error": str(e)}


# Singleton instance - initialized lazily to avoid import failures
try:
    graph_service = GraphAPIService()
except Exception as e:
    logger.warning(f"Failed to initialize GraphAPIService: {e}")
    graph_service = None
