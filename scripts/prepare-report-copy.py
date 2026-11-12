"""Prepare ONE explicit article-to-report copy offline. Never contacts or alters a database.

The article export is input data. No historical discovery, publication or deletion occurs.
Review the output, then POST it only to an explicitly approved target.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.report_schemas import ReportCreate


def prepare(article, report_id, project, component, author, source, status):
    return ReportCreate(
        report_id=report_id, title=article["title"], project=project, component=component,
        author=author, source=source, status=status, summary=article["summary"],
        environment=f"Copied from article {article['id']}; original created_at={article['created_at']}, updated_at={article['updated_at']}. Structured measurements and sections have not been extracted; consult the exact preserved Markdown.",
        raw_markdown=article["content_markdown"], related_article_ids=[article["id"]],
    ).model_dump(mode="json", exclude_none=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("article_json", type=Path)
    for name in ["report-id", "project", "component", "author", "source", "status"]:
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    article = json.loads(args.article_json.read_text())
    output = prepare(article, args.report_id, args.project, args.component, args.author, args.source, args.status)
    # Exclusive creation: never accidentally replace another prepared copy.
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"report_id": args.report_id, "markdown_sha256": hashlib.sha256(output["raw_markdown"].encode()).hexdigest(), "saved": True}))
