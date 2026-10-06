# Slack Assistant (Socket Mode)

Rocket Chat features a native, embedded Slack Assistant built with `packages/slack-assistant` using the official `slack-bolt` async framework.

---

## 1. Zero-Ingress Architecture via Socket Mode

Traditional Slack bots require a public HTTPS endpoint with valid SSL certificates, public DNS, and open firewall ports to receive incoming webhook events.

Rocket Chat operates 100% via **Slack Socket Mode**:

```mermaid
flowchart LR
    SlackCloud["Slack Cloud API\n(Slack App Engine)"]
    Firewall{"Corporate Firewall / NAT / VPN\n(No Inbound Ports Opened)"}
    Rocket["Rocket Chat Backend\n(Embedded slack-bolt Async Task)"]

    Rocket -->|Outbound WebSocket (WSS)| Firewall
    Firewall -->|Encrypted WebSocket (TLS 1.3)| SlackCloud
    SlackCloud -.->|Stream Events over Existing WSS| Rocket
```

### Operational Advantages
- **Zero Public IP:** The backend never requires a public IP address or inbound firewall exceptions.
- **NAT / VPN Friendly:** Runs inside private enterprise VPCs, AWS private subnets, or developer laptops without ngrok or tunnels.
- **Simplified Deployment:** Requires zero ingress rules or TLS certificates in Kubernetes.

---

## 2. Interactive Thread Experience

The Slack Assistant integrates directly into Slack's threaded messaging model:

1. **Mention Trigger:** When an engineer mentions `@Rocket` in a channel or initiates a direct message:
   ```text
   @Rocket investigate why the test_auth_token test is failing on main.
   ```
2. **Immediate Acknowledgment:** The assistant instantly replies in a thread, initializing an avionics status spinner:
   ```text
   🚀 Initializing sandbox workspace and checking out repository...
   ```
3. **Live Thread Updates:** As the ReAct state machine executes, the assistant updates thread status messages to reflect progress:
   - `🔍 Reading tests/unit/test_auth_token.py (lines 1-80)...`
   - `⚡ Executing pytest tests/unit/test_auth_token.py in sandbox...`
   - `✍️ Applying precision fix to src/auth/token.py...`
   - `✅ All tests passing. Generated signed commit with Co-authored-by trailers.`

---

## 3. In-Stream Decision Gates in Slack

When the agent requires human intervention (via `ask_question`), it converts the interactive options into native **Slack Block Kit** elements:

- **Button Blocks:** For single-choice selections (e.g. `[Proceed with Plan]`, `[Modify Approach]`).
- **Select Menus:** For selecting branches, models, or target environments.
- **Modal Dialogs:** For entering detailed write-in specifications.

Execution in the sandbox is paused until the engineer clicks an option in Slack, resuming the ReAct turn in real time.

---

## 4. Markdown Transliteration

Standard Markdown (GitHub Flavored Markdown) is automatically transliterated into Slack's proprietary `mrkdwn` syntax:
- Standard links `[Title](url)` -> `<url|Title>`
- Bold formatting `**text**` -> `*text*`
- Code blocks and ANSI terminal snippets are formatted for optimal readability inside Slack message blocks.
