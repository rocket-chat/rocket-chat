---
title: Skills from a repository
description: Connect a git repository of skills, import what you need, and let the repository stay their source of truth.
---

# Skills from a repository

Keep your skills in a git repository, versioned and reviewed like code, and let Rocket Chat follow it. You **connect** a repository as a source, **import** the skills you want for yourself, a team or the organization, and from then on the repository decides: a push updates the skills, a skill that leaves the repository is flagged, and a change you make to one becomes a pull request.

This is different from the skills a repository brings into a session when the agent clones it (`.rocket/skills/`, see [Skills](/use/extend#skills)): a source feeds the skills of **Settings → Skills**, available in every session, whatever repository the agent works on.

## What a repository offers

Any GitHub repository whose skills follow the Agent Skills format works: a skill is a folder holding a `SKILL.md` (YAML front matter with `name` and `description`, then the instructions), anywhere in the repository. The other files of that folder (scripts, references) travel with the skill. Nothing else about the layout is assumed.

- **Without a manifest**, every skill of the repository is offered on its own.
- **With `.claude-plugin/marketplace.json`** (a Claude plugin marketplace), each plugin it declares is offered as a collection of the skills under its folders (`source` and `skills` of the plugin; `skills/` by default). Skills no plugin claims are still offered, as **Standalone skills**. A plugin hosted in another repository is left out, with a note: connect that repository instead.

A `SKILL.md` that cannot be read (no front matter, no name) is listed with the reason and cannot be imported until it is fixed in the repository.

## Connecting a repository

**Settings → Sources → Connect a repository**: the repository (`owner/name` or its URL), the branch to follow (`main` by default), a display name, and the owner of the source, who decides who can import from it (you, a team or the organization, like any other object). Connecting reads the repository at once; when it cannot be read, the source is still connected and its card says why.

How the repository is read:

- **The organization's GitHub App** (the default): it must be installed on the repository, and the repository must be within the ones your organization lets the App reach ([GitHub](/administer/github)). Any owner can use it.
- **Your linked GitHub account**: for a personal source only. Your account (**Settings → Git identity**) must be able to read the repository, and the skills of such a source only go to your **Personal skills**: they cannot be imported for a team or the organization, nor moved there.

Who can connect: an administrator for the organization, a team's administrators for their team, and every member for themselves. Whoever can see a source can import from it; whoever manages it can synchronise or disconnect it.

## Importing skills

In **Settings → Skills**, **New skill** (or **New team skill**, **New org skill**) opens the editor with two modes: **Create new**, as before, or **Import from source**. Choose the source, then tick whole plugins or individual skills (a plugin's checkbox selects all of its skills; open it to choose some), and **Import**. The owner chosen in the editor is the owner of the imported skills: this is where a skill is assigned to a team, and nothing stops a team from importing another team's skills.

**A skill with the same name.** When the owner already has a skill with that name, nothing is imported and the editor lists the conflicts. Tick **Merge** on one to let the source's version replace the existing skill: it keeps its id (agents that name it still do) and follows the repository from then on. Skills you created before connecting a source are left as they are until you merge them.

An imported skill shows a badge with its source (and its plugin) that links to its folder on GitHub.

## Staying in sync

Rocket Chat reads a source again when its branch moves: a **push** webhook brings it forward, and every source is read at least every 15 minutes anyway. **Synchronise** on the source's card reads it now. Then:

- a skill whose folder changed is updated (name, description, instructions and files);
- a skill whose folder **left the repository** is deactivated and flagged *Out of sync*: it is no longer offered to agents, and the card invites you to delete it. If the folder comes back, the skill is reactivated;
- a skill whose `SKILL.md` became unreadable keeps its last good version and says why;
- a skill renamed in the repository onto a name another skill of its owner already has keeps its old name, and says so.

The push webhook needs the GitHub App to send **Push** events ([GitHub](/administer/github#4-subscribe-to-webhook-events)); without it, sources follow their repository every 15 minutes.

## Changing a synchronised skill

A synchronised skill is not edited in Rocket Chat. Its pencil reads **Propose a change**: edit the name, description or instructions and **Propose change**, and Rocket Chat opens a pull request on the repository, on a new `rocket/skill-<name>-…` branch from the branch the source follows, **with your linked GitHub account** (never the App). The front matter of `SKILL.md` is kept as written unless you change the name or description. The skill changes in Rocket Chat once the pull request is merged and the source is read again. If the file changed in the repository since the last read, the proposal is refused: synchronise, then propose again. Without a linked GitHub account, the editor tells you to link one.

Agents work the same way: when an agent with **Propose changes** proposes an edit to a synchronised skill ([Proposals](/use/extend#proposals-an-agent-improves-its-instructions-and-skills)), approving it opens the pull request as the reviewer, and the proposal links to it. The agent sees the pull request with `list_my_proposals`.

You can still turn a synchronised skill off, move it to another owner (except a skill of a personal source read with your account) or delete it.

## Files a skill brings

When the agent loads a synchronised skill, the other files of its folder are written under `/workspace/.rocket/skills/<name>/` in the session's sandbox (executables stay executable), and the instructions end with a note naming that folder: paths the instructions give relative to their folder (`scripts/run.sh`) resolve there. A file that is not UTF-8 text or is larger than 1 MB is not carried; the skill's source says which. An agent without a sandbox gets the instructions and the note, without the files.

## Disconnecting

Disconnecting a source keeps its skills: they are no longer synchronised and become ordinary skills you can edit.

## API

`GET /v1/external-sources`, `POST /v1/external-sources`, `PUT /v1/external-sources/{id}`, `DELETE /v1/external-sources/{id}`, `POST /v1/external-sources/{id}/sync`, `GET /v1/external-sources/{id}/catalogue`, `POST /v1/external-sources/{id}/import`, and `POST /v1/skills/{id}/change-request` for a change to a synchronised skill. Skills carry their `source` in `GET /v1/skills`. See the [REST reference](/api/rest). Connecting, changing, disconnecting, importing, synchronising (when something changed) and proposing a change are audited (`external_source.*`).
