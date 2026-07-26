from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "deployment" / "windows-service-operations.md"
NGINX_RUNBOOK = ROOT / "docs" / "deployment" / "windows-nginx.md"
RECOVERY_RUNBOOK = ROOT / "docs" / "deployment" / "windows-sqlite-recovery.md"
README = ROOT / "README.md"
SCRIPTS = ROOT / "scripts"

SECTION_FIELDS = (
    "Preconditions",
    "Authorized roles",
    "State and timeouts",
    "Pass/fail",
    "Escalation/rollback",
    "Trading-Safe State",
    "Evidence",
    "Traceability",
)


def _text(path: Path = RUNBOOK) -> str:
    return path.read_text(encoding="utf-8")


def _sections(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^## (.+)$", text, re.MULTILINE))
    return [
        (
            match.group(1),
            text[match.end() : matches[index + 1].start()]
            if index + 1 < len(matches)
            else text[match.end() :],
        )
        for index, match in enumerate(matches)
    ]


def _powershell_blocks(text: str) -> list[str]:
    return re.findall(r"```powershell\s*\n(.*?)```", text, re.DOTALL)


def _powershell() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell")


def test_every_runbook_section_has_complete_operator_contract() -> None:
    sections = _sections(_text())
    assert len(sections) >= 16
    for title, body in sections:
        for field in SECTION_FIELDS:
            assert f"**{field}:**" in body, f"{title}: missing {field}"


def test_runbook_covers_required_flows_and_exact_native_topology() -> None:
    text = _text()
    lowered = text.casefold()
    required = (
        "topology", "setup", "preflight", "ordered start", "planned stop",
        "restart", "reboot", "native update", "rollback", "crash loop",
        "backend failure", "nginx failure", "windows update", "certificate",
        "disk", "log failure", "monitoring", "hardening", "acl",
        "secret lifecycle", "restore hold", "disaster recovery",
        "operator evidence package", "isolated 90-day runbook drill",
    )
    assert all(term in lowered for term in required)
    assert "backend\\.venv\\scripts\\python.exe" in lowered
    assert (
        "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1"
        in text
    )
    assert "frontend/dist" in text
    assert "Nginx alone owns public HTTP/HTTPS" in text
    assert "Canonical selection is **NSSM**" in text
    assert "PM2 is a mutually exclusive approved alternative" in text
    assert "TradingBotNginx" in text and "depends on `TradingBotBackend`" in text
def test_readiness_liveness_restore_and_evidence_boundaries_are_explicit() -> None:
    text = _text()
    lowered = text.casefold()
    assert "`/healthz` is static **Edge Liveness** only" in text
    assert "**Backend Readiness** is exact `/api/v1/health/readiness`" in text
    assert "first over loopback and then through Nginx" in text
    assert "does **not** assert that any production host" in text
    assert "offline `PLAN`/PowerShell `WhatIf` tools by default" in text
    assert "reviewed host adapter" in lowered
    assert "repository supplies no production execution adapter" in lowered
    assert "at least 180 days" in lowered
    assert "distinct reviewer" in lowered
    assert "references rather than raw logs" in lowered
    assert "at least every 90 days" in lowered
    assert "all eight scenarios pass independently" in lowered
    assert "critical capacity update block" in lowered
    assert "restore hold handoff" in lowered
    assert "zero broker mutation" in lowered
    restore = dict(_sections(text))["12. Restore Hold handoff to Milestone 10.7"]
    for contract in (
        "manual recovery runbook", "every SQLite writer proven offline",
        "dry-run", "forensic", "post-check", "two-person sign-off",
        "do not auto-start", "first post-restore start", "full Startup Gate",
    ):
        assert contract.casefold() in restore.casefold()
    assert "windows-sqlite-recovery.md" in restore


def test_commands_are_existing_plan_wrappers_and_never_unguarded_execute() -> None:
    text = _text()
    blocks = _powershell_blocks(text)
    assert blocks
    commands = re.findall(r"\.\\scripts\\([A-Za-z0-9.-]+\.ps1)", "\n".join(blocks))
    assert commands
    for command in commands:
        assert (SCRIPTS / command).is_file(), command
    for line in "\n".join(blocks).splitlines():
        if "-Execute" in line:
            assert "-WhatIf" in line
    forbidden_commands = (
        "Start-Service", "Stop-Service", "Restart-Service", "Restart-Computer",
        "shutdown.exe", "nssm start", "nssm stop", "pm2 start", "pm2 stop",
        "Restore-Database.ps1", "nginx.exe -s reload",
    )
    assert not any(command.casefold() in "\n".join(blocks).casefold()
                   for command in forbidden_commands)


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is unavailable")
def test_all_documented_powershell_examples_parse_offline() -> None:
    shell = _powershell()
    assert shell is not None
    for index, block in enumerate(_powershell_blocks(_text())):
        command = (
            "$source = [Console]::In.ReadToEnd(); $tokens = $null; "
            "$errors = $null; "
            "[void][System.Management.Automation.Language.Parser]::ParseInput("
            "$source, [ref]$tokens, [ref]$errors); "
            "if ($errors.Count -ne 0) { $errors | Out-String | Write-Error; exit 1 }"
        )
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", command],
            input=block,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, f"block {index}: {result.stdout}{result.stderr}"
def test_local_markdown_links_resolve_and_cross_references_are_bidirectional() -> None:
    for source in (RUNBOOK, NGINX_RUNBOOK, RECOVERY_RUNBOOK, README):
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", _text(source)):
            if re.match(r"^[a-z]+://", target, re.IGNORECASE):
                continue
            relative = target.split("#", 1)[0]
            if not relative:
                continue
            assert (source.parent / relative).resolve().is_file(), (
                f"{source.name}: broken link {target}"
            )
    assert "windows-service-operations.md" in _text(NGINX_RUNBOOK)
    assert "windows-service-operations.md" in _text(RECOVERY_RUNBOOK)
    assert "docs/deployment/windows-service-operations.md" in _text(README)


def test_prohibited_instruction_and_secret_boundaries() -> None:
    text = _text()
    lowered = text.casefold()
    prohibited_instructions = (
        "install docker", "docker run", "docker compose", "kubectl ",
        "helm install", "podman run", "terraform apply", "deploy to aws",
        "deploy to azure", "deploy to gcp", "--host 0.0.0.0",
        "--workers 2", "--workers 3", "automatically connect mt5",
        "automatically start demo", "automatically start paper",
        "schedule restore", "automatically restore", "public uvicorn fallback",
    )
    assert not any(item in lowered for item in prohibited_instructions)
    assert not re.search(r"(?:password|token|secret|private[_ -]?key)\s*=\s*\S+", text, re.I)
    assert "never private-key content" in lowered
    assert "no production database/secret/account/network" in lowered


def test_traceability_covers_task_group_12_and_required_requirements() -> None:
    text = _text()
    traceability = "\n".join(
        body for _, body in _sections(text) if "**Traceability:**" in body
    )
    for task in ("12.1", "12.2", "12.3", "12.4", "12.5", "12.6"):
        assert task in traceability
    for requirement in ("14.1", "14.4", "14.6", "14.7", "14.14", "15.20", "15.25"):
        assert requirement in traceability


def test_existing_runbooks_keep_authoritative_boundaries() -> None:
    nginx = _text(NGINX_RUNBOOK)
    recovery = _text(RECOVERY_RUNBOOK)
    assert "Static `/healthz` hanya membuktikan **Edge Liveness**" in nginx
    assert "`/api/v1/health/readiness`" in nginx
    assert "Jangan mempublikasikan Uvicorn sebagai fallback" in nginx
    for contract in (
        "Restore Hold", "manual, backup-ID-driven, offline",
        "no-auto-start on success or failure", "two-person sign-off",
        "first post-restore start", "Backend Readiness", "Edge Liveness only",
    ):
        assert contract.casefold() in recovery.casefold()
