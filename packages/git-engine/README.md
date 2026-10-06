# Git Engine (`packages/git-engine`)

The **Git Engine** governs all repository interactions, branch management, commit attribution, cryptographic commit signing, and automated GitHub event ingestion (webhooks).

---

## 1. Features
* **Co-Author Attribution:** Automatically appends standard Git trailers (`Co-authored-by: Name <email>`) honoring human engineers and the RocketChat Bot.
* **Cryptographic Commit Signing:**
  * **GitHub App API Mode:** Uses GitHub App installation credentials for official GitHub **Verified** badges.
  * **GPG Key Mode:** Injects and configures GPG signing inside the isolated sandbox (`git commit -S`).
* **GitHub Webhook Ingestion:**
  * Validates HMAC SHA-256 signatures with constant-time equality check.
  * Listens to `issues.labeled` (`ai-fix`) and `issue_comment.created` (`@RocketChat`).
  * Automated Session Resume: Wakes hibernated sandbox containers, mounts persistent volumes, and triggers agent turns without duplicating resources.
* **100% Optional Integration:** The platform operates seamlessly with local repositories and scratchpads when GitHub credentials are not present.

---

## 2. Installation & Verification

Installed via the workspace root:
```bash
uv sync
```

Running unit and integration tests:
```bash
uv run pytest tests/unit/test_git_engine.py -v
uv run pytest tests/integration/test_git_and_webhook_interoperability.py -v
```

---

## 3. Environment Variables
* `GITHUB_APP_ID`: (Optional) GitHub App ID.
* `GITHUB_APP_PRIVATE_KEY`: (Optional) PEM private key string or path.
* `GITHUB_WEBHOOK_SECRET`: (Optional) HMAC secret for webhook signature verification.
* `GITHUB_TRIGGER_LABEL`: (Optional) Label triggering automated issue fix (defaults to `ai-fix`).
* `GIT_BOT_NAME`: (Optional) Bot committer name (defaults to `RocketChat Bot`).
* `GIT_BOT_EMAIL`: (Optional) Bot committer email (defaults to `bot@rocketchat.internal`).
