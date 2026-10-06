"""Write initial deployment secrets to a NEW protected directory, never to YAML.

Run interactively, then use kubectl --from-file commands from README.
Existing database credentials must be reused on subsequent deployments.
"""
import argparse
import getpass
import secrets
from pathlib import Path
from urllib.parse import quote

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
args = parser.parse_args()
if args.directory.exists():
    parser.error("Use a new directory to avoid overwriting existing credentials")
username = input("App username: ").strip()
if not username or ":" in username:
    parser.error("Username must be nonempty and contain no colon")
db_password = getpass.getpass("PostgreSQL password (16+ characters): ")
app_password = getpass.getpass("App password (16+ characters): ")
if min(len(db_password), len(app_password)) < 16:
    parser.error("Both passwords must contain at least 16 characters")
key = getpass.getpass("OpenAI key (optional; leave empty for external AI + web entry): ")
values = {
    "POSTGRES_PASSWORD": db_password,
    "DATABASE_URL": f"postgresql+psycopg://lifelog:{quote(db_password, safe='')}@lifelog-postgres:5432/lifelog",
    "APP_USERNAME": username,
    "APP_PASSWORD": app_password,
    "API_TOKEN": secrets.token_urlsafe(48),
}
if key:
    values["OPENAI_API_KEY"] = key
args.directory.mkdir(mode=0o700, parents=True)
for name, value in values.items():
    path = args.directory / name
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
print("Protected secret files created. Use kubectl --from-file; do not commit or print these files.")
