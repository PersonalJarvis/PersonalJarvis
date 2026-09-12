"""Allowlisted REST operations for the bundled service connectors.

Endpoints are provider-owned constants. Model input can fill identifiers and
documented parameters, but can never choose a host, method or credential header.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Operation:
    name: str
    method: str
    path: str
    description: str
    query: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    body: str = "none"


GRAPH = "https://graph.microsoft.com/v1.0"
BASES = {
    "outlook": GRAPH,
    "onedrive": GRAPH,
    "teams": GRAPH,
    "sharepoint": GRAPH,
    "onenote": GRAPH,
    "microsoft_todo": GRAPH,
    "azure": "https://management.azure.com",
    "google_cloud": "https://cloudresourcemanager.googleapis.com",
    "gitlab": "https://gitlab.com/api/v4",
    "x": "https://api.x.com/2",
    "linkedin": "https://api.linkedin.com",
    "meta": "https://graph.facebook.com/v23.0",
    "youtube_studio": "https://www.googleapis.com/youtube/v3",
    "hubspot": "https://api.hubapi.com",
    "figma": "https://api.figma.com/v1",
    "zoom": "https://api.zoom.us/v2",
}

op = Operation
OPERATIONS: dict[str, tuple[Operation, ...]] = {
    "outlook": (
        op(
            "list_messages",
            "GET",
            "/me/messages",
            "List or search Outlook mail.",
            ("$top", "$skip", "$search", "$filter", "$select"),
            {"$top": 20},
        ),
        op("read_message", "GET", "/me/messages/{message_id}", "Read an Outlook message by ID."),
        op(
            "send_mail",
            "POST",
            "/me/sendMail",
            (
                "Send mail. Body: "
                '{"message":{"subject":"...","body":{"contentType":"Text","content":"..."},"toRecipients":[{"emailAddress":{"address":"..."}}]},"saveToSentItems":true}.'
            ),
            body="json",
        ),
        op(
            "reply_mail",
            "POST",
            "/me/messages/{message_id}/reply",
            'Reply to an Outlook message. Body: {"comment":"..."}.',
            body="json",
        ),
        op(
            "list_events",
            "GET",
            "/me/calendarView",
            "List calendar events between startDateTime and endDateTime (ISO 8601 with timezone).",
            ("startDateTime", "endDateTime", "$top", "$skip"),
        ),
        op(
            "create_event",
            "POST",
            "/me/events",
            (
                "Create an event. Body requires subject, start/end objects with dateTime "
                "and timeZone; optional attendees."
            ),
            body="json",
        ),
        op(
            "update_event",
            "PATCH",
            "/me/events/{event_id}",
            "Update an existing calendar event using Graph event fields.",
            body="json",
        ),
    ),
    "onedrive": (
        op(
            "list_files",
            "GET",
            "/me/drive/root/children",
            "List files in the OneDrive root folder.",
            ("$top", "$skipToken", "$select"),
        ),
        op(
            "list_folder_files",
            "GET",
            "/me/drive/items/{item_id}/children",
            "List the contents of a OneDrive folder by item ID.",
            ("$top", "$skipToken", "$select"),
        ),
        op(
            "search_files",
            "GET",
            "/me/drive/root/search(q='{query}')",
            "Search OneDrive filenames and indexed content.",
        ),
        op(
            "read_file_metadata",
            "GET",
            "/me/drive/items/{item_id}",
            "Read metadata and provider download link for a file.",
        ),
        op(
            "upload_file",
            "PUT",
            "/me/drive/items/{parent_id}:/{filename}:/content",
            (
                "Upload base64 file content to a named OneDrive folder. Existing "
                "same-name files are replaced."
            ),
            body="binary",
        ),
        op(
            "share_file",
            "POST",
            "/me/drive/items/{item_id}/createLink",
            (
                'Create a sharing link. Body: {"type":"view","scope":"organization"}; '
                "anonymous links broaden access."
            ),
            body="json",
        ),
    ),
    "teams": (
        op(
            "list_chats",
            "GET",
            "/me/chats",
            "List Microsoft Teams chats (work or school accounts).",
            ("$top", "$skiptoken"),
        ),
        op(
            "list_messages",
            "GET",
            "/chats/{chat_id}/messages",
            "Read messages in a Teams chat.",
            ("$top", "$skiptoken"),
        ),
        op(
            "send_message",
            "POST",
            "/chats/{chat_id}/messages",
            'Send a Teams chat message. Body: {"body":{"contentType":"text","content":"..."}}.',
            body="json",
        ),
        op(
            "create_meeting",
            "POST",
            "/me/onlineMeetings",
            (
                "Create a Teams meeting and return its join URL. Body: subject, "
                "startDateTime, endDateTime. This does not ring participants."
            ),
            body="json",
        ),
    ),
    "sharepoint": (
        op(
            "search_sites",
            "GET",
            "/sites",
            "Search SharePoint sites by search parameter.",
            ("search",),
        ),
        op("list_libraries", "GET", "/sites/{site_id}/drives", "List a site's document libraries."),
        op(
            "search_documents",
            "GET",
            "/drives/{drive_id}/root/search(q='{query}')",
            "Search a SharePoint document library.",
        ),
        op("list_lists", "GET", "/sites/{site_id}/lists", "List SharePoint lists."),
        op(
            "list_items",
            "GET",
            "/sites/{site_id}/lists/{list_id}/items",
            "Read list items and fields.",
            ("$top", "$expand", "$filter"),
            {"$expand": "fields"},
        ),
    ),
    "onenote": (
        op("list_notebooks", "GET", "/me/onenote/notebooks", "List OneNote notebooks."),
        op(
            "list_sections",
            "GET",
            "/me/onenote/notebooks/{notebook_id}/sections",
            "List notebook sections.",
        ),
        op(
            "list_pages",
            "GET",
            "/me/onenote/sections/{section_id}/pages",
            "List pages in a section.",
            ("$top", "$skip"),
        ),
        op(
            "read_page",
            "GET",
            "/me/onenote/pages/{page_id}/content",
            "Read a OneNote page as HTML.",
        ),
        op(
            "create_page",
            "POST",
            "/me/onenote/sections/{section_id}/pages",
            "Create a OneNote page from HTML containing a title and body.",
            body="html",
        ),
        op(
            "update_page",
            "PATCH",
            "/me/onenote/pages/{page_id}/content",
            "Apply Graph OneNote patch commands: an array of target/action/content objects.",
            body="array",
        ),
    ),
    "microsoft_todo": (
        op("list_lists", "GET", "/me/todo/lists", "List Microsoft To Do task lists."),
        op(
            "list_tasks",
            "GET",
            "/me/todo/lists/{list_id}/tasks",
            "Read tasks.",
            ("$top", "$skip", "$filter"),
        ),
        op(
            "create_task",
            "POST",
            "/me/todo/lists/{list_id}/tasks",
            "Create a task. Body requires title; optional dueDateTime, importance, body.",
            body="json",
        ),
        op(
            "update_task",
            "PATCH",
            "/me/todo/lists/{list_id}/tasks/{task_id}",
            'Update a task; set status="completed" to complete it.',
            body="json",
        ),
    ),
    "azure": (
        op(
            "list_subscriptions",
            "GET",
            "/subscriptions",
            "List accessible Azure subscriptions.",
            defaults={"api-version": "2022-12-01"},
        ),
        op(
            "list_resources",
            "GET",
            "/subscriptions/{subscription_id}/resources",
            "List Azure resources.",
            ("$filter", "$top"),
            {"api-version": "2021-04-01"},
        ),
        op(
            "query_costs",
            "POST",
            "/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query",
            (
                "Read Azure costs. Body: "
                '{"type":"ActualCost","timeframe":"MonthToDate",'
                '"dataset":{"granularity":"Daily","aggregation":{"totalCost":'
                '{"name":"Cost","function":"Sum"}}}}. Requires billing permissions.'
            ),
            defaults={"api-version": "2023-03-01"},
            body="json",
        ),
    ),
    "google_cloud": (
        op(
            "list_projects",
            "GET",
            "/v3/projects:search",
            "Search accessible Google Cloud projects.",
            ("query", "pageSize", "pageToken"),
        ),
        op(
            "list_buckets",
            "GET",
            "https://storage.googleapis.com/storage/v1/b",
            "List Cloud Storage buckets for project.",
            ("project", "maxResults", "pageToken"),
        ),
        op(
            "list_objects",
            "GET",
            "https://storage.googleapis.com/storage/v1/b/{bucket}/o",
            "List objects in a Cloud Storage bucket.",
            ("prefix", "maxResults", "pageToken"),
        ),
        op(
            "list_billing_accounts",
            "GET",
            "https://cloudbilling.googleapis.com/v1/billingAccounts",
            "List billing accounts. This API does not return actual spend.",
            ("pageSize", "pageToken"),
        ),
        op(
            "query_billing_export",
            "POST",
            "https://bigquery.googleapis.com/bigquery/v2/projects/{project_id}/queries",
            (
                "Query a preconfigured billing export with read-only GoogleSQL SELECT. "
                "Body: query, useLegacySql=false, maximumBytesBilled. BigQuery query "
                "charges may apply."
            ),
            body="billing_query",
        ),
    ),
    "gitlab": (
        op(
            "list_projects",
            "GET",
            "/projects",
            "Search GitLab projects.",
            ("search", "page", "per_page"),
            {"membership": "true", "per_page": 20},
        ),
        op(
            "list_issues",
            "GET",
            "/projects/{project_id}/issues",
            "Read GitLab issues.",
            ("state", "search", "page", "per_page"),
        ),
        op(
            "create_issue",
            "POST",
            "/projects/{project_id}/issues",
            "Create a GitLab issue with title and description.",
            body="json",
        ),
        op(
            "list_merge_requests",
            "GET",
            "/projects/{project_id}/merge_requests",
            "List GitLab merge requests.",
            ("state", "page", "per_page"),
        ),
        op(
            "read_merge_request",
            "GET",
            "/projects/{project_id}/merge_requests/{merge_request_iid}",
            "Read a GitLab merge request.",
        ),
        op(
            "comment_merge_request",
            "POST",
            "/projects/{project_id}/merge_requests/{merge_request_iid}/notes",
            'Comment on a merge request. Body: {"body":"..."}.',
            body="json",
        ),
    ),
    "x": (
        op("read_profile", "GET", "/users/me", "Read the authenticated X profile."),
        op(
            "read_post",
            "GET",
            "/tweets/{tweet_id}",
            "Read an X post.",
            ("tweet.fields", "expansions"),
        ),
        op(
            "list_mentions",
            "GET",
            "/users/{user_id}/mentions",
            "Read mentions of an X user.",
            ("max_results", "pagination_token", "since_id"),
        ),
        op(
            "list_posts",
            "GET",
            "/users/{user_id}/tweets",
            "Read a user's X posts.",
            ("max_results", "pagination_token"),
        ),
        op(
            "create_post",
            "POST",
            "/tweets",
            'Publish an X post. Body: {"text":"..."}; optional reply.in_reply_to_tweet_id.',
            body="json",
        ),
    ),
    "linkedin": (
        op(
            "read_profile",
            "GET",
            "/v2/userinfo",
            "Read the signed-in LinkedIn profile (OpenID Connect).",
        ),
        op(
            "create_post",
            "POST",
            "/v2/ugcPosts",
            (
                "Publish a LinkedIn post. Body requires author URN, lifecycleState, "
                "specificContent with ShareContent, and visibility. Requires "
                "w_member_social. Personal inbox and contacts are not available through "
                "the general LinkedIn API."
            ),
            body="json",
        ),
    ),
    "meta": (
        op(
            "list_pages",
            "GET",
            "/me/accounts",
            "List Facebook Pages managed by this user.",
            ("fields", "after", "limit"),
            {"fields": "id,name,instagram_business_account"},
        ),
        op(
            "list_posts",
            "GET",
            "/{page_id}/posts",
            "Read a Facebook Page's posts.",
            ("fields", "after", "limit"),
        ),
        op(
            "create_post",
            "POST",
            "/{page_id}/feed",
            (
                'Publish on a Facebook Page. Body: {"message":"..."}. Requires a Page '
                "access token for that Page."
            ),
            body="json",
        ),
        op(
            "list_comments",
            "GET",
            "/{object_id}/comments",
            "Read comments on a Facebook post or Instagram media item.",
            ("fields", "after", "limit"),
        ),
        op(
            "list_instagram_media",
            "GET",
            "/{instagram_id}/media",
            "List media for an Instagram professional account.",
            ("fields", "after", "limit"),
            {"fields": "id,caption,media_type,permalink,timestamp"},
        ),
        op(
            "create_instagram_container",
            "POST",
            "/{instagram_id}/media",
            (
                "Create an Instagram publishing container. Body: image_url and caption, "
                "or documented video fields. The media URL must already be accessible to "
                "Meta."
            ),
            body="json",
        ),
        op(
            "publish_instagram_container",
            "POST",
            "/{instagram_id}/media_publish",
            'Publish a finished Instagram container. Body: {"creation_id":"..."}.',
            body="json",
        ),
    ),
    "youtube_studio": (
        op(
            "read_channel",
            "GET",
            "/channels",
            "Read your YouTube channel and audience counters.",
            defaults={"mine": "true", "part": "snippet,statistics,contentDetails"},
        ),
        op(
            "list_comments",
            "GET",
            "/commentThreads",
            "Read comments for a videoId or allThreadsRelatedToChannelId.",
            ("videoId", "allThreadsRelatedToChannelId", "pageToken", "maxResults"),
            {"part": "snippet,replies", "maxResults": 20},
        ),
        op(
            "reply_comment",
            "POST",
            "/comments",
            (
                "Reply to a YouTube comment. Body: "
                '{"snippet":{"parentId":"...","textOriginal":"..."}}.'
            ),
            defaults={"part": "snippet"},
            body="json",
        ),
        op(
            "read_analytics",
            "GET",
            "https://youtubeanalytics.googleapis.com/v2/reports",
            (
                "Read channel analytics. Supply startDate/endDate (YYYY-MM-DD), metrics, "
                "optional dimensions."
            ),
            ("startDate", "endDate", "metrics", "dimensions", "filters", "sort"),
            {"ids": "channel==MINE"},
        ),
        op(
            "upload_video",
            "POST",
            "https://www.googleapis.com/upload/youtube/v3/videos",
            (
                "Upload a local video file to your YouTube channel. Body: file_path "
                "(absolute), title, optional description and privacyStatus "
                "(private/unlisted/public; defaults private)."
            ),
            defaults={"uploadType": "resumable", "part": "snippet,status"},
            body="video",
        ),
    ),
    "hubspot": tuple(
        op(
            f"list_{kind}",
            "GET",
            f"/crm/v3/objects/{kind}",
            f"Read HubSpot {kind}.",
            ("limit", "after", "properties"),
        )
        for kind in ("contacts", "companies", "deals")
    )
    + (
        op(
            "search_contacts",
            "POST",
            "/crm/v3/objects/contacts/search",
            "Search HubSpot contacts using query, filterGroups, properties and limit.",
            body="json",
        ),
    ),
    "figma": (
        op("read_profile", "GET", "/me", "Read the authenticated Figma profile."),
        op("list_projects", "GET", "/teams/{team_id}/projects", "List projects for a Figma team."),
        op(
            "list_files",
            "GET",
            "/projects/{project_id}/files",
            "List design files in a Figma project.",
        ),
        op(
            "read_design",
            "GET",
            "/files/{file_key}",
            "Read a Figma design by file key.",
            ("depth", "ids"),
        ),
        op("list_comments", "GET", "/files/{file_key}/comments", "Read Figma file comments."),
    ),
    "zoom": (
        op(
            "list_meetings",
            "GET",
            "/users/me/meetings",
            "List scheduled Zoom meetings.",
            ("page_size", "next_page_token", "type"),
        ),
        op(
            "create_meeting",
            "POST",
            "/users/me/meetings",
            (
                "Create a Zoom meeting. Body: topic, type=1 for instant or type=2 with "
                "start_time and duration for scheduled. Open the returned join_url to "
                "join; creation does not launch the desktop app."
            ),
            body="json",
        ),
        op(
            "list_recordings",
            "GET",
            "/users/me/recordings",
            (
                "List cloud recordings and available transcript download links. Supply "
                "from/to dates. Requires cloud recording entitlement."
            ),
            ("from", "to", "page_size", "next_page_token"),
        ),
    ),
}
