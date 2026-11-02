"""GitHub and GitLab issue access through the user's authenticated CLI."""
import json
import re
import subprocess
from urllib.parse import quote, urlencode, urlsplit


BLOCKED_LABEL = "ralph:blocked"


class ApiError(ValueError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def cli_json(command, repo, payload=None):
    try:
        result = subprocess.run(command, cwd=repo, text=True, capture_output=True,
                                input=json.dumps(payload) if payload is not None else None, timeout=45)
    except subprocess.TimeoutExpired as error:
        raise ApiError(f"{command[0]} request timed out; retained run can be resumed") from error
    if result.returncode:
        detail = result.stderr.strip()
        status = re.search(r"HTTP[ /](\d{3})", detail)
        raise ApiError(f"{command[0]} request failed: {detail}", int(status[1]) if status else None)
    try:
        return json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError as error:
        raise ApiError(f"{command[0]} returned invalid JSON") from error


def remote_config(tracker, project, labels, repo):
    if not project:
        command = (["gh", "repo", "view", "--json", "url"] if tracker == "github"
                   else ["glab", "repo", "view", "-F", "json"])
        info = cli_json(command, repo)
        project = info["url" if tracker == "github" else "web_url"]
    if "://" not in project:
        project = f"https://{'github.com' if tracker == 'github' else 'gitlab.com'}/{project}"
    parsed = urlsplit(project)
    path = parsed.path.strip("/").removesuffix(".git")
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment or len(path.split("/")) < 2
            or any(not part or part in {".", ".."} for part in path.split("/"))
            or (tracker == "github" and len(path.split("/")) != 2)):
        raise ValueError("--project must be a repository path or repository URL")
    return dict(tracker=tracker, host=parsed.netloc, project=path,
                labels=sorted(set(labels or ["ready-for-agent"])))


