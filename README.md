# CCDI Federation AI

[![Install via npx skills](https://img.shields.io/badge/npx%20skills-install-blue)](https://www.skills.sh/cbiit/ccdi-federation-ai/ccdi-federation-ai-copilot)
[![snyk-security-scan](https://img.shields.io/github/actions/workflow/status/CBIIT/ccdi-federation-ai/snyk-security-scan.yml)](https://github.com/CBIIT/ccdi-federation-ai/actions)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Last Update](https://img.shields.io/github/last-commit/CBIIT/ccdi-federation-ai?label=Last%20update&style=classic)](https://github.com/CBIIT/ccdi-federation-ai)
[![Snyk Check](https://github.com/CBIIT/ccdi-federation-ai/actions/workflows/snyk-security-scan.yml/badge.svg)](https://github.com/CBIIT/ccdi-federation-ai/actions)

An AI agent skill for the [CCDI Data Federation API](https://ccdi.cancer.gov/data-federation-resource). This repository provides components for AI-ready CCDI Federation metadata, including an AgentSkill that standardizes AI interactions through workflows and routing.

## Skill Structure

Each skill directory contains:

- **SKILL.md** — Main instruction file with YAML frontmatter, routing rules, guardrails, and response style guidelines
- **scripts/** — Helper scripts and utilities (PV mapper, fallback API client)
- **references/** — OpenAPI spec, permissible-value metadata, and workflow references
- **agents/** — Agent-specific configuration

Repository layout:

- `skills/ccdi-federation-ai-copilot/` — AgentSkill docs, routing, and fallback scripts
- `docs/instructions/` — End-user and developer setup guides
- `mcp/c3dc/` — Separate, read-only MCP server for Clinical Commons GraphQL and CCDI model `Enum` values ([setup](mcp/c3dc/README.md))
- `mcp/ins/` — Separate, read-only MCP server for the Index of NCI Studies GraphQL API ([setup](mcp/ins/README.md))
- `mcp/ctdc/` — Separate, read-only MCP server for Clinical Data Commons GraphQL, without permissible values ([setup](mcp/ctdc/README.md))
- `mcp/icdc/` — Separate, read-only MCP server for Integrated Canine Data Commons GraphQL, without permissible values ([setup](mcp/icdc/README.md))
- `mcp/ccdi_dcc/` — Separate, read-only MCP server for CCDI DCC GraphQL and the same CCDI Model `Enum` values ([setup](mcp/ccdi_dcc/README.md))
- `mcp/popsci/` — Separate, read-only MCP server for Population Science study-level GraphQL metadata and PopSci Model `Enum` values ([setup](mcp/popsci/README.md))
- `mcp/cbioportal/` — Separate, read-only MCP server for CCDI cBioPortal REST operations, without a domain permissible-value catalog ([setup](mcp/cbioportal/README.md))
- `mcp/ccdc/` — Separate, read-only MCP server for Childhood Cancer Data Catalog participating resources and their linked dataset summaries ([setup](mcp/ccdc/README.md))
- `mcp/ods_content/` — Read-only MCP server for NCI Data Sharing Hub Markdown pages ([setup](mcp/ods_content/README.md))

## Getting Started

### Install Skill from GUI

Follow the step-by-step guide to install and use the skill on Chatgpt:

- [docs/instructions/chatgpt-instruction.md](./docs/instructions/chatgpt-instruction.md)

![Using the skill in chatgpt](./docs/instructions/images/9-codex-use-skill2.png)

### Use NPX to install a skill

Install the skill bundle via [npx](https://docs.npmjs.com/cli/commands/npx):

```bash
npx skills add CBIIT/ccdi-federation-ai
```

## Links

- [CCDI Data Federation](https://ccdi.cancer.gov/data-federation-resource)
- [CCDI Data Federation API documentation](https://cbiit.github.io/ccdi-federation-api/overview.html)
- [Childhood Cancer Data Initiative (CCDI)](https://ccdi.cancer.gov/)

## Disclaimer

This software is provided as-is for research and data exploration purposes. It is maintained by the [Center for Biomedical Informatics and Information Technology (CBIIT)](https://www.cancer.gov/about-nci/organization/cbiit) at the National Cancer Institute. All API calls made through this skill are read-only and access only publicly available CCDI Federation metadata.
