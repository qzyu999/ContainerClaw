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
- [Existing] /stop should immediately trigger the agent loop to end
- [Existing] /automation=X should adjust the number of automated votying ccycles
- * [Planned] /clear-workspace to clear workspace from the human chat interface
- [Planned] /normal=true/false to simplify the agent context window so they talk more normally and use less (or no) tooling
- [Planned] /tool-mute and /tool-unmute to remove the tool outputs from the main chatroom
- * [Planned] Snorkel: A way to see the actual context window for each agent
- [Planned] Subagents that work independently and async with the main chatroom
- [Planned] Telemetry/querying into the underlying Fluss tables, e.g., SELECT * FROM table WHERE user=Alice
- [Planned] Indicator for each agent (and subagents) for status (e.g., waiting, thinking, using tools, etc.)
- [Planned] Live Flink metrics on Fluss streams
- [Planned] Tier into Iceberg tables (after compaction etc. is fixed)

Milestones
- Add all the basic functionality - then refactor for cleanliness/modularity/efficient idempotent modular system around the stream -> document the stream-centric approach which should also be agent-centric (Noted with *)