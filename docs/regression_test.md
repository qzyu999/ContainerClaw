# Regression Testing
This document shows the full list of features that should remain stable as the codebase iterates.

- [Existing] On launch, the webapp should generate a new chat session
- [Existing] The chat session should not begin until the human user enters some text
- * [Planned] /commands should not trigger the agent loop
- [Existing] The agent loop should default to 0, with 1, 2, ..., N fully automated voting cycles allowed before halting and waiting for human input, can be set to -1 for infinite looping
    - * [Planned]The UI should have an option to adjust this, where it defaults to 0
    - [Planned] This information should be made available in the agent awareness
- [Existing] There should be a limit to the number of tool calls an agent can do in a single turn
    - * [Planned] The UI should also have an option to adjust this, where it defaults to 5
    - [Planned] This information should be made available in the agent awareness
- [Existing] /subagents
- [Existing] /cancel_subagent=<task_id>
- [Existing] /stop should immediately trigger the agent loop to end
- [Existing] /automation=X should adjust the number of automated votying ccycles
- * [Planned] /clear-workspace to clear workspace from the human chat interface
- [Planned] /normal=true/false to simplify the agent context window so they talk more normally and use less (or no) tooling
- [Planned] /tool-mute and /tool-unmute to remove the tool outputs from the main chatroom
- [Planned] Filter (via web UI)/mute tool-related actions in chatroom
- * [Planned] Snorkel: A way to see the actual context window for each agent
- [Planned] DeerFlow-style JSON memories for AI context HUD
- [Existing] Subagents that work independently and async with the main chatroom
- [Planned] Telemetry/querying into the underlying Fluss tables, e.g., SELECT * FROM table WHERE user=Alice
- [Planned] Indicator for each agent (and subagents) for status (e.g., waiting, thinking, using tools, etc.)
- [Planned] Live Flink metrics on Fluss streams (Starrocks may be better)
- [Planned] Tier into Iceberg tables (after compaction etc. is fixed)
- [Planned] Move config files to the root folder / config.yaml similar to DeerFlow
- [Planned] Final review agent that analyzes the votes/reasons and selects based on the collective output (based on GenSelect)
- [Planned] Integration: Google Workspace
- [Planned] Integration: Slack
- [Planned] Integration: GitHub
- [Planned] Integration: agent webbrowsing - allow them to do deep research etc. within the sandbox
- [Planned] Read-only access to other system files (may need to just do docker cp or mount large folders at startup with read-only)
- [Planned] Kaggle/autoresearch module: allow the agents to loop and improve on their own solutions through an API 
- [Planned] Kubernetes integrations
- [Planned] Project board seems to require manual refresh quite often
- [Planned] Visualization tab of the agentic DAG
- [Planned] README.md for all the commands and how to use them
- [Planned] Add enable/disable for integrations like Discord etc.
- [Planned] Able to edit the agent roster and prompts dynamically from UI/CLI
- [Planned] Turtle concept for problem density progressing over time
- [Planned] Refactor - abstract all the different verticals into a simpler plane like a data mesh where everything can be accessed/changed easily

Milestones
- [x] Add all the basic functionality - then refactor for cleanliness/modularity/efficient idempotent modular system around the stream -> document the stream-centric approach which should also be agent-centric. In particular, a concept for spawning subagents to organically allow for parallelization (swarm > static patterns) (Noted with *)
- [x] Refactor for an agnostic LLM API (Gemini, OpenAI, Anthropic, Ollama, MLX, etc.), where the number of primary voting agents, their API, and description can be customized (e.g. Agent('Alice', 'Software architect.', 'MLX'), Agent('Bob', 'Program Manager.', 'Gemini')) in a config.yaml. The entire repo's specific config files (include .env files and other global variables) can all be routed towards this single root-level config file. 
- [] Integrate a SELF.md and MEMORY.json (DeerFlow-inspired). It would also make sense for the config.yaml to allow for editing the prompts that go towards the voting process.
- [] Snorkel and DAG nodes should allow for a first-level telemetry into agent activity

Bugs
- [x] ⚠️ [StreamActivity] Poll error: - after long period of silence with agents