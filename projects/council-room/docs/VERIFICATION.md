# Windows package verification

The Council Room source was adapted and tested natively on Windows from a path containing spaces.

Verified:

- Python package build and installation on Windows
- Windows ACL restriction for generated state and configuration files
- `kc.bat` and `java.exe` runtime selection
- Windows x64 Temurin archive URL and published SHA-256
- Safe ZIP and TAR runtime extraction path checks
- Windows Codex pipe/timeout process path without POSIX selectors or process groups
- Source-relative `.cmd` launchers that require no PowerShell execution-policy change
- SQLite connections close cleanly so temporary databases can be removed on Windows
- 371 Python tests passed; five POSIX-only fixture/mode tests were skipped
- 24 Node browser workflow tests passed
- Synthetic signed-token Council Room demonstration passed

Not performed:

- Fresh live Keycloak realm provisioning and Windows Hello enrollment
- Live Gemini or Codex provider calls
- Public/LAN deployment
- File attachments
- Production-hardening item F-04 or live rollback

The package contains no live credentials, private databases, provider tokens, or personal conversation history. Optional providers remain disabled until configured locally.
