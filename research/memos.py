"""Memos and the project timeline."""

from __future__ import annotations

from .models import Memo


def _clean_body(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise ValueError("A memo needs a body")
    return body


def _validate_targets(project, result_set, code, page) -> None:
    targets = [result_set, code, page]
    if sum(target is not None for target in targets) > 1:
        raise ValueError("A memo can target only one thing")
    if result_set is not None and result_set.project_id != project.id:
        raise ValueError("The set belongs to another project")
    if code is not None and code.project_id != project.id:
        raise ValueError("The code belongs to another project")


def create_memo(
    project, *, author, body, result_set=None, code=None, page=None
) -> Memo:
    _validate_targets(project, result_set, code, page)
    return Memo.objects.create(
        project=project,
        author=author,
        body=_clean_body(body),
        result_set=result_set,
        code=code,
        page=page,
    )


def update_memo(memo: Memo, *, body: str) -> Memo:
    memo.body = _clean_body(body)
    memo.save(update_fields=["body", "modified"])
    return memo


def delete_memo(memo: Memo) -> None:
    memo.delete()


def project_timeline(project, *, limit: int | None = None) -> list[dict]:
    memos = project.memos.select_related(
        "author", "result_set", "code", "page"
    ).order_by("-created")
    events = project.events.select_related("actor", "result_set").order_by("-created")
    if limit is not None:
        memos = memos[:limit]
        events = events[:limit]

    entries = [
        {"kind": "memo", "created": memo.created, "memo": memo} for memo in memos
    ]
    entries.extend(
        {"kind": "event", "created": event.created, "event": event} for event in events
    )
    entries.sort(key=lambda entry: entry["created"], reverse=True)
    return entries[:limit] if limit is not None else entries
