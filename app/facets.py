"""Stack facets: the shared vocabulary that joins repos, sessions, announcements and posts in the graph.

A facet is a coarse technology surface ("actions", "mcp", "npm"). Sessions and announcements are tagged by
keyword lexicon (and optionally an LLM), repos are tagged from their manifests, so a question like
"what announced today touches my repo" is the path Repo -[:USES]-> Facet <-[:AFFECTS]- Announcement.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.request

LEXICON: dict[str, list[str]] = {
    "actions": ["github actions", "workflow", "ci/cd", "self-hosted runner", "runners", "actions"],
    "copilot": ["copilot"],
    "coding-agent": ["coding agent", "copilot agent", "agent hq", "mission control", "assign to copilot", "@copilot"],
    "copilot-cli": ["copilot cli", "in the terminal", "cli"],
    "custom-agents": ["custom agent", "agents.md", "agent skills", "skills"],
    "mcp": ["mcp", "model context protocol", "mcp server", "mcp registry"],
    "code-review": ["code review", "pull request", "pr review", "reviews"],
    "code-quality": ["code quality", "maintainability", "tech debt"],
    "security": ["secret scanning", "code scanning", "dependabot", "supply chain", "vulnerab", "codeql", "advanced security", "immutable releases", "sigstore", "attestation"],
    "enterprise": ["enterprise", "admin", "governance", "control plane", "policy", "compliance", "usage metrics"],
    "npm": ["npm", "node.js", "nodejs", "javascript", "typescript", "pnpm", "bun"],
    "python": ["python", "pypi", "pip "],
    "go": ["golang", " go "],
    "rust": ["rust", "cargo"],
    "java": ["java", "jetbrains", "intellij", "maven", "gradle", "spring"],
    "dotnet": [".net", "c#", "visual studio "],
    "vscode": ["vs code", "visual studio code"],
    "containers": ["docker", "container", "kubernetes", "k8s", "helm"],
    "open-source": ["open source", "maintainer", "oss", "sponsors"],
    "ai-models": ["github models", "llm", "openai", "anthropic", "claude", "gpt", "gemini", "grok", "model"],
    "spark": ["github spark"],
    "codespaces": ["codespaces", "dev container"],
    "issues-projects": ["github issues", "projects", "sub-issues", "issue types"],
    "education": ["students", "education", "classroom", "learning"],
    "octoverse": ["octoverse"],
    "accessibility": ["accessib"],
    "platform-engineering": ["platform engineering", "developer experience", "devex", "inner source"],
    "migration": ["migration", "modernization", "legacy"],
    "data-insights": ["metrics", "dashboard", "insights", "analytics", "measure"],
}

FACET_NAMES = sorted(LEXICON)


def facets_for_text(text: str, limit: int = 6) -> list[str]:
    t = " " + (text or "").lower() + " "
    scored = []
    for facet, kws in LEXICON.items():
        hits = sum(t.count(k.lower()) for k in kws)
        if hits:
            scored.append((hits, facet))
    scored.sort(reverse=True)
    return [f for _, f in scored[:limit]]


# ----- repos -------------------------------------------------------------------------------
_GH = re.compile(r"github\.com/([^/\s]+)/([^/\s#?]+)")


def _gh_json(url: str) -> dict | list | None:
    req = urllib.request.Request(url, headers={"User-Agent": "Backstage", "Accept": "application/vnd.github+json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=20))
    except Exception:
        return None


def repo_facets(repo_url: str) -> dict:
    """Read a public repo's manifests via the GitHub API and derive its stack facets with evidence."""
    m = _GH.search(repo_url)
    if not m:
        return {"error": "not a github.com repo url"}
    owner, name = m.group(1), m.group(2).removesuffix(".git")
    meta = _gh_json(f"https://api.github.com/repos/{owner}/{name}") or {}
    default = meta.get("default_branch", "main")
    tree = _gh_json(f"https://api.github.com/repos/{owner}/{name}/git/trees/{default}?recursive=1") or {}
    paths = [t["path"] for t in tree.get("tree", [])][:4000]
    langs = _gh_json(f"https://api.github.com/repos/{owner}/{name}/languages") or {}
    evidence: dict[str, list[str]] = {}

    def add(f: str, why: str):
        evidence.setdefault(f, [])
        if why not in evidence[f]:
            evidence[f].append(why)

    for p in paths:
        pl = p.lower()
        if pl.startswith(".github/workflows/"):
            add("actions", p)
        if pl in ("package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lockb"):
            add("npm", p)
        if pl in ("tsconfig.json",) or pl.endswith(".ts") and "node_modules" not in pl:
            add("npm", "TypeScript sources")
        if pl in ("requirements.txt", "pyproject.toml", "setup.py", "pipfile", "uv.lock"):
            add("python", p)
        if pl in ("go.mod",):
            add("go", p)
        if pl in ("cargo.toml",):
            add("rust", p)
        if pl.endswith(("pom.xml", "build.gradle", "build.gradle.kts")):
            add("java", p)
        if pl.endswith((".csproj", ".sln")):
            add("dotnet", p)
        if pl in ("dockerfile", "docker-compose.yml", "compose.yaml") or pl.endswith("/dockerfile"):
            add("containers", p)
        if pl in (".github/dependabot.yml", ".github/dependabot.yaml"):
            add("security", p)
        if "codeql" in pl:
            add("security", p)
        if pl in (".devcontainer/devcontainer.json",):
            add("codespaces", p)
        if pl in (".mcp.json", "mcp.json") or "mcp" in pl.split("/")[-1]:
            add("mcp", p)
        if pl in ("agents.md", ".github/copilot-instructions.md", "claude.md") or pl.startswith(".github/agents/"):
            add("custom-agents", p)
        if pl.startswith(".github/instructions/") or pl.startswith(".github/skills/") or pl.startswith(".claude/skills/"):
            add("custom-agents", p)
        if pl.startswith(".vscode/"):
            add("vscode", p)
    for lang in langs:
        ll = lang.lower()
        if ll in ("javascript", "typescript"):
            add("npm", f"language: {lang}")
        elif ll == "python":
            add("python", f"language: {lang}")
        elif ll == "go":
            add("go", f"language: {lang}")
        elif ll == "rust":
            add("rust", f"language: {lang}")
        elif ll in ("java", "kotlin"):
            add("java", f"language: {lang}")
        elif ll == "c#":
            add("dotnet", f"language: {lang}")
    # package.json dependencies -> ai-models / mcp hints
    if "package.json" in [p.lower() for p in paths]:
        pj = _gh_json(f"https://api.github.com/repos/{owner}/{name}/contents/package.json?ref={default}")
        if pj and pj.get("content"):
            try:
                deps = json.loads(base64.b64decode(pj["content"]).decode("utf-8", "ignore"))
                alldeps = {**deps.get("dependencies", {}), **deps.get("devDependencies", {})}
                for d in alldeps:
                    dl = d.lower()
                    if "modelcontextprotocol" in dl or dl.endswith("/mcp") or dl == "mcp":
                        add("mcp", f"dependency: {d}")
                    if dl in ("openai", "@anthropic-ai/sdk", "ai", "@ai-sdk/openai", "langchain", "@google/generative-ai"):
                        add("ai-models", f"dependency: {d}")
                    if dl == "typescript":
                        add("npm", "dependency: typescript")
            except Exception:
                pass
    if meta:
        add("open-source", f"public repo, {meta.get('stargazers_count', 0)} stars") if not meta.get("private") else None
    # every GitHub-hosted repo is touched by Copilot code review / coding agent announcements
    add("code-review", "pull requests on GitHub")
    add("coding-agent", "hosted on GitHub")
    return {
        "url": f"https://github.com/{owner}/{name}", "name": f"{owner}/{name}", "default_branch": default,
        "description": meta.get("description") or "", "stars": meta.get("stargazers_count", 0),
        "languages": list(langs)[:6], "facets": list(evidence), "evidence": evidence,
    }
