"""A persistent fake gh/glab API. Never contacts a remote service."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlsplit

path = Path(os.environ["RALPH_TEST_TRACKER"])
data = json.loads(path.read_text())
args = sys.argv[1:]
github = data["tracker"] == "github"
if args[:2] == ["repo", "view"]:
    print(json.dumps({"url": "https://github.com/org/project", "web_url": "https://gitlab.com/group/project"}))
    sys.exit(0)
assert args[:2] == ["api", "--hostname"], args
parsed = urlsplit(args[3])
endpoint = unquote(parsed.path)
method = args[args.index("--method") + 1]
payload = json.load(sys.stdin) if "--input" in args else None
data.setdefault("requests", []).append(dict(endpoint=endpoint, query=parse_qs(parsed.query),
                                           host=args[2], method=method, payload=payload))


def respond(value, status=0):
    path.write_text(json.dumps(data))
    if status:
        print(f"fake API error (HTTP {status})", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(value) if value is not None else "")
    sys.exit(0)


if endpoint in data.get("errors", {}):
    respond(None, data["errors"][endpoint])
if endpoint in data.get("responses", {}):
    respond(data["responses"][endpoint])
tail = endpoint.split("/issues", 1)
if len(tail) == 1:
    if method == "GET":
        respond({"name": "ralph:blocked"})
    respond(payload)
if not tail[1]:
    params = parse_qs(parsed.query)
    labels = params.get("labels", [""])[0].split(",")
    items = []
    for issue in data["issues"].values():
        names = [x["name"] for x in issue["labels"]] if github else issue["labels"]
        if issue["state"] != "closed" and all(label in names for label in labels):
            items.append(issue)
    page = int(params.get("page", ["1"])[0])
    respond(items[(page - 1) * 100:page * 100])
parts = tail[1].strip("/").split("/")
number = parts[0]
issue = data["issues"].get(number)
if not issue:
    respond(None, 404)
if len(parts) > 1 and parts[1] in {"dependencies", "links"}:
    respond([data["issues"][n] if github else dict(data["issues"][n], link_type="is_blocked_by")
             for n in data.get("links", {}).get(number, [])])
if len(parts) > 1 and parts[1] == "labels":
    names = [x["name"] for x in issue["labels"]]
    if method == "POST":
        names = list(dict.fromkeys(names + payload["labels"]))
    elif method == "DELETE":
        names = [x for x in names if x != parts[2]]
    issue["labels"] = [{"name": n} for n in names]
    respond(issue["labels"])
if method in {"PATCH", "PUT"}:
    if data.get("fail_close_once") and (payload.get("state") == "closed" or payload.get("state_event") == "close"):
        data["fail_close_once"] = False
        respond(None, 503)
    if payload.get("state") == "closed" or payload.get("state_event") == "close":
        issue["state"] = "closed"
    for field in ("body", "description"):
        if field in payload:
            issue[field] = payload[field]
    for label in payload.get("add_labels", "").split(","):
        if label and label not in issue["labels"]:
            issue["labels"].append(label)
    for label in payload.get("remove_labels", "").split(","):
        if label in issue["labels"]:
            issue["labels"].remove(label)
respond(issue)