class RemoteIssues:
    def __init__(self, config, repo):
        self.config, self.repo = config, repo
        self.github = config["tracker"] == "github"
        self.cli = "gh" if self.github else "glab"
        self.root = (f"repos/{config['project']}" if self.github
                     else "projects/" + quote(config["project"], safe=""))

    def api(self, endpoint, method="GET", payload=None):
        command = [self.cli, "api", "--hostname", self.config["host"], endpoint, "--method", method]
        if payload is not None:
            command += ["--input", "-"]
        return cli_json(command, self.repo, payload)

    def pages(self, endpoint, **params):
        page = 1
        while True:
            items = self.api(endpoint + "?" + urlencode(dict(params, per_page=100, page=page)))
            if not isinstance(items, list):
                raise ApiError(f"Expected an issue list from {self.cli}")
            yield from items
            if len(items) < 100:
                break
            page += 1

    def issue(self, number):
        issue = self.api(f"{self.root}/issues/{number}")
        if "pull_request" in issue:
            raise ValueError(f"#{number} is a pull request, not an issue")
        return issue

    def closed(self, issue):
        if issue.get("state") not in {"open", "opened", "closed"}:
            raise ApiError("Issue has no recognized state; cannot verify dependencies")
        return issue["state"] == "closed"

    def text_dependencies(self, body):
        """Read only explicit Blocked by fields, never unrelated #references."""
        sections = re.findall(r"^#{1,6}[ \t]+Blocked by[ \t]*\r?\n(.*?)(?=^#{1,6} |\Z)",
                              body, re.M | re.S | re.I)
        sections += re.findall(r"^(?:\*\*)?Blocked by:(?:\*\*)?[ \t]*(.*)$", body, re.M | re.I)
        if not sections:
            return None
        dependencies = []
        pattern = (r"https?://[^\s<>\)\]]+/(?:-/)?issues/\d+"
                   r"|(?:[\w.-]+/)*[\w.-]+#\d+|(?<![\w/])#\d+")
        for section in sections:
            refs = re.findall(pattern, section)
            if not refs and not re.fullmatch(r"[\s.*-]*(?:None(?: \(can start immediately\))?|No (?:blockers|dependencies))[\s.*-]*",
                                             section, re.I):
                raise ValueError("Unrecognized Blocked by field; use issue references or None")
            for ref in refs:
                if "://" in ref:
                    parsed = urlsplit(ref)
                    match = re.fullmatch(r"/(.+?)/(?:-/)?issues/(\d+)", parsed.path)
                    if parsed.netloc != self.config["host"] or not match:
                        raise ValueError(f"Cannot verify blocker on another tracker: {ref}")
                    project, number = match.groups()
                else:
                    project, number = ref.rsplit("#", 1)
                    project = project or self.config["project"]
                root = f"repos/{project}" if self.github else "projects/" + quote(project, safe="")
                blocker = self.api(f"{root}/issues/{number}")
                if "pull_request" in blocker:
                    raise ValueError(f"Blocker {ref} is a pull request, not an issue")
                dependencies.append(blocker)
        return dependencies

    def dependencies(self, issue):
        number = issue["number" if self.github else "iid"]
        text = self.text_dependencies(issue.get("body" if self.github else "description") or "")
        endpoint = (f"{self.root}/issues/{number}/dependencies/blocked_by" if self.github
                    else f"{self.root}/issues/{number}/links")
        try:
            # GitLab's links endpoint returns the full list and ignores page.
            native = list(self.pages(endpoint)) if self.github else self.api(endpoint)
            if not isinstance(native, list):
                raise ApiError(f"Expected dependency list from {self.cli}")
        except ApiError as error:
            # Older servers without native links can still use an explicit field.
            # Authentication/network failures must never make an issue runnable.
            if error.status not in {404, 410} or text is None:
                raise
            native = []
        if not self.github:
            native = [item for item in native if item["link_type"] == "is_blocked_by"]
        dependencies = native + (text or [])
        blocked = any(not self.closed(item) for item in dependencies)
        if self.github:
            blocked |= issue.get("issue_dependencies_summary", {}).get("blocked_by", 0) > 0
        urls = [item["html_url" if self.github else "web_url"] for item in dependencies]
        return blocked, sorted(set(urls))

    def normalize(self, issue):
        number = str(issue["number" if self.github else "iid"])
        labels = [x["name"] for x in issue["labels"]] if self.github else issue["labels"]
        status = "done" if self.closed(issue) else ("blocked" if BLOCKED_LABEL in labels else "ready-for-agent")
        blocked, urls = self.dependencies(issue)
        return dict(number=number, title=issue["title"],
                    url=issue["html_url" if self.github else "web_url"],
                    body=issue.get("body" if self.github else "description") or "",
                    status=status, blockers=[], blocked=blocked, dependencies=urls)

    def load(self):
        found = {}
        params = dict(state="open" if self.github else "opened", labels=",".join(self.config["labels"]))
        if not self.github:
            params["scope"] = "all"
        for issue in self.pages(f"{self.root}/issues", **params):
            if "pull_request" in issue:
                continue
            item = self.normalize(issue)
            found[item["number"]] = item
        return dict(sorted(found.items(), key=lambda pair: int(pair[0])))

    def current(self, number):
        return self.normalize(self.issue(number))

    def mark(self, number, status):
        endpoint = f"{self.root}/issues/{number}"
        if status == "done":
            if self.github:
                self.mark(number, "ready-for-agent")
            payload = ({"state": "closed", "state_reason": "completed"} if self.github
                       else {"state_event": "close", "remove_labels": BLOCKED_LABEL})
            self.api(endpoint, "PATCH" if self.github else "PUT", payload)
        elif self.github:
            if status == "blocked":
                # GitHub requires the label to exist before it can be attached.
                label = f"{self.root}/labels/{quote(BLOCKED_LABEL, safe='')}"
                try:
                    self.api(label)
                except ApiError as error:
                    if error.status != 404:
                        raise
                    self.api(f"{self.root}/labels", "POST", {"name": BLOCKED_LABEL, "color": "d93f0b"})
                self.api(endpoint + "/labels", "POST", {"labels": [BLOCKED_LABEL]})
            else:
                try:
                    self.api(endpoint + "/labels/" + quote(BLOCKED_LABEL, safe=""), "DELETE")
                except ApiError as error:
                    if error.status != 404:
                        raise
        else:
            field = "add_labels" if status == "blocked" else "remove_labels"
            self.api(endpoint, "PUT", {field: BLOCKED_LABEL})
