"""Submit an already written Markdown Article; never print API tokens or payloads."""
import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", default=os.environ.get("AUTOLOG_URL", "http://127.0.0.1:3000"))
parser.add_argument("--token-file", type=Path, help="Protected file containing API_TOKEN; otherwise use process environment")
parser.add_argument("--payload", type=Path, default=Path("docs/examples/nfs-article.json"))
parser.add_argument("--upsert", action="store_true")
args = parser.parse_args()
token = args.token_file.read_text().strip() if args.token_file else os.environ.get("API_TOKEN")
if not token:
    parser.error("Supply --token-file or a secure API_TOKEN environment binding")
try:
    payload = json.loads(args.payload.read_text())
except (OSError, ValueError):
    parser.error("Cannot read a valid JSON payload")
request = Request(args.url.rstrip("/") + ("/api/articles/upsert" if args.upsert else "/api/articles"),
                  method="POST", data=json.dumps(payload, ensure_ascii=False).encode(),
                  headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
try:
    with urlopen(request, timeout=30) as response:
        article = json.load(response)
        print(f"HTTP {response.status}; article id: {article['id']}; slug: {article['slug']}")
except HTTPError as error:
    raise SystemExit(f"API returned HTTP {error.code}. Check authentication, validation and update version; payload omitted.") from None
except URLError:
    raise SystemExit("Cannot reach AutoLog; check URL, TLS and network access.") from None
