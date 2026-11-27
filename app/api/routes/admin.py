"""A minimal server-rendered review table (brief section 7: "a simple admin table", no frontend build)."""
from html import escape
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.api.deps import SessionDep, TenantDep
from app.api.schemas import ReviewState
from app.db.models import Image, Post
from app.repositories import suggestions as suggestions_repo

router = APIRouter(prefix="/admin", tags=["review"])

STYLE = """body{font:14px system-ui,sans-serif;margin:24px;color:#1a1a1a}table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}th{background:#f4f4f4}
.ok{color:#0a6b2d;font-weight:600}.no{color:#a3121b;font-weight:600}code{font-size:12px}"""


@router.get("/review", response_class=HTMLResponse,
            summary="Read-only HTML table of suggestions (decide via POST /suggestions/{id}/approve|reject)")
def review_table(session: SessionDep, tenant_id: TenantDep,
                 review_state: Annotated[ReviewState | None, Query(alias="review")] = None,
                 limit: Annotated[int, Query(ge=1, le=500)] = 200) -> HTMLResponse:
    rows, total = suggestions_repo.list_(session, tenant_id, review=review_state, limit=limit)
    body = []
    for s, r in rows:
        post, image = session.get(Post, s.post_id), session.get(Image, s.image_id)
        verdict = f'<span class="{"ok" if s.verdict == "accepted" else "no"}">{escape(s.verdict.upper())}</span>'
        body.append(
            "<tr>"
            f"<td><code>{escape(post.slug)}</code><br>expects {escape(post.subject_key or '?')}</td>"
            f"<td><code>{escape(image.file_path.rsplit('/', 1)[-1])}</code><br>{escape(image.subject or '?')}</td>"
            f"<td>{s.rank}</td><td>{s.similarity:.3f}</td><td>{verdict}</td>"
            f"<td>{'<br>'.join(escape(x) for x in s.reasons) or '&mdash;'}</td>"
            f"<td>{escape(r.decision) if r else 'pending'}</td>"
            f"<td><code>{s.id}</code></td>"
            "</tr>"
        )
    html = (f"<!doctype html><html><head><meta charset='utf-8'><title>Review queue</title><style>{STYLE}</style>"
            f"</head><body><h1>Review queue: {escape(tenant_id)}</h1><p>{total} suggestion(s). Inspect one with "
            "<code>GET /suggestions/{id}/explain</code>; decide with <code>POST /suggestions/{id}/approve</code> "
            "or <code>/reject</code>.</p><table><tr><th>Post</th><th>Image</th><th>Rank</th><th>Similarity</th>"
            "<th>Guard</th><th>Reasons</th><th>Human review</th><th>Suggestion id</th></tr>"
            f"{''.join(body)}</table></body></html>")
    return HTMLResponse(html)
