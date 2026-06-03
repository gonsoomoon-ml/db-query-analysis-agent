# CLAUDE.md

## Language / 응답

When responding to the user (here, in the terminal), write in **Korean**. (Code identifiers, file paths, and git commit messages stay English.)

## Base code

This project is based on the workshop repository:
**https://github.com/gonsoomoon-ml/aiops-multi-agent-workshop**

Reference this repo as the foundation for the AIOps multi-agent work (DB query analysis agent, SRE agent, cost analyzer). See `design/requirement.md` for the project goals and existing agent configurations.

## Reference repos

- **https://github.com/gonsoomoon-ml/developer-briefing-agent** — 대화형 챗봇 코드 및 스트리밍 참조 (conversational chatbot code & streaming reference). Source of the `local-agent ↔ managed-agentcore` split, single `create_agent()` truth source, and `prompts/system_prompt.md` externalization patterns.
